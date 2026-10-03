from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_VERSION = "0.1.0"
BACKEND_DIR = Path(__file__).resolve().parents[1]


class PolicyParams(BaseModel):
    """Policy numbers. Every value is an [ASSUMPTION], documented in docs/assumptions.md.

    Money is in whole BDT here; the ledger converts to integer paisa.
    """

    # Limits
    cap_pct_of_salary: float = 20.0
    min_advance_bdt: int = 500
    min_tenure_days: int = 90
    max_advances_per_month: int = 2

    # Fees
    fee_flat_bdt: int = 25
    fee_pct_of_amount: float = 0.0

    # Repayment and recovery
    grace_days: int = 5
    carry_over_limit_reduction_pct: float = 50.0
    writeoff_after_days: int = 60

    # Risk tiers (M2 probability of repayment failure); above tier C goes to tier D
    tier_a_max_risk: float = 0.05
    tier_b_max_risk: float = 0.12
    tier_c_max_risk: float = 0.25
    tier_a_share_of_cap: float = 1.0
    tier_b_share_of_cap: float = 0.7
    tier_c_share_of_cap: float = 0.4
    attrition_downgrade_threshold: float = 0.30

    # Debt-trap guard
    cooling_off_consecutive_months: int = 3
    cooling_off_months: int = 1

    # Human oversight and fairness
    large_amount_threshold_bdt: int = 10_000
    abuse_flag_top_pct: float = 2.0
    fairness_gap_threshold_pp: float = 5.0

    # Capital and economics
    initial_pool_bdt: int = 5_000_000
    capital_buffer_pct: float = 15.0
    capital_rate_annual_pct: float = 12.0
    ops_cost_per_advance_bdt: int = 5


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_nested_delimiter="__", extra="ignore")

    seed: int = 42
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'seed.db').as_posix()}"
    cors_origins: str = "http://localhost:3000"
    anthropic_api_key: str | None = None
    llm_model: str = "claude-opus-5-5"
    llm_timeout_s: float = 20.0
    policy: PolicyParams = PolicyParams()

    # Simulation sessions (one SQLite file per browser session)
    sim_dir: str | None = None  # default: <system temp>/salarybridge_sessions
    session_ttl_hours: float = 6.0
    sim_start_day: int = 20  # the live simulation opens on this day of the first month after history

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
