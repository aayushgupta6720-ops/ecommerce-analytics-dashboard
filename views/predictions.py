import pandas as pd
import streamlit as st

from retail import charts as ch
from retail import data, models

st.header("Predictions")
m = data.model_metrics()
st.caption(
    f"Three models, each trained on data up to **{pd.Timestamp(m['calibration_end']):%d %b %Y}** and scored on the "
    f"following year (to {pd.Timestamp(m['holdout_end']):%d %b %Y}), which they never saw. Every model is compared "
    "with a simple baseline. This page uses that fixed split, so the sidebar filters don't apply here. "
    "Models are implemented with numpy/scipy in `retail/models.py` and retrained by `scripts/train_models.py`."
)

forecast_tab, churn_tab, clv_tab = st.tabs(["Revenue forecast", "Churn risk", "Customer lifetime value"])

# ---------------------------------------------------------------- forecast
with forecast_tab:
    fc = data.model_table("forecast")
    scores = m["forecast"]["scores"]
    st.markdown(
        "**Verdict: seasonality is the whole story.** Forecasting 12 months ahead from Nov 2010, "
        "\"same month last year\" misses by 8% overall; the usual baselines miss by 49–80% because they can't "
        "see the autumn peak coming."
    )
    names = {"seasonal_naive": "Same month last year", "moving_average_3": "3-month average", "naive": "Last month"}
    cols = st.columns(3)
    for col, key in zip(cols, names):
        col.metric(f"{names[key]}: error (WAPE)", ch.pct(scores[key]["wape"]), border=True,
                   help="Total absolute error ÷ total revenue over the 12 test months. "
                        f"MAPE: {ch.pct(scores[key]['mape'])}.")
    test = fc[fc["split"] == "test"]
    series = {"Actual": fc["actual"]} | {names[k]: fc[k].where(fc["split"] == "test") for k in names}
    shade = (test["month"].min(), test["month"].max(), "Test year (unseen)")
    ch.show(ch.multi_line(fc["month"], series, title="Monthly net revenue: actual vs 12-month-ahead forecasts",
                          yprefix="£", ink="Actual", shade=shade))
    st.caption("With two years of data there is exactly one earlier year to learn the seasonal pattern from, so "
               "\"same month last year\" is the honest benchmark here. A model with trend would need more history.")
    with st.expander("Monthly figures"):
        st.dataframe(fc.rename(columns={"month": "Month", "actual": "Actual", "split": "Split"} | names),
                     hide_index=True, width="stretch",
                     column_config={c: st.column_config.NumberColumn(c, format="£%,.0f")
                                    for c in ["Actual", *names.values()]}
                     | {"Month": st.column_config.DateColumn("Month", format="MMM YYYY")})

