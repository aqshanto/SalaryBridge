"""M4: how much money will be advanced in each of the next 3 months? (P10 / P50 / P90)

A forecast is made at the start of month m0 for target months m0, m0+1, m0+2 (horizon 1–3),
using only months up to m0-1. Two levels:
- world: the total across employers (this sets the capital pool);
- employer: per employer (for the ops table).
Summing employer P90s overstates the total P90, so the world level has its own model.

The target is modelled relative to the series' own recent level:
    y_rel = (amount / headcount) / level,   level = mean per-head amount over the last 6 months.
Tree models cannot extrapolate beyond the values seen in training; v1 modelled per-head amounts
directly and badly under-forecast a world with higher per-head borrowing (Profile B). Scaling by
the series' own level lets one model serve small, large, calm and busy series. Quantiles survive
multiplying back by the known level x headcount.
The P10–P90 interval is then conformally adjusted on the last 4 training months so that it
covers about 80% of outcomes (split conformal, no test data used). v3: the widening is computed
separately for peak target months (the Eid month or the month right after it) and calm months
(Mondrian conformal), so an Eid surge in the calibration window no longer widens the band for calm months.

required_pool = P90 x (1 + capital_buffer_pct / 100)

Train:  .venv/Scripts/python.exe -m app.ml.m4_capital
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

from app.config import PolicyParams, get_settings
from app.ml.common import ARTIFACTS_DIR, load_artifact, save_artifact, update_metrics
from data.generator import PROFILES, _add_months, generate

MODEL_NAME = "m4_capital"
VERSION = "m4-v3"
QUANTILES = (0.1, 0.5, 0.9)
HORIZONS = (1, 2, 3)
COVERAGE_TARGET = 0.8
# More worlds than the classifiers: one world gives only ~20 monthly totals.
M4_SEEDS = tuple(42 + 1000 * i for i in range(20))
# v1 (per-head target) results, kept so the change is auditable. The weakness was found by looking at
# Profile B, so for M4 Profile B is no longer a fully untouched stress test.
V1_RESULTS = {
    "target": "per-head amount (no level scaling)",
    "test_profile_a.world": {"mae_p50_bdt": 78320.1, "coverage_p10_p90": 0.9556, "pool_covers_actual_share": 1.0},
    "test_profile_a.employer": {"mae_p50_bdt": 10301.9, "coverage_p10_p90": 0.8229},
    "test_profile_b.world": {"mae_p50_bdt": 554961.4, "coverage_p10_p90": 0.0, "pool_covers_actual_share": 0.2},
    "test_profile_b.employer": {"mae_p50_bdt": 11956.6, "coverage_p10_p90": 0.7344},
}
# v2 results (one conformal widening for every month). v3 sizes the widening separately for peak and calm
# target months, because v2 over-covered calm months after calibrating on Eid months. This weakness was
# found on the test months (T2-A), so the v3 change was chosen once from that finding and not tuned further.
V2_RESULTS = {"test_profile_a.world": {"mae_p50_bdt": 90970.7, "coverage_p10_p90": 0.9222, "mean_interval_width_bdt": 415725.3}, "test_profile_a.employer": {"mae_p50_bdt": 11741.2, "coverage_p10_p90": 0.8156, "mean_interval_width_bdt": 40216.8}, "test_profile_b.world": {"mae_p50_bdt": 162604.8, "coverage_p10_p90": 0.7556, "mean_interval_width_bdt": 539338.0}, "test_profile_b.employer": {"mae_p50_bdt": 11622.0, "coverage_p10_p90": 0.8828, "mean_interval_width_bdt": 46280.3}}
MIN_GROUP_CALIB = 10  # fewer calibration rows than this in a group -> use the pooled widening
EVAL_SEEDS_B = (42, 1042, 2042, 3042, 4042)
LEVEL_FLOOR = 1.0  # BDT per head; avoids dividing by ~0 for series with no recent advances
FEATURES = [
    "horizon",
    "target_month_of_year",
    "target_is_eid",
    "last_is_eid",
    "lag1_rel",
    "lag2_rel",
    "lag3_rel",
    "seasonal_rel",
    "requests_lag1_rel",
    "log_headcount",
]


def monthly_panel(tables: dict) -> pd.DataFrame:
    """One row per (employer, month index): amount advanced, requests and headcount."""
    start = pd.Timestamp(tables["meta"]["start"].iloc[0]).date()
    months = int(tables["meta"]["months"].iloc[0])
    profile = PROFILES[str(tables["meta"]["profile"].iloc[0])]
    month_keys = [_add_months(start, i).isoformat()[:7] for i in range(months)]
    index = {m: i for i, m in enumerate(month_keys)}

    att = tables["attendance"].merge(tables["employees"][["employee_id", "employer_id"]], on="employee_id")
    head = att.groupby(["employer_id", "work_month"]).agg(headcount=("employee_id", "size"))
    amount = tables["advances"].groupby(["employer_id", "work_month"])["amount"].sum().rename("amount")
    requests = tables["advance_requests"].groupby(["employer_id", "work_month"]).size().rename("requests")
    panel = head.join(amount, how="left").join(requests, how="left").fillna({"amount": 0, "requests": 0}).reset_index()
    panel["month_index"] = panel["work_month"].map(index)
    eid = {f"{y}-{m:02d}" for y, m in profile.eid_months}
    panel["is_eid"] = panel["work_month"].isin(eid).astype(int)
    panel.attrs.update(start=start, months=months, eid_months=eid)
    return panel.sort_values(["employer_id", "month_index"]).reset_index(drop=True)


def world_panel(panel: pd.DataFrame) -> pd.DataFrame:
    world = (
        panel.groupby(["month_index", "work_month", "is_eid"])
        .agg(amount=("amount", "sum"), requests=("requests", "sum"), headcount=("headcount", "sum"))
        .reset_index()
    )
    world["employer_id"] = "ALL"
    world.attrs.update(panel.attrs)
    return world


def _rows_for_series(series: pd.DataFrame, m0_values, start: date, eid_months: set, with_target: bool) -> list[dict]:
    """Feature rows for one series (one employer or the world) at each forecast origin m0."""
    s = series.set_index("month_index")
    rows = []
    for m0 in m0_values:
        last = m0 - 1
        if last not in s.index:
            continue
        head = float(s.at[last, "headcount"])
        if head <= 0:
            continue

        def per_head(i: int) -> float:
            return float(s.at[i, "amount"]) / head if i in s.index else np.nan

        for h in HORIZONS:
            target = m0 + h - 1
            if with_target and target not in s.index:
                continue
            target_month = _add_months(start, target)
            seasonal = per_head(target - 12) if target - 12 <= last else np.nan
            mean6 = np.nanmean([per_head(i) for i in range(max(0, last - 5), last + 1)])
            level = max(float(mean6), LEVEL_FLOOR)
            req = [float(s.at[i, "requests"]) / head for i in range(max(0, last - 5), last + 1) if i in s.index]
            req_level = max(float(np.mean(req)), 1e-6)
            row = {
                "employer_id": series["employer_id"].iloc[0],
                "origin": m0,
                "target_index": target,
                "target_month": target_month.isoformat()[:7],
                "horizon": h,
                "target_month_of_year": target_month.month,
                "target_is_eid": int(target_month.isoformat()[:7] in eid_months),
                "last_is_eid": int(s.at[last, "is_eid"]),
                "lag1_per_head": per_head(last),
                "lag2_per_head": per_head(last - 1),
                "lag3_per_head": per_head(last - 2),
                "mean6_per_head": mean6,
                "seasonal_per_head": seasonal,
                "level": level,
                "lag1_rel": per_head(last) / level,
                "lag2_rel": per_head(last - 1) / level,
                "lag3_rel": per_head(last - 2) / level,
                "seasonal_rel": seasonal / level,
                "requests_lag1_rel": (float(s.at[last, "requests"]) / head) / req_level,
                "log_headcount": float(np.log(head)),
                "headcount": head,
            }
            if with_target:
                row["actual"] = float(s.at[target, "amount"])
            rows.append(row)
    return rows


def build_rows(series_panel: pd.DataFrame, world: str = "") -> pd.DataFrame:
    start, months, eid = series_panel.attrs["start"], series_panel.attrs["months"], series_panel.attrs["eid_months"]
    rows = []
    for _, series in series_panel.groupby("employer_id"):
        rows += _rows_for_series(series, range(1, months), start, eid, with_target=True)
    df = pd.DataFrame(rows)
    df["world"] = world
    return df


@dataclass
class QuantileForecaster:
    models: dict  # alpha -> LGBMRegressor (target relative to the series' level)
    conformal_q: float  # widening of [P10, P90] in relative units
    residual_q: dict = field(default_factory=dict)  # baseline per-head residual quantiles
    conformal_q_by_eid: dict = field(default_factory=dict)  # {0: calm-month widening, 1: peak-month widening}; empty = pooled

    def _widening(self, X: pd.DataFrame) -> np.ndarray:
        if not self.conformal_q_by_eid:
            return np.full(len(X), self.conformal_q)
        eid = _peak(X)
        return np.where(eid == 1, self.conformal_q_by_eid.get(1, self.conformal_q), self.conformal_q_by_eid.get(0, self.conformal_q))

    def predict(self, X: pd.DataFrame) -> pd.DataFrame:
        head = (X["headcount"] * X["level"]).to_numpy(float)  # relative -> BDT
        raw = {a: self.models[a].predict(X[FEATURES]) for a in QUANTILES}
        q = self._widening(X)
        lo = np.minimum(raw[0.1], raw[0.5]) - q
        hi = np.maximum(raw[0.9], raw[0.5]) + q
        mid = np.clip(raw[0.5], lo, hi)
        return pd.DataFrame({"p10": np.maximum(lo, 0) * head, "p50": np.maximum(mid, 0) * head, "p90": np.maximum(hi, 0) * head}, index=X.index)


def _baseline(X: pd.DataFrame, kind: str) -> np.ndarray:
    lag1 = X["lag1_per_head"].to_numpy(float)
    if kind == "previous_month":
        return lag1
    seasonal = X["seasonal_per_head"].to_numpy(float)
    return np.where(np.isnan(seasonal), lag1, seasonal)


def pinball(y: np.ndarray, q: np.ndarray, alpha: float) -> float:
    d = y - q
    return float(np.mean(np.maximum(alpha * d, (alpha - 1) * d)))


def _peak(X: pd.DataFrame) -> np.ndarray:
    """Peak months for the conformal groups: the Eid month itself or the month right after it."""
    return ((X["target_is_eid"].to_numpy(int) == 1) | (X["last_is_eid"].to_numpy(int) == 1)).astype(int)


def _conformal_quantile(scores: np.ndarray) -> float:
    n = len(scores)
    level = min(1.0, np.ceil((n + 1) * COVERAGE_TARGET) / n)
    return float(np.quantile(scores, level))


def _fit_level(fit: pd.DataFrame, calib: pd.DataFrame) -> QuantileForecaster:
    y_fit = fit["actual"] / fit["headcount"] / fit["level"]
    y_fit_per_head = fit["actual"] / fit["headcount"]
    models = {}
    for a in QUANTILES:
        m = LGBMRegressor(objective="quantile", alpha=a, n_estimators=300, learning_rate=0.05, num_leaves=15, min_child_samples=20, random_state=0, verbose=-1)
        m.fit(fit[FEATURES], y_fit)
        models[a] = m
    forecaster = QuantileForecaster(models, 0.0)
    y_cal = (calib["actual"] / calib["headcount"] / calib["level"]).to_numpy()
    lo = np.minimum(models[0.1].predict(calib[FEATURES]), models[0.5].predict(calib[FEATURES]))
    hi = np.maximum(models[0.9].predict(calib[FEATURES]), models[0.5].predict(calib[FEATURES]))
    scores = np.maximum(lo - y_cal, y_cal - hi)
    forecaster.conformal_q = _conformal_quantile(scores)
    eid = _peak(calib)
    for group in (0, 1):
        part = scores[eid == group]
        if len(part) >= MIN_GROUP_CALIB:
            forecaster.conformal_q_by_eid[group] = _conformal_quantile(part)
    for kind in ("previous_month", "seasonal_naive"):
        resid = y_fit_per_head.to_numpy() - _baseline(fit, kind)
        forecaster.residual_q[kind] = {a: float(np.nanquantile(resid, a)) for a in QUANTILES}
    return forecaster


def evaluate_level(forecaster: QuantileForecaster, test: pd.DataFrame, buffer_pct: float | None = None) -> dict:
    y = test["actual"].to_numpy(float)
    pred = forecaster.predict(test)
    out = {"n": int(len(test)), "actual_mean_bdt": round(float(y.mean()), 1)}
    out["model"] = {
        "mae_p50_bdt": round(float(np.mean(np.abs(y - pred["p50"]))), 1),
        "pinball_bdt": {str(a): round(pinball(y, pred[f"p{int(a * 100)}"].to_numpy(), a), 1) for a in QUANTILES},
        "coverage_p10_p90": round(float(np.mean((y >= pred["p10"]) & (y <= pred["p90"]))), 4),
        "mean_interval_width_bdt": round(float(np.mean(pred["p90"] - pred["p10"])), 1),
    }
    head = test["headcount"].to_numpy(float)
    for kind in ("previous_month", "seasonal_naive"):
        point = _baseline(test, kind)
        rq = forecaster.residual_q[kind]
        q = {a: np.maximum(point + rq[a], 0) * head for a in QUANTILES}
        out[kind] = {
            "mae_p50_bdt": round(float(np.mean(np.abs(y - q[0.5]))), 1),
            "pinball_bdt": {str(a): round(pinball(y, q[a], a), 1) for a in QUANTILES},
            "coverage_p10_p90": round(float(np.mean((y >= q[0.1]) & (y <= q[0.9]))), 4),
        }
    if buffer_pct is not None:
        pool = pred["p90"].to_numpy() * (1 + buffer_pct / 100)
        out["pool_covers_actual_share"] = round(float(np.mean(y <= pool)), 4)
    by_h = []
    for h in HORIZONS:
        mask = (test["horizon"] == h).to_numpy()
        by_h.append({"horizon": h, "coverage_p10_p90": round(float(np.mean((y[mask] >= pred["p10"][mask]) & (y[mask] <= pred["p90"][mask]))), 4), "mae_p50_bdt": round(float(np.mean(np.abs(y[mask] - pred["p50"][mask]))), 1)})
    out["by_horizon"] = by_h
    return out


@dataclass
class CapitalModel:
    world: QuantileForecaster
    employer: QuantileForecaster
    version: str


def _world_rows(profile: str, seeds, scale: float):
    employer_rows, world_rows, test_starts = [], [], []
    for s in seeds:
        tables = generate(profile, seed=s, scale=scale)
        panel = monthly_panel(tables)
        test_start = int(panel.attrs["months"] - PROFILES[profile].test_months)
        e = build_rows(panel, f"{profile}{s}")
        w = build_rows(world_panel(panel), f"{profile}{s}")
        e["is_test"], w["is_test"] = e["target_index"] >= test_start, w["target_index"] >= test_start
        e["is_calib"] = (~e["is_test"]) & (e["target_index"] >= test_start - 4)
        w["is_calib"] = (~w["is_test"]) & (w["target_index"] >= test_start - 4)
        # Forecasts made before the test window must not see test-window months as lags: origins are < target, and
        # targets in the test window are excluded from fit/calibration by the masks above.
        employer_rows.append(e)
        world_rows.append(w)
    return pd.concat(employer_rows, ignore_index=True), pd.concat(world_rows, ignore_index=True)


def train(seeds: tuple[int, ...] = M4_SEEDS, scale: float = 1.0, artifacts_dir: Path = ARTIFACTS_DIR, policy: PolicyParams | None = None, eval_seeds_b: tuple[int, ...] = EVAL_SEEDS_B) -> dict:
    policy = policy or get_settings().policy
    emp, world = _world_rows("A", seeds, scale)
    split = lambda d: (d[~d["is_test"] & ~d["is_calib"]], d[d["is_calib"]], d[d["is_test"]])  # noqa: E731
    e_fit, e_cal, e_test = split(emp)
    w_fit, w_cal, w_test = split(world)
    model = CapitalModel(world=_fit_level(w_fit, w_cal), employer=_fit_level(e_fit, e_cal), version=VERSION)

    emp_b, world_b = _world_rows("B", eval_seeds_b, scale)
    metrics = {
        "version": VERSION,
        "target": "BDT advanced in the target month (per employer, and total)",
        "features": FEATURES,
        "training": {
            "profile": "A",
            "seeds": list(seeds),
            "scale": scale,
            "rows_fit": {"world": int(len(w_fit)), "employer": int(len(e_fit))},
            "rows_calibration": {"world": int(len(w_cal)), "employer": int(len(e_cal))},
            "split": "time-based on target month: last 3 months of every world are test; the 4 months before calibrate the interval",
            "conformal_q_per_head": {"world": round(model.world.conformal_q, 4), "employer": round(model.employer.conformal_q, 4)},
            "conformal_q_by_eid": {lvl: {str(k): round(v, 4) for k, v in getattr(model, lvl).conformal_q_by_eid.items()} for lvl in ("world", "employer")},
        },
        "coverage_target": COVERAGE_TARGET,
        "previous_version": {"version": "m4-v1", **V1_RESULTS},
        "previous_version_v2": {"version": "m4-v2", "change": "one pooled conformal widening", **V2_RESULTS},
        "capital_buffer_pct": policy.capital_buffer_pct,
        "test_profile_a": {
            "world": evaluate_level(model.world, w_test, policy.capital_buffer_pct),
            "employer": evaluate_level(model.employer, e_test),
        },
        "test_profile_b": {
            "world": evaluate_level(model.world, world_b[world_b["is_test"]], policy.capital_buffer_pct),
            "employer": evaluate_level(model.employer, emp_b[emp_b["is_test"]]),
        },
    }
    save_artifact(MODEL_NAME, model, artifacts_dir)
    update_metrics(MODEL_NAME, metrics, artifacts_dir)
    return metrics


_MODEL: CapitalModel | None = None


def load_model(artifacts_dir: Path = ARTIFACTS_DIR) -> CapitalModel:
    global _MODEL
    if artifacts_dir != ARTIFACTS_DIR:
        return load_artifact(MODEL_NAME, artifacts_dir)
    if _MODEL is None:
        _MODEL = load_artifact(MODEL_NAME, artifacts_dir)
    return _MODEL


def capital_forecast(panel: pd.DataFrame, origin: int, policy: PolicyParams | None = None, model: CapitalModel | None = None, months_ahead: int = 3) -> dict:
    """Forecast the next `months_ahead` months from a monthly panel whose last observed month is origin-1."""
    policy = policy or get_settings().policy
    model = model or load_model()
    start, eid = panel.attrs["start"], panel.attrs["eid_months"]
    wp = world_panel(panel)
    w_rows = pd.DataFrame(_rows_for_series(wp, [origin], start, eid, with_target=False))
    w_rows = w_rows[w_rows["horizon"] <= months_ahead].reset_index(drop=True)
    w_pred = model.world.predict(w_rows)
    months = [
        {
            "month": r.target_month,
            "horizon": int(r.horizon),
            "p10_bdt": int(p.p10),
            "p50_bdt": int(p.p50),
            "p90_bdt": int(p.p90),
            "required_pool_bdt": int(p.p90 * (1 + policy.capital_buffer_pct / 100)),
        }
        for r, p in zip(w_rows.itertuples(), w_pred.itertuples())
    ]
    e_rows = []
    for _, series in panel.groupby("employer_id"):
        e_rows += _rows_for_series(series, [origin], start, eid, with_target=False)
    e_rows = pd.DataFrame(e_rows)
    e_rows = e_rows[e_rows["horizon"] <= months_ahead].reset_index(drop=True)
    e_pred = model.employer.predict(e_rows)
    per_employer = [
        {"employer_id": r.employer_id, "month": r.target_month, "p10_bdt": int(p.p10), "p50_bdt": int(p.p50), "p90_bdt": int(p.p90)}
        for r, p in zip(e_rows.itertuples(), e_pred.itertuples())
    ]
    return {"origin_month": _add_months(start, origin).isoformat()[:7], "months": months, "per_employer": per_employer, "buffer_pct": policy.capital_buffer_pct, "model_version": model.version}


def _print(metrics: dict) -> None:
    for split in ("test_profile_a", "test_profile_b"):
        for level in ("world", "employer"):
            e = metrics[split][level]
            m = e["model"]
            print(
                f"{split} {level}: n={e['n']} mean={e['actual_mean_bdt']:,.0f} | MAE p50 model {m['mae_p50_bdt']:,.0f} "
                f"vs prev {e['previous_month']['mae_p50_bdt']:,.0f} vs seasonal {e['seasonal_naive']['mae_p50_bdt']:,.0f} | "
                f"coverage model {m['coverage_p10_p90']} vs prev {e['previous_month']['coverage_p10_p90']} | "
                f"pinball90 model {m['pinball_bdt']['0.9']:,.0f} vs prev {e['previous_month']['pinball_bdt']['0.9']:,.0f}"
                + (f" | pool covers {e['pool_covers_actual_share']}" if "pool_covers_actual_share" in e else "")
            )


if __name__ == "__main__":
    from app.ml import m4_capital

    m4_capital._print(m4_capital.train())
