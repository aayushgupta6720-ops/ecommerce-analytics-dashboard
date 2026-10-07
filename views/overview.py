import streamlit as st

from retail import charts as ch
from retail import data

f = data.page_header("Overview", "Headline sales performance, trend and when customers order.")
k, delta, prev = data.kpis_with_deltas(f)
if data.empty_state(k, "sales"):
    st.stop()


def fmt_delta(key: str, points: bool = False) -> str | None:
    d = delta[key]
    if d is None:
        return None
    return f"{d * 100:+.1f} pp" if points else f"{d:+.1%}"


cols = st.columns(6)
cols[0].metric("Net revenue", ch.money(k["revenue"]), fmt_delta("revenue"), border=True,
               help=f"Product sales minus cancelled product lines. Gross sales: {ch.money(k['gross_sales'], 2)}.")
cols[1].metric("Orders", f"{k['orders']:,}", fmt_delta("orders"), border=True)
cols[2].metric("Customers", f"{k['customers']:,}", fmt_delta("customers"), border=True,
               help="Identified customers only; ~23% of sales lines have no customer ID.")
cols[3].metric("Avg order value", f"£{k['aov']:,.0f}", fmt_delta("aov"), border=True,
               help="Net revenue ÷ orders.")
cols[4].metric("Units (net)", ch.number(k["units"]), fmt_delta("units"), border=True,
               help="Units sold minus units cancelled.")
cols[5].metric("Cancel rate", ch.pct(k["cancel_rate"]), fmt_delta("cancel_rate", points=True),
               delta_color="inverse", border=True, help="Value of cancelled product lines ÷ gross product sales.")
if prev:
    st.caption(f"Changes compare with the previous {(f.end - f.start).days + 1} days "
               f"({prev.start:%d %b %Y} – {prev.end:%d %b %Y}).")
else:
    st.caption("No change figures: the previous period of the same length starts before the data does.")

monthly = data.compute("monthly_revenue", f, start=f.start, end=f.end)
view = st.segmented_control("Revenue view", ["Timeline", "Year over year"], default="Timeline",
                            label_visibility="collapsed")
if view == "Year over year":
    ch.show(ch.lines_by_year(monthly, title="Monthly revenue by year"))
else:
    ch.show(ch.timeline(monthly, "month", "revenue", title="Monthly revenue"))
if monthly["partial"].any():
    st.caption("Hollow markers are partial months (the data ends on 9 Dec 2011, or the date filter cuts the month).")
with st.expander("Monthly figures"):
    st.dataframe(
        monthly[["month", "revenue", "orders", "customers", "partial"]], hide_index=True, width="stretch",
        column_config={"month": st.column_config.DateColumn("Month", format="MMM YYYY"),
                       "revenue": st.column_config.NumberColumn("Revenue", format="£%,.0f")},
    )

left, right = st.columns([3, 2], gap="large")
with left:
    grid = data.compute("weekday_hour", f)
    ch.show(ch.heatmap(grid, title="Orders by weekday and hour", xlabel="hour", ylabel="",
                       hover_name="orders", height=330))
    st.caption("The shop takes almost no orders on Saturdays, and Sunday trading starts late morning.")
with right:
    countries = data.compute("country_summary", f).head(10)
    ch.show(ch.bar_h(countries["country"], countries["revenue"], title="Top countries by revenue",
                     hover=[f"{c}: {ch.money(r)} · {s:.1%} of revenue"
                            for c, r, s in zip(countries["country"], countries["revenue"], countries["share"])]))
