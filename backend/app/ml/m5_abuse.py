"""M5: unusual borrowing patterns (Isolation Forest) plus a chronic-borrowing rule.

Features describe a person's request history before the decision date: how often, how many months
in a row, how close to the full limit, how early in the month, how quickly repeated. The latent
`behaviour` label (normal / chronic / abuser) is used only to measure precision@k, never as input.

Isolation Forest is unsupervised. A request is flagged when its score is in the top
`abuse_flag_top_pct` of training scores; flagged requests go to a person (app/rules/abuse.py).

Train:  .venv/Scripts/python.exe -m app.ml.m5_abuse
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from app.config import PolicyParams, get_settings
from app.ml.common import ARTIFACTS_DIR, TRAIN_SEEDS, load_artifact, save_artifact, update_metrics
from app.rules.abuse import AbuseFlags
from app.rules.eligibility import consecutive_months_before
from data.generator import PROFILES, _add_months, generate

MODEL_NAME = "m5_abuse"
VERSION = "m5-v1"
FEATURES = [
    "requests_6m",
    "request_months_6m",
    "streak_months",
    "full_cap_share",
    "mean_requested_to_cap",
    "mean_request_day",
    "share_before_day_20",
    "min_gap_days",
    "double_request_months",
    "declined_share",
    "tenure_days",
]
FORBIDDEN_FEATURES = {"behaviour", "gender", "region", "age_band", "end_date", "end_reason"}
K_VALUES = (25, 50, 100)
NO_GAP = 365


def behaviour_features(requests: pd.DataFrame, employees: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """One row per employee who requested in the 6 months before `as_of` and is still employed then."""
    req = requests.copy()
    req["request_date"] = pd.to_datetime(req["request_date"])
    past = req[req["request_date"] < pd.Timestamp(as_of)]
    recent = past[past["request_date"] >= pd.Timestamp(as_of - timedelta(days=182))]
    people = employees.set_index("employee_id")
    active = people[(pd.to_datetime(people["hire_date"]) < pd.Timestamp(as_of)) & (people["end_date"].isna() | (pd.to_datetime(people["end_date"]) >= pd.Timestamp(as_of)))]
    ids = sorted(set(recent["employee_id"]) & set(active.index))
    if not ids:
        return pd.DataFrame(columns=["employee_id", "as_of", *FEATURES])
    r = recent[recent["employee_id"].isin(ids)].sort_values(["employee_id", "request_date"]).copy()
    cap = r["hard_cap"].astype(float).to_numpy()
    r["ratio"] = np.divide(r["amount_requested"].astype(float).to_numpy(), cap, out=np.full(len(r), 5.0), where=cap > 0).clip(0, 5)
    r["full"] = (r["amount_requested"] >= r["hard_cap"]).astype(float)
    r["day"] = r["request_date"].dt.day
    r["before20"] = (r["day"] < 20).astype(float)
    r["declined"] = (r["status"] == "declined").astype(float)
    same_person = r["employee_id"].eq(r["employee_id"].shift())
    r["gap"] = np.where(same_person, (r["request_date"] - r["request_date"].shift()).dt.days, np.nan)

    g = r.groupby("employee_id")
    out = pd.DataFrame(
        {
            "requests_6m": g.size(),
            "request_months_6m": g["work_month"].nunique(),
            "full_cap_share": g["full"].mean(),
            "mean_requested_to_cap": g["ratio"].mean(),
            "mean_request_day": g["day"].mean().astype(float),
            "share_before_day_20": g["before20"].mean(),
            "min_gap_days": g["gap"].min().fillna(NO_GAP).astype(int),
            "double_request_months": (r.groupby(["employee_id", "work_month"]).size() >= 2).groupby(level=0).sum().astype(int),
            "declined_share": g["declined"].mean(),
        }
    )
    months_by_person = past[past["employee_id"].isin(ids)].groupby("employee_id")["work_month"].agg(frozenset)
    out["streak_months"] = [consecutive_months_before(as_of, months_by_person[eid]) for eid in out.index]
    hire = pd.to_datetime(active.loc[out.index, "hire_date"])
    out["tenure_days"] = (pd.Timestamp(as_of) - hire).dt.days.to_numpy()
    out["as_of"] = as_of
    out = out.reset_index().rename(columns={"index": "employee_id"})
    return out[["employee_id", "as_of", *FEATURES]]


def snapshots(tables: dict, first: int, last: int) -> pd.DataFrame:
    """Feature rows at the start of each month index in [first, last)."""
    start = pd.Timestamp(tables["meta"]["start"].iloc[0]).date()
    frames = [behaviour_features(tables["advance_requests"], tables["employees"], _add_months(start, i)) for i in range(first, last)]
    df = pd.concat(frames, ignore_index=True)
    return df.merge(tables["employees"][["employee_id", "behaviour"]], on="employee_id", how="left")


@dataclass
class AbuseModel:
    forest: IsolationForest
    features: list[str]
    threshold: float  # anomaly score at the top `abuse_flag_top_pct` of training rows
    top_pct: float
    version: str

    def score(self, X: pd.DataFrame) -> np.ndarray:
        return -self.forest.score_samples(X[self.features])  # higher = more unusual


def precision_at_k(score: np.ndarray, labels: pd.Series, positive: set[str], k: int) -> float:
    top = np.argsort(-score)[:k]
    return round(float(labels.iloc[top].isin(positive).mean()), 4)


def _evaluate(model: AbuseModel, test: pd.DataFrame, chronic_streak: int) -> dict:
    s = model.score(test)
    baseline = test["requests_6m"].to_numpy(float) + 1e-3 * test["full_cap_share"].to_numpy(float)
    labels = test["behaviour"].reset_index(drop=True)
    flagged = s >= model.threshold
    chronic_rule = test["streak_months"].to_numpy() >= chronic_streak
    out = {
        "candidates": int(len(test)),
        "planted": {b: int((labels == b).sum()) for b in ("chronic", "abuser")},
        "flagged_by_model": int(flagged.sum()),
        "flagged_share": round(float(flagged.mean()), 4),
        "flagged_precision": round(float(labels[flagged].isin({"chronic", "abuser"}).mean()), 4) if flagged.any() else None,
        "abusers_flagged": int((flagged & (labels == "abuser")).sum()),
        "chronic_rule_flagged": int(chronic_rule.sum()),
        "chronic_rule_precision": round(float(labels[chronic_rule].isin({"chronic"}).mean()), 4) if chronic_rule.any() else None,
        "precision_at_k": [],
    }
    for k in K_VALUES:
        if k > len(test):
            continue
        out["precision_at_k"].append(
            {
                "k": k,
                "model_any": precision_at_k(s, labels, {"chronic", "abuser"}, k),
                "baseline_any": precision_at_k(baseline, labels, {"chronic", "abuser"}, k),
                "model_abuser": precision_at_k(s, labels, {"abuser"}, k),
                "baseline_abuser": precision_at_k(baseline, labels, {"abuser"}, k),
            }
        )
    return out


def train(seeds: tuple[int, ...] = TRAIN_SEEDS, scale: float = 1.0, artifacts_dir: Path = ARTIFACTS_DIR, policy: PolicyParams | None = None) -> dict:
    policy = policy or get_settings().policy
    profile = PROFILES["A"]
    test_start = profile.months - profile.test_months
    train_rows, test_by_world = [], {}
    for s in seeds:
        tables = generate("A", seed=s, scale=scale)
        train_rows.append(snapshots(tables, 6, test_start))
        test_by_world[f"A{s}"] = snapshots(tables, test_start, test_start + 1)
    fit = pd.concat(train_rows, ignore_index=True)
    forest = IsolationForest(n_estimators=300, max_samples=512, random_state=0).fit(fit[FEATURES])
    train_scores = -forest.score_samples(fit[FEATURES])
    threshold = float(np.quantile(train_scores, 1 - policy.abuse_flag_top_pct / 100))
    model = AbuseModel(forest, FEATURES, threshold, policy.abuse_flag_top_pct, VERSION)

    def pooled(frames: dict) -> dict:
        per_world = {w: _evaluate(model, df, policy.cooling_off_consecutive_months) for w, df in frames.items()}
        mean_p = []
        for i, k in enumerate(K_VALUES):
            vals = [w["precision_at_k"][i] for w in per_world.values() if len(w["precision_at_k"]) > i]
            if vals:
                mean_p.append({"k": k, **{key: round(float(np.mean([v[key] for v in vals])), 4) for key in ("model_any", "baseline_any", "model_abuser", "baseline_abuser")}})
        return {"mean_precision_at_k": mean_p, "per_world": per_world}

    test_b = {}
    for s in seeds:
        tables = generate("B", seed=s, scale=scale)
        test_b[f"B{s}"] = snapshots(tables, test_start, test_start + 1)

    metrics = {
        "version": VERSION,
        "method": "IsolationForest on request-history features; flag = top abuse_flag_top_pct of training scores",
        "features": FEATURES,
        "training": {"profile": "A", "seeds": list(seeds), "scale": scale, "rows": int(len(fit)), "snapshots": f"start of months 6..{test_start - 1}"},
        "threshold": round(threshold, 4),
        "top_pct": policy.abuse_flag_top_pct,
        "baseline": "rank by number of requests in the last 6 months (rule threshold)",
        "label_note": "precision is measured against the generator's planted chronic borrowers and abusers; labels are never features",
        "test_profile_a": pooled(test_by_world),
        "test_profile_b": pooled(test_b),
    }
    save_artifact(MODEL_NAME, model, artifacts_dir)
    update_metrics(MODEL_NAME, metrics, artifacts_dir)
    return metrics


_MODEL: AbuseModel | None = None


def load_model(artifacts_dir: Path = ARTIFACTS_DIR) -> AbuseModel:
    global _MODEL
    if artifacts_dir != ARTIFACTS_DIR:
        return load_artifact(MODEL_NAME, artifacts_dir)
    if _MODEL is None:
        _MODEL = load_artifact(MODEL_NAME, artifacts_dir)
    return _MODEL


def abuse_check(features: pd.DataFrame, policy: PolicyParams | None = None, model: AbuseModel | None = None) -> list[AbuseFlags]:
    """Flags for rows built by behaviour_features(). People with no recent requests get no row and no flag."""
    policy = policy or get_settings().policy
    model = model or load_model()
    scores = model.score(features)
    return [
        AbuseFlags(anomaly_score=round(float(s), 4), unusual_pattern=bool(s >= model.threshold), chronic=bool(streak >= policy.cooling_off_consecutive_months))
        for s, streak in zip(scores, features["streak_months"])
    ]


def _print(metrics: dict) -> None:
    for split in ("test_profile_a", "test_profile_b"):
        e = metrics[split]
        print(split)
        for row in e["mean_precision_at_k"]:
            print(f"  {row}")
        w = next(iter(e["per_world"].values()))
        print(f"  first world: {({k: v for k, v in w.items() if k != 'precision_at_k'})}")


if __name__ == "__main__":
    from app.ml import m5_abuse

    assert set(m5_abuse.FEATURES).isdisjoint(m5_abuse.FORBIDDEN_FEATURES)
    m5_abuse._print(m5_abuse.train())
