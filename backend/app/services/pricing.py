"""Pricing engine: monthly unit economics of different ways to share the cost of an advance.

Phase 1 showed a loss at the ৳25 flat worker fee (break-even ৳49-60). Raising the worker fee alone
would move the whole cost onto low-paid workers, so this module compares revenue mixes:

- worker fee: flat, or tiered by advance size (small advances stay cheap);
- employer co-pay per advance (an employee benefit, like a salary-advance perk);
- payroll-linked fee per employee per month (PEPM) for salaries the employer disburses through upay.

Costs use the same formula as the Validation page sliders (plan.md §10). Every price here is an
[ASSUMPTION]; uptake is assumed unchanged across scenarios. The live product fee is not changed by this module.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

# Advance-size bands for the tiered worker fee: (upper bound inclusive in BDT, key).
AMOUNT_BANDS = ((2_000, "upto_2000"), (5_000, "upto_5000"), (10**9, "above_5000"))


@dataclass(frozen=True)
class Scenario:
    key: str
    worker_fee: dict  # band key -> BDT per advance
    employer_copay_bdt: float = 0.0  # per approved advance
    payroll_fee_pepm_bdt: float = 0.0  # per salary disbursed through upay, per month
    recommended: bool = False


def flat(fee: float) -> dict:
    return {key: fee for _, key in AMOUNT_BANDS}


TIERED = {"upto_2000": 15, "upto_5000": 25, "above_5000": 40}
# Worker-only slab sized to break even in Profile A without employer money (shows why fees alone are not enough).
WORKER_ONLY_SLAB = {"upto_2000": 30, "upto_5000": 50, "above_5000": 70}

SCENARIOS = (
    Scenario("current_flat_25", flat(25)),
    Scenario("worker_pays_break_even", flat(0)),  # filled in with the break-even fee at evaluation time
    Scenario("tiered_worker_fee", TIERED),
    Scenario("employer_copay_25", flat(25), employer_copay_bdt=25),
    Scenario("payroll_fee_10_pepm", flat(25), payroll_fee_pepm_bdt=10),
    Scenario("worker_only_slab", WORKER_ONLY_SLAB),
    Scenario("recommended_mix", TIERED, employer_copay_bdt=20, payroll_fee_pepm_bdt=10, recommended=True),
)


def monthly_costs(base: dict, policy) -> dict:
    """Costs per sandbox month from the measured economics base and the policy assumptions."""
    m = base["world_months"]
    advances = base["advances_ml"] / m
    funds = base["funds_ml_bdt"] / m
    rate = policy.capital_rate_annual_pct / 100
    out = {
        "advances": advances,
        "funds": funds,
        "loss": base["loss_ml_bdt"] / m,
        "capital": funds * (base["avg_days_outstanding"] / 365) * rate,
        "idle": funds * (policy.capital_buffer_pct / 100) * (30 / 365) * rate,
        "ops": advances * policy.ops_cost_per_advance_bdt,
    }
    out["total"] = out["loss"] + out["capital"] + out["idle"] + out["ops"]
    return out


def evaluate(base: dict, policy, scenario: Scenario) -> dict:
    costs = monthly_costs(base, policy)
    m = base["world_months"]
    bands = {k: v / m for k, v in base["advances_by_band"].items()}
    advances = costs["advances"]
    worker_fee = scenario.worker_fee
    if scenario.key == "worker_pays_break_even":
        worker_fee = flat(math.ceil(costs["total"] / advances) if advances else 0)
    worker_rev = sum(bands.get(k, 0) * fee for k, fee in worker_fee.items())
    employer_rev = advances * scenario.employer_copay_bdt
    payroll_rev = base["upay_payroll_employees"] * scenario.payroll_fee_pepm_bdt
    revenue = worker_rev + employer_rev + payroll_rev
    net = revenue - costs["total"]
    # Employer co-pay per advance that would break even with this scenario's worker fee and payroll fee.
    copay_needed = max(0.0, (costs["total"] - worker_rev - payroll_rev) / advances) if advances else 0.0
    return {
        **asdict(scenario),
        "worker_fee": worker_fee,
        "avg_worker_fee_bdt": round(worker_rev / advances, 2) if advances else 0.0,
        "revenue": {"worker": round(worker_rev), "employer_copay": round(employer_rev), "payroll_fee": round(payroll_rev), "total": round(revenue)},
        "costs": {k: round(v) for k, v in costs.items() if k not in ("advances", "funds")},
        "net_bdt_per_month": round(net),
        "viable": net >= 0,
        "copay_needed_to_break_even_bdt": math.ceil(copay_needed),
    }


def scenario_table(base: dict, policy) -> dict:
    costs = monthly_costs(base, policy)
    return {
        "advances_per_month": round(costs["advances"], 1),
        "upay_payroll_employees": base["upay_payroll_employees"],
        "break_even_worker_fee_bdt": math.ceil(costs["total"] / costs["advances"]) if costs["advances"] else 0,
        "scenarios": [evaluate(base, policy, s) for s in SCENARIOS],
        "assumptions": "Prices are [ASSUMPTION]; uptake is assumed equal in every scenario; costs follow plan.md §10.",
    }


def band_of(amount: float) -> str:
    for upper, key in AMOUNT_BANDS:
        if amount <= upper:
            return key
    return AMOUNT_BANDS[-1][1]
