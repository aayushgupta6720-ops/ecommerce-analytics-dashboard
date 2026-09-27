import streamlit as st

from retail import charts as ch
from retail import data

f = data.page_header(
    "Returns & cancellations",
    "Cancelled invoice lines (invoice numbers starting with C). Product lines only, so postage, "
    "fees and manual adjustments are left out.",
)
k = data.compute("kpis", f)
monthly = data.compute("cancellation_monthly", f)
if data.empty_state(k, "sales"):
    st.stop()

cancels = data.compute("country_returns", f)
m1, m2, m3 = st.columns(3)
m1.metric("Cancelled value", ch.money(k["cancelled_value"], 2), border=True)
m2.metric("Cancellation rate", ch.pct(k["cancel_rate"]), border=True, help="Cancelled value ÷ gross product sales.")
m3.metric("Revenue after cancellations", ch.money(k["revenue"] - k["cancelled_value"], 2), border=True)

left, right = st.columns(2, gap="large")
with left:
    ch.show(ch.timeline(monthly, "month", "cancel_rate", title="Cancellation rate by month", fmt=ch.pct,
                        partial=None, yfmt=".0%", tickprefix=""))
with right:
    ch.show(ch.bar_v(monthly["month"], monthly["cancelled"], title="Cancelled value by month", height=360, yprefix="£",
                     hover=[f"{m:%b %Y}: {ch.money(v)} cancelled" for m, v in zip(monthly["month"], monthly["cancelled"])]))
biggest = data.compute("largest_cancellations", f, n=5)
if len(biggest):
    top2 = biggest.head(2)["share_of_cancelled"].sum()
    st.caption(f"A cancellation can refer to an order placed in an earlier month, so a single month's rate can spike. "
               f"The two largest single cancellations make up {top2:.0%} of all cancelled value (table below).")
with st.expander("Largest single cancellations", expanded=bool(len(biggest)) and biggest.head(2)["share_of_cancelled"].sum() > 0.2):
    st.dataframe(
        biggest, hide_index=True, width="stretch",
        column_config={
            "invoice_date": st.column_config.DatetimeColumn("Date", format="D MMM YYYY"), "invoice": "Invoice",
            "customer_id": st.column_config.NumberColumn("Customer", format="%d"), "stock_code": "Code",
            "description": "Product", "quantity": st.column_config.NumberColumn("Units", format="%,d"),
            "country": "Country", "value": st.column_config.NumberColumn("Value", format="£%,.0f"),
            "share_of_cancelled": st.column_config.NumberColumn("% of all cancelled", format="percent"),
        },
    )

st.subheader("Most-cancelled products")
min_units = st.select_slider("Only products with at least this many units sold", [10, 50, 100, 250, 500, 1000], 100)
products = data.compute("product_returns", f, min_units_sold=min_units)
if not data.empty_state(products, "cancelled products"):
    a, b = st.columns(2, gap="large")
    with a:
        top = products.head(10)
        ch.show(ch.bar_h(ch.nice(top["description"], 36), top["cancelled_value"],
                         title="By cancelled value",
                         hover=[f"{d}<br>{ch.money(v)} cancelled · {r:.0%} of units"
                                for d, v, r in zip(top["description"], top["cancelled_value"], top["return_rate"])]))
    with b:
        top = products.nlargest(10, "return_rate")
        ch.show(ch.bar_h(ch.nice(top["description"], 36), top["return_rate"],
                         title="By share of units cancelled", fmt=ch.pct,
                         hover=[f"{d}<br>{c:,} of {s:,} units cancelled"
                                for d, c, s in zip(top["description"], top["units_cancelled"], top["units_sold"])]))
        st.caption("Over 100% means more units were cancelled than sold in this period "
                   "(the original orders fell before it).")
    st.dataframe(
        products, hide_index=True, width="stretch", height=300,
        column_config={
            "stock_code": "Code", "description": "Product",
            "units_sold": st.column_config.NumberColumn("Units sold", format="%,d"),
            "revenue": st.column_config.NumberColumn("Revenue", format="£%,.0f"),
            "units_cancelled": st.column_config.NumberColumn("Units cancelled", format="%,d"),
            "cancelled_value": st.column_config.NumberColumn("Cancelled value", format="£%,.0f"),
            "cancellations": st.column_config.NumberColumn("Cancel invoices", format="%,d"),
            "return_rate": st.column_config.NumberColumn("% units cancelled", format="percent"),
        },
    )

top = cancels.head(10)
ch.show(ch.bar_h(top["country"], top["cancelled"], title="Cancelled value by country",
                 hover=[f"{c}: {ch.money(v)} cancelled ({r:.1%} of sales)"
                        for c, v, r in zip(top["country"], top["cancelled"], top["cancel_rate"])]))

st.markdown("**Customers with the most cancelled value** (3+ orders)")
customers = data.compute("customer_returns", f, min_orders=3)
st.dataframe(
    customers.head(50), hide_index=True, width="stretch", height=330,
    column_config={
        "customer_id": st.column_config.NumberColumn("Customer", format="%d"), "country": "Country",
        "orders": st.column_config.NumberColumn("Orders", format="%,d"),
        "revenue": st.column_config.NumberColumn("Revenue", format="£%,.0f"),
        "cancellations": st.column_config.NumberColumn("Cancel invoices", format="%,d"),
        "cancelled_value": st.column_config.NumberColumn("Cancelled", format="£%,.0f"),
        "cancel_rate": st.column_config.NumberColumn("% of revenue", format="percent"),
    },
)
