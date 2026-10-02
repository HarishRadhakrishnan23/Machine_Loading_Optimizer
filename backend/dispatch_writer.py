"""
dispatch_writer.py — Engine 1 Model E: converts `dispatch_orchestrator.FinalRow`
into the exact dict shape MCH_SCHEDULE_OUTPUT expects for insertion.

This is the ONLY place a `TimePoint` becomes a real Oracle TIMESTAMP
(`shift_clock.timepoint_to_datetime` — CLAUDE.md "Shift clock-time
convention"). Everything upstream of here works in TimePoint; everything
downstream (db.py's INSERT) works in real dicts/columns.

LINE_NO is always 1 here — CLAUDE.md §E.14: it increments only when one
order-operation's own balance is genuinely split across more than one
machine (a mid-batch machine breakdown forcing a handoff), which this engine
doesn't yet implement (flagged as a rare, not-yet-built case in
dispatch_orchestrator.py). The engine currently produces exactly one
PlacedOperation per (PRODUCTION_ORDER, OPERATION_NO), so LINE_NO=1 is correct
for every row this writer will ever see, not a placeholder covering a gap.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from dispatch_orchestrator import FinalRow
from shift_clock import timepoint_to_datetime


def to_oracle_row(row: FinalRow, run_id: str, generated_at: datetime) -> dict:
    """One MCH_SCHEDULE_OUTPUT row, column names matching the DDL exactly."""
    return {
        "RUN_ID": run_id,
        "PRODUCTION_ORDER": row.production_order,
        "OPERATION_NO": row.operation_no,
        "LINE_NO": 1,
        "TASK": row.task,
        "WORK_CENTER": row.machine,
        "SHIFT": row.shift,
        "SCHEDULED_DATE": row.scheduled_date,
        "BALANCE_QTY": row.balance_qty,
        "GENERATED_AT": generated_at,
        "BATCH_KEY": row.batch_key,
        "IS_SAFETY_STOCK": "Y" if row.is_safety_stock else "N",
        "FIXTURE_ID": row.fixture_id,
        "LOCATOR_ID": row.locator_id,
        "START_TIMESTAMP": timepoint_to_datetime(row.start) if row.start is not None else None,
        "END_TIMESTAMP": timepoint_to_datetime(row.end) if row.end is not None else None,
        "REMARK": row.remark,
        "ORDER_COMPLETION_DATE": row.order_completion_date,
        "ORDER_COMPLETION_SHIFT": row.order_completion_shift,
    }


def to_oracle_rows(
    rows: list[FinalRow],
    run_id: Optional[str] = None,
    generated_at: Optional[datetime] = None,
) -> tuple[str, list[dict]]:
    """
    Converts every FinalRow for one run, sharing one RUN_ID/GENERATED_AT
    across all of them. Returns (run_id, rows) — run_id is generated here if
    not supplied, so the caller can log/report it even when it didn't pick one.
    """
    run_id = run_id or str(uuid.uuid4())
    generated_at = generated_at or datetime.now()
    return run_id, [to_oracle_row(r, run_id, generated_at) for r in rows]
