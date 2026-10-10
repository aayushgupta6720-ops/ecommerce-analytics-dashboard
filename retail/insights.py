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


def _last_active_year(df: pd.DataFrame, last_purchase: pd.Series) -> pd.Series:
    """Each customer's net revenue in the 365 days up to and including their last purchase."""
    p = metrics.product_rows(df, ["customer_id", "invoice_date", "revenue"])
    p = p[p["customer_id"].isin(last_purchase.index)]
    last = p["customer_id"].map(last_purchase)
    in_year = (p["invoice_date"] > last - pd.Timedelta(days=365)) & (
        p["invoice_date"] < last.dt.normalize() + pd.Timedelta(days=1))
    p = p[in_year]
    return p.groupby("customer_id")["revenue"].sum()


def winback(df: pd.DataFrame, snapshot: date) -> pd.DataFrame:
    """Lapsed high-value customers (RFM At Risk / Can't Lose) and what they spent in their last active year:
    the 365 days up to their own last purchase. (It used to be the year before last, which missed the
    recent spend of the many who lapsed within the last year.) Sorted by that spend: the order to contact them in."""
    r = metrics.rfm(df, snapshot)
    lapsed = r[r["segment"].isin(LAPSED_SEGMENTS)].copy()
    spend = _last_active_year(df, lapsed.set_index("customer_id")["last_purchase"])
    lapsed["last_active_year_revenue"] = lapsed["customer_id"].map(spend).fillna(0.0)
    cols = ["customer_id", "segment", "country", "recency", "frequency", "monetary", "last_active_year_revenue"]
    return lapsed[cols].sort_values(["last_active_year_revenue", "customer_id"], ascending=[False, True],
                                    ignore_index=True)


def winback_baseline(df: pd.DataFrame, snapshot: date) -> dict:
    """What lapsed customers did with no campaign: those At Risk / Can't Lose a year before `snapshot`,
    the share who ordered again within that year, and what they spent then against their last active
    year. A win-back campaign is worth only the returns it adds to these."""
    earlier = pd.Timestamp(snapshot) - pd.Timedelta(days=365)
    before = df[df["invoice_date"] < earlier]
    r = metrics.rfm(before, earlier.date())
    lapsed = r[r["segment"].isin(LAPSED_SEGMENTS)].set_index("customer_id")["last_purchase"]
    if lapsed.empty:
        return {"snapshot": earlier.date(), "customers": 0, "returned_share": float("nan"), "spend_ratio": float("nan")}
    prior = _last_active_year(before, lapsed)
    p = metrics.product_rows(df, ["customer_id", "invoice_date", "revenue"])
    after = p[(p["invoice_date"] >= earlier) & (p["invoice_date"] < pd.Timestamp(snapshot))
              & p["customer_id"].isin(lapsed.index)]
    returned = after["customer_id"].unique()
    return {
        "snapshot": earlier.date(),
        "customers": int(len(lapsed)),
        "returned_share": float(len(returned) / len(lapsed)),
        # of those who came back: next-year spend against their last active year
        "spend_ratio": (float(after["revenue"].sum() / prior.reindex(returned).fillna(0).sum())
                        if len(returned) else 0.0),
        "history_days": int((earlier - df["invoice_date"].min()).days),
    }


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


def _usual_quantity(sales: pd.DataFrame, lines: pd.Index) -> pd.Series:
    """For each of `lines` (an index into `sales`), the median quantity of the same product's other sales
    lines; NaN for a product sold only on that line."""
    codes = sales["stock_code"].astype(str)
    wanted = codes.isin(set(codes.loc[lines]))
    by_code = dict(tuple(sales.loc[wanted, "quantity"].groupby(codes[wanted])))
    out = {}
    for i in lines:
        others = by_code[codes.at[i]].drop(i)
        out[i] = float(others.median()) if len(others) else np.nan
    return pd.Series(out, dtype=float)


