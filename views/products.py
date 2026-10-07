import streamlit as st

from retail import charts as ch
from retail import data, metrics

f = data.page_header("Products", "Which products drive revenue, how concentrated sales are, "
                                 "and a per-product drill-down.")
summary = data.compute("product_summary", f)
if data.empty_state(summary, "product sales"):
    st.stop()

RANKINGS = {"Net revenue": ("revenue", ch.money), "Units (net)": ("units", ch.number),
            "Orders": ("orders", ch.number)}
c1, c2, _ = st.columns([1, 2, 3])
rank_by = c1.selectbox("Rank by", list(RANKINGS))
n = c2.slider("How many products", 5, 25, 10)
st.caption("Revenue and units are net of cancellations, so an order that was cancelled in full counts for nothing.")
col, fmt = RANKINGS[rank_by]

left, right = st.columns(2, gap="large")
with left:
    top = summary.nlargest(n, col, keep="first")
    ch.show(ch.bar_h(ch.nice(top["description"]), top[col], title=f"Top {n} products by {rank_by.lower()}",
                     fmt=fmt, hover=[f"{s} · {d}<br>{ch.money(r)} · {u:,} units · {o:,} orders"
                                     for s, d, r, u, o in zip(top["stock_code"], top["description"], top["revenue"],
                                                              top["units"], top["orders"])]))
with right:
    p = data.compute("pareto", f)
    point = metrics.pareto_point(p, 0.8)
    ch.show(ch.pareto_curve(p, point, title="Revenue concentration (Pareto)"))
    st.caption(f"{len(p):,} products sold. The top {point:.0%} of them bring in 80% of revenue.")

with st.expander("All products"):
    st.dataframe(
        summary, hide_index=True, width="stretch",
        column_config={"stock_code": "Code", "description": "Product",
                       "revenue": st.column_config.NumberColumn("Revenue", format="£%,.0f"),
                       "units": st.column_config.NumberColumn("Units", format="%,d"),
                       "orders": st.column_config.NumberColumn("Orders", format="%,d"),
                       "customers": st.column_config.NumberColumn("Customers", format="%,d")},
    )

st.subheader("Product drill-down")
choices = summary.head(500)
labels = dict(zip(choices["stock_code"], choices["stock_code"] + " · " + ch.nice(choices["description"])))
code = st.selectbox("Product (top 500 by revenue)", list(labels), format_func=labels.get)
row = summary.set_index("stock_code").loc[code]
m1, m2, m3, m4 = st.columns(4)
m1.metric("Net revenue", ch.money(row["revenue"], 2), border=True)
m2.metric("Units (net)", f"{row['units']:,}", border=True)
m3.metric("Orders", f"{row['orders']:,}", border=True)
m4.metric("Revenue rank", f"#{summary.index[summary['stock_code'] == code][0] + 1:,}", border=True)

detail = data.compute("product_detail", f, stock_code=code)
a, b, c = st.columns(3, gap="large")
with a:
    ch.show(ch.bar_v(detail["monthly"]["month"], detail["monthly"]["revenue"], title="Monthly revenue", yprefix="£",
                     hover=[f"{m:%b %Y}: {ch.money(v)}" for m, v in zip(detail["monthly"]["month"],
                                                                      detail["monthly"]["revenue"])]))
with b:
    prices = detail["prices"].nlargest(12, "units").sort_values("price")
    ch.show(ch.bar_v(prices["price"].map(lambda v: f"£{v:.2f}"), prices["units"], title="Units sold at each price",
                     fmt=ch.number, hover=[f"£{p:.2f}: {u:,} units on {n:,} lines"
                                           for p, u, n in zip(prices["price"], prices["units"], prices["lines"])]))
    st.caption("Lower unit prices usually mean wholesale-quantity orders.")
with c:
    ch.show(ch.bar_h(detail["countries"]["country"], detail["countries"]["revenue"], title="Top countries"))
