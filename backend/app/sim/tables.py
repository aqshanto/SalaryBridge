"""Tables in each simulation session's SQLite file."""

from sqlalchemy import Boolean, Column, Date, Float, Integer, MetaData, String, Table

metadata = MetaData()

sim_meta = Table(
    "sim_meta",
    metadata,
    Column("key", String(40), primary_key=True),
    Column("value", String(200), nullable=False),
)

sim_payroll_runs = Table(
    "sim_payroll_runs",
    metadata,
    Column("employer_id", String(10), primary_key=True),
    Column("work_month", String(7), primary_key=True),
    Column("scheduled_date", Date, nullable=False),
    Column("actual_date", Date),
    Column("status", String(10), nullable=False),
    Column("delay_days", Integer, nullable=False),
    Column("paid_share", Float, nullable=False),
    Column("paid", Boolean, nullable=False, default=False),
)

sim_closed_employers = Table(
    "sim_closed_employers",
    metadata,
    Column("employer_id", String(10), primary_key=True),
    Column("closed_on", Date, nullable=False),
)

# Forced payroll outcome for a scenario (F15): used instead of the random draw for that run.
sim_overrides = Table(
    "sim_overrides",
    metadata,
    Column("employer_id", String(10), primary_key=True),
    Column("work_month", String(7), primary_key=True),
    Column("status", String(10), nullable=False),
    Column("delay_days", Integer, nullable=False),
    Column("paid_share", Float, nullable=False),
)

# People who left during the session.
sim_resignations = Table(
    "sim_resignations",
    metadata,
    Column("employee_id", String(30), primary_key=True),
    Column("end_date", Date, nullable=False),
    Column("reason", String(30), nullable=False),
)

# Advances paid out in this session.
sim_advances = Table(
    "sim_advances",
    metadata,
    Column("advance_id", String(20), primary_key=True),
    Column("decision_id", String(20), nullable=False),
    Column("employee_id", String(30), nullable=False, index=True),
    Column("employer_id", String(10), nullable=False),
    Column("work_month", String(7), nullable=False),
    Column("issue_date", Date, nullable=False),
    Column("amount_requested", Integer, nullable=False),
    Column("hard_cap", Integer, nullable=False),
    Column("amount", Integer, nullable=False),
    Column("fee", Integer, nullable=False),
    Column("due_date", Date, nullable=False),
    Column("grace_end", Date, nullable=False),
    Column("tier", String(1), nullable=False),
    # open | carried_over | pending_write_off | recovered | written_off
    Column("status", String(20), nullable=False),
    Column("recovered_by_grace", Boolean),
    Column("settled_date", Date),
)

# Every collection against an advance, by recovery step. write_off rows record the unpaid balance.
sim_repayments = Table(
    "sim_repayments",
    metadata,
    Column("repayment_id", Integer, primary_key=True, autoincrement=True),
    Column("advance_id", String(20), nullable=False, index=True),
    Column("date", Date, nullable=False),
    Column("amount", Integer, nullable=False),
    Column("step", String(20), nullable=False),  # employer_remittance | wallet_debit | carry_over | write_off
)

# Every offer decision, with its inputs and model versions (audit log).
sim_decisions = Table(
    "sim_decisions",
    metadata,
    Column("decision_id", String(20), primary_key=True),
    Column("sim_date", Date, nullable=False),
    Column("employee_id", String(30), nullable=False, index=True),
    Column("requested_bdt", Integer, nullable=False),
    Column("status", String(10), nullable=False),
    Column("approved_bdt", Integer, nullable=False),
    Column("payload", String, nullable=False),  # JSON: inputs, features, outputs, model versions
)

# What happened to a decision afterwards: accepted by the employee, or approved/rejected by ops.
sim_reviews = Table(
    "sim_reviews",
    metadata,
    Column("decision_id", String(20), primary_key=True),
    Column("action", String(10), nullable=False),  # accepted | approved | rejected
    Column("actor", String(20), nullable=False),  # employee | ops
    Column("note", String(500)),
    Column("sim_date", Date, nullable=False),
    Column("amount_bdt", Integer, nullable=False),
    Column("advance_id", String(20)),
)
