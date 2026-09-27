import streamlit as st

from retail import charts as ch
from retail import data

f = data.page_header("Geography", "Where revenue comes from. Use *Exclude United Kingdom* in the sidebar to compare international markets.")
countries = data.compute("country_summary", f)
if data.empty_state(countries, "sales"):
    st.stop()

uk = countries.loc[countries["country"] == "United Kingdom", "share"]
m1, m2, m3, m4 = st.columns(4)
m1.metric("Countries with sales", f"{len(countries):,}", border=True)
intl = countries[countries["country"] != "United Kingdom"]
if len(intl):
    m2.metric("Top international market", intl.iloc[0]["country"], ch.money(intl.iloc[0]["revenue"]),
              delta_color="off", delta_arrow="off", border=True)
else:
    m2.metric("Top international market", "–", border=True)
m3.metric("UK share of revenue", ch.pct(uk.iloc[0]) if len(uk) else "excluded", border=True)
m4.metric("International revenue", ch.money(countries.loc[countries["country"] != "United Kingdom", "revenue"].sum(), 2),
          border=True)

ch.show(ch.choropleth(countries, title="Revenue by country"))
unmapped = countries[countries["iso3"].isna()]
if len(unmapped):
    st.caption("Not on the map (no single ISO country): "
               + ", ".join(f"{c} ({ch.money(r)})" for c, r in zip(unmapped["country"], unmapped["revenue"])) + ".")

left, right = st.columns(2, gap="large")
with left:
    by_aov = countries[countries["orders"] >= 20].nlargest(10, "aov")
    ch.show(ch.bar_h(by_aov["country"], by_aov["aov"], title="Highest average order value",
                     fmt=lambda v: f"£{v:,.0f}",
                     hover=[f"{c}: £{a:,.0f} per order over {o:,} orders"
                            for c, a, o in zip(by_aov["country"], by_aov["aov"], by_aov["orders"])]))
    st.caption("Countries with at least 20 orders. Big average orders usually mean wholesale buyers.")
with right:
    regions = countries.groupby("region", as_index=False)["revenue"].sum().sort_values("revenue", ascending=False)
    ch.show(ch.bar_h(regions["region"], regions["revenue"], title="Revenue by region"))

st.markdown("**All countries**")
st.dataframe(
    countries[["country", "region", "revenue", "share", "orders", "customers", "aov"]],
    hide_index=True, width="stretch", height=420,
    column_config={
        "country": "Country", "region": "Region",
        "revenue": st.column_config.NumberColumn("Revenue", format="£%,.0f"),
        "share": st.column_config.ProgressColumn("Share", format="percent", min_value=0, max_value=1),
        "orders": st.column_config.NumberColumn("Orders", format="%,d"),
        "customers": st.column_config.NumberColumn("Customers", format="%,d"),
        "aov": st.column_config.NumberColumn("Avg order", format="£%,.0f"),
    },
)
