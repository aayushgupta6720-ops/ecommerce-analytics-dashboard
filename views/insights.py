import pandas as pd
import streamlit as st

from retail import charts as ch
from retail import data

b = data.insights_bundle()
st.header("Insights & actions")
st.caption(
    "Five recommendations drawn from all two years of data (the sidebar filters don't apply here). Each one shows "
    "the evidence, the action, the value at stake under an assumption you can change, and how to measure it. "
    "Values are sized opportunities, not forecasts. Effort is an estimate."
)

# Assumptions live in session state so the summary table reflects the sliders further down.
DEFAULTS = {"champ_retained": 5, "winback_rate": 10, "peak_lost": 2.0, "guest_converted": 30,
            "bulk_threshold": 1000, "handling_cost": 10}
A = {k: st.session_state.get(k, v) for k, v in DEFAULTS.items()}

champ = b["champions"]
champ_revenue = champ["last_year_revenue"].sum()
wb = b["winback"]
wb_prev = wb["prev_year_revenue"].sum()
latest = b["peak"]["years"][-1]
g = b["guests"]
bulk = data.bulk_cancellations(A["bulk_threshold"])
bundles = b["bundles"]

value = {
    "champions": champ_revenue * A["champ_retained"] / 100,
    "winback": wb_prev * A["winback_rate"] / 100,
    "peak": latest["peak"] * A["peak_lost"] / 100,
    "guests": g["gross_per_year"] * A["guest_converted"] / 100,
    "bulk": bulk["cancelled_value_per_year"] * A["handling_cost"] / 100,
}
summary = pd.DataFrame([
    ("1", "Keep Champions buying", f"{b['champion_share']['customer_share']:.0%} of customers bring in "
     f"{b['champion_share']['revenue_share']:.0%} of revenue", f"{ch.money(value['champions'])} protected / year",
     "Medium", "Champions' repeat rate and spend"),
    ("2", "Win back lapsed high-value customers", f"{len(wb):,} customers spent {ch.money(wb_prev)} in their last "
     "active year", f"{ch.money(value['winback'])} recovered / year", "Low–medium", "Reactivation vs a hold-out group"),
    ("3", "Plan stock and staff for Sep–Nov", f"{latest['peak_share']:.0%} of annual revenue; "
     f"{latest['top_month_multiple']:.1f}× in November", f"{ch.money(value['peak'])} protected / season",
     "Medium–high", "Forecast error, Sep–Nov stock-outs"),
    ("4", "Turn guest checkouts into accounts", f"{g['gross_share']:.0%} of gross sales can't be contacted",
     f"{ch.money(value['guests'])} made reachable / year", "Low", "Share of revenue with a customer ID"),
    ("5", "Confirm bulk orders before picking", f"{bulk['share_of_cancelled']:.0%} of cancelled value from "
     f"{bulk['cancelled_lines']} lines", f"{ch.money(value['bulk'])} handling saved / year", "Low",
     "Cancelled value on large lines"),
], columns=["#", "Recommendation", "Evidence", "Value at stake", "Effort", "Measure it by"])
# A Markdown table wraps long cells; a data grid would truncate six text columns.
header = "| " + " | ".join(summary.columns) + " |\n|" + "|".join(["---"] * len(summary.columns)) + "|\n"
st.markdown(header + "\n".join("| " + " | ".join(map(str, row)) + " |" for row in summary.itertuples(index=False)))
st.caption("Values use the assumptions set below; change a slider and this table updates. They're different kinds "
           "of value (protected, recovered, reachable, cost avoided), so they aren't added together.")

