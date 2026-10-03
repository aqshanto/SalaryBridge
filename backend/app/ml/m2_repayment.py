"""M2: will this advance fail to be recovered by its due date + grace?

The calibrated probability maps to a tier (app/rules/tiers.py) that shrinks the rule-based hard cap.

Point-in-time rules for features:
- employer risk = M1 snapshot from day 1 or day 20, whichever is the latest on or before the issue date;
- a past advance's outcome is used only if its grace period ended before the issue date;
- gender, region, age band and the latent behaviour label are never features.

Known limitation: history only has outcomes for advances that were approved (by the pilot rules),
so the model learns from approved advances only.

Train (after M1):  .venv/Scripts/python.exe -m app.ml.m2_repayment
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
from app.ml.common import ARTIFACTS_DIR, TRAIN_SEEDS, binary_metrics, load_artifact, save_artifact, update_metrics
from app.rules.tiers import TIERS, tier_for, tiered_amount
from data.generator import INDUSTRIES, generate

MODEL_NAME = "m2_repayment"
VERSION = "m2-v1"
INDUSTRY_COLUMNS = [f"industry_{name}" for name in INDUSTRIES]
FEATURES = [
    "tenure_days",
    "log_salary",
    "salary_to_upay",
    "amount_to_salary",
    "day_of_month",
    "earned_ratio",
    "prior_advances",
    "prior_known",
    "prior_failures",
    "prior_failure_rate",
    "advances_6m",
    "same_month_prior",
    "same_month_prior_ratio",
    "streak_months",
    "employer_prob_late",
    "employer_headcount",
    *INDUSTRY_COLUMNS,
]
FORBIDDEN_FEATURES = {"gender", "region", "age_band", "behaviour", "end_date", "end_reason", "reliability_type"}
TENURE_BUCKETS = [0, 365, 730, 1825, 10**6]

REASONS = {
    "tenure_days": ("SHORT_TENURE", "LONG_TENURE"),
    "prior_failures": ("PAST_ADVANCE_LATE_OR_UNPAID", "PAST_ADVANCES_REPAID"),
    "prior_failure_rate": ("PAST_ADVANCE_LATE_OR_UNPAID", "PAST_ADVANCES_REPAID"),
    "prior_known": ("MANY_PAST_ADVANCES", "FEW_PAST_ADVANCES"),
    "prior_advances": ("MANY_PAST_ADVANCES", "FEW_PAST_ADVANCES"),
    "advances_6m": ("FREQUENT_ADVANCES", "INFREQUENT_ADVANCES"),
    "streak_months": ("ADVANCES_EVERY_MONTH", "NO_RECENT_STREAK"),
    "employer_prob_late": ("EMPLOYER_PAYROLL_RISK", "EMPLOYER_PAYS_ON_TIME"),
    "amount_to_salary": ("LARGE_SHARE_OF_SALARY", "SMALL_SHARE_OF_SALARY"),
    "earned_ratio": ("FEW_DAYS_EARNED", "MOST_OF_MONTH_EARNED"),
    "day_of_month": ("FEW_DAYS_EARNED", "MOST_OF_MONTH_EARNED"),
    "same_month_prior": ("SECOND_ADVANCE_THIS_MONTH", "FIRST_ADVANCE_THIS_MONTH"),
    "same_month_prior_ratio": ("SECOND_ADVANCE_THIS_MONTH", "FIRST_ADVANCE_THIS_MONTH"),
    "salary_to_upay": ("SALARY_NOT_IN_UPAY", "SALARY_PAID_INTO_UPAY"),
    "log_salary": ("LOWER_SALARY_BAND", "HIGHER_SALARY_BAND"),
}


def compute_features(adv: pd.DataFrame, employees: pd.DataFrame, employers: pd.DataFrame, grace_days: int) -> pd.DataFrame:
    """Features for each row of `adv` using only earlier rows of the same employee.

    `adv` needs: advance_id, employee_id, employer_id, issue_date, due_date, amount,
    recovered_by_grace (NaN when unknown) and employer_prob_late. Works for training batches and
    for a single live request appended to that employee's history.
    """
    df = adv.merge(
        employees[["employee_id", "salary_bdt", "hire_date", "salary_to_upay"]], on="employee_id", how="left"
    ).merge(employers[["employer_id", "industry", "headcount"]], on="employer_id", how="left")
    df["issue_date"] = pd.to_datetime(df["issue_date"])
    df = df.sort_values(["employee_id", "issue_date", "advance_id"]).reset_index(drop=True)

    issue = df["issue_date"].to_numpy("datetime64[D]")
    grace_end = (pd.to_datetime(df["due_date"]) + pd.Timedelta(days=grace_days)).to_numpy("datetime64[D]")
    failed = 1.0 - df["recovered_by_grace"].astype(float).to_numpy()  # NaN stays NaN
    amount = df["amount"].to_numpy(float)
    month_no = (df["issue_date"].dt.year * 12 + df["issue_date"].dt.month).to_numpy()

    cols = {k: np.zeros(len(df)) for k in ("prior_advances", "prior_known", "prior_failures", "advances_6m", "same_month_prior", "same_month_amount", "streak_months")}
    for idx in df.groupby("employee_id").indices.values():
        for pos, k in enumerate(idx):
            prior = idx[:pos]
            if not len(prior):
                continue
            known = grace_end[prior] < issue[k]
            cols["prior_advances"][k] = len(prior)
            cols["prior_known"][k] = known.sum()
            cols["prior_failures"][k] = np.nansum(failed[prior][known])
            cols["advances_6m"][k] = (issue[prior] >= issue[k] - np.timedelta64(180, "D")).sum()
            same = month_no[prior] == month_no[k]
            cols["same_month_prior"][k] = same.sum()
            cols["same_month_amount"][k] = amount[prior][same].sum()
            months = set(month_no[prior].tolist())
            streak, m = 0, month_no[k] - 1
            while m in months:
                streak, m = streak + 1, m - 1
            cols["streak_months"][k] = streak

    salary = df["salary_bdt"].astype(float)
    day = df["issue_date"].dt.day
    out = pd.DataFrame(
        {
            "advance_id": df["advance_id"],
            "employee_id": df["employee_id"],
            "employer_id": df["employer_id"],
            "issue_date": df["issue_date"].dt.date,
            "tenure_days": (df["issue_date"] - pd.to_datetime(df["hire_date"])).dt.days,
            "log_salary": np.log(salary),
            "salary_to_upay": df["salary_to_upay"].astype(int),
            "amount_to_salary": df["amount"] / salary,
            "day_of_month": day,
            "earned_ratio": day / df["issue_date"].dt.days_in_month,
            "prior_advances": cols["prior_advances"],
            "prior_known": cols["prior_known"],
            "prior_failures": cols["prior_failures"],
            "prior_failure_rate": np.divide(cols["prior_failures"], cols["prior_known"], out=np.zeros(len(df)), where=cols["prior_known"] > 0),
            "advances_6m": cols["advances_6m"],
            "same_month_prior": cols["same_month_prior"],
            "same_month_prior_ratio": cols["same_month_amount"] / salary,
            "streak_months": cols["streak_months"],
            "employer_prob_late": df["employer_prob_late"].astype(float),
            "employer_headcount": df["headcount"],
        }
    )
    for name in INDUSTRIES:
        out[f"industry_{name}"] = (df["industry"] == name).astype(int)
    return out


def _snapshot_date(issue: pd.Series) -> pd.Series:
    issue = pd.to_datetime(issue)
    day = np.where(issue.dt.day >= m1.DECISION_DAY, m1.DECISION_DAY, 1)
    return pd.to_datetime({"year": issue.dt.year, "month": issue.dt.month, "day": day}).dt.date


def build_dataset(tables: dict, m1_model: m1.EmployerRiskModel, grace_days: int, world: str = "") -> pd.DataFrame:
    snaps = m1.snapshot_probabilities(tables, m1_model, grace_days)
    adv = tables["advances"].merge(tables["advance_requests"][["request_id", "hard_cap", "amount_requested"]], on="request_id")
    adv["as_of"] = _snapshot_date(adv["issue_date"])
    adv = adv.merge(snaps, on=["employer_id", "as_of"], how="left")
    feats = compute_features(adv, tables["employees"], tables["employers"], grace_days)
    test_start = pd.Timestamp(tables["meta"]["test_start"].iloc[0]).date()
    keep = adv[["advance_id", "amount", "fee", "loss_amount", "hard_cap", "amount_requested", "recovered_by_grace"]]
    data = feats.merge(keep, on="advance_id")
    data["target"] = (~data["recovered_by_grace"].astype(bool)).astype(int)
    data["is_test"] = pd.to_datetime(data["issue_date"]).dt.date >= test_start
    data["month_index"] = (pd.to_datetime(data["issue_date"]).dt.to_period("M") - pd.Period(pd.Timestamp(tables["meta"]["start"].iloc[0]), "M")).apply(lambda p: p.n)
    data["world"] = world
    data = data.merge(tables["employees"][["employee_id", "gender", "region"]], on="employee_id", how="left")  # audit only
    return data


def _tenure_bucket(tenure_days: pd.Series) -> pd.Series:
    return pd.cut(tenure_days, TENURE_BUCKETS, right=False, labels=False)


@dataclass
class RepaymentRiskModel:
    calibrated: CalibratedClassifierCV
    booster: LGBMClassifier
    features: list[str]
    version: str
    baseline_by_tenure: dict  # tenure bucket -> failure rate in training data

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        return self.calibrated.predict_proba(X[self.features])[:, 1]

    def baseline_proba(self, X: pd.DataFrame) -> np.ndarray:
        buckets = _tenure_bucket(X["tenure_days"])
        overall = float(np.mean(list(self.baseline_by_tenure.values())))
        return np.array([self.baseline_by_tenure.get(int(b), overall) if pd.notna(b) else overall for b in buckets])

    def reasons(self, X: pd.DataFrame, top: int = 3) -> list[list[dict]]:
        values = shap.TreeExplainer(self.booster).shap_values(X[self.features])
        if isinstance(values, list):
            values = values[1]
        out = []
        for row in np.asarray(values):
            ranked = [i for i in np.argsort(-np.abs(row)) if self.features[i] in REASONS]
            seen, items = set(), []
            for i in ranked:
                code = REASONS[self.features[i]][0 if row[i] > 0 else 1]
                if code in seen:
                    continue
                seen.add(code)
                items.append(
                    {"feature": self.features[i], "code": code, "direction": "raises_risk" if row[i] > 0 else "lowers_risk", "shap": round(float(row[i]), 4)}
                )
                if len(items) == top:
                    break
            out.append(items)
        return out


def policy_comparison(
    data: pd.DataFrame,
    risk: np.ndarray,
    baseline: np.ndarray,
    policy: PolicyParams,
    tiers: list | None = None,
    extra_scores: dict[str, np.ndarray] | None = None,
) -> dict:
    """Compare the flat 20% cap (history) with ML tiers, and rankings at equal approval rates.

    Loss for a smaller offer is scaled by offered/actual amount (losses come from the whole balance
    being unpaid or a share of it), which is an approximation documented in the report.
    """
    due = (data["amount"] + data["fee"]).to_numpy(float)
    loss = data["loss_amount"].to_numpy(float)
    amount = data["amount"].to_numpy(float)
    flat = {
        "approval_rate": 1.0,
        "funds_deployed_bdt": int(amount.sum()),
        "loss_bdt": int(loss.sum()),
        "loss_rate": round(float(loss.sum() / due.sum()), 4),
    }

    tiers = tiers if tiers is not None else [tier_for(float(r), policy) for r in risk]
    offered = np.array(
        [tiered_amount(int(cap), int(req), t, policy) for cap, req, t in zip(data["hard_cap"], data["amount"], tiers)], dtype=float
    )
    approved = offered > 0
    scale = np.divide(offered, amount, out=np.zeros_like(offered), where=amount > 0)
    ml_loss = loss * scale
    ml_due = offered + np.where(approved, data["fee"], 0)
    ml = {
        "approval_rate": round(float(approved.mean()), 4),
        "queued_for_human": int(sum(t.needs_human for t in tiers)),
        "funds_deployed_bdt": int(offered.sum()),
        "loss_bdt": int(ml_loss.sum()),
        "loss_rate": round(float(ml_loss.sum() / max(ml_due.sum(), 1)), 4),
        "tier_counts": {t: int(sum(x.tier == t for x in tiers)) for t in TIERS},
    }

    def loss_rate_after_declining(score: np.ndarray, share: float) -> float:
        keep = np.argsort(score)[: int(round(len(score) * (1 - share)))]
        return round(float(loss[keep].sum() / due[keep].sum()), 4)

    scores = {"ml": risk, "tenure_rule": baseline, "random": np.random.default_rng(0).random(len(data)), **(extra_scores or {})}
    equal = [
        {"decline_share": share, **{f"loss_rate_{name}": loss_rate_after_declining(score, share) for name, score in scores.items()}}
        for share in (0.05, 0.10, 0.20)
    ]
    return {"flat_cap": flat, "ml_tiers": ml, "equal_approval": equal}


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
    by_tenure = train_part.groupby(_tenure_bucket(train_part["tenure_days"]))["target"].mean()
    model = RepaymentRiskModel(calibrated, booster, FEATURES, VERSION, {int(k): float(v) for k, v in by_tenure.items()})

    def evaluate(part: pd.DataFrame) -> dict:
        risk, base = model.predict_proba(part), model.baseline_proba(part)
        return {
            "model": binary_metrics(part["target"], risk),
            "baseline": binary_metrics(part["target"], base),
            "policy": policy_comparison(part, risk, base, policy),
        }

    profile_b = _datasets("B", seeds, m1_model, policy.grace_days, scale)
    metrics = {
        "version": VERSION,
        "target": f"advance not fully recovered by due date + {policy.grace_days} days",
        "features": FEATURES,
        "training": {
            "profile": "A",
            "seeds": list(seeds),
            "scale": scale,
            "rows_fit": int(len(fit_part)),
            "rows_calibration": int(len(calib_part)),
            "split": "time-based: last 3 months of every world are test; the 4 months before are calibration",
            "selection_note": "history only contains advances approved by the pilot rules",
            "employer_feature": "M1 snapshot (day 1 or 20) on or before the issue date",
        },
        "baseline": {"name": "failure rate by tenure bucket in training data", "by_bucket": model.baseline_by_tenure},
        "tier_thresholds": {"A": policy.tier_a_max_risk, "B": policy.tier_b_max_risk, "C": policy.tier_c_max_risk},
        "test_profile_a": evaluate(test_part),
        "test_profile_b": evaluate(profile_b[profile_b["is_test"]]),
    }
    save_artifact(MODEL_NAME, model, artifacts_dir)
    update_metrics(MODEL_NAME, metrics, artifacts_dir)
    return metrics


_MODEL: RepaymentRiskModel | None = None


def load_model(artifacts_dir: Path = ARTIFACTS_DIR) -> RepaymentRiskModel:
    global _MODEL
    if artifacts_dir != ARTIFACTS_DIR:
        return load_artifact(MODEL_NAME, artifacts_dir)
    if _MODEL is None:
        _MODEL = load_artifact(MODEL_NAME, artifacts_dir)
    return _MODEL


def repayment_risk(features: pd.DataFrame, policy: PolicyParams | None = None, model: RepaymentRiskModel | None = None) -> list[dict]:
    """Probability, tier and reasons for rows built by compute_features()."""
    policy = policy or get_settings().policy
    model = model or load_model()
    probs = model.predict_proba(features)
    reasons = model.reasons(features)
    out = []
    for p, r in zip(probs, reasons):
        tier = tier_for(float(p), policy)
        out.append(
            {
                "prob_fail": round(float(p), 4),
                "tier": tier.tier,
                "share_of_cap": tier.share_of_cap,
                "needs_human": tier.needs_human,
                "reasons": r,
                "model_version": model.version,
            }
        )
    return out


def _print(metrics: dict) -> None:
    for name in ("test_profile_a", "test_profile_b"):
        m, b, pol = metrics[name]["model"], metrics[name]["baseline"], metrics[name]["policy"]
        print(f"{name}: n={m['n']} pos={m['positive_rate']:.3f} PR-AUC {m['pr_auc']} vs {b['pr_auc']} | Brier {m['brier']} vs {b['brier']}")
        print(f"  flat cap: {pol['flat_cap']}")
        print(f"  ml tiers: {pol['ml_tiers']}")
        for row in pol["equal_approval"]:
            print(f"  equal approval {row}")


if __name__ == "__main__":
    from app.ml import m2_repayment

    assert set(m2_repayment.FEATURES).isdisjoint(m2_repayment.FORBIDDEN_FEATURES)
    m2_repayment._print(m2_repayment.train())
