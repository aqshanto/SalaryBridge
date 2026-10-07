"""M1: will an employer's next payroll be late beyond the grace period?

Decision point: day 20 of the work month (requests cluster after day 20). The target is that
month's payroll run (paid in the following month): 1 if it defaults, is partial, or is paid
more than `grace_days` late.

Features use only what is observable on the decision date. A past run that has not been paid
yet counts with its delay so far. The latent `reliability_type` is never a feature.

Train:  .venv/Scripts/python.exe -m app.ml.m1_employer
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import shap
from lightgbm import LGBMClassifier
from sklearn.calibration import CalibratedClassifierCV

from app.config import PolicyParams, get_settings
from app.ml.common import ARTIFACTS_DIR, TRAIN_SEEDS, binary_metrics, load_artifact, save_artifact, update_metrics
from data.generator import INDUSTRIES, _add_months, generate

MODEL_NAME = "m1_employer"
VERSION = "m1-v2"
DECISION_DAY = 20
INDUSTRY_COLUMNS = [f"industry_{name}" for name in INDUSTRIES]
FEATURES = [
    "months_observed",
    "last_late_beyond_grace",
    "last_delay_days",
    "last_partial",
    "late_rate_3m",
    "late_rate_6m",
    "late_rate_all",
    "mean_delay_6m",
    "max_delay_6m",
    "partial_6m",
    "months_since_late",
    "payroll_day",
    "pay_month",
    *INDUSTRY_COLUMNS,
]
FORBIDDEN_FEATURES = {"reliability_type", "closed_month_index", "gender", "region"}

# feature -> (reason code when it raises risk, reason code when it lowers risk)
REASONS = {
    "last_late_beyond_grace": ("EMPLOYER_LATE_LAST_MONTH", "EMPLOYER_ON_TIME_LAST_MONTH"),
    "last_delay_days": ("EMPLOYER_LATE_LAST_MONTH", "EMPLOYER_ON_TIME_LAST_MONTH"),
    "last_partial": ("EMPLOYER_PARTIAL_PAY", "EMPLOYER_PAID_IN_FULL"),
    "late_rate_3m": ("EMPLOYER_OFTEN_LATE_RECENTLY", "EMPLOYER_ON_TIME_RECENTLY"),
    "late_rate_6m": ("EMPLOYER_OFTEN_LATE", "EMPLOYER_USUALLY_ON_TIME"),
    "late_rate_all": ("EMPLOYER_OFTEN_LATE", "EMPLOYER_USUALLY_ON_TIME"),
    "mean_delay_6m": ("EMPLOYER_LONG_DELAYS", "EMPLOYER_SHORT_DELAYS"),
    "max_delay_6m": ("EMPLOYER_LONG_DELAYS", "EMPLOYER_SHORT_DELAYS"),
    "partial_6m": ("EMPLOYER_PARTIAL_PAY", "EMPLOYER_PAID_IN_FULL"),
    "months_since_late": ("EMPLOYER_LATE_RECENTLY", "EMPLOYER_LONG_ON_TIME_STREAK"),
    "months_observed": ("EMPLOYER_SHORT_HISTORY", "EMPLOYER_LONG_HISTORY"),
}


def is_late_beyond_grace(status: str, delay_days: int, grace_days: int) -> bool:
    return status in ("default", "partial") or delay_days > grace_days


def observed_runs(runs: pd.DataFrame, as_of: date, grace_days: int) -> pd.DataFrame:
    """What could be known about an employer's past runs on `as_of` (runs: one employer, any order)."""
    past = runs[pd.to_datetime(runs["scheduled_date"]).dt.date < as_of].copy()
    if past.empty:
        return past.assign(obs_delay=[], obs_partial=[], obs_late=[])
    scheduled = pd.to_datetime(past["scheduled_date"]).dt.date
    # Compare as timestamps: an all-NaT column (only defaulted runs) has no .dt.date objects.
    actual = pd.to_datetime(past["actual_date"])
    paid_by_now = actual.notna() & (actual <= pd.Timestamp(as_of))
    elapsed = np.array([(as_of - d).days for d in scheduled])
    past["obs_delay"] = np.where(paid_by_now, past["delay_days"], elapsed)
    past["obs_partial"] = (paid_by_now & (past["status"] == "partial")).astype(int)
    past["obs_late"] = (
        (past["obs_partial"] == 1) | (past["obs_delay"] > grace_days) | (past["status"].eq("default") & (elapsed > grace_days))
    ).astype(int)
    return past.sort_values("scheduled_date", key=pd.to_datetime)