# ---------------------------------------------------------------- 1. Champions
with st.container(border=True):
    st.subheader("1 · Keep Champions buying")
    left, right = st.columns([3, 2], gap="large")
    with left:
        share = b["champion_share"]
        st.markdown(
            f"- **Evidence:** {share['customers']:,} Champions ({share['customer_share']:.0%} of identified "
            f"customers) bring in **{share['revenue_share']:.0%} of net revenue**: {ch.money(champ_revenue)} in the "
            "last 365 days.\n"
            f"- **Right now:** none of them is in the riskiest 20% on the churn model "
            f"(highest risk percentile: {champ['risk_percentile'].max():.0%}). This is about keeping a healthy group "
            "healthy, not a rescue.\n"
            "- **Action:** a VIP tier with early access to new ranges and a named contact; review the watch list "
            "monthly and call anyone whose churn risk climbs.\n"
            "- **Measure:** Champions' 90-day repeat rate and spend, against the same months last year."
        )
    with right:
        st.slider("Champion revenue a VIP programme keeps (%)", 1, 20, key="champ_retained",
                  value=DEFAULTS["champ_retained"])
        st.metric("Revenue protected per year", ch.money(value["champions"]), border=True)
    with st.expander(f"Watch list: Champions by current churn risk (as of {pd.Timestamp(b['snapshot']):%d %b %Y})"):
        st.dataframe(champ.head(50), hide_index=True, width="stretch", height=300,
                     column_config={"customer_id": st.column_config.NumberColumn("Customer", format="%d"),
                                    "country": "Country", "recency": "Days since order", "frequency": "Orders",
                                    "monetary": st.column_config.NumberColumn("Lifetime spend", format="£%,.0f"),
                                    "last_year_revenue": st.column_config.NumberColumn("Last 365 days",
                                                                                       format="£%,.0f"),
                                    "risk_percentile": st.column_config.ProgressColumn(
                                        "Churn risk percentile", format="percent", min_value=0, max_value=1)})
        st.download_button("Download the Champions watch list (CSV)", champ.to_csv(index=False),
                           "champions_watch_list.csv", "text/csv", icon=":material/download:")

# ---------------------------------------------------------------- 2. Win-back
with st.container(border=True):
    st.subheader("2 · Win back lapsed high-value customers")
    left, right = st.columns([3, 2], gap="large")
    with left:
        counts = wb["segment"].value_counts()
        at_risk, cant_lose = counts.get("At Risk", 0), counts.get("Can't Lose", 0)
        st.markdown(
            f"- **Evidence:** {len(wb):,} customers in the *At Risk* ({at_risk:,}) and *Can't Lose* "
            f"({cant_lose:,}) segments used to buy often, then went quiet "
            f"for about a year (median {wb['recency'].median():.0f} days). In their last active year they spent "
            f"**{ch.money(wb_prev)}**.\n"
            "- **Action:** a personal win-back email with an offer on their usual categories, highest previous "
            "spend first. Keep a random 10% as a hold-out group to measure the real effect.\n"
            "- **Measure:** share who order again within 90 days, versus the hold-out group."
        )
    with right:
        st.slider("Customers won back (%)", 5, 30, key="winback_rate", value=DEFAULTS["winback_rate"])
        st.metric("Revenue recovered per year", ch.money(value["winback"]), border=True,
                  help="Won-back share × what these customers spent in their last active year.")
    with st.expander("Contact list, highest previous spend first"):
        st.dataframe(wb.head(50), hide_index=True, width="stretch", height=300,
                     column_config={"customer_id": st.column_config.NumberColumn("Customer", format="%d"),
                                    "segment": "Segment", "country": "Country", "recency": "Days since order",
                                    "frequency": "Orders", "monetary": st.column_config.NumberColumn(
                                        "Lifetime spend", format="£%,.0f"),
                                    "prev_year_revenue": st.column_config.NumberColumn(
                                        "Spend in last active year", format="£%,.0f")})
        st.download_button("Download the win-back list (CSV)", wb.to_csv(index=False), "winback_list.csv", "text/csv",
                           icon=":material/download:")

# ---------------------------------------------------------------- 3. Peak
with st.container(border=True):
    st.subheader("3 · Plan stock and staff for September–November")
    left, right = st.columns([3, 2], gap="large")
    years = b["peak"]["years"]
    with left:
        st.markdown(
            "- **Evidence:** the autumn peak repeats almost exactly: "
            + "; ".join(f"{y['year']}: **{y['peak_share']:.0%}** of the year's revenue in Sep–Nov, "
                        f"{y['top_month']} at {y['top_month_multiple']:.2f}× an average month" for y in years)
            + ". \"Same month last year\" forecast 2011 to within 8% (Predictions page).\n"
            "- **Action:** place stock orders by August using last year's Sep–Nov sales by product, plus trend; "
            "line up temporary warehouse staff for October and November.\n"
            "- **Measure:** forecast error and stock-outs on the top 200 products during Sep–Nov."
        )
    with right:
        st.slider("Share of peak demand lost today to stock-outs or capacity (%)", 0.5, 10.0, step=0.5,
                  key="peak_lost", value=DEFAULTS["peak_lost"])
        st.metric("Revenue protected per season", ch.money(value["peak"]), border=True,
                  help=f"Assumed lost share × Sep–Nov revenue in {latest['year']} ({ch.money(latest['peak'])}). "
                       "Stock-outs aren't in the data, so this share is an assumption.")
    monthly = b["peak"]["monthly"]
    ch.show(ch.bar_v(monthly["month"], monthly["revenue"], title="Monthly net revenue (Sep–Nov highlighted)",
                     highlight=monthly["month"].dt.month.isin([9, 10, 11]), yprefix="£", height=260,
                     hover=[f"{m:%b %Y}: {ch.money(v)}" for m, v in zip(monthly["month"], monthly["revenue"])]))

