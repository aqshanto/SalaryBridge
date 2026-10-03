import ast
from datetime import date
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import create_engine, insert
from sqlalchemy.pool import StaticPool

from app.ledger import Ledger, LedgerError, bdt_to_paisa, paisa_to_bdt
from app.ledger.ledger import lines

D = date(2026, 9, 25)


@pytest.fixture
def ledger():
    # In-memory: these tests check ledger logic, not disk speed (one shared connection keeps the data).
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    yield Ledger(engine)
    engine.dispose()


def test_balanced_entry_moves_money(ledger):
    ledger.post_entry(D, "capital", [("upay_pool", bdt_to_paisa(100_000)), ("upay_capital", -bdt_to_paisa(100_000))])
    ledger.post_entry(
        D,
        "advance_payout",
        [("employee_wallet:E001-0001-00001", bdt_to_paisa(5_000)), ("upay_pool", -bdt_to_paisa(5_000))],
        ref="A000001",
    )
    assert ledger.balance("upay_pool") == bdt_to_paisa(95_000)
    assert ledger.balance("employee_wallet:E001-0001-00001") == 500_000
    assert ledger.reconcile().ok


@pytest.mark.parametrize(
    "entry_lines, message",
    [
        ([("upay_pool", 100), ("upay_capital", -99)], "does not balance"),
        ([("upay_pool", 0), ("upay_capital", 0)], "Zero amount"),
        ([("upay_pool", 100)], "at least two"),
        ([("upay_pool", 100.0), ("upay_capital", -100)], "int paisa"),
        ([("upay_pool", True), ("upay_capital", -1)], "int paisa"),
        ([("somebody_else", 100), ("upay_capital", -100)], "Unknown account"),
        ([("employer:E001; drop", 100), ("upay_capital", -100)], "Unknown account"),
    ],
)
def test_invalid_entries_are_rejected_and_nothing_is_written(ledger, entry_lines, message):
    with pytest.raises(LedgerError, match=message):
        ledger.post_entry(D, "bad", entry_lines)
    assert ledger.reconcile().entries == 0


def test_reconcile_after_1000_random_entries(ledger):
    rng = np.random.default_rng(0)
    accounts = ["upay_pool", "upay_capital", "fees_income", "loss_provision"] + [
        f"employee_wallet:W{i}" for i in range(20)
    ] + [f"employer:E{i:03d}" for i in range(5)]
    for i in range(1000):
        n = int(rng.integers(2, 5))
        chosen = rng.choice(accounts, size=n, replace=False)
        amounts = [int(rng.integers(1, 1_000_000)) * int(rng.choice([-1, 1])) for _ in range(n - 1)]
        if sum(amounts) == 0:
            amounts[0] += 1
        amounts.append(-sum(amounts))
        ledger.post_entry(D, "random", list(zip(chosen.tolist(), amounts)), ref=f"R{i}")
    result = ledger.reconcile()
    assert result.ok and result.entries == 1000 and result.total_paisa == 0
    assert sum(ledger.balances().values()) == 0


def test_reconcile_detects_tampering(ledger):
    entry_id = ledger.post_entry(D, "capital", [("upay_pool", 100), ("upay_capital", -100)])
    with ledger.engine.begin() as conn:
        conn.execute(insert(lines).values(entry_id=entry_id, account="upay_pool", amount_paisa=1))
    result = ledger.reconcile()
    assert not result.ok and result.unbalanced_entry_ids == [entry_id]


def test_balances_by_prefix(ledger):
    ledger.post_entry(D, "x", [("employee_wallet:A", 300), ("employee_wallet:B", 200), ("upay_pool", -500)])
    assert ledger.balances("employee_wallet:") == {"employee_wallet:A": 300, "employee_wallet:B": 200}


def test_money_conversion():
    assert bdt_to_paisa(25) == 2_500
    assert bdt_to_paisa("250.50") == 25_050
    assert bdt_to_paisa(Decimal("0.005")) == 1
    assert paisa_to_bdt(-12_345) == "-123.45"
    with pytest.raises(TypeError):
        bdt_to_paisa(1.5)


def test_no_float_money_in_ledger_code():
    """Static check: the ledger package never calls float() or uses float literals."""
    files = list((Path(__file__).resolve().parents[1] / "app" / "ledger").glob("*.py"))
    assert len(files) >= 3
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            assert not (isinstance(node, ast.Constant) and type(node.value) is float), f"float literal in {path}"
            assert not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "float"), f"float() in {path}"
