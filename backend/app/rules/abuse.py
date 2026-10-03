"""Route M5 behaviour flags. A flag never declines anyone by itself: it sends the request to a person."""

from __future__ import annotations

from dataclasses import dataclass, replace

from app.rules.tiers import TierDecision


@dataclass(frozen=True)
class AbuseFlags:
    anomaly_score: float
    unusual_pattern: bool  # M5 score in the top `abuse_flag_top_pct` of training scores
    chronic: bool  # advances in `cooling_off_consecutive_months` months in a row (the eligibility rule pauses these)


def route(decision: TierDecision, flags: AbuseFlags) -> tuple[TierDecision, list[str]]:
    """Return the decision after flags, plus reason codes for the trace."""
    codes = []
    if flags.chronic:
        codes.append("CHRONIC_BORROWING")
    if flags.unusual_pattern:
        codes.append("UNUSUAL_BORROWING_PATTERN")
        decision = replace(decision, needs_human=True)
    return decision, codes
