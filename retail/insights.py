"""Evidence behind the Insights & actions page. Pure pandas/numpy, computed on the full dataset.

Each function returns the numbers one recommendation rests on. Money values are net of cancellations
unless the name says gross. "Per year" figures divide by the data's span (Dec 2009 - Dec 2011, ~2.02 years).
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from retail import metrics

LAPSED_SEGMENTS = ("At Risk", "Can't Lose")


def data_years(df: pd.DataFrame) -> float:
    span = df["invoice_date"].max().normalize() - df["invoice_date"].min().normalize()
    return (span.days + 1) / 365.25


def _net_by_customer(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    """Net revenue per identified customer for invoice dates in [start, end)."""
    p = metrics.product_rows(df, ["customer_id", "invoice_date", "revenue"])
    p = p[p["customer_id"].notna() & (p["invoice_date"] >= start) & (p["invoice_date"] < end)]
    return p.groupby("customer_id")["revenue"].sum()


def winback(df: pd.DataFrame, snapshot: date) -> pd.DataFrame:
    """Lapsed high-value customers (RFM At Risk / Can't Lose) and what they spent in the year before last,
    i.e. the year they were active. Sorted by that spend: the order to contact them in."""
    r = metrics.rfm(df, snapshot)
    lapsed = r[r["segment"].isin(LAPSED_SEGMENTS)].copy()
    snap = pd.Timestamp(snapshot)
    prev = _net_by_customer(df, snap - pd.Timedelta(days=730), snap - pd.Timedelta(days=365))
    lapsed["prev_year_revenue"] = lapsed["customer_id"].map(prev).fillna(0.0)
    cols = ["customer_id", "segment", "country", "recency", "frequency", "monetary", "prev_year_revenue"]
    return lapsed[cols].sort_values(["prev_year_revenue", "customer_id"], ascending=[False, True], ignore_index=True)


def champions(df: pd.DataFrame, snapshot: date, current_scores: pd.DataFrame | None = None) -> pd.DataFrame:
    """Champions, their net revenue in the last 365 days, and (if given) their current churn-risk percentile.
    Sorted with the riskiest first: the watch list."""
    r = metrics.rfm(df, snapshot)
    champs = r[r["segment"] == "Champions"].copy()
    snap = pd.Timestamp(snapshot)
    champs["last_year_revenue"] = champs["customer_id"].map(
        _net_by_customer(df, snap - pd.Timedelta(days=365), snap)).fillna(0.0)
    if current_scores is not None:
        risk = current_scores.set_index("customer_id")["risk_percentile"]
        champs["risk_percentile"] = champs["customer_id"].map(risk)
    else:
        champs["risk_percentile"] = np.nan
    cols = ["customer_id", "country", "recency", "frequency", "monetary", "last_year_revenue", "risk_percentile"]
    return champs[cols].sort_values(["risk_percentile", "last_year_revenue"], ascending=[False, False],
                                    na_position="last", ignore_index=True)


def segment_share(df: pd.DataFrame, snapshot: date, segment: str = "Champions") -> dict[str, float]:
    s = metrics.segment_summary(metrics.rfm(df, snapshot)).set_index("segment")
    return {"customers": int(s.loc[segment, "customers"]), "customer_share": float(s.loc[segment, "customer_share"]),
            "revenue_share": float(s.loc[segment, "revenue_share"])}


def peak_season(df: pd.DataFrame, months: tuple[int, ...] = (9, 10, 11)) -> dict:
    """Share of a Dec-Nov year's net revenue earned in the peak months, for each full Dec-Nov year."""
    lo, hi = df["invoice_date"].min().date(), df["invoice_date"].max().date()
    m = metrics.monthly_revenue(df, lo, hi)
    m = m[~m["partial"]].copy()
    m["season_year"] = m["month"].dt.year + (m["month"].dt.month == 12)  # Dec counts towards the next year
    years = []
    for year, part in m.groupby("season_year"):
        if len(part) < 12:
            continue
        peak = part[part["month"].dt.month.isin(months)]
        years.append({
            "year": f"Dec {year - 1} – Nov {year}",
            "total": float(part["revenue"].sum()),
            "peak": float(peak["revenue"].sum()),
            "peak_share": float(peak["revenue"].sum() / part["revenue"].sum()),
            "top_month": f"{part.loc[part['revenue'].idxmax(), 'month']:%b %Y}",
            "top_month_multiple": float(part["revenue"].max() / part["revenue"].mean()),
        })
    return {"years": years, "monthly": m[["month", "revenue"]].reset_index(drop=True)}


def bulk_cancellations(df: pd.DataFrame, unit_threshold: int = 1000) -> dict[str, float]:
    """How much cancelled value comes from very large lines, and how many large sales lines a
    confirm-before-fulfilment rule would have to check."""
    c = metrics.cancel_rows(df, ["quantity", "revenue"])
    units, value = -c["quantity"].to_numpy(), -c["revenue"].to_numpy()
    big = units >= unit_threshold
    sales_big = int((metrics.sales_rows(df, ["quantity"])["quantity"].to_numpy() >= unit_threshold).sum())
    months = data_years(df) * 12
    return {
        "threshold": unit_threshold,
        "cancelled_lines": int(big.sum()),
        "cancelled_value": float(value[big].sum()),
        "share_of_cancelled": float(value[big].sum() / value.sum()) if value.sum() else 0.0,
        "cancelled_value_per_year": float(value[big].sum() / data_years(df)),
        "sales_lines_to_confirm": sales_big,
        "sales_lines_per_month": sales_big / months if months else 0.0,
    }


def guests(df: pd.DataFrame) -> dict[str, float]:
    """Sales from checkouts without a customer ID: they can't be segmented, retained or contacted."""
    s = metrics.sales_rows(df, ["customer_id", "revenue", "invoice"])
    g = s["customer_id"].isna().to_numpy()
    gross = s["revenue"].to_numpy()
    return {
        "line_share": float(g.mean()) if len(g) else 0.0,
        "gross": float(gross[g].sum()),
        "gross_share": float(gross[g].sum() / gross.sum()) if gross.sum() else 0.0,
        "gross_per_year": float(gross[g].sum() / data_years(df)),
        "orders": int(s.loc[g, "invoice"].nunique()),
        "orders_share": float(s.loc[g, "invoice"].nunique() / s["invoice"].nunique()) if len(s) else 0.0,
    }


def bundle_opportunities(df: pd.DataFrame, top_n: int = 8, min_support: float = 0.01) -> pd.DataFrame:
    """For the strongest product pairs: orders that had A but not B, and what B is worth per order.
    `missed_value` is the ceiling if every one of those orders had added B."""
    rules = metrics.basket_rules(df, min_support=min_support)
    if rules.empty:
        return pd.DataFrame(columns=["antecedent_desc", "consequent_desc", "lift", "confidence",
                                     "orders_without_b", "value_per_b_order", "missed_value"])
    pairs = rules.assign(key=[tuple(sorted(k)) for k in zip(rules["antecedent"], rules["consequent"], strict=True)])
    pairs = pairs.drop_duplicates("key").head(top_n).reset_index(drop=True)
    s = metrics.sales_rows(df, ["invoice", "stock_code"])
    codes = s["stock_code"].astype(str).to_numpy()
    invoices = s["invoice"].astype(str).to_numpy()
    wanted = set(pairs["antecedent"]) | set(pairs["consequent"])
    baskets = {code: set(invoices[codes == code]) for code in wanted}
    summary = metrics.product_summary(df).set_index("stock_code")
    pairs["orders_without_b"] = [len(baskets[a] - baskets[b]) for a, b in zip(pairs["antecedent"], pairs["consequent"],
                                                                              strict=True)]
    pairs["value_per_b_order"] = [summary.loc[b, "revenue"] / summary.loc[b, "orders"] for b in pairs["consequent"]]
    pairs["missed_value"] = pairs["orders_without_b"] * pairs["value_per_b_order"]
    return pairs[["antecedent", "antecedent_desc", "consequent", "consequent_desc", "lift", "confidence",
                  "orders_without_b", "value_per_b_order", "missed_value"]]
