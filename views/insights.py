import pandas as pd
import streamlit as st

from retail import charts as ch
from retail import data

b = data.insights_bundle()
st.header("Insights & actions")
st.caption(
    "Five recommendations drawn from all two years of data (the sidebar filters don't apply here). Each one shows "
    "the evidence, the action, the value at stake under an assumption you can change, and how to measure it. "
    "Values are sized opportunities, not forecasts, and they're revenue, not profit: the data has no costs or "
    "margins. Effort is an estimate."
)

# Assumptions live in session state so the summary table reflects the sliders further down.
DEFAULTS = {"champ_retained": 5, "winback_rate": 10, "peak_lost": 2.0, "guest_converted": 30,
            "bulk_threshold": 1000, "handling_cost": 10}
A = {k: st.session_state.get(k, v) for k, v in DEFAULTS.items()}

champ = b["champions"]
champ_revenue = champ["last_year_revenue"].sum()
wb = b["winback"]
wb_prev = wb["last_active_year_revenue"].sum()
base = b["winback_baseline"]
latest = b["peak"]["years"][-1]
g = b["guests"]
bulk = data.bulk_cancellations(A["bulk_threshold"])
bundles = b["bundles"]

value = {
    "champions": champ_revenue * A["champ_retained"] / 100,
    # Extra customers won back, spending what lapsed customers who came back on their own spent.
    "winback": wb_prev * A["winback_rate"] / 100 * base["spend_ratio"],
    "peak": latest["peak"] * A["peak_lost"] / 100,
    "guests": g["gross_per_year"] * A["guest_converted"] / 100,
    "bulk": bulk["genuine_value_per_year"] * A["handling_cost"] / 100,
}
summary = pd.DataFrame([
    ("1", "Keep Champions buying", f"{b['champion_share']['customer_share']:.0%} of identified customers bring in "
     f"{b['champion_share']['revenue_share']:.0%} of their revenue", f"{ch.money(value['champions'])} protected / year",
     "Medium", "Champions' repeat rate and spend"),
    ("2", "Win back lapsed high-value customers", f"{len(wb):,} customers spent {ch.money(wb_prev)} in their last "
     f"active year; {base['returned_share']:.0%} of such customers come back anyway",
     f"{ch.money(value['winback'])} extra / year", "Low–medium", "Reactivation vs a hold-out group"),
    ("3", "Plan stock and staff for Sep–Nov", f"{latest['peak_share']:.0%} of annual revenue; "
     f"{latest['top_month_multiple']:.1f}× in November", f"{ch.money(value['peak'])} protected / season",
     "Medium–high", "Forecast error, Sep–Nov stock-outs"),
    ("4", "Link web-shop orders to customer records", f"{g['gross_share']:.0%} of gross sales have no customer ID, "
     f"{g['web_shop_share_of_guest_gross']:.0%} of it from the web shop", f"{ch.money(value['guests'])} made "
     "analysable / year", "Low–medium", "Share of revenue with a customer ID, by channel"),
    ("5", "Check unusual quantities at order entry", f"{bulk['keying_errors']} mistyped lines are "
     f"{bulk['keying_error_share']:.0%} of cancelled value", f"{ch.money(value['bulk'])} handling saved / year",
     "Low", "Lines cancelled within an hour of entry"),
], columns=["#", "Recommendation", "Evidence", "Value at stake", "Effort", "Measure it by"])
# A Markdown table wraps long cells; a data grid would truncate six text columns.
header = "| " + " | ".join(summary.columns) + " |\n|" + "|".join(["---"] * len(summary.columns)) + "|\n"
st.markdown(header + "\n".join("| " + " | ".join(map(str, row)) + " |" for row in summary.itertuples(index=False)))
st.caption("Values use the assumptions set below; change a slider and this table updates. They're different kinds "
           "of value (protected, recovered, made analysable, cost avoided), so they aren't added together.")

