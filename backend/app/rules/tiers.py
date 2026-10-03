"""Map an ML risk probability to a tier. The tier only shrinks the rule-based hard cap."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import PolicyParams

TIERS = ("A", "B", "C", "D")


@dataclass(frozen=True)
class TierDecision:
    tier: str
    share_of_cap: float  # 0 for tier D: no automatic offer
    needs_human: bool
    risk: float


def tier_for(risk: float, policy: PolicyParams) -> TierDecision:
    if risk <= policy.tier_a_max_risk:
        return TierDecision("A", policy.tier_a_share_of_cap, False, risk)
    if risk <= policy.tier_b_max_risk:
        return TierDecision("B", policy.tier_b_share_of_cap, False, risk)
    if risk <= policy.tier_c_max_risk:
        return TierDecision("C", policy.tier_c_share_of_cap, False, risk)
    return TierDecision("D", 0.0, True, risk)


def apply_attrition(decision: TierDecision, prob_leave: float, policy: PolicyParams) -> tuple[TierDecision, bool]:
    """Drop one tier when the person is likely to leave before payday. Returns (decision, downgraded)."""
    if prob_leave < policy.attrition_downgrade_threshold or decision.tier == "D":
        return decision, False
    shares = {"A": policy.tier_a_share_of_cap, "B": policy.tier_b_share_of_cap, "C": policy.tier_c_share_of_cap, "D": 0.0}
    lower = TIERS[TIERS.index(decision.tier) + 1]
    return TierDecision(lower, shares[lower], lower == "D", decision.risk), True


def tiered_amount(hard_cap_bdt: int, requested_bdt: int, decision: TierDecision, policy: PolicyParams) -> int:
    """Amount offered automatically: min(request, share x hard cap) rounded down to 100; 0 if under the minimum."""
    if decision.needs_human:
        return 0
    limit = int(hard_cap_bdt * decision.share_of_cap) // 100 * 100
    amount = min(requested_bdt, limit)
    return amount if amount >= policy.min_advance_bdt else 0
