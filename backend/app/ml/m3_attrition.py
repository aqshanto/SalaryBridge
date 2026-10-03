"""M3: will the person leave the job before this advance's payday?

Unit: one approved advance at its request date (the only moment M3 is used). Target: the employee
resigns (voluntarily or in the abuse pattern) after the issue date and before the due date.
Employer closures are excluded here; M1 covers employer risk.

If the probability is at or above `attrition_downgrade_threshold`, a rule (app/rules/tiers.py)
drops the M2 tier by one step.

Attendance is deliberately not a feature: in the synthetic world absences carry no signal about
leaving, and the live simulator records no attendance, so using it would create train/serve skew.

Train (after M1 and M2):  .venv/Scripts/python.exe -m app.ml.m3_attrition
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import shap
from lightgbm import LGBMClassifier
from sklearn.calibration import CalibratedClassifierCV

from app.config import PolicyParams, get_settings
from app.ml import m1_employer as m1
from app.ml import m2_repayment as m2
from app.ml.common import ARTIFACTS_DIR, TRAIN_SEEDS, binary_metrics, load_artifact, save_artifact, update_metrics
from app.rules.tiers import apply_attrition, tier_for
from data.generator import generate

MODEL_NAME = "m3_attrition"
VERSION = "m3-v1"
LEAVING_REASONS = ("voluntary", "abuse_pattern")
FEATURES = [
    "tenure_days",
    "log_salary",
    "salary_to_upay",
    "requested_to_cap",
    "amount_to_salary",
    "day_of_month",
    "prior_advances",
    "advances_6m",
    "same_month_prior",
    "streak_months",
    "prior_failures",
    "employer_prob_late",
    "employer_headcount",
    *m2.INDUSTRY_COLUMNS,
]
FORBIDDEN_FEATURES = m2.FORBIDDEN_FEATURES

REASONS = {
    "tenure_days": ("SHORT_TENURE", "LONG_TENURE"),
    "requested_to_cap": ("ASKED_FOR_FULL_LIMIT", "ASKED_FOR_PART_OF_LIMIT"),
    "amount_to_salary": ("LARGE_SHARE_OF_SALARY", "SMALL_SHARE_OF_SALARY"),
    "prior_advances": ("MANY_PAST_ADVANCES", "FEW_PAST_ADVANCES"),
    "advances_6m": ("FREQUENT_ADVANCES", "INFREQUENT_ADVANCES"),
    "streak_months": ("ADVANCES_EVERY_MONTH", "NO_RECENT_STREAK"),
    "prior_failures": ("PAST_ADVANCE_LATE_OR_UNPAID", "PAST_ADVANCES_REPAID"),
    "same_month_prior": ("SECOND_ADVANCE_THIS_MONTH", "FIRST_ADVANCE_THIS_MONTH"),
    "day_of_month": ("EARLY_IN_MONTH", "LATE_IN_MONTH"),
}


def add_request_features(data: pd.DataFrame) -> pd.DataFrame:
    data = data.copy()
    cap = data["hard_cap"].astype(float)
    data["requested_to_cap"] = np.divide(data["amount_requested"].astype(float), cap, out=np.ones(len(data)), where=cap > 0).clip(0, 5)
    return data


def build_dataset(tables: dict, m1_model: m1.EmployerRiskModel, grace_days: int, world: str = "") -> pd.DataFrame:
    data = add_request_features(m2.build_dataset(tables, m1_model, grace_days, world))
    data = data.rename(columns={"target": "m2_target"})
    people = tables["employees"][["employee_id", "end_date", "end_reason"]]
    due = tables["advances"][["advance_id", "due_date"]]
    data = data.merge(people, on="employee_id", how="left").merge(due, on="advance_id", how="left")
    end = pd.to_datetime(data["end_date"])
    leaves = (
        end.notna()
        & data["end_reason"].isin(LEAVING_REASONS)
        & (end > pd.to_datetime(data["issue_date"]))
        & (end < pd.to_datetime(data["due_date"]))
    )
    data["target"] = leaves.astype(int)
    return data


@dataclass
class AttritionModel:
    calibrated: CalibratedClassifierCV
    booster: LGBMClassifier
    features: list[str]
    version: str
    baseline_by_tenure: dict

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        return self.calibrated.predict_proba(X[self.features])[:, 1]

    def baseline_proba(self, X: pd.DataFrame) -> np.ndarray:
        buckets = m2._tenure_bucket(X["tenure_days"])
        overall = float(np.mean(list(self.baseline_by_tenure.values())))
        return np.array([self.baseline_by_tenure.get(int(b), overall) if pd.notna(b) else overall for b in buckets])

    def reasons(self, X: pd.DataFrame, top: int = 3) -> list[list[dict]]:
        values = shap.TreeExplainer(self.booster).shap_values(X[self.features])
        if isinstance(values, list):
            values = values[1]
        out = []
        for row in np.asarray(values):
            seen, items = set(), []
            for i in np.argsort(-np.abs(row)):
                if self.features[i] not in REASONS:
                    continue
                code = REASONS[self.features[i]][0 if row[i] > 0 else 1]
                if code in seen:
                    continue
                seen.add(code)
                items.append({"feature": self.features[i], "code": code, "direction": "raises_risk" if row[i] > 0 else "lowers_risk", "shap": round(float(row[i]), 4)})
                if len(items) == top:
                    break
            out.append(items)
        return out


def combined_tiers(p_fail: np.ndarray, p_leave: np.ndarray, policy: PolicyParams) -> tuple[list, int]:
    tiers, downgraded = [], 0
    for pf, pl in zip(p_fail, p_leave):
        tier, changed = apply_attrition(tier_for(float(pf), policy), float(pl), policy)
        tiers.append(tier)
        downgraded += int(changed)
    return tiers, downgraded


def _datasets(profile: str, seeds, m1_model, grace_days: int, scale: float) -> pd.DataFrame:
    return pd.concat(
        [build_dataset(generate(profile, seed=s, scale=scale), m1_model, grace_days, world=f"{profile}{s}") for s in seeds],
        ignore_index=True,
    )


def train(
    seeds: tuple[int, ...] = TRAIN_SEEDS,
    scale: float = 1.0,
    artifacts_dir: Path = ARTIFACTS_DIR,
    policy: PolicyParams | None = None,
) -> dict:
    policy = policy or get_settings().policy
    m1_model = m1.load_model(artifacts_dir)
    m2_model = m2.load_model(artifacts_dir)
    data = _datasets("A", seeds, m1_model, policy.grace_days, scale)
    train_part, test_part = data[~data["is_test"]], data[data["is_test"]]
    calib_cut = train_part["month_index"].max() - 3
    fit_part = train_part[train_part["month_index"] < calib_cut]
    calib_part = train_part[train_part["month_index"] >= calib_cut]

    booster = LGBMClassifier(
        n_estimators=300, learning_rate=0.05, num_leaves=15, min_child_samples=40, subsample=0.8, subsample_freq=1, random_state=0, verbose=-1
    )
    booster.fit(fit_part[FEATURES], fit_part["target"])
    calibrated = CalibratedClassifierCV(booster, method="sigmoid", cv="prefit")
    calibrated.fit(calib_part[FEATURES], calib_part["target"])
    by_tenure = train_part.groupby(m2._tenure_bucket(train_part["tenure_days"]))["target"].mean()
    model = AttritionModel(calibrated, booster, FEATURES, VERSION, {int(k): float(v) for k, v in by_tenure.items()})

    def evaluate(part: pd.DataFrame) -> dict:
        p_leave, base = model.predict_proba(part), model.baseline_proba(part)
        p_fail = m2_model.predict_proba(part)
        tiers, downgraded = combined_tiers(p_fail, p_leave, policy)
        combined_score = 1 - (1 - p_fail) * (1 - p_leave)
        return {
            "model": binary_metrics(part["target"], p_leave),
            "baseline": binary_metrics(part["target"], base),
            "downgraded_by_attrition": downgraded,
            "policy_m2_only": m2.policy_comparison(part, p_fail, m2_model.baseline_proba(part), policy),
            "policy_m2_plus_m3": m2.policy_comparison(
                part, p_fail, m2_model.baseline_proba(part), policy, tiers=tiers, extra_scores={"m3": p_leave, "m2_m3_combined": combined_score}
            ),
        }

    profile_b = _datasets("B", seeds, m1_model, policy.grace_days, scale)
    metrics = {
        "version": VERSION,
        "target": "employee resigns (voluntary or abuse pattern) after the issue date and before the due date",
        "features": FEATURES,
        "attendance_note": "attendance excluded: no signal in the synthetic world and not recorded by the live simulator",
        "training": {
            "profile": "A",
            "seeds": list(seeds),
            "scale": scale,
            "rows_fit": int(len(fit_part)),
            "rows_calibration": int(len(calib_part)),
            "split": "time-based: last 3 months of every world are test; the 4 months before are calibration",
        },
        "baseline": {"name": "resignation rate by tenure bucket in training data", "by_bucket": model.baseline_by_tenure},
        "downgrade_threshold": policy.attrition_downgrade_threshold,
        "test_profile_a": evaluate(test_part),
        "test_profile_b": evaluate(profile_b[profile_b["is_test"]]),
    }
    save_artifact(MODEL_NAME, model, artifacts_dir)
    update_metrics(MODEL_NAME, metrics, artifacts_dir)
    return metrics


_MODEL: AttritionModel | None = None


def load_model(artifacts_dir: Path = ARTIFACTS_DIR) -> AttritionModel:
    global _MODEL
    if artifacts_dir != ARTIFACTS_DIR:
        return load_artifact(MODEL_NAME, artifacts_dir)
    if _MODEL is None:
        _MODEL = load_artifact(MODEL_NAME, artifacts_dir)
    return _MODEL


def attrition_risk(features: pd.DataFrame, model: AttritionModel | None = None) -> list[dict]:
    """Probability of leaving before payday, with reasons, for rows with M3 features."""
    model = model or load_model()
    probs = model.predict_proba(features)
    return [
        {"prob_leave": round(float(p), 4), "reasons": r, "model_version": model.version}
        for p, r in zip(probs, model.reasons(features))
    ]


def _print(metrics: dict) -> None:
    for name in ("test_profile_a", "test_profile_b"):
        e = metrics[name]
        m, b = e["model"], e["baseline"]
        print(f"{name}: n={m['n']} pos={m['positive_rate']:.4f} PR-AUC {m['pr_auc']} vs {b['pr_auc']} | Brier {m['brier']} vs {b['brier']} | downgraded {e['downgraded_by_attrition']}")
        for key in ("policy_m2_only", "policy_m2_plus_m3"):
            t = e[key]["ml_tiers"]
            print(f"  {key}: approval {t['approval_rate']} loss_rate {t['loss_rate']} funds {t['funds_deployed_bdt']:,} queued {t['queued_for_human']}")
        for row in e["policy_m2_plus_m3"]["equal_approval"]:
            print(f"  equal approval {row}")


if __name__ == "__main__":
    from app.ml import m3_attrition

    assert set(m3_attrition.FEATURES).isdisjoint(m3_attrition.FORBIDDEN_FEATURES)
    m3_attrition._print(m3_attrition.train())
