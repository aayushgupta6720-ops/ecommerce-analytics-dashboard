"""Predictive models, implemented with numpy/scipy (no ML framework, so the Render instance stays small).

Every model is scored on data it never saw: it is fitted on the calibration window and evaluated on the
following holdout window, against a simple baseline. scripts/train_models.py runs everything and writes
the results the Predictions page reads.

- BG/NBD (Fader, Hardie & Lee 2005): how many purchases each customer will make, and P(still active).
- Gamma-Gamma (Fader, Hardie & Lee 2005): expected spend per purchase; combined with BG/NBD gives value.
- Churn: L2-regularised logistic regression on RFM-style features, validated on a later cutoff.
- Forecast: 12-month-ahead monthly revenue, seasonal naive vs naive and moving-average baselines.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import optimize, special

from retail.metrics import product_rows

WEEK = 7.0


# ---------------------------------------------------------------- shared helpers

def purchase_days(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (customer, day) with a sale: the 'transaction' unit for BG/NBD. `net` is the day's
    net revenue (sales minus any cancellations the same day)."""
    p = product_rows(df, ["customer_id", "invoice_date", "revenue", "is_cancellation"])
    p = p[p["customer_id"].notna()]
    p = p.assign(day=p["invoice_date"].dt.normalize(), sale=~p["is_cancellation"])
    days = p.groupby(["customer_id", "day"]).agg(net=("revenue", "sum"), sale=("sale", "any")).reset_index()
    return days[days["sale"]].drop(columns="sale").reset_index(drop=True)


