"""Pure pandas metric functions. No Streamlit imports, so everything here is unit-testable.

Every function takes the cleaned transactions frame (see scripts/build_dataset.py),
usually already narrowed by `filter_frame`, and returns a small aggregate.

Definitions used throughout:
- sales rows: product lines that are not cancellations (the ETL already dropped price <= 0
  and negative-quantity non-cancellations, so quantity and price are positive here)
- cancellations: product lines on 'C' invoices; their quantity and revenue are negative
- revenue: NET of cancellations, i.e. sales value plus the (negative) cancelled value in the
  same slice. An order placed and then cancelled in full therefore nets to zero instead of
  ranking as a top product or a big-spending customer.
- gross sales: sales rows only; the base for the cancellation rate
- units: net of cancelled units
- orders, customers, recency, frequency: counted on sales rows (things that actually happened)
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

def filter_mask(df: pd.DataFrame, start: date, end: date, countries: tuple[str, ...] = ()) -> np.ndarray:
    """Boolean row mask: invoice_date in [start, end] (inclusive days) and, if given, in `countries`."""
    ts = df["invoice_date"]
    mask = np.array((ts >= pd.Timestamp(start)) & (ts < pd.Timestamp(end) + pd.Timedelta(days=1)), dtype=bool)
    if countries:
        mask &= df["country"].isin(countries).to_numpy()
    return mask


def filter_frame(df: pd.DataFrame, start: date, end: date, countries: tuple[str, ...] = ()) -> pd.DataFrame:
    """Rows with invoice_date in [start, end] (inclusive days) and, if given, in `countries`."""
    mask = filter_mask(df, start, end, countries)
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


def product_rows(df: pd.DataFrame, cols: list[str] | None = None) -> pd.DataFrame:
    """Sales and cancellations together: summing their revenue gives net revenue."""
    mask = df["is_product"].to_numpy()
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
    gross = float(revenue_col[sale].sum())
    cancelled = float(-revenue_col[cancel].sum())
    revenue = gross - cancelled
    orders = int(np.unique(_codes(df["invoice"])[sale]).size)
    customers = df["customer_id"]
    has_id = sale & customers.notna().to_numpy()
    n_customers = int(np.unique(customers.to_numpy(dtype="int64", na_value=-1)[has_id]).size)
    return {
        "revenue": revenue,
        "gross_sales": gross,
        "orders": orders,
        "customers": n_customers,
        "aov": revenue / orders if orders else 0.0,
        "units": int(df["quantity"].to_numpy()[is_product].sum()),
        "cancelled_value": cancelled,
        "cancel_rate": cancelled / gross if gross else 0.0,
    }


def _codes(col: pd.Series) -> np.ndarray:
    """Integer codes for a categorical column (or a factorization of a plain one)."""
    return col.cat.codes.to_numpy() if isinstance(col.dtype, pd.CategoricalDtype) else pd.factorize(col)[0]


def _codes_labels(col: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    if isinstance(col.dtype, pd.CategoricalDtype):
        return col.cat.codes.to_numpy(), col.cat.categories.to_numpy()
    codes, labels = pd.factorize(col)
    return codes, np.asarray(labels)


def _distinct_count(group: np.ndarray, value: np.ndarray, n: int) -> np.ndarray:
    """Number of distinct non-negative `value`s per `group` code (0..n-1), without a pandas groupby."""
    keep = value >= 0
    group, value = group[keep].astype(np.int64), value[keep].astype(np.int64)
    if not len(value):
        return np.zeros(n, dtype=np.int64)
    width = int(value.max()) + 1
    return np.bincount(np.unique(group * width + value) // width, minlength=n)


def _by_code(df: pd.DataFrame, column: str) -> dict[str, np.ndarray]:
    """Net revenue, net units, orders and customers per category of `column`, via bincounts on codes.

    Far lighter than a groupby on a 1M-row frame (which copies several columns); these feed the
    Overview, Products and Geography pages on a 512MB instance.
    """
    product = df["is_product"].to_numpy()
    sale = product & ~df["is_cancellation"].to_numpy()
    codes, labels = _codes_labels(df[column])
    n = len(labels)
    sale_codes = codes[sale]
    return {
        "labels": labels,
        "codes": codes,
        "revenue": np.bincount(codes[product], weights=df["revenue"].to_numpy()[product], minlength=n),
        "units": np.bincount(codes[product], weights=df["quantity"].to_numpy()[product], minlength=n),
        "sale_lines": np.bincount(sale_codes, minlength=n),
        "orders": _distinct_count(sale_codes, _codes(df["invoice"])[sale], n),
        "customers": _distinct_count(sale_codes, df["customer_id"].to_numpy(dtype="int64", na_value=-1)[sale], n),
        "product": product,
    }


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
    product = df["is_product"].to_numpy()
    sale = product & ~df["is_cancellation"].to_numpy()
    cols = ["month", "revenue", "orders", "customers", "partial", "year", "month_num"]
    if not sale.any():
        return pd.DataFrame(columns=cols)
    months_all = df["invoice_date"].to_numpy()[product].astype("datetime64[M]").astype(np.int64)
    first = months_all.min()
    idx_all = (months_all - first).astype(np.int64)
    n = int(idx_all.max()) + 1
    # Net revenue: cancellations count (negatively) in the month they were made.
    revenue = np.bincount(idx_all, weights=df["revenue"].to_numpy()[product], minlength=n)
    idx = idx_all[~df["is_cancellation"].to_numpy()[product]]
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
    out = out[(out["orders"] > 0) | (out["revenue"] != 0)].reset_index(drop=True)
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
    """Per product: net revenue and units (cancellations subtracted), orders and customers from sales.
    Only products with at least one sale in the slice are listed."""
    agg = _by_code(df, "stock_code")
    # Description of each product's first line (descriptions are canonical per stock code after the ETL).
    first_line = np.full(len(agg["labels"]), -1)
    prod_idx = np.flatnonzero(agg["product"])
    uniq, first = np.unique(agg["codes"][prod_idx], return_index=True)
    first_line[uniq] = prod_idx[first]
    keep = agg["sale_lines"] > 0
    # Look descriptions up by category code: Series.to_numpy() would build 1M Python strings.
    desc_codes, desc_labels = _codes_labels(df["description"])
    desc = desc_labels[desc_codes[first_line[keep]]]
    out = pd.DataFrame({
        "stock_code": agg["labels"][keep].astype(str),
        "description": desc.astype(str),
        "revenue": agg["revenue"][keep],
        "units": agg["units"][keep].round().astype(np.int64),
        "orders": agg["orders"][keep],
        "customers": agg["customers"][keep],
    })
    return out.sort_values("revenue", ascending=False, ignore_index=True)


def top_products(df: pd.DataFrame, by: str = "revenue", n: int = 10) -> pd.DataFrame:
    return product_summary(df).nlargest(n, by, keep="first").reset_index(drop=True)


def pareto(df: pd.DataFrame) -> pd.DataFrame:
    """Products ranked by net revenue with cumulative shares, for an 80/20 curve.

    Products whose sales were all cancelled (net revenue <= 0) earn nothing and are left out.
    """
    p = product_summary(df)[["stock_code", "description", "revenue"]]
    p = p[p["revenue"] > 0].reset_index(drop=True)
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
    net = product_rows(one, ["invoice_date", "revenue", "quantity", "country"])
    monthly = (
        net.assign(month=month_start(net["invoice_date"]))
        .groupby("month").agg(revenue=("revenue", "sum"), units=("quantity", "sum")).reset_index()
    )
    prices = (
        s.groupby("price").agg(units=("quantity", "sum"), lines=("invoice", "size")).reset_index()
        .sort_values("price")
    )
    countries = (
        net.groupby("country", observed=True).agg(revenue=("revenue", "sum"), units=("quantity", "sum"))
        .reset_index().sort_values("revenue", ascending=False).head(10)
    )
    countries["country"] = countries["country"].astype(str)
    return {"monthly": monthly, "prices": prices, "countries": countries}


# ---------------------------------------------------------------- geography

def country_summary(df: pd.DataFrame) -> pd.DataFrame:
    agg = _by_code(df, "country")
    keep = agg["sale_lines"] > 0
    out = pd.DataFrame({"country": agg["labels"][keep], "orders": agg["orders"][keep],
                        "customers": agg["customers"][keep], "revenue": agg["revenue"][keep]})
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
    """1-5 scores by rank, so small groups never produce duplicate bin edges. Tied values share the score
    where their group starts (rank method "min"): with "first", ties were broken by customer ID, and two
    customers with the same recency and frequency could land in different segments."""
    pct = values.rank(method="min", pct=True, ascending=ascending)
    return np.ceil(pct * 5).clip(1, 5).astype(int)


def rfm(df: pd.DataFrame, snapshot: date) -> pd.DataFrame:
    """One row per identified customer: recency (days), frequency (orders), monetary (net GBP), scores, segment.

    Monetary is net of the customer's cancellations in the slice, so a cancelled bulk order doesn't
    turn a small customer into a big spender.
    """
    s = sales_rows(df, ["customer_id", "invoice_date", "invoice", "revenue", "country"])
    s = s[s["customer_id"].notna()]
    c = cancel_rows(df, ["customer_id", "revenue"])
    cancelled = c[c["customer_id"].notna()].groupby("customer_id")["revenue"].sum()
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
    out["monetary"] = out["monetary"] + out["customer_id"].map(cancelled).fillna(0.0).to_numpy()
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
    sale = df["is_product"].to_numpy() & ~df["is_cancellation"].to_numpy() & df["customer_id"].notna().to_numpy()
    cust = df["customer_id"].to_numpy(dtype="int64", na_value=-1)[sale]
    first = pd.Series(df["invoice_date"].to_numpy()[sale]).groupby(cust).min()
    first.index.name = "customer_id"
    return month_start(first)


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
    c = cancel_rows(df, ["customer_id", "invoice_date", "revenue"])
    c = c[c["customer_id"].notna()]
    window_start = month_start(s["invoice_date"]).min()

    def with_period(frame: pd.DataFrame) -> pd.DataFrame:
        frame = frame.assign(month=month_start(frame["invoice_date"]), cohort=frame["customer_id"].map(acquired))
        frame = frame[frame["cohort"] >= window_start]
        return frame.assign(period=(frame["month"].dt.year - frame["cohort"].dt.year) * 12
                            + (frame["month"].dt.month - frame["cohort"].dt.month))

    s, c = with_period(s), with_period(c)
    out = (
        s.groupby(["cohort", "period"])
        .agg(customers=("customer_id", "nunique"), revenue=("revenue", "sum"))
        .reset_index()
    )
    # Active customers come from sales; revenue is net of the cohort's cancellations in that month.
    cancelled = c.groupby(["cohort", "period"])["revenue"].sum()
    keys = pd.MultiIndex.from_frame(out[["cohort", "period"]])
    out["revenue"] = out["revenue"].to_numpy() + cancelled.reindex(keys).fillna(0.0).to_numpy()
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
    # Work on integer category codes: one (invoice, item) key per basket line, de-duplicated with np.unique.
    inv = _codes(s["invoice"]).astype(np.int64)
    item = _codes(s["stock_code"]).astype(np.int64)
    n_codes = int(item.max()) + 1
    keys = np.unique(inv * n_codes + item)
    pair_inv, pair_item = keys // n_codes, keys % n_codes
    n_baskets = int(np.unique(pair_inv).size)
    counts = pd.Series(np.bincount(pair_item, minlength=n_codes))
    counts = counts[counts > 0].sort_values(ascending=False, kind="stable")
    min_count = max(2, math.ceil(min_support * n_baskets))
    keep = counts[counts >= min_count].head(max_items)
    if len(keep) < 2:
        return pd.DataFrame(columns=cols)
    in_keep = np.isin(pair_item, keep.index.to_numpy())
    pair_inv, pair_item = pair_inv[in_keep], pair_item[in_keep]

    inv_idx, _ = pd.factorize(pair_inv)
    item_idx, items = pd.factorize(pair_item)
    x = sparse.csr_matrix(
        (np.ones(len(pair_inv), dtype=np.int32), (inv_idx, item_idx)),
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
    # Explicit tie-breaks: A->B and B->A share lift and support, and the order must not depend on platform.
    return out[cols].sort_values(["lift", "support", "antecedent", "consequent"],
                                 ascending=[False, False, True, True], ignore_index=True)


# ---------------------------------------------------------------- returns & cancellations

def cancellation_monthly(df: pd.DataFrame) -> pd.DataFrame:
    product = df["is_product"].to_numpy()
    cancel = df["is_cancellation"].to_numpy()[product]
    if not product.any():
        return pd.DataFrame(columns=["month", "gross", "cancelled", "cancel_rate"])
    months = df["invoice_date"].to_numpy()[product].astype("datetime64[M]").astype(np.int64)
    first = months.min()
    idx = months - first
    n = int(idx.max()) + 1
    revenue = df["revenue"].to_numpy()[product]
    gross = np.bincount(idx[~cancel], weights=revenue[~cancel], minlength=n)
    cancelled = -np.bincount(idx[cancel], weights=revenue[cancel], minlength=n)
    seen = np.bincount(idx, minlength=n) > 0
    out = pd.DataFrame({"month": (np.arange(n) + first).astype("datetime64[M]").astype("datetime64[s]"),
                        "gross": gross, "cancelled": cancelled})[seen].reset_index(drop=True)
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


# ---------------------------------------------------------------- breakdown (Ask the data page)

GROUPINGS = ("none", "year", "quarter", "month", "day", "weekday", "hour", "country", "product", "customer")
TIME_GROUPINGS = ("year", "quarter", "month", "day")
KPI_COLUMNS = ["revenue", "gross_sales", "orders", "customers", "aov", "units", "products",
               "cancelled_value", "cancel_rate"]
_PERIOD_STEP = {"year": pd.DateOffset(years=1), "quarter": pd.DateOffset(months=3),
                "month": pd.DateOffset(months=1), "day": pd.DateOffset(days=1)}


def _group_codes(df: pd.DataFrame, by: str) -> tuple[np.ndarray, pd.DataFrame]:
    """A group code per row (-1 = left out) and the key columns, one row per code."""
    n_rows = len(df)
    if by == "none":
        return np.zeros(n_rows, dtype=np.int64), pd.DataFrame({"group": ["All"]})
    if by in ("country", "product"):
        column = "country" if by == "country" else "stock_code"
        codes, labels = _codes_labels(df[column])
        return codes.astype(np.int64), pd.DataFrame({column: labels.astype(str)})
    if by == "customer":
        ids = df["customer_id"].to_numpy(dtype="int64", na_value=-1)
        known = ids >= 0
        labels, inverse = np.unique(ids[known], return_inverse=True)
        codes = np.full(n_rows, -1, dtype=np.int64)
        codes[known] = inverse
        return codes, pd.DataFrame({"customer_id": labels})
    ts = df["invoice_date"].to_numpy()
    if by == "weekday":  # 1970-01-01, day 0, was a Thursday
        return (ts.astype("datetime64[D]").astype(np.int64) + 3) % 7, pd.DataFrame({"weekday": WEEKDAYS})
    if by == "hour":
        return ts.astype("datetime64[h]").astype(np.int64) % 24, pd.DataFrame({"hour": np.arange(24)})
    if by not in TIME_GROUPINGS:
        raise ValueError(f"unknown grouping {by!r}")
    unit = "D" if by == "day" else "Y" if by == "year" else "M"
    idx = ts.astype(f"datetime64[{unit}]").astype(np.int64)
    if by == "quarter":
        idx = idx // 3
    first = int(idx.min()) if n_rows else 0
    periods = np.arange(first, int(idx.max()) + 1 if n_rows else 0)
    starts = (periods * 3 if by == "quarter" else periods).astype(f"datetime64[{unit}]").astype("datetime64[s]")
    return idx - first, pd.DataFrame({by: starts})


def breakdown(df: pd.DataFrame, by: str = "none", mask: np.ndarray | None = None,
              start: date | None = None, end: date | None = None) -> pd.DataFrame:
    """Every headline KPI per group, with kpis()' definitions: each row equals kpis() on that group's rows,
    except that an order whose lines straddle a period boundary counts once, in its first line's period.

    `by` is one of GROUPINGS ("none" gives a single row for the whole slice); `mask` narrows the rows
    without copying the frame. Groups with no product lines are dropped, and customer groups leave out
    guest lines (no customer ID). Ratios are NaN where undefined (no orders, or no gross sales). Time
    groups get `partial` when the [start, end] window covers only part of the period.
    """
    codes, keys = _group_codes(df, by)
    n = len(keys)
    product = df["is_product"].to_numpy() & (codes >= 0)
    if mask is not None:
        product &= mask
    is_cancel = df["is_cancellation"].to_numpy()
    sale, cancel = product & ~is_cancel, product & is_cancel
    revenue = df["revenue"].to_numpy()
    sale_codes = codes[sale]
    gross = np.bincount(sale_codes, weights=revenue[sale], minlength=n)
    cancelled = -np.bincount(codes[cancel], weights=revenue[cancel], minlength=n)
    invoices = _codes(df["invoice"])[sale]
    if by in TIME_GROUPINGS or by in ("weekday", "hour"):
        # An order counts once, in its first line's period (as in monthly_revenue and weekday_hour): a few
        # invoices have lines either side of a minute boundary, which can fall in different periods.
        _, first_line = np.unique(invoices, return_index=True)
        orders = np.bincount(sale_codes[first_line], minlength=n)
    else:
        orders = _distinct_count(sale_codes, invoices, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        aov = np.where(orders > 0, (gross - cancelled) / orders, np.nan)
        cancel_rate = np.where(gross > 0, cancelled / gross, np.nan)
    out = keys.assign(
        revenue=gross - cancelled,
        gross_sales=gross,
        orders=orders,
        customers=_distinct_count(sale_codes, df["customer_id"].to_numpy(dtype="int64", na_value=-1)[sale], n),
        aov=aov,
        units=np.bincount(codes[product], weights=df["quantity"].to_numpy()[product], minlength=n).round()
        .astype(np.int64),
        products=_distinct_count(sale_codes, _codes(df["stock_code"])[sale], n),
        cancelled_value=cancelled,
        cancel_rate=cancel_rate,
    )
    keep = np.bincount(codes[product], minlength=n) > 0
    if by == "product":
        # Description of each product's first line, looked up by category code (as in product_summary).
        first_line = np.full(n, -1)
        rows = np.flatnonzero(product)
        uniq, first = np.unique(codes[rows], return_index=True)
        first_line[uniq] = rows[first]
        desc_codes, desc_labels = _codes_labels(df["description"])
        desc = np.full(n, "", dtype=object)
        desc[keep] = desc_labels[desc_codes[first_line[keep]]].astype(str)
        out.insert(1, "description", desc)
    out = out[keep].reset_index(drop=True)
    if by in TIME_GROUPINGS and start is not None and end is not None:
        period_end = out[by] + _PERIOD_STEP[by] - pd.Timedelta(days=1)
        out["partial"] = (out[by] < pd.Timestamp(start)) | (period_end > pd.Timestamp(end))
    return out
