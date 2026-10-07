"""Model tests. The statistical models are checked by parameter recovery: simulate customers from known
parameters, fit, and confirm the fit recovers them and predicts the simulated future."""

import itertools

import numpy as np
import pandas as pd
import pytest

from retail import models


def simulate_bgnbd(n, r, alpha, a, b, T, horizon, seed=0):
    """Customers from the BG/NBD story: Poisson purchasing (rate ~ Gamma(r, alpha)) and a chance p ~ Beta(a, b)
    of dropping out after each repeat purchase. Returns calibration (x, t_x) and purchases in (T, T+horizon]."""
    rng = np.random.default_rng(seed)
    lam, p = rng.gamma(r, 1 / alpha, n), rng.beta(a, b, n)
    x, t_x, future = np.zeros(n), np.zeros(n), np.zeros(n)
    for i in range(n):
        t = 0.0
        while True:
            t += rng.exponential(1 / lam[i])
            if t > T + horizon:
                break
            if t <= T:
                x[i] += 1
                t_x[i] = t
            else:
                future[i] += 1
            if rng.random() < p[i]:
                break
    return x, t_x, np.full(n, float(T)), future


def test_bgnbd_recovers_parameters_and_predicts_the_simulated_future():
    true = dict(r=0.5, alpha=5.0, a=0.8, b=3.0)
    x, t_x, T, future = simulate_bgnbd(6000, **true, T=52, horizon=52)
    fit = models.BGNBD.fit(x, t_x, T)
    assert fit.r == pytest.approx(true["r"], rel=0.15)
    assert fit.alpha == pytest.approx(true["alpha"], rel=0.2)
    predicted = fit.expected_purchases(52, x, t_x, T)
    assert predicted.sum() == pytest.approx(future.sum(), rel=0.08)
    # Better than chance at telling who buys again.
    assert models.auc(predicted, future > 0) > 0.75


def test_bgnbd_p_alive_rules():
    fit = models.BGNBD(r=0.5, alpha=5.0, a=0.8, b=3.0)
    # One-time buyers are certainly alive in BG/NBD (dropout only happens after a repeat purchase) ...
    assert fit.p_alive([0], [0], [52])[0] == 1.0
    # ... and a frequent buyer who has gone quiet is less likely alive than one who just bought.
    quiet, recent = fit.p_alive([10, 10], [10, 51], [52, 52])
    assert quiet < recent


def test_gamma_gamma_recovers_the_average_order_value():
    rng = np.random.default_rng(1)
    p, q, gamma = 6.0, 4.0, 15.0
    n = 4000
    x = rng.integers(1, 10, n)
    nu = rng.gamma(q, 1 / gamma, n)
    m = np.array([rng.gamma(p, 1 / nu[i], x[i]).mean() for i in range(n)])
    fit = models.GammaGamma.fit(x, m)
    assert fit.population_mean() == pytest.approx(p * gamma / (q - 1), rel=0.08)
    # With many purchases the estimate leans on the customer's own average.
    assert fit.expected_value([50], [100.0])[0] == pytest.approx(100.0, rel=0.1)
    assert fit.expected_value([0], [0.0])[0] == pytest.approx(fit.population_mean())


def test_logistic_regression_recovers_coefficients():
    rng = np.random.default_rng(2)
    X = pd.DataFrame(rng.normal(size=(5000, 3)), columns=["a", "b", "c"])
    true = np.array([-0.5, 1.5, -1.0, 0.0])
    y = rng.random(5000) < 1 / (1 + np.exp(-(true[0] + X.to_numpy() @ true[1:])))
    fit = models.LogisticModel.fit(X, y.astype(int), l2=1.0)
    np.testing.assert_allclose(fit.coef, true, atol=0.15)
    prob = fit.predict_proba(X)
    assert ((prob > 0.5) == y).mean() > 0.75


def test_auc_matches_brute_force():
    rng = np.random.default_rng(3)
    scores = rng.integers(0, 20, 300).astype(float)  # plenty of ties
    labels = rng.random(300) < 0.4
    pos, neg = scores[labels], scores[~labels]
    brute = np.mean([1.0 if p > n else 0.5 if p == n else 0.0 for p, n in itertools.product(pos, neg)])
    assert models.auc(scores, labels) == pytest.approx(brute)
    assert models.auc([1, 2, 3, 4], [0, 0, 1, 1]) == 1.0
    assert models.auc([4, 3, 2, 1], [0, 0, 1, 1]) == 0.0


def test_top_share_and_spearman():
    assert models.top_share([3, 2, 1, 0, 0, 0, 0, 0, 0, 0], [50, 30, 20, 0, 0, 0, 0, 0, 0, 0], 0.1) == 0.5
    assert models.spearman([1, 2, 3], [10, 20, 30]) == pytest.approx(1.0)


def test_forecast_baselines_and_errors():
    months = pd.date_range("2010-01-01", periods=12, freq="MS")
    train = pd.Series(np.arange(1, 13, dtype=float) * 100, index=months)
    fc = models.forecast_baselines(train, 12)
    assert fc.index[0] == pd.Timestamp("2011-01-01")
    assert list(fc["seasonal_naive"]) == list(train.to_numpy())  # same month last year
    assert (fc["naive"] == 1200).all()
    assert (fc["moving_average_3"] == 1100).all()
    assert models.mape([100, 200], [110, 180]) == pytest.approx(0.1)
    assert models.wape([100, 200], [110, 180]) == pytest.approx(30 / 300)


def test_clv_dataset_on_the_tiny_fixture(tiny):
    d = models.clv_dataset(tiny, pd.Timestamp("2010-01-31"), pd.Timestamp("2010-02-28")).set_index("customer_id")
    # customer 1: one purchase day in January, then Feb 10 in the holdout; Feb net = 5 + 30 - 20 cancelled
    assert (d.loc[1, "x"], d.loc[1, "T"]) == (0, 27 / 7)
    assert (d.loc[1, "actual_purchases"], d.loc[1, "actual_revenue"]) == (1, 15)
    # customer 2: bought on Jan 20, nothing after
    assert (d.loc[2, "actual_purchases"], d.loc[2, "actual_revenue"]) == (0, 0)


def test_churn_dataset_on_the_tiny_fixture(tiny):
    d = models.churn_dataset(tiny, pd.Timestamp("2010-02-01"), horizon_days=30).set_index("customer_id")
    assert list(d.index) == [1, 2]
    assert (d.loc[1, "churned"], d.loc[2, "churned"]) == (0, 1)  # customer 1 bought again on Feb 10
    assert (d.loc[1, "recency_days"], d.loc[2, "recency_days"]) == (27, 11)
    assert (d.loc[1, "is_uk"], d.loc[2, "is_uk"]) == (1, 0)