def employer_features(employer: pd.Series, runs: pd.DataFrame, as_of: date, grace_days: int) -> dict:
    """Feature row for one employer at `as_of`. `runs` may include future runs; they are ignored."""
    obs = observed_runs(runs, as_of, grace_days)
    late, delay, partial = obs["obs_late"].to_numpy(), obs["obs_delay"].to_numpy(), obs["obs_partial"].to_numpy()

    def tail_mean(x: np.ndarray, n: int) -> float:
        return float(x[-n:].mean()) if len(x) else 0.0

    late_idx = np.flatnonzero(late)
    payday = _add_months(as_of.replace(day=1), 1)
    row = {
        "months_observed": len(obs),
        "last_late_beyond_grace": int(late[-1]) if len(late) else 0,
        "last_delay_days": int(delay[-1]) if len(delay) else 0,
        "last_partial": int(partial[-1]) if len(partial) else 0,
        "late_rate_3m": tail_mean(late, 3),
        "late_rate_6m": tail_mean(late, 6),
        "late_rate_all": tail_mean(late, len(late)),
        "mean_delay_6m": tail_mean(delay, 6),
        "max_delay_6m": float(delay[-6:].max()) if len(delay) else 0.0,
        "partial_6m": int(partial[-6:].sum()) if len(partial) else 0,
        "months_since_late": int(len(late) - 1 - late_idx[-1]) if len(late_idx) else 99,
        "headcount": int(employer["headcount"]),
        "payroll_day": int(employer["payroll_day"]),
        "pay_month": payday.month,
    }
    for name in INDUSTRIES:
        row[f"industry_{name}"] = int(employer["industry"] == name)
    return row


def build_dataset(tables: dict, grace_days: int, world: str = "") -> pd.DataFrame:
    """One row per (employer, work month) with a known outcome, from month 1 onwards."""
    runs, employers = tables["payroll_runs"], tables["employers"].set_index("employer_id")
    start = pd.Timestamp(tables["meta"]["start"].iloc[0]).date()
    test_start = pd.Timestamp(tables["meta"]["test_start"].iloc[0]).date()
    rows = []
    for employer_id, emp_runs in runs.groupby("employer_id"):
        for run in emp_runs.itertuples():
            if run.month_index < 1:
                continue
            work_month = _add_months(start, run.month_index)
            as_of = work_month.replace(day=DECISION_DAY)
            row = employer_features(employers.loc[employer_id], emp_runs, as_of, grace_days)
            row.update(
                {
                    "world": world,
                    "employer_id": employer_id,
                    "work_month": work_month.isoformat()[:7],
                    "month_index": run.month_index,
                    "is_test": work_month >= test_start,
                    "target": int(is_late_beyond_grace(run.status, run.delay_days, grace_days)),
                }
            )
            rows.append(row)
    return pd.DataFrame(rows)


def _datasets(profile: str, seeds: tuple[int, ...], grace_days: int, scale: float) -> pd.DataFrame:
    return pd.concat(
        [build_dataset(generate(profile, seed=s, scale=scale), grace_days, world=f"{profile}{s}") for s in seeds],
        ignore_index=True,
    )


SNAPSHOT_DAYS = (1, DECISION_DAY)


def snapshot_probabilities(tables: dict, model: "EmployerRiskModel", grace_days: int) -> pd.DataFrame:
    """M1 probability per employer on day 1 and day 20 of every month (only data known on that date).

    Downstream models pick the latest snapshot on or before their own decision date, so no future
    payroll information leaks into them.
    """
    runs, employers = tables["payroll_runs"], tables["employers"].set_index("employer_id", drop=False)
    start = pd.Timestamp(tables["meta"]["start"].iloc[0]).date()
    months = int(tables["meta"]["months"].iloc[0])
    rows, keys = [], []
    for employer_id, emp_runs in runs.groupby("employer_id"):
        for i in range(months):
            for day in SNAPSHOT_DAYS:
                as_of = _add_months(start, i).replace(day=day)
                rows.append(employer_features(employers.loc[employer_id], emp_runs, as_of, grace_days))
                keys.append((employer_id, as_of))
    probs = model.predict_proba(pd.DataFrame(rows))
    return pd.DataFrame({"employer_id": [k[0] for k in keys], "as_of": [k[1] for k in keys], "employer_prob_late": probs})


@dataclass
class EmployerRiskModel:
    calibrated: CalibratedClassifierCV
    booster: LGBMClassifier
    features: list[str]
    version: str
    baseline: dict  # P(late next | late last month) from training data

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        return self.calibrated.predict_proba(X[self.features])[:, 1]

    def baseline_proba(self, X: pd.DataFrame) -> np.ndarray:
        return np.where(X["last_late_beyond_grace"] == 1, self.baseline["after_late"], self.baseline["after_ok"])

    def reasons(self, X: pd.DataFrame, top: int = 3) -> list[list[dict]]:
        values = shap.TreeExplainer(self.booster).shap_values(X[self.features])
        if isinstance(values, list):  # older shap returns one array per class
            values = values[1]
        out = []
        for row in np.asarray(values):
            seen, items = set(), []
            for i in np.argsort(-np.abs(row)):
                if self.features[i] not in REASONS:
                    continue
                code = REASONS[self.features[i]][0 if row[i] > 0 else 1]
                if code in seen:  # two features can map to the same reason; show it once
                    continue
                seen.add(code)
                items.append(
                    {"feature": self.features[i], "code": code, "direction": "raises_risk" if row[i] > 0 else "lowers_risk", "shap": round(float(row[i]), 4)}
                )
                if len(items) == top:
                    break
            out.append(items)
        return out