# ---------------------------------------------------------------- churn
with churn_tab:
    c = m["churn"]
    scores = data.model_table("churn")
    st.markdown(
        f"**Verdict: good at ranking who will lapse; re-base the probabilities each season.** Of the customers "
        f"active in the year before {pd.Timestamp(c['test_cutoff']):%d %b %Y}, {ch.pct(c['test_churn_rate'], 0)} made "
        f"no purchase in the next {c['horizon_days']} days. The model, trained on the same season a year earlier "
        f"({pd.Timestamp(c['train_cutoff']):%d %b %Y}), ranks them better than the obvious rule, *longest since last "
        "order*."
    )
    k1, k2, k3 = st.columns(3)
    k1.metric("Model AUC", f"{c['auc_model']:.3f}",
              f"{c['auc_model'] - c['auc_recency_baseline']:+.3f} vs recency rule", border=True,
              help="Chance a random churner is scored above a random non-churner. 0.5 = coin flip.")
    k2.metric("Churners in the top 20% riskiest", ch.pct(c["precision_top20"]),
              f"base rate {ch.pct(c['test_churn_rate'])}", delta_color="off", border=True)
    k3.metric("Churners caught by contacting that 20%", ch.pct(c["recall_top20"]), border=True)

    left, right = st.columns(2, gap="large")
    with left:
        y = scores["churned"].to_numpy()
        roc_model = models.roc_curve(scores["churn_probability"], y)
        roc_base = models.roc_curve(scores["recency_days"], y)
        fig = ch.multi_line(
            roc_model["fpr"], {f"Model (AUC {c['auc_model']:.2f})": roc_model["tpr"]},
            title="ROC curve on the test cutoff", xtitle="False positive rate", ytitle="True positive rate",
            yfmt=".0%", xfmt=".0%", diagonal=True, hover_fmt=ch.pct)
        fig.add_scatter(x=roc_base["fpr"], y=roc_base["tpr"], mode="lines",
                        name=f"Recency rule (AUC {c['auc_recency_baseline']:.2f})",
                        line=dict(color=ch.tok()["series"][1], width=2), hoverinfo="skip")
        fig.update_yaxes(range=[0, 1.02])
        ch.show(fig)
    with right:
        cal = pd.DataFrame(c["calibration"])
        ch.show(ch.grouped_columns(cal["decile"].astype(str),
                                   {"Predicted": cal["predicted"], "Observed": cal["observed"]},
                                   title="Calibration: predicted vs observed churn by risk decile", fmt=ch.pct,
                                   yfmt=".0%", xtitle="risk decile (10 = riskiest)"))

    labels = {"recency_days": "Days since last order", "frequency_365": "Purchase days, last year",
              "log_monetary_365": "Spend last year (log)", "tenure_days": "Days since first order",
              "log_avg_order_value": "Avg order value (log)", "log_products_365": "Distinct products (log)",
              "cancel_share_365": "Share of spend cancelled", "is_uk": "UK customer"}
    coef = pd.Series({labels[k]: v for k, v in c["coefficients"].items() if k != "intercept"})
    ch.show(ch.signed_bars_h(pd.Series(coef.index, index=coef.index), coef,
                             title="What drives churn risk (standardised coefficients)",
                             neg_label="lowers churn risk", pos_label="raises churn risk"))
    st.caption("Red raises churn risk, blue lowers it. Coefficients are per standard deviation of each feature.")

    seasonal = pd.Series(c["seasonal_churn_rates"])
    s_left, s_right = st.columns([2, 3], gap="large")
    with s_left:
        ch.show(ch.bar_v(pd.to_datetime(seasonal.index).strftime("%b %Y"), seasonal, title="Churn rate by cutoff date",
                         fmt=ch.pct, yfmt=".0%", height=280,
                         hover=[f"Cutoff {pd.Timestamp(k):%d %b %Y}: {v:.1%} lapsed in the next 90 days"
                                for k, v in seasonal.items()]))
    with s_right:
        st.markdown("**Why train on the same season**")
        st.markdown(
            f"How many customers lapse depends on when you look: windows starting in September run into the "
            f"pre-Christmas buying season, so far fewer customers lapse than in windows starting December to June. "
            f"Training on another season shifts every probability. Even same-season training predicts "
            f"{ch.pct(c['mean_predicted'], 0)} churn on average against {ch.pct(c['test_churn_rate'], 0)} observed, "
            f"because autumn 2011 saw more lapsing than autumn 2010. The ranking (AUC) barely moves with the training "
            f"date, so use the scores to decide **who** to contact first, and re-base the probabilities each season."
        )

    st.markdown("**Riskiest customers at the test cutoff**")
    risky = scores.sort_values("churn_probability", ascending=False)
    st.dataframe(
        risky[["customer_id", "churn_probability", "recency_days", "frequency_365", "churned"]].head(50),
        hide_index=True, width="stretch", height=300,
        column_config={"customer_id": st.column_config.NumberColumn("Customer", format="%d"),
                       "churn_probability": st.column_config.ProgressColumn("Churn risk", format="percent",
                                                                            min_value=0, max_value=1),
                       "recency_days": "Days since last order", "frequency_365": "Purchase days, last year",
                       "churned": st.column_config.CheckboxColumn("Actually churned")},
    )
    st.download_button("Download churn scores (CSV)", scores.to_csv(index=False), "churn_scores.csv", "text/csv",
                       icon=":material/download:")