def auc(scores: np.ndarray, labels: np.ndarray) -> float:
    """ROC AUC via the rank-sum (Mann-Whitney) statistic; ties get average ranks."""
    scores, labels = np.asarray(scores, float), np.asarray(labels, bool)
    n_pos, n_neg = labels.sum(), (~labels).sum()
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    ranks = pd.Series(scores).rank(method="average").to_numpy()
    return float((ranks[labels].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def roc_curve(scores: np.ndarray, labels: np.ndarray, points: int = 101) -> pd.DataFrame:
    """False/true positive rates at evenly spaced score thresholds (enough points for a chart)."""
    scores, labels = np.asarray(scores, float), np.asarray(labels, bool)
    thresholds = np.quantile(scores, np.linspace(1, 0, points))
    tpr = [(scores[labels] >= t).mean() for t in thresholds]
    fpr = [(scores[~labels] >= t).mean() for t in thresholds]
    return pd.DataFrame({"fpr": [0.0, *fpr, 1.0], "tpr": [0.0, *tpr, 1.0]})


def top_share(scores: np.ndarray, actual: np.ndarray, fraction: float = 0.1) -> float:
    """Share of the actual total captured by the top `fraction` of customers by score."""
    order = np.argsort(-np.asarray(scores, float), kind="stable")
    k = max(1, int(round(len(order) * fraction)))
    total = np.asarray(actual, float).sum()
    return float(np.asarray(actual, float)[order[:k]].sum() / total) if total else float("nan")


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    return float(pd.Series(a).rank().corr(pd.Series(b).rank()))


def paired_gap(stat, model: np.ndarray, baseline: np.ndarray, target: np.ndarray, n_boot: int = 1000,
               seed: int = 0) -> dict[str, float]:
    """stat(model, target) - stat(baseline, target), with a 95% interval from resampling customers (the same
    resample for both, so the interval is for the gap, not for each score)."""
    model, baseline, target = (np.asarray(x, float) for x in (model, baseline, target))
    rng = np.random.default_rng(seed)
    gaps = []
    for _ in range(n_boot):
        i = rng.integers(0, len(target), len(target))
        gaps.append(stat(model[i], target[i]) - stat(baseline[i], target[i]))
    low, high = np.nanpercentile(gaps, [2.5, 97.5])
    return {"gap": stat(model, target) - stat(baseline, target), "low": float(low), "high": float(high)}


# ---------------------------------------------------------------- BG/NBD

@dataclass(frozen=True)
class BGNBD:
    r: float
    alpha: float
    a: float
    b: float

    @staticmethod
    def neg_log_likelihood(params: np.ndarray, x: np.ndarray, t_x: np.ndarray, T: np.ndarray) -> float:
        """Mean negative log-likelihood. x = repeat purchases, t_x = time of last purchase, T = age (weeks)."""
        r, alpha, a, b = params
        a1 = special.gammaln(r + x) - special.gammaln(r) + r * np.log(alpha)
        a2 = special.gammaln(a + b) + special.gammaln(b + x) - special.gammaln(b) - special.gammaln(a + b + x)
        a3 = -(r + x) * np.log(alpha + T)
        with np.errstate(divide="ignore"):
            a4 = np.where(x > 0, np.log(a) - np.log(np.maximum(b + x - 1, 1e-12)) - (r + x) * np.log(alpha + t_x),
                          -np.inf)
        return float(-(a1 + a2 + np.logaddexp(a3, a4)).mean())

    @classmethod
    def fit(cls, x, t_x, T) -> BGNBD:
        x, t_x, T = (np.asarray(v, float) for v in (x, t_x, T))
        objective = lambda log_p: cls.neg_log_likelihood(np.exp(log_p), x, t_x, T)  # noqa: E731
        res = optimize.minimize(objective, np.zeros(4), method="Nelder-Mead",
                                options={"maxiter": 10_000, "xatol": 1e-9, "fatol": 1e-12})
        if not res.success:
            raise RuntimeError(f"BG/NBD fit did not converge: {res.message}")
        return cls(*np.exp(res.x))

    def _alive_odds(self, x, t_x, T):
        x, t_x, T = (np.asarray(v, float) for v in (x, t_x, T))
        safe_b = self.b + x - 1 + (x == 0)  # avoid 0 in the denominator; the term is masked for x == 0
        return (x > 0) * (self.a / safe_b) * ((self.alpha + T) / (self.alpha + t_x)) ** (self.r + x)

    def p_alive(self, x, t_x, T) -> np.ndarray:
        return 1.0 / (1.0 + self._alive_odds(x, t_x, T))

    def expected_purchases(self, t: float, x, t_x, T) -> np.ndarray:
        """E[purchases in (T, T + t]] given each customer's history."""
        x, t_x, T = (np.asarray(v, float) for v in (x, t_x, T))
        r, alpha, a, b = self.r, self.alpha, self.a, self.b
        hyp = special.hyp2f1(r + x, b + x, a + b + x - 1, t / (alpha + T + t))
        num = (a + b + x - 1) / (a - 1) * (1 - ((alpha + T) / (alpha + T + t)) ** (r + x) * hyp)
        return num / (1.0 + self._alive_odds(x, t_x, T))


# ---------------------------------------------------------------- Gamma-Gamma

@dataclass(frozen=True)
class GammaGamma:
    p: float
    q: float
    gamma: float

    @staticmethod
    def neg_log_likelihood(params: np.ndarray, x: np.ndarray, m: np.ndarray) -> float:
        """x = number of repeat purchases (> 0), m = their average value."""
        p, q, g = params
        ll = (special.gammaln(p * x + q) - special.gammaln(p * x) - special.gammaln(q) + q * np.log(g)
              + (p * x - 1) * np.log(m) + p * x * np.log(x) - (p * x + q) * np.log(g + m * x))
        return float(-ll.mean())

    @classmethod
    def fit(cls, x, m) -> GammaGamma:
        x, m = np.asarray(x, float), np.asarray(m, float)
        objective = lambda log_p: cls.neg_log_likelihood(np.exp(log_p), x, m)  # noqa: E731
        res = optimize.minimize(objective, np.zeros(3), method="Nelder-Mead",
                                options={"maxiter": 10_000, "xatol": 1e-9, "fatol": 1e-12})
        if not res.success:
            raise RuntimeError(f"Gamma-Gamma fit did not converge: {res.message}")
        return cls(*np.exp(res.x))

    def population_mean(self) -> float:
        return self.p * self.gamma / (self.q - 1)

    def expected_value(self, x, m) -> np.ndarray:
        """Expected spend per purchase; customers with no repeat purchases get the population mean."""
        x, m = np.asarray(x, float), np.asarray(m, float)
        cond = (self.gamma + m * x) * self.p / (self.p * x + self.q - 1)
        return np.where(x > 0, cond, self.population_mean())


def clv_dataset(df: pd.DataFrame, cal_end: pd.Timestamp, holdout_end: pd.Timestamp) -> pd.DataFrame:
    """Per customer first seen by `cal_end`: BG/NBD inputs from the calibration window and what actually
    happened in the holdout window (cal_end, holdout_end]."""
    days = purchase_days(df)
    cal = days[days["day"] <= cal_end]
    g = cal.groupby("customer_id")
    out = pd.DataFrame({"first": g["day"].min(), "last": g["day"].max(), "n": g.size(),
                        "cal_revenue": g["net"].sum()})
    out["x"] = out["n"] - 1
    out["t_x"] = (out["last"] - out["first"]).dt.days / WEEK
    out["T"] = (cal_end - out["first"]).dt.days / WEEK
    # Gamma-Gamma uses the average value of repeat purchase days (the first is excluded, as in the paper).
    repeat = cal.merge(out[["first"]], left_on="customer_id", right_index=True)
    repeat = repeat[(repeat["day"] > repeat["first"]) & (repeat["net"] > 0)]
    rg = repeat.groupby("customer_id")["net"]
    out["repeat_value"] = rg.mean()
    out["repeat_days_positive"] = rg.size()
    hold = days[(days["day"] > cal_end) & (days["day"] <= holdout_end)]
    out["actual_purchases"] = hold.groupby("customer_id").size().reindex(out.index).fillna(0).astype(int)
    # Actual holdout value: net revenue on every product line (cancellations included).
    p = product_rows(df, ["customer_id", "invoice_date", "revenue"])
    day = pd.Timedelta(days=1)
    in_holdout = (p["invoice_date"] >= cal_end + day) & (p["invoice_date"] < holdout_end + day)
    p = p[p["customer_id"].notna() & in_holdout]
    out["actual_revenue"] = p.groupby("customer_id")["revenue"].sum().reindex(out.index).fillna(0.0)
    out["observed_acquisition"] = out["first"] >= df["invoice_date"].min().normalize() + pd.offsets.MonthBegin(1)
    return out.reset_index()


# ---------------------------------------------------------------- churn

CHURN_FEATURES = ["recency_days", "frequency_365", "log_monetary_365", "tenure_days", "log_avg_order_value",
                  "log_products_365", "cancel_share_365", "is_uk"]


def churn_dataset(df: pd.DataFrame, cutoff: pd.Timestamp, horizon_days: int = 90) -> pd.DataFrame:
    """Customers active in the 365 days before `cutoff`, their features as of the cutoff, and whether they
    made no purchase in the next `horizon_days` (churned = 1)."""
    p = product_rows(df, ["customer_id", "invoice_date", "invoice", "stock_code", "revenue", "is_cancellation",
                          "country"])
    p = p[p["customer_id"].notna()]
    before = p[p["invoice_date"] < cutoff]
    year = before[before["invoice_date"] >= cutoff - pd.Timedelta(days=365)]
    sales_year = year[~year["is_cancellation"]]
    active = sales_year["customer_id"].unique()
    sales_all = before[~before["is_cancellation"]]
    g_all = sales_all[sales_all["customer_id"].isin(active)].groupby("customer_id")
    g_year = sales_year.groupby("customer_id")
    net_year = year.groupby("customer_id")["revenue"].sum()
    cancelled_year = -year[year["is_cancellation"]].groupby("customer_id")["revenue"].sum()
    gross_year = sales_year.groupby("customer_id")["revenue"].sum()
    orders_year = g_year["invoice"].nunique()
    out = pd.DataFrame(index=pd.Index(np.sort(active), name="customer_id"))
    out["recency_days"] = (cutoff - g_all["invoice_date"].max()).dt.days
    out["frequency_365"] = g_year["invoice_date"].apply(lambda d: d.dt.normalize().nunique())
    out["log_monetary_365"] = np.log1p(net_year.reindex(out.index).fillna(0).clip(lower=0))
    out["tenure_days"] = (cutoff - g_all["invoice_date"].min()).dt.days
    out["log_avg_order_value"] = np.log1p((net_year / orders_year).reindex(out.index).fillna(0).clip(lower=0))
    out["log_products_365"] = np.log1p(g_year["stock_code"].nunique())
    out["cancel_share_365"] = (cancelled_year.reindex(out.index).fillna(0)
                               / gross_year.reindex(out.index)).fillna(0).clip(0, 1)
    out["is_uk"] = (g_all["country"].agg(lambda c: c.mode().iat[0]).astype(str) == "United Kingdom").astype(int)
    after = p[(p["invoice_date"] >= cutoff) & (p["invoice_date"] < cutoff + pd.Timedelta(days=horizon_days))
              & ~p["is_cancellation"]]
    out["churned"] = (~out.index.isin(after["customer_id"].unique())).astype(int)
    return out.reset_index()


@dataclass(frozen=True)
class LogisticModel:
    means: np.ndarray
    stds: np.ndarray
    coef: np.ndarray  # first element is the intercept
    features: tuple[str, ...]

    @classmethod
    def fit(cls, X: pd.DataFrame, y: np.ndarray, l2: float = 1.0) -> LogisticModel:
        means, stds = X.mean().to_numpy(), X.std(ddof=0).replace(0, 1).to_numpy()
        Z = np.column_stack([np.ones(len(X)), (X.to_numpy(float) - means) / stds])
        y = np.asarray(y, float)

        def loss(w):
            z = Z @ w
            nll = np.logaddexp(0, z).sum() - y @ z
            return nll + 0.5 * l2 * (w[1:] @ w[1:])

        def grad(w):
            prob = special.expit(Z @ w)
            g = Z.T @ (prob - y)
            g[1:] += l2 * w[1:]
            return g

        res = optimize.minimize(loss, np.zeros(Z.shape[1]), jac=grad, method="L-BFGS-B",
                                options={"maxiter": 1000, "gtol": 1e-10})
        if not res.success:
            raise RuntimeError(f"Logistic regression did not converge: {res.message}")
        return cls(means, stds, res.x, tuple(X.columns))

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        Z = (X[list(self.features)].to_numpy(float) - self.means) / self.stds
        return special.expit(self.coef[0] + Z @ self.coef[1:])


# ---------------------------------------------------------------- forecast

def monthly_series(df: pd.DataFrame) -> pd.Series:
    """Net revenue per full calendar month (the final, partial month is dropped)."""
    p = product_rows(df, ["invoice_date", "revenue"])
    month = p["invoice_date"].dt.to_period("M")
    s = p.groupby(month)["revenue"].sum()
    last_day = df["invoice_date"].max()
    if last_day < (last_day + pd.offsets.MonthEnd(0)).normalize():
        s = s.iloc[:-1]
    s.index = s.index.to_timestamp()
    return s


def forecast_baselines(train: pd.Series, horizon: int) -> pd.DataFrame:
    """12-month-ahead forecasts made at the end of `train`: naive, 3-month moving average, seasonal naive."""
    idx = pd.date_range(train.index[-1] + pd.offsets.MonthBegin(1), periods=horizon, freq="MS")
    season = {ts.month: v for ts, v in train.iloc[-12:].items()}
    return pd.DataFrame({
        "naive": train.iloc[-1],
        "moving_average_3": train.iloc[-3:].mean(),
        "seasonal_naive": [season[ts.month] for ts in idx],
    }, index=idx)


def mape(actual: np.ndarray, forecast: np.ndarray) -> float:
    actual, forecast = np.asarray(actual, float), np.asarray(forecast, float)
    return float(np.mean(np.abs(forecast - actual) / np.abs(actual)))


def wape(actual: np.ndarray, forecast: np.ndarray) -> float:
    actual, forecast = np.asarray(actual, float), np.asarray(forecast, float)
    return float(np.abs(forecast - actual).sum() / np.abs(actual).sum())