def bulk_cancellations(df: pd.DataFrame, unit_threshold: int = 1000, usual_multiple: float = 50,
                       keying_minutes: int = 60) -> dict[str, float]:
    """How much cancelled value comes from very large lines, how much of that is keying errors (a line
    cancelled within `keying_minutes` of being ordered with the same product, customer and quantity), and
    how many sales lines a check at order entry would flag: at least `unit_threshold` units and
    `usual_multiple` times the product's usual line, or a product with no other sales."""
    c = metrics.cancel_rows(df, ["invoice_date", "stock_code", "customer_id", "quantity", "revenue"])
    units, value = -c["quantity"].to_numpy(), -c["revenue"].to_numpy()
    big = units >= unit_threshold
    sales = metrics.sales_rows(df, ["invoice_date", "stock_code", "customer_id", "quantity"]).reset_index(drop=True)
    key = ["stock_code", "customer_id", "quantity"]
    cb = c[big].assign(quantity=units[big], value=value[big]).reset_index(drop=True).fillna({"customer_id": -1})
    orders = sales.fillna({"customer_id": -1}).rename(columns={"invoice_date": "ordered"}).rename_axis("sale")
    matched = cb.rename_axis("cancel").reset_index().merge(orders.reset_index(), on=key, how="inner")
    gap = matched["invoice_date"] - matched["ordered"]
    quick = matched[(gap >= pd.Timedelta(0)) & (gap <= pd.Timedelta(minutes=keying_minutes))]
    keyed = cb.index.isin(quick["cancel"])
    big_sales = sales.index[sales["quantity"] >= unit_threshold]
    usual = _usual_quantity(sales, big_sales)
    flagged = usual.isna() | (sales.loc[big_sales, "quantity"].to_numpy() >= usual_multiple * usual)
    # A mistyped line counts as caught if the check flags the order line it was cancelled against.
    caught = quick[quick["sale"].map(flagged).fillna(False).astype(bool)]["cancel"].nunique()
    years, months = data_years(df), data_years(df) * 12
    total = value.sum()
    genuine_value = float(cb.loc[~keyed, "value"].sum())
    return {
        "threshold": unit_threshold,
        "cancelled_lines": int(big.sum()),
        "cancelled_value": float(value[big].sum()),
        "share_of_cancelled": float(value[big].sum() / total) if total else 0.0,
        "cancelled_value_per_year": float(value[big].sum() / years),
        "keying_errors": int(keyed.sum()),
        "keying_error_units": [int(u) for u in cb.loc[keyed, "quantity"].sort_values(ascending=False)],
        "keying_error_share": float(cb.loc[keyed, "value"].sum() / total) if total else 0.0,
        "keying_errors_caught": int(caught),
        "genuine_lines": int((~keyed).sum()),
        "genuine_share": genuine_value / total if total else 0.0,
        "genuine_value_per_year": genuine_value / years,
        "sales_lines_to_confirm": int(flagged.sum()),
        "sales_lines_per_month": float(flagged.sum() / months) if months else 0.0,
    }


def guests(df: pd.DataFrame) -> dict[str, float]:
    """Sales from orders without a customer ID: they can't be segmented, retained or contacted. Most come
    through the retailer's own web shop (orders carrying its DOTCOM POSTAGE line, code DOT), which sells to
    consumers: one unit per line at about twice the price the wholesale accounts pay."""
    s = metrics.sales_rows(df, ["customer_id", "revenue", "invoice", "stock_code", "quantity", "price",
                                "invoice_date"])
    g = s["customer_id"].isna().to_numpy()
    gross = s["revenue"].to_numpy()
    web = set(df.loc[df["stock_code"].astype(str) == "DOT", "invoice"].astype(str))
    on_web = s["invoice"].astype(str).isin(web).to_numpy()
    web_invoices = df[df["invoice"].astype(str).isin(web)].groupby("invoice")["customer_id"].apply(
        lambda ids: ids.isna().all())
    month = s["invoice_date"].dt.to_period("M")
    prices = s.groupby([s["stock_code"].astype(str), month, pd.Series(g, index=s.index, name="guest")])[
        "price"].median().unstack().dropna()
    return {
        "line_share": float(g.mean()) if len(g) else 0.0,
        "gross": float(gross[g].sum()),
        "gross_share": float(gross[g].sum() / gross.sum()) if gross.sum() else 0.0,
        "gross_per_year": float(gross[g].sum() / data_years(df)),
        "orders": int(s.loc[g, "invoice"].nunique()),
        "orders_share": float(s.loc[g, "invoice"].nunique() / s["invoice"].nunique()) if len(s) else 0.0,
        "web_shop_share_of_guest_gross": float(gross[g & on_web].sum() / gross[g].sum()) if g.any() else 0.0,
        "web_shop_orders_without_id": float(web_invoices.mean()) if len(web_invoices) else 0.0,
        "median_units_guest": float(np.median(s.loc[g, "quantity"])) if g.any() else 0.0,
        "median_units_identified": float(np.median(s.loc[~g, "quantity"])) if (~g).any() else 0.0,
        "price_ratio": float((prices[True] / prices[False]).median()) if len(prices) else float("nan"),
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
