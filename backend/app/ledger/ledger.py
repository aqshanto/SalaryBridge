"""Double-entry ledger.

Every journal entry has at least two lines whose amounts (integer paisa) sum to zero.
Sign convention: a positive line adds money to an account, a negative line takes money out.
So `balance(account)` is the money currently sitting in that account.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    Engine,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    func,
    insert,
    select,
)

from app.ledger.money import is_paisa

SYSTEM_ACCOUNTS = {"upay_capital", "upay_pool", "fees_income", "loss_provision"}
ACCOUNT_PREFIXES = ("employee_wallet:", "employer:", "receivable:")
_ID = re.compile(r"^[A-Za-z0-9_\-]+$")

metadata = MetaData()

entries = Table(
    "ledger_entries",
    metadata,
    Column("entry_id", Integer, primary_key=True, autoincrement=True),
    Column("entry_date", Date, nullable=False),
    Column("kind", String(40), nullable=False),
    Column("ref", String(40)),
    Column("memo", String(200)),
)

lines = Table(
    "ledger_lines",
    metadata,
    Column("line_id", Integer, primary_key=True, autoincrement=True),
    Column("entry_id", Integer, ForeignKey("ledger_entries.entry_id"), nullable=False, index=True),
    Column("account", String(80), nullable=False, index=True),
    Column("amount_paisa", BigInteger, nullable=False),
)


class LedgerError(ValueError):
    pass


def validate_account(account: str) -> None:
    if account in SYSTEM_ACCOUNTS:
        return
    for prefix in ACCOUNT_PREFIXES:
        if account.startswith(prefix) and _ID.match(account[len(prefix):]):
            return
    raise LedgerError(f"Unknown account: {account!r}")


@dataclass
class Reconciliation:
    ok: bool
    total_paisa: int
    entries: int
    unbalanced_entry_ids: list[int] = field(default_factory=list)


class Ledger:
    def __init__(self, engine: Engine):
        self.engine = engine
        metadata.create_all(engine)

    def post_entry(
        self,
        entry_date: date,
        kind: str,
        entry_lines: list[tuple[str, int]],
        ref: str | None = None,
        memo: str | None = None,
    ) -> int:
        """Post one balanced entry atomically and return its id."""
        if len(entry_lines) < 2:
            raise LedgerError("An entry needs at least two lines")
        for account, amount in entry_lines:
            validate_account(account)
            if not is_paisa(amount):
                raise LedgerError(f"Amount for {account} must be int paisa, got {type(amount).__name__}")
            if amount == 0:
                raise LedgerError(f"Zero amount line for {account}")
        total = sum(amount for _, amount in entry_lines)
        if total != 0:
            raise LedgerError(f"Entry does not balance: lines sum to {total} paisa")

        with self.engine.begin() as conn:
            entry_id = conn.execute(
                insert(entries).values(entry_date=entry_date, kind=kind, ref=ref, memo=memo)
            ).inserted_primary_key[0]
            conn.execute(
                insert(lines),
                [{"entry_id": entry_id, "account": a, "amount_paisa": amt} for a, amt in entry_lines],
            )
        return entry_id

    def balance(self, account: str) -> int:
        validate_account(account)
        with self.engine.connect() as conn:
            value = conn.execute(
                select(func.coalesce(func.sum(lines.c.amount_paisa), 0)).where(lines.c.account == account)
            ).scalar_one()
        return int(value)

    def balances(self, prefix: str | None = None) -> dict[str, int]:
        query = select(lines.c.account, func.sum(lines.c.amount_paisa)).group_by(lines.c.account)
        if prefix:
            query = query.where(lines.c.account.startswith(prefix))
        with self.engine.connect() as conn:
            return {account: int(total) for account, total in conn.execute(query)}

    def reconcile(self) -> Reconciliation:
        """Check that every entry balances and the whole ledger sums to zero."""
        with self.engine.connect() as conn:
            unbalanced = [
                row.entry_id
                for row in conn.execute(
                    select(lines.c.entry_id)
                    .group_by(lines.c.entry_id)
                    .having(func.sum(lines.c.amount_paisa) != 0)
                )
            ]
            total = conn.execute(select(func.coalesce(func.sum(lines.c.amount_paisa), 0))).scalar_one()
            count = conn.execute(select(func.count()).select_from(entries)).scalar_one()
        return Reconciliation(ok=not unbalanced and total == 0, total_paisa=int(total), entries=int(count), unbalanced_entry_ids=unbalanced)
