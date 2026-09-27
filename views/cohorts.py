import streamlit as st

from retail import charts as ch
from retail import data

f = data.page_header(
    "Cohort retention",
    "Customers grouped by the month of their first-ever order. Each row shows how many came back in later months.",
)
c = data.cohorts(f)
if data.empty_state(c, "customer cohorts (a cohort needs its first order inside the selected period)"):
    st.stop()

MEASURES = {
    "Retention %": ("retention", ".0%", "retention"),
    "Active customers": ("customers", ",.0f", "customers"),
    "Revenue": ("revenue", "£,.0f", "revenue"),
}
measure = st.segmented_control("Show", list(MEASURES), default="Retention %")
col, zfmt, name = MEASURES[measure or "Retention %"]

grid = c.pivot(index="cohort", columns="period", values=col)
grid.index = grid.index.strftime("%b %Y")
# Month 0 is 100% by definition, so drop it from the colour scale or it flattens everything else.
shown = grid.drop(columns=0) if col == "retention" and grid.shape[1] > 1 else grid
zmax = float(shown.max().max()) if col == "retention" else None
ch.show(ch.heatmap(shown, title=f"{measure} by months since first order", xlabel="month", ylabel="cohort",
                   zfmt=zfmt.replace("£", ""), hover_name=name, height=max(360, 22 * len(grid) + 120), zmax=zmax))
if col == "retention":
    st.caption("Month 0 (always 100%) is hidden so later months get the full colour range.")
if (c["cohort"].min().year, c["cohort"].min().month) == (2009, 12):
    st.caption("The Dec 2009 cohort is where the data starts, so it also contains long-standing customers. "
               "That's why it retains much better than later cohorts.")

left, right = st.columns([2, 3], gap="large")
with left:
    sizes = c[c["period"] == 0]
    ch.show(ch.bar_v(sizes["cohort"], sizes["cohort_size"], title="New customers per month", fmt=ch.number,
                     hover=[f"{m:%b %Y}: {n:,} new customers" for m, n in zip(sizes["cohort"], sizes["cohort_size"])]))
with right:
    later = c[c["period"] > 0]
    curve = later.groupby("period").agg(customers=("customers", "sum"))
    base = c[c["period"] == 0].set_index("cohort")["cohort_size"]
    # Weighted average: returning customers / size of the cohorts old enough to have reached that month.
    eligible = {p: base[base.index.isin(later.loc[later["period"] == p, "cohort"])].sum() for p in curve.index}
    curve["retention"] = curve["customers"] / curve.index.map(eligible)
    curve = curve.reset_index()
    fig = ch.bar_v(curve["period"], curve["retention"], title="Average retention by month since first order",
                   fmt=ch.pct, yfmt=".0%",
                   hover=[f"Month {p}: {v:.1%} came back" for p, v in zip(curve["period"], curve["retention"])])
    fig.update_xaxes(title_text="months since first order", dtick=2)
    ch.show(fig)

with st.expander("Cohort table"):
    st.dataframe(grid, width="stretch")