def train(
    seeds: tuple[int, ...] = TRAIN_SEEDS,
    scale: float = 1.0,
    artifacts_dir: Path = ARTIFACTS_DIR,
    policy: PolicyParams | None = None,
) -> dict:
    policy = policy or get_settings().policy
    data = _datasets("A", seeds, policy.grace_days, scale)
    train_part = data[~data["is_test"]]
    test_part = data[data["is_test"]]
    # Calibrate on the last 4 training months of each world (time-based, not random).
    calib_cut = train_part["month_index"].max() - 3
    fit_part = train_part[train_part["month_index"] < calib_cut]
    calib_part = train_part[train_part["month_index"] >= calib_cut]

    booster = LGBMClassifier(
        n_estimators=200, learning_rate=0.05, num_leaves=7, min_child_samples=20, subsample=0.8, subsample_freq=1, random_state=0, verbose=-1
    )
    booster.fit(fit_part[FEATURES], fit_part["target"])
    calibrated = CalibratedClassifierCV(booster, method="sigmoid", cv="prefit")
    calibrated.fit(calib_part[FEATURES], calib_part["target"])

    late_last = train_part["last_late_beyond_grace"] == 1
    baseline = {
        "after_late": float(train_part.loc[late_last, "target"].mean()),
        "after_ok": float(train_part.loc[~late_last, "target"].mean()),
    }
    model = EmployerRiskModel(calibrated, booster, FEATURES, VERSION, baseline)

    profile_b = _datasets("B", seeds, policy.grace_days, scale)
    profile_b = profile_b[profile_b["is_test"]]
    metrics = {
        "version": VERSION,
        "target": f"payroll defaulted, partial, or more than {policy.grace_days} days late",
        "decision_day": DECISION_DAY,
        "features": FEATURES,
        "training": {
            "profile": "A",
            "seeds": list(seeds),
            "scale": scale,
            "rows_fit": int(len(fit_part)),
            "rows_calibration": int(len(calib_part)),
            "split": "time-based: last 3 months of every world are test; the 4 months before are calibration",
        },
        "baseline": {"name": "P(late | late last month) from training data", **baseline},
        "test_profile_a": {
            "model": binary_metrics(test_part["target"], model.predict_proba(test_part)),
            "baseline": binary_metrics(test_part["target"], model.baseline_proba(test_part)),
        },
        "test_profile_b": {
            "model": binary_metrics(profile_b["target"], model.predict_proba(profile_b)),
            "baseline": binary_metrics(profile_b["target"], model.baseline_proba(profile_b)),
        },
    }
    save_artifact(MODEL_NAME, model, artifacts_dir)
    update_metrics(MODEL_NAME, metrics, artifacts_dir)
    return metrics


def employer_risk(employer: pd.Series, runs: pd.DataFrame, as_of: date, model: EmployerRiskModel | None = None, grace_days: int | None = None) -> dict:
    """Probability that this employer's payroll for the month of `as_of` is late beyond grace, with reasons."""
    model = model or load_model()
    grace = get_settings().policy.grace_days if grace_days is None else grace_days
    X = pd.DataFrame([employer_features(employer, runs, as_of, grace)])
    return {
        "employer_id": employer["employer_id"],
        "as_of": as_of.isoformat(),
        "prob_late": round(float(model.predict_proba(X)[0]), 4),
        "reasons": model.reasons(X)[0],
        "model_version": model.version,
    }


_MODEL: EmployerRiskModel | None = None


def load_model(artifacts_dir: Path = ARTIFACTS_DIR) -> EmployerRiskModel:
    global _MODEL
    if _MODEL is None or artifacts_dir != ARTIFACTS_DIR:
        model = load_artifact(MODEL_NAME, artifacts_dir)
        if artifacts_dir != ARTIFACTS_DIR:
            return model
        _MODEL = model
    return _MODEL


def _print(metrics: dict) -> None:
    for name in ("test_profile_a", "test_profile_b"):
        m, b = metrics[name]["model"], metrics[name]["baseline"]
        print(
            f"{name}: n={m['n']} pos={m['positive_rate']:.3f} | "
            f"PR-AUC model {m['pr_auc']} vs baseline {b['pr_auc']} | Brier model {m['brier']} vs baseline {b['brier']}"
        )


if __name__ == "__main__":
    # Import by module name so the pickled class path is app.ml.m1_employer, not __main__.
    from app.ml import m1_employer

    assert set(m1_employer.FEATURES).isdisjoint(m1_employer.FORBIDDEN_FEATURES)
    m1_employer._print(m1_employer.train())
