"""Policy simulation and validation report (plan.md §9). Every number in the report comes from this script.

Replays the test window (last 3 months) of five Profile A worlds and five Profile B worlds under:
- no product: nobody gets an advance (reference point);
- flat cap: every request the pilot rules approved gets its full amount (what history recorded);
- ML tier: M2 tier with the M3 downgrade; tier D goes to a person and is counted as NOT approved here.

Run:  .venv/Scripts/python.exe -m scripts.validate
Writes artifacts/validation.json and docs/validation_report.md.

Known limits (also written into the report):
- Outcomes exist only for advances the pilot rules approved; requests the rules declined have no outcome.
- A smaller ML offer's loss is scaled by offered/actual amount (losses are a whole or partial unpaid balance).
- M5 (unusual patterns) is not part of the replay: it only routes to a person and has no outcome to replay.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from app.config import BACKEND_DIR, get_settings
from app.ml import m1_employer as m1
from app.ml import m2_repayment as m2
from app.ml import m3_attrition as m3
from app.ml.common import ARTIFACTS_DIR, TRAIN_SEEDS, binary_metrics
from app.rules.tiers import tiered_amount
from data.generator import PROFILES, generate

REPORT = BACKEND_DIR.parent / "docs" / "validation_report.md"
OUT = ARTIFACTS_DIR / "validation.json"
SIZE_BINS = [0, 100, 250, 10**9]
SIZE_LABELS = ["small (<100)", "medium (100-249)", "large (250+)"]
STEPS = ["employer_remittance", "wallet_debit", "carry_over", "write_off"]
RISK_BANDS = [0, 0.1, 0.3, 1.01]
RISK_BAND_LABELS = ["employer risk < 10%", "10-30%", ">= 30%"]

# Analyst notes are written by a person after reading the numbers. They never contain numbers that are
# not printed from the data above them, and they are shown separately from the generated results.
ANALYST_NOTES: dict[str, str] = {
    "T2-A": (
        "The interval over-covers (it is wider than intended). The four calibration months of Profile A include synthetic "
        "Eid months (docs/assumptions.md), so the conformal widening is sized for an Eid surge, while the test months have "
        "no Eid. Effect: upay would hold more capital than needed: safe against shortfall, but it costs money (see the "
        "economics sliders). For real data: calibrate on a longer window or widen only for Eid months. Not tuned here, "
        "because tuning on the test result would make this check meaningless."
    ),
    "T3-A-employer_size": (
        "Explained. v2 removed employer headcount from M1, M2 and M3 (the v1 audit could not rule out a direct size effect). "
        "Inside the lowest employer-risk band, approval is the same for every size (table above). The remaining gap is "
        "driven by how many advances sit in the highest employer-risk band, and it now runs against large employers, whose "
        "flat-cap loss rate is also the highest (table above); staff of small employers are approved most often. So the gap "
        "follows observed employer payroll reliability, not size. Requests in the 10-30% employer-risk band, where groups "
        "are small, stay eligible for human review."
    ),
}


def _rate(num: float, den: float) -> float | None:
    return round(float(num) / float(den), 4) if den else None


def _world(profile: str, seed: int, m1_model, policy) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    tables = generate(profile, seed=seed)
    data = m3.build_dataset(tables, m1_model, policy.grace_days, world=f"{profile}{seed}")
    test = data[data["is_test"]].copy()
    size = pd.cut(tables["employers"].set_index("employer_id")["headcount"], SIZE_BINS, right=False, labels=SIZE_LABELS)
    test["employer_size"] = test["employer_id"].map(size).astype(str)

    test_start = pd.Timestamp(tables["meta"]["test_start"].iloc[0])
    req = tables["advance_requests"]
    req = req[pd.to_datetime(req["request_date"]) >= test_start].merge(tables["employees"][["employee_id", "gender", "region"]], on="employee_id")
    req["employer_size"] = req["employer_id"].map(size).astype(str)
    req["world"] = f"{profile}{seed}"

    reps = tables["repayments"]
    reps = reps[reps["advance_id"].isin(test["advance_id"])]
    # How long money stays out: issue date to the date the advance was settled (or written off).
    adv = tables["advances"].set_index("advance_id")
    days_out = (pd.to_datetime(adv["settled_date"]) - pd.to_datetime(adv["issue_date"])).dt.days
    test["days_out"] = test["advance_id"].map(days_out).astype(float)
    return test, req, reps


def _policies(test: pd.DataFrame, policy) -> pd.DataFrame:
    """Add ML-tier columns to the test advances."""
    p_fail = m2.load_model().predict_proba(test)
    p_leave = m3.load_model().predict_proba(test)
    tiers, _ = m3.combined_tiers(p_fail, p_leave, policy)
    test = test.assign(p_fail=p_fail, p_leave=p_leave, tier=[t.tier for t in tiers])
    offered = [tiered_amount(int(c), int(a), t, policy) for c, a, t in zip(test["hard_cap"], test["amount"], tiers)]
    test["ml_offered"] = offered
    test["ml_approved"] = test["ml_offered"] > 0
    scale = np.where(test["amount"] > 0, test["ml_offered"] / test["amount"], 0.0)
    test["ml_loss"] = test["loss_amount"] * scale
    test["ml_due"] = np.where(test["ml_approved"], test["ml_offered"] + test["fee"], 0)
    test["due"] = test["amount"] + test["fee"]
    test["queued"] = [t.needs_human for t in tiers]
    return test


def _policy_table(test: pd.DataFrame) -> dict:
    n = len(test)
    flat = {
        "approval_rate": 1.0,
        "funds_deployed_bdt": int(test["amount"].sum()),
        "loss_bdt": int(test["loss_amount"].sum()),
        "loss_rate": _rate(test["loss_amount"].sum(), test["due"].sum()),
    }
    ml = {
        "approval_rate": _rate(test["ml_approved"].sum(), n),
        "queued_for_human": int(test["queued"].sum()),
        "funds_deployed_bdt": int(test["ml_offered"].sum()),
        "loss_bdt": int(test["ml_loss"].sum()),
        "loss_rate": _rate(test["ml_loss"].sum(), test["ml_due"].sum()),
        "tier_counts": {t: int((test["tier"] == t).sum()) for t in "ABCD"},
    }
    # Equal approval: decline the same share as ML does, chosen by each ranking (full amounts kept).
    share = 1 - (ml["approval_rate"] or 0)

    def loss_after_declining(score: np.ndarray) -> float | None:
        keep = np.argsort(score)[: int(round(n * (1 - share)))]
        return _rate(test["loss_amount"].to_numpy()[keep].sum(), test["due"].to_numpy()[keep].sum())

    tenure_score = -test["tenure_days"].to_numpy(float)
    combined = 1 - (1 - test["p_fail"]) * (1 - test["p_leave"])
    equal = {
        "decline_share": round(float(share), 4),
        "flat_cap_random": flat["loss_rate"],  # a random subset has the same expected loss rate
        "tenure_rule": loss_after_declining(tenure_score),
        "m2_rank": loss_after_declining(test["p_fail"].to_numpy()),
        "m3_rank": loss_after_declining(test["p_leave"].to_numpy()),
        "m2_m3_combined_rank": loss_after_declining(combined.to_numpy()),
    }
    return {"n_advances": n, "no_product": {"approval_rate": 0.0, "funds_deployed_bdt": 0, "loss_bdt": 0, "loss_rate": None}, "flat_cap": flat, "ml_tier": ml, "equal_approval": equal}


def _economics_base(test: pd.DataFrame, world_months: int) -> dict:
    """Inputs for the economics sliders (plan.md §10). The page applies the formula; this only measures."""
    approved = test[test["ml_approved"]]
    return {
        "world_months": world_months,
        "advances_ml": int(len(approved)),
        "funds_ml_bdt": int(approved["ml_offered"].sum()),
        "loss_ml_bdt": int(test["ml_loss"].sum()),
        "advances_flat": int(len(test)),
        "funds_flat_bdt": int(test["amount"].sum()),
        "loss_flat_bdt": int(test["loss_amount"].sum()),
        "avg_days_outstanding": round(float(approved["days_out"].mean()), 2),
        "mean_monthly_funds_ml_bdt": int(approved["ml_offered"].sum() / world_months),
    }


def _recovery(reps: pd.DataFrame, test: pd.DataFrame) -> dict:
    due = float(test["due"].sum())
    by_step = reps.groupby("step")["amount"].sum()
    return {s: _rate(by_step.get(s, 0), due) for s in STEPS}


def _fairness(test: pd.DataFrame, req: pd.DataFrame, threshold_pp: float) -> dict:
    out = {}
    for col in ("gender", "region", "employer_size"):
        groups = {}
        for g, part in test.groupby(col):
            groups[str(g)] = {
                "advances": int(len(part)),
                "ml_approval_rate": _rate(part["ml_approved"].sum(), len(part)),
                "ml_mean_share_of_cap": round(float((part["ml_offered"] / part["hard_cap"].clip(lower=1)).mean()), 4),
                "flat_cap_loss_rate": _rate(part["loss_amount"].sum(), part["due"].sum()),
                "ml_loss_rate": _rate(part["ml_loss"].sum(), part["ml_due"].sum()),
            }
        for g, part in req.groupby(col):
            groups.setdefault(str(g), {})["rule_approval_rate"] = _rate((part["status"] == "approved").sum(), len(part))
        approvals = [v["ml_approval_rate"] for v in groups.values() if v.get("ml_approval_rate") is not None]
        rule_approvals = [v["rule_approval_rate"] for v in groups.values() if v.get("rule_approval_rate") is not None]
        gap = round((max(approvals) - min(approvals)) * 100, 2) if approvals else None
        rule_gap = round((max(rule_approvals) - min(rule_approvals)) * 100, 2) if rule_approvals else None
        out[col] = {"groups": groups, "ml_approval_gap_pp": gap, "rule_approval_gap_pp": rule_gap, "threshold_pp": threshold_pp, "within_threshold": gap is not None and gap <= threshold_pp}
    # Decomposition: ML approval by group within bands of the employer's payroll risk (M1 at decision time,
    # an observable input), to see whether a gap follows employer payroll reliability or the group itself.
    band = pd.cut(test["employer_prob_late"], RISK_BANDS, right=False, labels=RISK_BAND_LABELS)
    for col in ("employer_size",):
        table = test.assign(band=band).pivot_table(index="band", columns=col, values="ml_approved", aggfunc=["mean", "size"], observed=False)
        out[col]["by_employer_risk_band"] = {
            str(b): {str(g): {"ml_approval_rate": None if pd.isna(table[("mean", g)][b]) else round(float(table[("mean", g)][b]), 4), "advances": int(table[("size", g)][b])} for g in table["mean"].columns}
            for b in table.index
        }
    return out


def _calibration(test: pd.DataFrame) -> dict:
    return {
        "m2_repayment_failure": binary_metrics(test["m2_target"], test["p_fail"]),
        "m3_leaves_before_payday": binary_metrics(test["target"], test["p_leave"]),
    }


def evaluate_profile(profile: str, seeds, m1_model, policy) -> dict:
    tests, reqs, reps = [], [], []
    for s in seeds:
        t, r, p = _world(profile, s, m1_model, policy)
        tests.append(t)
        reqs.append(r)
        reps.append(p)
    test = _policies(pd.concat(tests, ignore_index=True), policy)
    req = pd.concat(reqs, ignore_index=True)
    rep = pd.concat(reps, ignore_index=True)
    return {
        "worlds": [f"{profile}{s}" for s in seeds],
        "policies": _policy_table(test),
        "economics_base": _economics_base(test, world_months=len(seeds) * PROFILES[profile].test_months),
        "recovery_share_by_step_flat_cap": _recovery(rep, test),
        "fairness": _fairness(test, req, policy.fairness_gap_threshold_pp),
        "calibration": _calibration(test),
    }


def targets(result: dict, metrics: dict) -> list[dict]:
    out = []
    for profile in ("profile_a", "profile_b"):
        pol = result[profile]["policies"]
        ml, flat = pol["ml_tier"]["loss_rate"], pol["flat_cap"]["loss_rate"]
        out.append(
            {
                "id": f"T1-{profile[-1].upper()}",
                "target": "ML-tier loss rate < flat-cap loss rate at an equal approval rate",
                "value": f"ML {ml} vs flat cap {flat} (ML approves {pol['ml_tier']['approval_rate']})",
                "pass": ml is not None and flat is not None and ml < flat,
            }
        )
    m4 = metrics.get("m4_capital", {})
    for split in ("test_profile_a", "test_profile_b"):
        cov = m4.get(split, {}).get("world", {}).get("model", {}).get("coverage_p10_p90")
        out.append(
            {
                "id": f"T2-{split[-1].upper()}",
                "target": "M4 P10–P90 coverage between 75% and 85% (total pool)",
                "value": f"{cov}",
                "pass": cov is not None and 0.75 <= cov <= 0.85,
            }
        )
    for profile in ("profile_a", "profile_b"):
        for col, f in result[profile]["fairness"].items():
            key = f"T3-{profile[-1].upper()}-{col}"
            out.append(
                {
                    "id": key,
                    "target": f"ML approval-rate gap across {col} <= {f['threshold_pp']} pp (or explained)",
                    "value": f"{f['ml_approval_gap_pp']} pp",
                    "pass": bool(f["within_threshold"]),
                }
            )
    for t in out:
        t["explained"] = (not t["pass"]) and t["id"] in ANALYST_NOTES
    return out


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.2%}" if abs(v) <= 1 else f"{v:,.0f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def render(result: dict) -> str:
    lines = [
        "# Validation Report",
        "",
        f"Generated by `python -m scripts.validate` on synthetic data (seed list {result['seeds']}). Do not edit by hand.",
        "Test window = the last 3 months of every world; models never trained on it. Profile B is a harsher world the models never saw "
        "(for M4 it is not fully unseen: its v1 weakness was found on Profile B, see metrics.json).",
        "",
        "## Targets (declared in plan.md §9 before these numbers existed)",
        "",
        "| ID | Target | Value | Result |",
        "|---|---|---|---|",
    ]
    for t in result["targets"]:
        verdict = "PASS" if t["pass"] else ("FAIL (explained below)" if t.get("explained") else "FAIL")
        lines.append(f"| {t['id']} | {t['target']} | {t['value']} | **{verdict}** |")

    for name, key in (("Profile A (training world, unseen months)", "profile_a"), ("Profile B (stress world)", "profile_b")):
        r = result[key]
        pol = r["policies"]
        lines += ["", f"## {name}", "", f"{pol['n_advances']:,} advances in the test window across {len(r['worlds'])} worlds.", ""]
        lines += ["### Policies", "", "| Policy | Approval rate | Funds deployed (BDT) | Loss (BDT) | Loss rate |", "|---|---|---|---|---|"]
        for label, p in (("No product", pol["no_product"]), ("Flat cap (history)", pol["flat_cap"]), ("ML tier (tier D → person, counted as not approved)", pol["ml_tier"])):
            lines.append(f"| {label} | {_fmt(p['approval_rate'])} | {_fmt(p['funds_deployed_bdt'])} | {_fmt(p['loss_bdt'])} | {_fmt(p['loss_rate'])} |")
        tc = pol["ml_tier"]["tier_counts"]
        lines += ["", f"ML tiers: A {tc['A']:,} · B {tc['B']:,} · C {tc['C']:,} · D {tc['D']:,} (to a person: {pol['ml_tier']['queued_for_human']:,}).", ""]
        eq = pol["equal_approval"]
        lines += [
            f"### Who to decline, at an equal approval rate (decline {eq['decline_share']:.1%}, full amounts)",
            "",
            "| Ranking used to decline | Loss rate of the approved |",
            "|---|---|",
        ]
        for label, k in (("Random (= flat cap)", "flat_cap_random"), ("Tenure rule", "tenure_rule"), ("M2 repayment risk", "m2_rank"), ("M3 leaving risk", "m3_rank"), ("M2 + M3 combined", "m2_m3_combined_rank")):
            lines.append(f"| {label} | {_fmt(eq[k])} |")
        lines += ["", "### Recovery by waterfall step (flat cap, share of money due)", "", "| Step | Share |", "|---|---|"]
        for s, v in r["recovery_share_by_step_flat_cap"].items():
            lines.append(f"| {s} | {_fmt(v)} |")
        lines += ["", "### Calibration", "", "| Model | n | Positive rate | PR-AUC | Brier |", "|---|---|---|---|---|"]
        for label, m in r["calibration"].items():
            lines.append(f"| {label} | {m['n']:,} | {_fmt(m['positive_rate'])} | {m['pr_auc']} | {m['brier']} |")
        lines += ["", "Calibration bins (mean predicted → observed):", ""]
        for label, m in r["calibration"].items():
            bins = ", ".join(f"{b['mean_predicted']:.3f}→{b['observed_rate']:.3f}" for b in m["calibration"])
            lines.append(f"- {label}: {bins}")
        lines += ["", "### Fairness (audit groups are never model features)", ""]
        for col, f in r["fairness"].items():
            lines += [
                f"**{col}** — ML approval gap {f['ml_approval_gap_pp']} pp (threshold {f['threshold_pp']} pp), pilot-rule approval gap {f['rule_approval_gap_pp']} pp",
                "",
                "| Group | Advances | Rule approval | ML approval | ML mean share of cap | Loss rate flat → ML |",
                "|---|---|---|---|---|---|",
            ]
            for g, v in f["groups"].items():
                lines.append(
                    f"| {g} | {_fmt(v.get('advances'))} | {_fmt(v.get('rule_approval_rate'))} | {_fmt(v.get('ml_approval_rate'))} | {_fmt(v.get('ml_mean_share_of_cap'))} | {_fmt(v.get('flat_cap_loss_rate'))} → {_fmt(v.get('ml_loss_rate'))} |"
                )
            lines.append("")
            if "by_employer_risk_band" in f:
                groups = list(next(iter(f["by_employer_risk_band"].values())))
                lines += [f"ML approval by {col}, within bands of employer payroll risk (M1 at decision time):", "", "| Employer risk band | " + " | ".join(groups) + " |", "|---|" + "---|" * len(groups)]
                for band_label, cells in f["by_employer_risk_band"].items():
                    lines.append(f"| {band_label} | " + " | ".join(f"{_fmt(c['ml_approval_rate'])} (n={c['advances']:,})" for c in cells.values()) + " |")
                lines.append("")
    m4 = result["m4"]
    lines += [
        "## Capital pool (M4)",
        "",
        "| Split | P10–P90 coverage (total) | Pool (P90 + buffer) covered actual month | MAE P50 model vs previous month (BDT) |",
        "|---|---|---|---|",
    ]
    for split in ("test_profile_a", "test_profile_b"):
        w = m4.get(split, {}).get("world", {})
        lines.append(
            f"| {split} | {_fmt(w.get('model', {}).get('coverage_p10_p90'))} | {_fmt(w.get('pool_covers_actual_share'))} | "
            f"{_fmt(w.get('model', {}).get('mae_p50_bdt'))} vs {_fmt(w.get('previous_month', {}).get('mae_p50_bdt'))} |"
        )
    lines += [
        "",
        "## Known limits",
        "",
        "- All data is synthetic; these results show the method works on its own assumptions, not on real upay data.",
        "- Outcomes exist only for advances the pilot rules approved.",
        "- A smaller ML offer's loss is scaled by offered/actual amount.",
        "- M5 (unusual patterns) is not part of the replay; it only sends cases to a person.",
        "- Tier D requests are counted as not approved; in the product a person may approve them.",
        "",
    ]
    if ANALYST_NOTES:
        lines += ["## Analyst notes (written by a person after reading the numbers above)", ""]
        for key, note in ANALYST_NOTES.items():
            lines.append(f"- **{key}**: {note}")
        lines.append("")
    return "\n".join(lines)


def main(seeds: tuple[int, ...] = TRAIN_SEEDS, out: Path = OUT, report: Path = REPORT) -> dict:
    settings = get_settings()
    policy = settings.policy
    m1_model = m1.load_model()
    metrics = json.loads((ARTIFACTS_DIR / "metrics.json").read_text(encoding="utf-8"))
    result = {
        "generated_on": date.today().isoformat(),
        "seeds": list(seeds),
        "profile_a": evaluate_profile("A", seeds, m1_model, policy),
        "profile_b": evaluate_profile("B", seeds, m1_model, policy),
        "m1": {k: metrics["m1_employer"][k] for k in ("test_profile_a", "test_profile_b")},
        "m4": {k: metrics["m4_capital"][k] for k in ("test_profile_a", "test_profile_b")},
        "analyst_notes": ANALYST_NOTES,
        "limits": [
            "synthetic data only",
            "outcomes only for pilot-approved advances",
            "smaller offers' losses scaled by amount",
            "M5 not replayed",
            "tier D counted as not approved",
        ],
    }
    result["targets"] = targets(result, metrics)
    out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    report.write_text(render(result), encoding="utf-8")
    return result


if __name__ == "__main__":
    out = main()
    for t in out["targets"]:
        print(f"{t['id']:<22} {'PASS' if t['pass'] else 'FAIL'}  {t['value']}")
    print(f"wrote {OUT} and {REPORT}")
