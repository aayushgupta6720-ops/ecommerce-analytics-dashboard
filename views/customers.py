import streamlit as st

from retail import charts as ch
from retail import data, metrics

f = data.page_header(
    "Customers (RFM)",
    "Customers scored 1–5 on **R**ecency, **F**requency and **M**onetary value, then grouped into segments "
    "by their R and F scores. Spend is net of cancellations. Only customers with an ID are included.",
)
r = data.rfm(f)
if data.empty_state(r, "identified customers"):
    st.stop()
seg = metrics.segment_summary(r)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Customers", f"{len(r):,}", border=True)
m2.metric("Median days since last order", f"{r['recency'].median():,.0f}", border=True)
m3.metric("Median orders per customer", f"{r['frequency'].median():,.0f}", border=True)
m4.metric("Median spend per customer", f"£{r['monetary'].median():,.0f}", border=True)
st.caption(f"Recency is measured from {f.end:%d %b %Y}, the end of the selected period.")

left, right = st.columns(2, gap="large")
with left:
    ch.show(ch.treemap(
        seg, label="segment", size="customers", color="revenue_share", title="Segments sized by customers",
        hover=[f"{s}<br>{c:,} customers ({cs:.1%})<br>{ch.money(v)} revenue ({rs:.1%})"
               for s, c, cs, v, rs in zip(seg["segment"], seg["customers"], seg["customer_share"],
                                          seg["revenue"], seg["revenue_share"])],
    ))
    shade = "darker" if ch.mode() == "light" else "brighter"
    st.caption(f"Tile size is the number of customers; {shade} tiles earn a bigger share of revenue.")
with right:
    ch.show(ch.grouped_bars_h(seg["segment"], {"Share of customers": seg["customer_share"],
                                               "Share of revenue": seg["revenue_share"]},
                              title="Customer share vs revenue share"))

DEFINITIONS = {
    "Champions": "Bought recently, buy often. Reward them.",
    "Loyal": "Buy often, fairly recently. Upsell.",
    "Potential Loyalist": "Recent, a few orders. Nurture into loyal.",
    "New Customers": "Very recent first order. Onboard well.",
    "Promising": "Recent but only one order.",
    "Need Attention": "Average recency and frequency. Re-engage before they slip.",
    "About to Sleep": "Getting stale, few orders.",
    "At Risk": "Used to buy regularly, not lately. Win back.",
    "Can't Lose": "Were top buyers, gone quiet. Win back urgently.",
    "Hibernating": "Long gone, few orders.",
}
table = seg.assign(meaning=seg["segment"].map(DEFINITIONS))
st.dataframe(
    table[["segment", "meaning", "customers", "customer_share", "revenue", "revenue_share",
           "avg_recency", "avg_frequency", "avg_monetary"]],
    hide_index=True, width="stretch",
    column_config={
        "segment": "Segment", "meaning": st.column_config.TextColumn("What it means", width="large"),
        "customers": st.column_config.NumberColumn("Customers", format="%,d"),
        "customer_share": st.column_config.NumberColumn("% customers", format="percent"),
        "revenue": st.column_config.NumberColumn("Revenue", format="£%,.0f"),
        "revenue_share": st.column_config.NumberColumn("% revenue", format="percent"),
        "avg_recency": st.column_config.NumberColumn("Avg days since order", format="%.0f"),
        "avg_frequency": st.column_config.NumberColumn("Avg orders", format="%.1f"),
        "avg_monetary": st.column_config.NumberColumn("Avg spend", format="£%,.0f"),
    },
)

pick = st.selectbox("Highlight a segment", seg["segment"].tolist(), index=0)
ch.show(ch.scatter_highlight(
    r, "recency", "frequency", highlight=r["segment"] == pick, highlight_name=pick,
    title="Every customer: days since last order vs number of orders",
    xlabel="Days since last order", ylabel="Orders (log scale)", log_y=True,
    hover=[f"Customer {c} · {s}<br>{rec:,} days · {fr:,} orders · {ch.money(mo)}"
           for c, s, rec, fr, mo in zip(r["customer_id"], r["segment"], r["recency"], r["frequency"], r["monetary"])],
))

export = r[["customer_id", "country", "recency", "frequency", "monetary", "r_score", "f_score", "m_score", "segment"]]
st.download_button("Download customer segments (CSV)", export.to_csv(index=False), "rfm_segments.csv", "text/csv",
                   icon=":material/download:")
