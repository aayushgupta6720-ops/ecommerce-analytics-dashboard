"""Fit and evaluate the predictive models, and write what the Predictions page shows.

    python scripts/train_models.py          # fit, evaluate, write data/processed/models/
    python scripts/train_models.py --check  # recompute and fail if metrics differ from the committed ones

Split: calibration up to 2010-12-09, holdout the following 365 days (2010-12-10 to 2011-12-09). Churn is
trained at one cutoff and tested at a later one. Every model is compared with a simple baseline.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from retail import models  # noqa: E402

OUT = ROOT / "data" / "processed" / "models"
CAL_END = pd.Timestamp("2010-12-09")
HOLDOUT_END = pd.Timestamp("2011-12-09")
HOLDOUT_WEEKS = 365 / models.WEEK
# Churn is strongly seasonal (90-day windows starting in September catch the pre-Christmas buying season), so
# the model trains on the same season a year before the test cutoff.
CHURN_TRAIN_CUTOFF = pd.Timestamp("2010-09-11")
CHURN_TEST_CUTOFF = pd.Timestamp("2011-09-11")  # +90 days reaches the last day of data
SEASONAL_CUTOFFS = ["2010-09-11", "2010-12-11", "2011-03-11", "2011-06-11", "2011-09-11"]
CHURN_HORIZON = 90
RF_FEATURES = ("recency_days", "frequency_365")


def _by_history(d: pd.DataFrame, mask: pd.Series) -> dict:
    s = d[mask]
    return {
        "customers": int(len(s)),
        "mae_model": float((s["predicted_purchases"] - s["actual_purchases"]).abs().mean()),
        "mae_baseline": float((s["baseline_purchases"] - s["actual_purchases"]).abs().mean()),
        "spearman_model": models.spearman(s["predicted_revenue"], s["actual_revenue"]),
        "spearman_baseline": models.spearman(s["baseline_revenue"], s["actual_revenue"]),
    }


def clv(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    d = models.clv_dataset(df, CAL_END, HOLDOUT_END)
    # Fit only on customers whose first purchase we observe: Dec 2009 is where the data starts, so those
    # customers' earlier history is missing and makes dropout look rarer than it is.
    fit_on = d[d["observed_acquisition"]]
    bg = models.BGNBD.fit(fit_on["x"], fit_on["t_x"], fit_on["T"])
    d["predicted_purchases"] = bg.expected_purchases(HOLDOUT_WEEKS, d["x"], d["t_x"], d["T"])
    d["p_alive"] = bg.p_alive(d["x"], d["t_x"], d["T"])

    rep = d[d["repeat_days_positive"].fillna(0) > 0]
    gg = models.GammaGamma.fit(rep["repeat_days_positive"], rep["repeat_value"])
    d["expected_order_value"] = gg.expected_value(d["repeat_days_positive"].fillna(0), d["repeat_value"].fillna(0))
    d["predicted_revenue"] = d["predicted_purchases"] * d["expected_order_value"]

    # Baselines: keep buying at the calibration rate; spend what you spent in the previous 365 days.
    d["baseline_purchases"] = np.where(d["T"] > 0, d["x"] / d["T"].where(d["T"] > 0, 1) * HOLDOUT_WEEKS, 0.0)
    days = models.purchase_days(df)
    last_year = days[(days["day"] > CAL_END - pd.Timedelta(days=365)) & (days["day"] <= CAL_END)]
    d["baseline_revenue"] = d["customer_id"].map(last_year.groupby("customer_id")["net"].sum()).fillna(0.0)

    returned = (d["actual_purchases"] > 0).to_numpy()
    deciles = pd.qcut(d["predicted_revenue"].rank(method="first"), 10, labels=list(range(1, 11)))
    decile_table = (d.groupby(deciles, observed=True)[["predicted_revenue", "actual_revenue"]].mean()
                    .reset_index(names="decile"))
    metrics = {
        "customers": int(len(d)),
        "fitted_on": int(len(fit_on)),
        "bgnbd": {"r": bg.r, "alpha": bg.alpha, "a": bg.a, "b": bg.b},
        "gamma_gamma": {"p": gg.p, "q": gg.q, "gamma": gg.gamma},
        "gg_frequency_value_corr": float(np.corrcoef(rep["repeat_days_positive"], rep["repeat_value"])[0, 1]),
        "purchases": {
            "predicted_total": float(d["predicted_purchases"].sum()),
            "baseline_total": float(d["baseline_purchases"].sum()),
            "actual_total": int(d["actual_purchases"].sum()),
            "mae_model": float((d["predicted_purchases"] - d["actual_purchases"]).abs().mean()),
            "mae_baseline": float((d["baseline_purchases"] - d["actual_purchases"]).abs().mean()),
        },
        "revenue": {
            "predicted_total": float(d["predicted_revenue"].sum()),
            "baseline_total": float(d["baseline_revenue"].sum()),
            "actual_total": float(d["actual_revenue"].sum()),
            "spearman_model": models.spearman(d["predicted_revenue"], d["actual_revenue"]),
            "spearman_baseline": models.spearman(d["baseline_revenue"], d["actual_revenue"]),
            "top10_share_model": models.top_share(d["predicted_revenue"], d["actual_revenue"]),
            "top10_share_baseline": models.top_share(d["baseline_revenue"], d["actual_revenue"]),
            "spearman_gap": models.paired_gap(models.spearman, d["predicted_revenue"], d["baseline_revenue"],
                                              d["actual_revenue"]),
        },
        # "Will they buy again in the holdout year?" scored by expected purchases. P(alive) is reported but is
        # a poor ranking score here: BG/NBD gives every one-time buyer P(alive) = 1.
        "return_auc_model": models.auc(d["predicted_purchases"], returned),
        "return_auc_baseline": models.auc(d["baseline_purchases"], returned),
        "p_alive_auc": models.auc(d["p_alive"], returned),
        "one_time_buyers_returned": float(returned[d["x"].to_numpy() == 0].mean()),
        "returned_share": float(returned.mean()),
        "short_history": _by_history(d, d["T"] < 26),
        "long_history": _by_history(d, d["T"] >= 26),
    }
    cols = ["customer_id", "first", "x", "t_x", "T", "cal_revenue", "p_alive", "predicted_purchases",
            "expected_order_value", "predicted_revenue", "baseline_purchases", "baseline_revenue",
            "actual_purchases", "actual_revenue", "observed_acquisition"]
    return d[cols], {**metrics, "deciles": decile_table.to_dict("list")}


def churn(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    train = models.churn_dataset(df, CHURN_TRAIN_CUTOFF, CHURN_HORIZON)
    test = models.churn_dataset(df, CHURN_TEST_CUTOFF, CHURN_HORIZON)
    model = models.LogisticModel.fit(train[models.CHURN_FEATURES], train["churned"])
    test["churn_probability"] = model.predict_proba(test[models.CHURN_FEATURES])
    # The fair comparison: the same logistic regression on the two features that carry most of the signal.
    rf = models.LogisticModel.fit(train[list(RF_FEATURES)], train["churned"])
    test["rf_probability"] = rf.predict_proba(test[list(RF_FEATURES)])
    y = test["churned"].to_numpy()
    top = test["churn_probability"].rank(method="first", ascending=False) <= round(0.2 * len(test))
    deciles = pd.qcut(test["churn_probability"].rank(method="first"), 10, labels=list(range(1, 11)))
    calib = (test.groupby(deciles, observed=True)
             .agg(predicted=("churn_probability", "mean"), observed=("churned", "mean")).reset_index(names="decile"))
    metrics = {
        "train_cutoff": str(CHURN_TRAIN_CUTOFF.date()), "test_cutoff": str(CHURN_TEST_CUTOFF.date()),
        "horizon_days": CHURN_HORIZON,
        "train_customers": int(len(train)), "test_customers": int(len(test)),
        "train_churn_rate": float(train["churned"].mean()), "test_churn_rate": float(y.mean()),
        "auc_model": models.auc(test["churn_probability"], y),
        "auc_recency_baseline": models.auc(test["recency_days"], y),
        "auc_recency_frequency": models.auc(test["rf_probability"], y),
        "auc_gap_vs_recency": models.paired_gap(models.auc, test["churn_probability"], test["recency_days"], y),
        "auc_gap_vs_recency_frequency": models.paired_gap(models.auc, test["churn_probability"],
                                                          test["rf_probability"], y),
        "precision_top20": float(y[top.to_numpy()].mean()),
        "recall_top20": float(y[top.to_numpy()].sum() / y.sum()),
        "coefficients": dict(zip(("intercept", *models.CHURN_FEATURES), map(float, model.coef), strict=True)),
        "mean_predicted": float(test["churn_probability"].mean()),
        "seasonal_churn_rates": {c: float(models.churn_dataset(df, pd.Timestamp(c), CHURN_HORIZON)["churned"].mean())
                                 for c in SEASONAL_CUTOFFS},
        "calibration": calib.to_dict("list"),
    }
    metrics["scaler"] = {"means": list(map(float, model.means)), "stds": list(map(float, model.stds))}
    # Score every customer active in the last year as of the end of the data: who to look after now. The
    # outcome is unknown (it's in the future), and the probabilities belong to the September training
    # season, so the Insights page uses the rank (risk percentile), not the probability.
    now = df["invoice_date"].max().normalize() + pd.Timedelta(days=1)
    current = models.churn_dataset(df, now, CHURN_HORIZON).drop(columns="churned")
    current["churn_probability"] = model.predict_proba(current[models.CHURN_FEATURES])
    current["risk_percentile"] = current["churn_probability"].rank(pct=True)
    metrics["current_cutoff"] = str(now.date())
    metrics["current_customers"] = int(len(current))
    return (test[["customer_id", *models.CHURN_FEATURES, "churn_probability", "rf_probability", "churned"]],
            current[["customer_id", *models.CHURN_FEATURES, "churn_probability", "risk_percentile"]]), metrics


def forecast(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    series = models.monthly_series(df)
    train, test = series.iloc[:12], series.iloc[12:24]
    fc = models.forecast_baselines(train, len(test))
    table = pd.DataFrame({"actual": series.iloc[:24]}).join(fc)
    table["split"] = ["train"] * 12 + ["test"] * 12
    scores = {name: {"mape": models.mape(test, fc[name]), "wape": models.wape(test, fc[name])} for name in fc}
    metrics = {"train": [str(train.index[0].date()), str(train.index[-1].date())],
               "test": [str(test.index[0].date()), str(test.index[-1].date())], "scores": scores}
    return table.reset_index(names="month"), metrics


def run() -> tuple[dict, dict[str, pd.DataFrame]]:
    df = pd.read_parquet(ROOT / "data" / "processed" / "transactions.parquet")
    clv_table, clv_metrics = clv(df)
    (churn_table, current_table), churn_metrics = churn(df)
    fc_table, fc_metrics = forecast(df)
    metrics = {"calibration_end": str(CAL_END.date()), "holdout_end": str(HOLDOUT_END.date()),
               "clv": clv_metrics, "churn": churn_metrics, "forecast": fc_metrics}
    return metrics, {"clv": clv_table, "churn": churn_table, "churn_current": current_table, "forecast": fc_table}


def _close(a, b, path="") -> list[str]:
    if isinstance(a, dict):
        if set(a) != set(b):
            return [f"{path}: keys differ"]
        return [m for k in a for m in _close(a[k], b[k], f"{path}.{k}")]
    if isinstance(a, list):
        if len(a) != len(b):
            return [f"{path}: length differs"]
        return [m for i, (x, y) in enumerate(zip(a, b, strict=True)) for m in _close(x, y, f"{path}[{i}]")]
    if isinstance(a, float) or isinstance(b, float):
        # The linear-algebra libraries on macOS and on CI's Linux runner move a fitted coefficient in its
        # last digits, which moves an AUC by about 1e-6; a gap between two AUCs (~0.001) can't meet a
        # relative tolerance at that size, so small values get an absolute one.
        ok = math.isclose(float(a), float(b), rel_tol=1e-6, abs_tol=1e-5)
        return [] if ok else [f"{path}: {b} -> {a}"]
    return [] if a == b else [f"{path}: {b} -> {a}"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="compare with committed metrics instead of writing")
    args = parser.parse_args()
    metrics, tables = run()
    metrics = json.loads(json.dumps(metrics, default=str))
    if args.check:
        committed = json.loads((OUT / "metrics.json").read_text())
        problems = _close(metrics, committed)
        for p in problems[:20]:
            print("  differs:", p)
        print("model metrics match the committed ones" if not problems else f"{len(problems)} metric(s) changed")
        return 1 if problems else 0
    OUT.mkdir(parents=True, exist_ok=True)
    for name, table in tables.items():
        table.to_parquet(OUT / f"{name}.parquet", index=False)
    (OUT / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    c, ch, fc = metrics["clv"], metrics["churn"], metrics["forecast"]["scores"]
    print(f"CLV: {c['customers']:,} customers; purchases predicted {c['purchases']['predicted_total']:,.0f} "
          f"vs actual {c['purchases']['actual_total']:,}; MAE {c['purchases']['mae_model']:.2f} "
          f"(baseline {c['purchases']['mae_baseline']:.2f}); return AUC {c['return_auc_model']:.3f} "
          f"(baseline {c['return_auc_baseline']:.3f})")
    rv = c["revenue"]
    print(f"     revenue Spearman {rv['spearman_model']:.3f} (baseline {rv['spearman_baseline']:.3f}); "
          f"top-10% capture {rv['top10_share_model']:.1%} (baseline {rv['top10_share_baseline']:.1%})")
    print(f"Churn: AUC {ch['auc_model']:.3f} vs recency {ch['auc_recency_baseline']:.3f}; churn rate "
          f"{ch['test_churn_rate']:.1%}; precision@top20% {ch['precision_top20']:.1%}")
    print("Forecast WAPE: " + ", ".join(f"{k} {v['wape']:.1%}" for k, v in fc.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