# ---------------------------------------------------------------- 4. Guests
with st.container(border=True):
    st.subheader("4 · Turn guest checkouts into accounts")
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown(
            f"- **Evidence:** {g['line_share']:.0%} of sales lines and **{g['gross_share']:.0%} of gross sales** "
            f"({ch.money(g['gross_per_year'])} a year) come from {g['orders']:,} orders with no customer ID. Those "
            "buyers can't be segmented, protected (1) or won back (2).\n"
            "- **Action:** offer order tracking or a small next-order discount for creating an account at checkout, "
            "and capture an email on every guest order.\n"
            "- **Measure:** share of revenue with a customer ID, monthly."
        )
    with right:
        st.slider("Guest orders converted to accounts (%)", 5, 80, key="guest_converted",
                  value=DEFAULTS["guest_converted"])
        st.metric("Revenue made reachable per year", ch.money(value["guests"]), border=True,
                  help="Not new revenue: revenue from customers you could then retain and market to.")

# ---------------------------------------------------------------- 5. Bulk orders
with st.container(border=True):
    st.subheader("5 · Confirm bulk orders before picking")
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown(
            f"- **Evidence:** cancelled lines of {bulk['threshold']:,}+ units are only **{bulk['cancelled_lines']} "
            f"lines**, but **{bulk['share_of_cancelled']:.0%} of all cancelled value** "
            f"({ch.money(bulk['cancelled_value_per_year'])} a year of goods picked, then cancelled). The two biggest "
            "were single orders cancelled in full.\n"
            f"- **Action:** hold any order line of {bulk['threshold']:,}+ units for a quick confirmation call before "
            f"picking. That's about **{bulk['sales_lines_per_month']:.0f} lines a month** to check.\n"
            "- **Measure:** cancelled value on large lines, and time from order to dispatch for them."
        )
    with right:
        st.select_slider("Hold lines of at least (units)", [250, 500, 1000, 2500, 5000], key="bulk_threshold",
                         value=DEFAULTS["bulk_threshold"])
        st.slider("Handling cost as a share of goods value (%)", 2, 30, key="handling_cost",
                  value=DEFAULTS["handling_cost"])
        st.metric("Handling cost avoided per year", ch.money(value["bulk"]), border=True)

# ---------------------------------------------------------------- not a priority
with st.expander("Considered, not prioritised: \"complete the set\" prompts"):
    ceiling = bundles["missed_value"].sum()
    st.markdown(
        f"The strongest product pairs (Poppy's Playhouse rooms, matching bowls and candles) are bought together "
        f"30–46× more often than chance. But orders that had one item without its partner add up to only "
        f"**{ch.money(ceiling)}** for these {len(bundles)} pairs, even if every one of those orders had added the "
        f"partner item. At a realistic 20% take-up that's about {ch.money(ceiling * 0.2)}. Worth doing if it's cheap "
        "to add to the site, but it's small next to 1–5."
    )
    st.dataframe(bundles[["antecedent_desc", "consequent_desc", "lift", "confidence", "orders_without_b",
                          "value_per_b_order", "missed_value"]],
                 hide_index=True, width="stretch",
                 column_config={"antecedent_desc": "If they bought", "consequent_desc": "Suggest",
                                "lift": st.column_config.NumberColumn("Lift", format="%.1f×"),
                                "confidence": st.column_config.NumberColumn("Confidence", format="percent"),
                                "orders_without_b": st.column_config.NumberColumn("Orders missing it", format="%,d"),
                                "value_per_b_order": st.column_config.NumberColumn("Value per order", format="£%.2f"),
                                "missed_value": st.column_config.NumberColumn("Ceiling", format="£%,.0f")})
