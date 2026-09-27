"""Pure pandas metric functions. No Streamlit imports, so everything here is unit-testable.

Every function takes the cleaned transactions frame (see scripts/build_dataset.py),
usually already narrowed by `filter_frame`, and returns a small aggregate.

Definitions used throughout:
- sales rows: product lines that are not cancellations (the ETL already dropped price <= 0
  and negative-quantity non-cancellations, so quantity and price are positive here)
- revenue: gross sales on sales rows, quantity * price, in GBP
- orders: distinct sales invoices
- cancellations: product lines on 'C' invoices; their revenue is negative
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
from scipy import sparse

from retail.countries import iso3, region

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ---------------------------------------------------------------- filtering helpers

def filter_frame(df: pd.DataFrame, start: date, end: date, countries: tuple[str, ...] = ()) -> pd.DataFrame:
    """Rows with invoice_date in [start, end] (inclusive days) and, if given, in `countries`."""
    ts = df["invoice_date"]
    mask = (ts >= pd.Timestamp(start)) & (ts < pd.Timestamp(end) + pd.Timedelta(days=1))
    if countries:
        mask &= df["country"].isin(countries)
    # The unfiltered view is the common case; skip copying the whole frame for it.
    # Callers treat the result as read-only either way.
    return df if mask.all() else df[mask]


def sales_rows(df: pd.DataFrame, cols: list[str] | None = None) -> pd.DataFrame:
    """Product sales lines. Pass `cols` to copy only those columns (the frame is 1M rows)."""
    mask = df["is_product"].to_numpy() & ~df["is_cancellation"].to_numpy()
    return df.loc[mask, cols] if cols is not None else df[mask]


def cancel_rows(df: pd.DataFrame, cols: list[str] | None = None) -> pd.DataFrame:
    mask = df["is_product"].to_numpy() & df["is_cancellation"].to_numpy()
    return df.loc[mask, cols] if cols is not None else df[mask]


def month_start(ts: pd.Series) -> pd.Series:
    # numpy month truncation; far cheaper than .dt.to_period("M") on a million rows
    return pd.Series(ts.to_numpy().astype("datetime64[M]").astype("datetime64[s]"), index=ts.index, name=ts.name)


def previous_period(start: date, end: date) -> tuple[date, date]:
    """The window of the same length immediately before [start, end]."""
    days = (end - start).days + 1
    return start - timedelta(days=days), start - timedelta(days=1)


# ---------------------------------------------------------------- KPIs & trends

def kpis(df: pd.DataFrame) -> dict[str, float]:
    # Works on masked numpy arrays instead of copying columns: this runs on every filter change.
    is_product = df["is_product"].to_numpy()
    is_cancel = df["is_cancellation"].to_numpy()
    sale, cancel = is_product & ~is_cancel, is_product & is_cancel
    revenue_col = df["revenue"].to_numpy()
    revenue = float(revenue_col[sale].sum())
    orders = int(np.unique(_codes(df["invoice"])[sale]).size)
    customers = df["customer_id"]
    has_id = sale & customers.notna().to_numpy()
    n_customers = int(np.unique(customers.to_numpy(dtype="int64", na_value=-1)[has_id]).size)
    cancelled = float(-revenue_col[cancel].sum())
    return {
        "revenue": revenue,
        "orders": orders,
        "customers": n_customers,
        "aov": revenue / orders if orders else 0.0,
        "units": int(df["quantity"].to_numpy()[sale].sum()),
        "cancelled_value": cancelled,
        "cancel_rate": cancelled / revenue if revenue else 0.0,
    }


def _codes(col: pd.Series) -> np.ndarray:
    """Integer codes for a categorical column (or a factorization of a plain one)."""
    return col.cat.codes.to_numpy() if isinstance(col.dtype, pd.CategoricalDtype) else pd.factorize(col)[0]


def kpi_deltas(current: dict[str, float], previous: dict[str, float] | None) -> dict[str, float | None]:
    """Relative change per KPI; the cancellation rate is compared in percentage points."""
    out: dict[str, float | None] = {}
    for key, value in current.items():
        if previous is None:
            out[key] = None
        elif key == "cancel_rate":
            out[key] = value - previous[key]
        else:
            prev = previous[key]
            out[key] = (value - prev) / prev if prev else None
    return out


def _partial_months(months: pd.Series, start: date, end: date) -> pd.Series:
    """True where the [start, end] window covers only part of the calendar month."""
    month_end = months + pd.offsets.MonthEnd(0)
    covered_from = months.where(months >= pd.Timestamp(start), pd.Timestamp(start))
    covered_to = month_end.where(month_end <= pd.Timestamp(end), pd.Timestamp(end))
    return (covered_to - covered_from).dt.days + 1 < months.dt.days_in_month


def monthly_revenue(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    # numpy bincounts over month indices keep the peak small; this backs the landing-page chart.
    sale = df["is_product"].to_numpy() & ~df["is_cancellation"].to_numpy()
    cols = ["month", "revenue", "orders", "customers", "partial", "year", "month_num"]
    if not sale.any():
        return pd.DataFrame(columns=cols)
    months = df["invoice_date"].to_numpy()[sale].astype("datetime64[M]").astype(np.int64)
    first = months.min()
    idx = (months - first).astype(np.int64)
    n = int(idx.max()) + 1
    revenue = np.bincount(idx, weights=df["revenue"].to_numpy()[sale], minlength=n)
    # An invoice has a single timestamp, so each invoice's first line marks one order in one month.
    _, first_line = np.unique(_codes(df["invoice"])[sale], return_index=True)
    orders = np.bincount(idx[first_line], minlength=n)
    cust = df["customer_id"].to_numpy(dtype="int64", na_value=-1)[sale]
    known = cust >= 0
    pairs = np.unique(idx[known] * (int(cust.max()) + 1) + cust[known]) if known.any() else np.array([], dtype=np.int64)
    customers = np.bincount(pairs // (int(cust.max()) + 1), minlength=n) if known.any() else np.zeros(n, dtype=int)

    out = pd.DataFrame({
        "month": (np.arange(n) + first).astype("datetime64[M]").astype("datetime64[s]"),
        "revenue": revenue, "orders": orders, "customers": customers,
    })
    out = out[out["orders"] > 0].reset_index(drop=True)
    out["partial"] = _partial_months(out["month"], start, end)
    out["year"] = out["month"].dt.year
    out["month_num"] = out["month"].dt.month
    return out[cols]


def weekday_hour(df: pd.DataFrame) -> pd.DataFrame:
    """Distinct orders by weekday (rows, Mon-Sun) x hour of day (columns)."""
    s = sales_rows(df, ["invoice", "invoice_date"]).drop_duplicates("invoice")
    grid = pd.crosstab(s["invoice_date"].dt.dayofweek, s["invoice_date"].dt.hour)
    hours = range(int(grid.columns.min()), int(grid.columns.max()) + 1) if len(grid.columns) else range(0)
    grid = grid.reindex(index=range(7), columns=hours, fill_value=0)
    grid.index = WEEKDAYS
    return grid


# ---------------------------------------------------------------- products

def product_summary(df: pd.DataFrame) -> pd.DataFrame:
    s = sales_rows(df, ["stock_code", "description", "revenue", "quantity", "invoice", "customer_id"])
    out = (
        s.groupby("stock_code", observed=True)
        .agg(
            description=("description", "first"),
            revenue=("revenue", "sum"),
            units=("quantity", "sum"),
            orders=("invoice", "nunique"),
            customers=("customer_id", "nunique"),
        )
        .reset_index()
    )
    out["stock_code"] = out["stock_code"].astype(str)
    out["description"] = out["description"].astype(str)
    return out.sort_values("revenue", ascending=False, ignore_index=True)


def top_products(df: pd.DataFrame, by: str = "revenue", n: int = 10) -> pd.DataFrame:
    return product_summary(df).nlargest(n, by, keep="first").reset_index(drop=True)


def pareto(df: pd.DataFrame) -> pd.DataFrame:
    """Products ranked by revenue with cumulative shares, for an 80/20 curve."""
    p = product_summary(df)[["stock_code", "description", "revenue"]]
    total = p["revenue"].sum()
    p["product_share"] = np.arange(1, len(p) + 1) / len(p) if len(p) else []
    p["cum_revenue_share"] = p["revenue"].cumsum() / total if total else 0.0
    return p


def pareto_point(p: pd.DataFrame, revenue_share: float = 0.8) -> float:
    """Share of products needed to reach `revenue_share` of revenue."""
    if p.empty:
        return 0.0
    idx = int(np.searchsorted(p["cum_revenue_share"].to_numpy(), revenue_share))
    return float(p["product_share"].iloc[min(idx, len(p) - 1)])


def product_detail(df: pd.DataFrame, stock_code: str) -> dict[str, pd.DataFrame]:
    one = df[df["stock_code"] == stock_code]
    s = sales_rows(one, ["invoice_date", "revenue", "quantity", "price", "invoice", "country"])
    monthly = (
        s.assign(month=month_start(s["invoice_date"]))
        .groupby("month").agg(revenue=("revenue", "sum"), units=("quantity", "sum")).reset_index()
    )
    prices = (
        s.groupby("price").agg(units=("quantity", "sum"), lines=("invoice", "size")).reset_index()
        .sort_values("price")
    )
    countries = (
        s.groupby("country", observed=True).agg(revenue=("revenue", "sum"), units=("quantity", "sum"))
        .reset_index().sort_values("revenue", ascending=False).head(10)
    )
    countries["country"] = countries["country"].astype(str)
    return {"monthly": monthly, "prices": prices, "countries": countries}


# ---------------------------------------------------------------- geography

def country_summary(df: pd.DataFrame) -> pd.DataFrame:
    s = sales_rows(df, ["country", "revenue", "invoice", "customer_id"])
    out = (
        s.groupby("country", observed=True)
        .agg(revenue=("revenue", "sum"), orders=("invoice", "nunique"), customers=("customer_id", "nunique"))
        .reset_index()
    )
    out["country"] = out["country"].astype(str)
    out["aov"] = out["revenue"] / out["orders"]
    total = out["revenue"].sum()
    out["share"] = out["revenue"] / total if total else 0.0
    out["iso3"] = out["country"].map(iso3)
    out["region"] = out["country"].map(region)
    return out.sort_values("revenue", ascending=False, ignore_index=True)


# ---------------------------------------------------------------- RFM

# (recency score, frequency score) -> segment. Covers all 25 combinations.
def _segment(r: int, f: int) -> str:
    if r <= 2:
        return "Hibernating" if f <= 2 else "At Risk" if f <= 4 else "Can't Lose"
    if r == 3:
        return "About to Sleep" if f <= 2 else "Need Attention" if f == 3 else "Loyal"
    if r == 4:
        return "Promising" if f == 1 else "Potential Loyalist" if f <= 3 else "Loyal"
    return "New Customers" if f == 1 else "Potential Loyalist" if f <= 3 else "Champions"


SEGMENTS = [
    "Champions", "Loyal", "Potential Loyalist", "New Customers", "Promising",
    "Need Attention", "About to Sleep", "At Risk", "Can't Lose", "Hibernating",
]
SEGMENT_LOOKUP = {(r, f): _segment(r, f) for r in range(1, 6) for f in range(1, 6)}


def _quintile(values: pd.Series, ascending: bool = True) -> pd.Series:
    """1-5 scores by rank, so ties and small groups never produce duplicate bin edges."""
    pct = values.rank(method="first", pct=True, ascending=ascending)
    return np.ceil(pct * 5).clip(1, 5).astype(int)


def rfm(df: pd.DataFrame, snapshot: date) -> pd.DataFrame:
    """One row per identified customer: recency (days), frequency (orders), monetary (GBP), scores, segment."""
    s = sales_rows(df, ["customer_id", "invoice_date", "invoice", "revenue", "country"])
    s = s[s["customer_id"].notna()]
    out = (
        s.groupby("customer_id")
        .agg(
            last_purchase=("invoice_date", "max"),
            frequency=("invoice", "nunique"),
            monetary=("revenue", "sum"),
            country=("country", lambda c: c.mode().iat[0]),
        )
        .reset_index()
        .sort_values("customer_id", ignore_index=True)
    )
    if out.empty:
        return out.assign(recency=[], r_score=[], f_score=[], m_score=[], segment=[])
    out["country"] = out["country"].astype(str)
    out["recency"] = (pd.Timestamp(snapshot) - out["last_purchase"].dt.normalize()).dt.days
    out["r_score"] = _quintile(out["recency"], ascending=False)
    out["f_score"] = _quintile(out["frequency"])
    out["m_score"] = _quintile(out["monetary"])
    out["segment"] = [SEGMENT_LOOKUP[rf] for rf in zip(out["r_score"], out["f_score"])]
    return out


def segment_summary(r: pd.DataFrame) -> pd.DataFrame:
    if r.empty:
        return pd.DataFrame(columns=["segment", "customers", "revenue", "avg_recency", "avg_frequency",
                                     "avg_monetary", "customer_share", "revenue_share"])
    out = (
        r.groupby("segment")
        .agg(customers=("customer_id", "size"), revenue=("monetary", "sum"),
             avg_recency=("recency", "mean"), avg_frequency=("frequency", "mean"),
             avg_monetary=("monetary", "mean"))
        .reindex(SEGMENTS).dropna(subset=["customers"]).reset_index()
    )
    out["customers"] = out["customers"].astype(int)
    out["customer_share"] = out["customers"] / out["customers"].sum()
    out["revenue_share"] = out["revenue"] / out["revenue"].sum()
    return out


# ---------------------------------------------------------------- cohorts

def first_purchase_month(df: pd.DataFrame) -> pd.Series:
    """customer_id -> month of first purchase. Call on the FULL dataset, not a filtered one."""
    s = sales_rows(df, ["customer_id", "invoice_date"])
    s = s[s["customer_id"].notna()]
    return month_start(s.groupby("customer_id")["invoice_date"].min())


def cohort_table(df: pd.DataFrame, acquired: pd.Series) -> pd.DataFrame:
    """Long table: cohort month x months since acquisition -> active customers, retention, revenue.

    Only cohorts acquired inside the filtered window are included, and the cohort size is
    the number of those customers active in their acquisition month (period 0).
    """
    s = sales_rows(df, ["customer_id", "invoice_date", "revenue"])
    s = s[s["customer_id"].notna()]
    cols = ["cohort", "period", "customers", "revenue", "cohort_size", "retention"]
    if s.empty:
        return pd.DataFrame(columns=cols)
    s = s.assign(month=month_start(s["invoice_date"]), cohort=s["customer_id"].map(acquired))
    s = s[s["cohort"] >= s["month"].min()]
    s["period"] = (s["month"].dt.year - s["cohort"].dt.year) * 12 + (s["month"].dt.month - s["cohort"].dt.month)
    out = (
        s.groupby(["cohort", "period"])
        .agg(customers=("customer_id", "nunique"), revenue=("revenue", "sum"))
        .reset_index()
    )
    size = out[out["period"] == 0].set_index("cohort")["customers"]
    out["cohort_size"] = out["cohort"].map(size)
    out = out[out["cohort_size"].notna()]
    out["cohort_size"] = out["cohort_size"].astype(int)
    out["retention"] = out["customers"] / out["cohort_size"]
    return out[cols].reset_index(drop=True)


# ---------------------------------------------------------------- market basket

def basket_rules(df: pd.DataFrame, min_support: float = 0.01, max_items: int = 1000) -> pd.DataFrame:
    """Pairwise association rules A -> B over sales invoices.

    support    = share of all baskets containing both A and B
    confidence = P(B in basket | A in basket)
    lift       = confidence / support(B); > 1 means bought together more than chance

    Items below `min_support` can't be in a pair above it, so they're dropped before the
    sparse co-occurrence product; `max_items` caps memory if min_support is set very low.
    """
    cols = ["antecedent", "antecedent_desc", "consequent", "consequent_desc",
            "pair_baskets", "support", "confidence", "lift"]
    s = sales_rows(df, ["invoice", "stock_code", "description"])
    if s.empty:
        return pd.DataFrame(columns=cols)
    # Work on the integer category codes; converting 800k codes to strings costs ~100MB.
    inv = s["invoice"].cat.codes.to_numpy()
    item = s["stock_code"].cat.codes.to_numpy()
    pairs_df = pd.DataFrame({"inv": inv, "item": item}).drop_duplicates()
    n_baskets = pairs_df["inv"].nunique()
    counts = pairs_df["item"].value_counts()
    min_count = max(2, math.ceil(min_support * n_baskets))
    keep = counts[counts >= min_count].head(max_items)
    if len(keep) < 2:
        return pd.DataFrame(columns=cols)
    pairs_df = pairs_df[pairs_df["item"].isin(keep.index)]

    inv_idx, _ = pd.factorize(pairs_df["inv"])
    item_idx, items = pd.factorize(pairs_df["item"])
    x = sparse.csr_matrix(
        (np.ones(len(pairs_df), dtype=np.int32), (inv_idx, item_idx)),
        shape=(inv_idx.max() + 1, len(items)),
    )
    co = (x.T @ x).tocoo()
    pairs = (co.row != co.col) & (co.data >= min_count)
    a, b, n_ab = co.row[pairs], co.col[pairs], co.data[pairs]

    item_count = keep.reindex(items).to_numpy()
    codes = s["stock_code"].cat.categories.to_numpy()
    desc = s.drop_duplicates("stock_code").set_index("stock_code")["description"].astype(str)
    desc.index = desc.index.astype(str)
    out = pd.DataFrame({
        "antecedent": codes[np.asarray(items)[a]].astype(str),
        "consequent": codes[np.asarray(items)[b]].astype(str),
        "pair_baskets": n_ab,
        "support": n_ab / n_baskets,
        "confidence": n_ab / item_count[a],
        "lift": (n_ab / item_count[a]) / (item_count[b] / n_baskets),
    })
    out["antecedent_desc"] = out["antecedent"].map(desc)
    out["consequent_desc"] = out["consequent"].map(desc)
    return out[cols].sort_values(["lift", "support"], ascending=False, ignore_index=True)


# ---------------------------------------------------------------- returns & cancellations

def cancellation_monthly(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["invoice_date", "revenue"]
    s, c = sales_rows(df, cols), cancel_rows(df, cols)
    gross = s.groupby(month_start(s["invoice_date"]))["revenue"].sum()
    cancelled = -c.groupby(month_start(c["invoice_date"]))["revenue"].sum()
    out = pd.DataFrame({"gross": gross, "cancelled": cancelled}).fillna(0.0)
    out.index.name = "month"
    out = out.reset_index()
    out["cancel_rate"] = np.where(out["gross"] > 0, out["cancelled"] / out["gross"].where(out["gross"] > 0, 1), np.nan)
    return out


def product_returns(df: pd.DataFrame, min_units_sold: int = 100) -> pd.DataFrame:
    cols = ["stock_code", "description", "quantity", "revenue", "invoice"]
    s, c = sales_rows(df, cols), cancel_rows(df, cols)
    sold = s.groupby("stock_code", observed=True).agg(
        description=("description", "first"), units_sold=("quantity", "sum"), revenue=("revenue", "sum"))
    returned = c.groupby("stock_code", observed=True).agg(
        units_cancelled=("quantity", lambda q: -q.sum()),
        cancelled_value=("revenue", lambda r: -r.sum()),
        cancellations=("invoice", "nunique"),
    )
    out = sold.join(returned, how="inner").reset_index()
    out = out[out["units_sold"] >= min_units_sold]
    out["stock_code"] = out["stock_code"].astype(str)
    out["description"] = out["description"].astype(str)
    out["return_rate"] = out["units_cancelled"] / out["units_sold"]
    return out.sort_values("cancelled_value", ascending=False, ignore_index=True)


def country_returns(df: pd.DataFrame) -> pd.DataFrame:
    s, c = sales_rows(df, ["country", "revenue"]), cancel_rows(df, ["country", "revenue"])
    gross = s.groupby("country", observed=True)["revenue"].sum()
    cancelled = -c.groupby("country", observed=True)["revenue"].sum()
    out = pd.DataFrame({"gross": gross, "cancelled": cancelled}).fillna(0.0)
    out = out[out["gross"] > 0].reset_index()
    out["country"] = out["country"].astype(str)
    out["cancel_rate"] = out["cancelled"] / out["gross"]
    return out.sort_values("cancelled", ascending=False, ignore_index=True)


def customer_returns(df: pd.DataFrame, min_orders: int = 3) -> pd.DataFrame:
    cols = ["customer_id", "invoice", "revenue", "country"]
    s, c = sales_rows(df, cols), cancel_rows(df, cols)
    s, c = s[s["customer_id"].notna()], c[c["customer_id"].notna()]
    bought = s.groupby("customer_id").agg(
        orders=("invoice", "nunique"), revenue=("revenue", "sum"), country=("country", "first"))
    returned = c.groupby("customer_id").agg(
        cancellations=("invoice", "nunique"), cancelled_value=("revenue", lambda r: -r.sum()))
    out = bought.join(returned, how="inner").reset_index()
    out = out[out["orders"] >= min_orders]
    out["country"] = out["country"].astype(str)
    out["cancel_rate"] = out["cancelled_value"] / out["revenue"]
    return out.sort_values("cancelled_value", ascending=False, ignore_index=True)


def largest_cancellations(df: pd.DataFrame, n: int = 5) -> pd.DataFrame:
    """The biggest single cancelled lines by value, with their share of all cancelled value."""
    c = cancel_rows(df, ["invoice_date", "invoice", "customer_id", "stock_code", "description",
                         "quantity", "revenue", "country"])
    total = -c["revenue"].sum()
    out = c.nsmallest(n, "revenue")[["invoice_date", "invoice", "customer_id", "stock_code", "description",
                                      "quantity", "revenue", "country"]].copy()
    out["quantity"] = -out["quantity"]
    out["value"] = -out["revenue"]
    out["share_of_cancelled"] = out["value"] / total if total else 0.0
    for col in ["invoice", "stock_code", "description", "country"]:
        out[col] = out[col].astype(str)
    return out.drop(columns="revenue").reset_index(drop=True)
