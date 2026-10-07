"""Payroll adapters (Phase 2): turn an employer's payroll or time-card export into one internal record type.

Each HR system exports differently, so an adapter only maps columns; everything after that (matching staff,
updating earned days) is shared. New systems are added by registering a column mapping, not new code paths.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class PayrollRecord:
    employee_id: str
    unpaid_absent_days: int
    net_salary_bdt: int | None = None


class PayrollFormatError(ValueError):
    pass


@dataclass(frozen=True)
class ColumnMap:
    """Column names in the employer's export for each internal field."""

    employee_id: str
    unpaid_absent_days: str
    net_salary_bdt: str | None = None


# Mappings for the export layouts we expect to meet first. [ASSUMPTION]: real column names are confirmed at onboarding.
MAPPINGS: dict[str, ColumnMap] = {
    "generic": ColumnMap("employee_id", "unpaid_absent_days", "net_salary_bdt"),
    "hr_export": ColumnMap("Emp ID", "Unpaid Leave Days", "Net Pay"),  # typical spreadsheet export from an HR tool
    "timecard": ColumnMap("staff_code", "absent_unpaid"),  # attendance-only feed from a time clock
}


def _int(value, field: str, row: int, allow_blank: bool = False) -> int | None:
    if value is None or str(value).strip() == "":
        if allow_blank:
            return None
        raise PayrollFormatError(f"row {row}: '{field}' is empty")
    try:
        number = int(float(str(value).replace(",", "").strip()))
    except ValueError as exc:
        raise PayrollFormatError(f"row {row}: '{field}' is not a number") from exc
    if number < 0:
        raise PayrollFormatError(f"row {row}: '{field}' is negative")
    return number


def _records(rows: list[dict], cmap: ColumnMap) -> list[PayrollRecord]:
    if rows and cmap.employee_id not in rows[0]:
        raise PayrollFormatError(f"missing column '{cmap.employee_id}'")
    out = []
    for i, row in enumerate(rows, start=1):
        emp = str(row.get(cmap.employee_id, "")).strip()
        if not emp:
            raise PayrollFormatError(f"row {i}: '{cmap.employee_id}' is empty")
        days = _int(row.get(cmap.unpaid_absent_days), cmap.unpaid_absent_days, i)
        if days > 31:
            raise PayrollFormatError(f"row {i}: more than 31 absent days")
        salary = _int(row.get(cmap.net_salary_bdt), cmap.net_salary_bdt, i, allow_blank=True) if cmap.net_salary_bdt else None
        out.append(PayrollRecord(emp, days, salary))
    return out


def parse(content: str, fmt: str = "csv", mapping: str = "generic") -> list[PayrollRecord]:
    if mapping not in MAPPINGS:
        raise PayrollFormatError(f"unknown mapping '{mapping}' (known: {', '.join(MAPPINGS)})")
    cmap = MAPPINGS[mapping]
    if fmt == "csv":
        rows = list(csv.DictReader(io.StringIO(content.lstrip("﻿"))))
    elif fmt == "json":
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise PayrollFormatError("body is not valid JSON") from exc
        rows = data.get("records", data) if isinstance(data, dict) else data
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise PayrollFormatError("JSON must be a list of objects or {\"records\": [...]}")
    else:
        raise PayrollFormatError(f"unknown format '{fmt}' (csv or json)")
    if not rows:
        raise PayrollFormatError("no rows")
    if len(rows) > 20_000:
        raise PayrollFormatError("too many rows (max 20,000 per file)")
    return _records(rows, cmap)