# ---------------------------------------------------------------- CLV
with clv_tab:
    v = m["clv"]
    clv = data.model_table("clv")
    pu, rv = v["purchases"], v["revenue"]
    over = pu["predicted_total"] / pu["actual_total"] - 1
    st.markdown(
        "**Verdict: good for ranking and for newer customers, not for forecasting totals.** "
        "A BG/NBD model predicts how many times each customer will buy; a Gamma-Gamma model predicts how much they "
        "spend each time. Together they rank customers about as well as \"last year's spend\", predict purchase "
        "counts better, especially for customers with little history, but overshoot the total number of purchases by "
        f"{ch.pct(over, 0)}."
    )
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Purchase error per customer", f"{pu['mae_model']:.2f}",
              f"{pu['mae_model'] - pu['mae_baseline']:+.2f} vs baseline", delta_color="inverse", border=True,
              help="Mean absolute error in holdout purchases. Baseline: keep buying at the calibration-year rate.")
    k2.metric("Who buys again (AUC)", f"{v['return_auc_model']:.3f}",
              f"{v['return_auc_model'] - v['return_auc_baseline']:+.3f} vs baseline", border=True)
    k3.metric("Revenue ranking (Spearman)", f"{rv['spearman_model']:.3f}",
              f"{rv['spearman_model'] - rv['spearman_baseline']:+.3f} vs last year's spend", border=True)
    k4.metric("Total purchases", f"{pu['predicted_total']:,.0f} predicted", f"{pu['actual_total']:,} actual",
              delta_color="off", border=True)

    left, right = st.columns([3, 2], gap="large")
    with left:
        dec = pd.DataFrame(v["deciles"])
        ch.show(ch.grouped_columns(dec["decile"].astype(str),
                                   {"Predicted": dec["predicted_revenue"], "Actual": dec["actual_revenue"]},
                                   title="Average holdout revenue per customer, by predicted-value decile",
                                   yprefix="£", xtitle="predicted value decile (10 = highest)"))
    with right:
        sh, lo = v["short_history"], v["long_history"]
        comparison = pd.DataFrame({
            "Customers": [f"Under 26 weeks of history ({sh['customers']:,})", f"26+ weeks ({lo['customers']:,})"],
            "Purchase error: model": [sh["mae_model"], lo["mae_model"]],
            "Purchase error: baseline": [sh["mae_baseline"], lo["mae_baseline"]],
            "Revenue ranking: model": [sh["spearman_model"], lo["spearman_model"]],
            "Revenue ranking: baseline": [sh["spearman_baseline"], lo["spearman_baseline"]],
        })
        st.markdown("**Where the model helps**")
        st.dataframe(comparison, hide_index=True, width="stretch",
                     column_config={c: st.column_config.NumberColumn(c, format="%.2f")
                                    for c in comparison.columns[1:]})
        st.caption(
            "For new customers, extrapolating a few weeks of buying is unreliable; the model's shrinkage towards the "
            "population pattern cuts that error a lot. For established customers, last year is already a good guide."
        )
    st.caption(
        f"Why the overshoot: the model is fitted on the {v['fitted_on']:,} customers whose first purchase we observe "
        "(Dec 2009 is where the data starts). Many 2010 customers bought only in their first months and never "
        "returned in 2011, and a few months of history isn't enough for the model to learn that. Note also that "
        f"BG/NBD treats one-time buyers as certainly still active (P(alive) = 1), yet only "
        f"{ch.pct(v['one_time_buyers_returned'], 0)} of them returned, so P(alive) alone is a poor ranking score here."
    )
    with st.expander("Model parameters"):
        b, g = v["bgnbd"], v["gamma_gamma"]
        st.markdown(
            f"- **BG/NBD**: r = {b['r']:.3f}, α = {b['alpha']:.3f}, a = {b['a']:.4f}, b = {b['b']:.3f} "
            "(time in weeks)\n"
            f"- **Gamma-Gamma**: p = {g['p']:.3f}, q = {g['q']:.3f}, γ = {g['gamma']:.3f}; "
            f"average spend per purchase £{g['p'] * g['gamma'] / (g['q'] - 1):,.0f}\n"
            f"- Gamma-Gamma assumes spend per purchase doesn't depend on how often a customer buys; the correlation "
            f"between the two here is {v['gg_frequency_value_corr']:+.2f}."
        )
    st.markdown("**Highest predicted value for the holdout year**")
    top = clv.sort_values("predicted_revenue", ascending=False).head(50)
    st.dataframe(
        top[["customer_id", "p_alive", "predicted_purchases", "predicted_revenue", "actual_purchases",
             "actual_revenue"]],
        hide_index=True, width="stretch", height=300,
        column_config={"customer_id": st.column_config.NumberColumn("Customer", format="%d"),
                       "p_alive": st.column_config.NumberColumn("P(alive)", format="%.2f"),
                       "predicted_purchases": st.column_config.NumberColumn("Predicted purchases", format="%.1f"),
                       "predicted_revenue": st.column_config.NumberColumn("Predicted revenue", format="£%,.0f"),
                       "actual_purchases": st.column_config.NumberColumn("Actual purchases", format="%d"),
                       "actual_revenue": st.column_config.NumberColumn("Actual revenue", format="£%,.0f")},
    )
    st.download_button("Download CLV predictions (CSV)", clv.to_csv(index=False), "clv_predictions.csv", "text/csv",
                       icon=":material/download:")
