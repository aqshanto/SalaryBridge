import time

import pandas as pd
import pytest
from sqlalchemy import create_engine, inspect

from data.generator import PROFILES, generate
from data.seed import ensure_seed_db, write_seed_db

TABLES = {
    "employers",
    "employees",
    "payroll_runs",
    "attendance",
    "advance_requests",
    "advances",
    "repayments",
    "resignations",
    "meta",
}


@pytest.fixture(scope="module")
def world():
    start = time.perf_counter()
    tables = generate("A", seed=42)
    tables["_seconds"] = time.perf_counter() - start
    return tables


def test_same_seed_gives_identical_tables():
    a, b = generate("A", seed=7, scale=0.1), generate("A", seed=7, scale=0.1)
    for name in TABLES:
        pd.testing.assert_frame_equal(a[name], b[name])


def test_different_seed_gives_different_world():
    a, b = generate("A", seed=7, scale=0.1), generate("A", seed=8, scale=0.1)
    assert not a["employees"]["salary_bdt"].equals(b["employees"]["salary_bdt"])


def test_generation_is_fast_and_sized_as_planned(world):
    p = PROFILES["A"]
    assert world["_seconds"] < 60
    assert set(world) - {"_seconds"} == TABLES
    assert len(world["employers"]) == p.n_employers
    assert world["employers"]["headcount"].sum() == p.n_employees
    assert world["payroll_runs"]["month_index"].max() < p.months


def test_every_employer_type_present(world):
    assert set(world["employers"]["reliability_type"]) == {"on_time", "sometimes_late", "often_late", "default_risk"}


def test_requests_surge_at_month_end(world):
    day = pd.to_datetime(world["advance_requests"]["request_date"]).dt.day
    late_rate = (day >= 25).sum() / day[day >= 25].nunique()
    early_rate = (day <= 5).sum() / day[day <= 5].nunique()
    assert late_rate > 2 * early_rate


def test_often_late_employers_are_late_more_often(world):
    runs = world["payroll_runs"].merge(world["employers"][["employer_id", "reliability_type"]], on="employer_id")
    late = runs["status"].isin(["late", "partial"])
    by_type = late.groupby(runs["reliability_type"]).mean()
    assert by_type["often_late"] > by_type["sometimes_late"] > by_type["on_time"]


def test_lateness_persists(world):
    runs = world["payroll_runs"].sort_values(["employer_id", "month_index"]).copy()
    runs["late"] = runs["status"].isin(["late", "partial"])
    runs["prev_late"] = runs.groupby("employer_id")["late"].shift()
    runs = runs.dropna(subset=["prev_late"])
    runs = runs[runs["employer_id"].isin(runs.loc[runs["late"], "employer_id"].unique())]
    assert runs.loc[runs["prev_late"].astype(bool), "late"].mean() > runs.loc[~runs["prev_late"].astype(bool), "late"].mean()


def test_eid_months_have_more_requests_per_active_employee(world):
    eid = {f"{y}-{m:02d}" for y, m in PROFILES["A"].eid_months}
    req = world["advance_requests"].groupby("work_month").size()
    active = world["attendance"].groupby("work_month").size()
    rate = req / active
    assert rate[rate.index.isin(eid)].mean() > 1.3 * rate[~rate.index.isin(eid)].mean()


def test_chronic_borrowers_request_more_months(world):
    months = world["advance_requests"].groupby("employee_id")["work_month"].nunique()
    active = world["attendance"].groupby("employee_id").size()
    share = (months.reindex(active.index, fill_value=0) / active).rename("share")
    behaviour = world["employees"].set_index("employee_id")["behaviour"]
    by_group = share.groupby(behaviour.reindex(share.index)).mean()
    assert by_group["chronic"] > 5 * by_group["normal"]


def test_short_tenure_resigns_more(world):
    emp = world["employees"]
    voluntary = emp["end_reason"].eq("voluntary")
    short = (pd.to_datetime(emp["hire_date"]) > pd.Timestamp(PROFILES["A"].start) - pd.Timedelta(days=365))
    assert voluntary[short].mean() > voluntary[~short].mean()


def test_losses_come_only_from_modelled_causes(world):
    adv = world["advances"]
    lost = adv[adv["loss_amount"] > 0]
    assert len(lost) > 0
    assert set(lost["loss_cause"]) <= {"employer_default", "resigned_before_payday", "partial_payroll", "carry_over_failed"}
    assert (adv["recovered_amount"] + adv["loss_amount"] == adv["amount"] + adv["fee"]).all()


def test_repayments_add_up_per_advance(world):
    paid = world["repayments"].groupby("advance_id")["amount"].sum()
    adv = world["advances"].set_index("advance_id")
    assert (paid.reindex(adv.index) == adv["amount"] + adv["fee"]).all()


def test_approved_advances_respect_policy_caps(world):
    adv = world["advances"].merge(world["employees"][["employee_id", "salary_bdt"]], on="employee_id")
    assert (adv["amount"] <= 0.20 * adv["salary_bdt"] + 1e-9).all()
    assert (adv["amount"] >= 500).all()


def test_audit_attributes_exist(world):
    emp = world["employees"]
    assert set(emp["gender"]) == {"female", "male"}
    assert set(emp["region"]) == {"rural", "urban"}


def test_seed_db_written_and_rebuilt_only_when_missing(tmp_path):
    url = f"sqlite:///{(tmp_path / 'seed.db').as_posix()}"
    counts = write_seed_db(url, seed=3, scale=0.05)
    engine = create_engine(url)
    assert set(inspect(engine).get_table_names()) == TABLES
    engine.dispose()
    assert counts["employers"] >= 2
    assert ensure_seed_db(url, seed=3, scale=0.05) is False

    missing = f"sqlite:///{(tmp_path / 'sub' / 'new.db').as_posix()}"
    assert ensure_seed_db(missing, seed=3, scale=0.05) is True
    assert (tmp_path / "sub" / "new.db").exists()