# ---------------------------------------------------------------- 1. Champions
with st.container(border=True):
    st.subheader("1 · Keep Champions buying")
    left, right = st.columns([3, 2], gap="large")
    with left:
        share = b["champion_share"]
        st.markdown(
            f"- **Evidence:** {share['customers']:,} Champions ({share['customer_share']:.0%} of identified "
            f"customers) bring in **{share['revenue_share']:.0%} of identified customers' net revenue**: "
            f"{ch.money(champ_revenue)} in the last 365 days. Web-shop orders without a customer ID (4) aren't in "
            "that total.\n"
            f"- **Right now:** none of them is in the riskiest 20% on the churn model "
            f"(highest risk percentile: {champ['risk_percentile'].max():.0%}). That's partly by construction: the "
            "model leans on the same recency and frequency that make someone a Champion. So this is about keeping a "
            "healthy group healthy, not a rescue, and the watch list matters for the few whose risk climbs.\n"
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
            f"for a while (median {wb['recency'].median():.0f} days). In their last active year (the 365 days up "
            f"to their last order) they spent **{ch.money(wb_prev)}**.\n"
            f"- **Without a campaign:** of the {base['customers']:,} customers in these segments on "
            f"{pd.Timestamp(base['snapshot']):%d %b %Y}, **{base['returned_share']:.0%} ordered again within a year** "
            f"on their own, spending {base['spend_ratio']:.0%} of what they had the year before (a snapshot with "
            f"only {base['history_days']} days of history behind it). A campaign is worth only the returns it adds.\n"
            "- **Action:** a personal win-back email with an offer on their usual categories, highest previous "
            "spend first. Keep a random 10% as a hold-out group to measure the real effect.\n"
            "- **Measure:** share who order again within 90 days, versus the hold-out group."
        )
    with right:
        st.slider("Extra customers won back, beyond those who return anyway (%)", 5, 30, key="winback_rate",
                  value=DEFAULTS["winback_rate"])
        st.metric("Extra revenue per year", ch.money(value["winback"]), border=True,
                  help="Extra won-back share × what these customers spent in their last active year × the share "
                       "of it that customers who came back on their own went on to spend.")
    with st.expander("Contact list, highest previous spend first"):
        st.dataframe(wb.head(50), hide_index=True, width="stretch", height=300,
                     column_config={"customer_id": st.column_config.NumberColumn("Customer", format="%d"),
                                    "segment": "Segment", "country": "Country", "recency": "Days since order",
                                    "frequency": "Orders", "monetary": st.column_config.NumberColumn(
                                        "Lifetime spend", format="£%,.0f"),
                                    "last_active_year_revenue": st.column_config.NumberColumn(
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

# ---------------------------------------------------------------- 4. Web shop
with st.container(border=True):
    st.subheader("4 · Link web-shop orders to customer records")
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown(
            f"- **Evidence:** {g['line_share']:.0%} of sales lines and **{g['gross_share']:.0%} of gross sales** "
            f"({ch.money(g['gross_per_year'])} a year) come from {g['orders']:,} orders with no customer ID. They "
            "look like the retailer's own web shop, not wholesale buyers skipping a login: "
            f"{g['web_shop_orders_without_id']:.0%} of orders carrying the web shop's DOTCOM POSTAGE line have no "
            f"customer ID, and those orders hold **{g['web_shop_share_of_guest_gross']:.0%}** of this revenue. Their "
            f"lines are {g['median_units_guest']:.0f} unit against {g['median_units_identified']:.0f} for the "
            f"accounts, at about **{g['price_ratio']:.1f}× the price** for the same product in the same month: "
            "consumer orders. So the segments, churn model and win-back list (1, 2) cover the wholesale accounts "
            "only.\n"
            "- **Action:** pass the web shop's customer email or account ID into the order system, so its buyers can "
            "be analysed and looked after as a channel of their own.\n"
            "- **Measure:** share of revenue with a customer ID, by channel."
        )
    with right:
        st.slider("Web-shop revenue linked to a customer (%)", 5, 100, key="guest_converted",
                  value=DEFAULTS["guest_converted"])
        st.metric("Revenue made analysable per year", ch.money(value["guests"]), border=True,
                  help="Not new revenue: consumer revenue that segmentation and retention work could then cover.")

# ---------------------------------------------------------------- 5. Order entry
with st.container(border=True):
    st.subheader("5 · Check unusual quantities at order entry")
    left, right = st.columns([3, 2], gap="large")
    with left:
        units = ", ".join(f"{u:,}" for u in bulk["keying_error_units"][:2])
        st.markdown(
            f"- **Evidence:** the biggest cancellations are typing mistakes. Lines of {units} units were cancelled "
            f"within the hour they were entered, and the {bulk['keying_errors']} lines of {bulk['threshold']:,}+ units "
            "cancelled that fast (same product, customer and quantity) are "
            f"**{bulk['keying_error_share']:.0%} of all cancelled value**. The other {bulk['genuine_lines']} large "
            f"cancellations are {bulk['genuine_share']:.0%} ({ch.money(bulk['genuine_value_per_year'])} of goods a "
            "year).\n"
            f"- **Action:** ask for confirmation at order entry when a line is at least {bulk['threshold']:,} units "
            "and 50 times the product's usual line, or the product has never sold before. That flags about "
            f"**{bulk['sales_lines_per_month']:.0f} lines a month**, and would have flagged "
            f"{bulk['keying_errors_caught']} of the {bulk['keying_errors']} mistyped ones.\n"
            "- **Measure:** lines cancelled within an hour of entry, and cancelled value on large lines."
        )
    with right:
        st.select_slider("Check lines of at least (units)", [250, 500, 1000, 2500, 5000], key="bulk_threshold",
                         value=DEFAULTS["bulk_threshold"])
        st.slider("Handling cost as a share of goods value (%)", 2, 30, key="handling_cost",
                  value=DEFAULTS["handling_cost"])
        st.metric("Handling cost avoided per year", ch.money(value["bulk"]), border=True,
                  help="Handling on the genuine large cancellations, if confirming at entry stopped them before "
                       "picking. Small on its own: the check is mainly insurance against a mistyped order being "
                       "picked.")

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
