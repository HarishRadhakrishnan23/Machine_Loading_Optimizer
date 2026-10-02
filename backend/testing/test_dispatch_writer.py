"""
test_dispatch_writer.py — FinalRow -> Oracle row dict conversion.

Run: backend/venv/Scripts/python.exe backend/testing/test_dispatch_writer.py
"""

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dispatch_orchestrator import FinalRow
from dispatch_timeline import TimePoint
from dispatch_writer import to_oracle_row, to_oracle_rows

DAY = date(2026, 1, 5)  # a Monday


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def scheduled_row():
    return FinalRow(
        production_order="O1", operation_no=40.0, task="VB04", machine="2PMC16", shift="first",
        scheduled_date=DAY, balance_qty=10, batch_key="10~150~CS~DFS", is_safety_stock=False,
        fixture_id="FIX-A", locator_id="LOC-1", start=TimePoint(DAY, 0, 90.0), end=TimePoint(DAY, 0, 150.0),
        remark=None, order_completion_date=date(2026, 1, 10), order_completion_shift="second",
    )


def excluded_row():
    return FinalRow(
        production_order="O1", operation_no=50.0, task="VA03", machine=None, shift=None, scheduled_date=None,
        balance_qty=5, batch_key="10~150~CS~DFS", is_safety_stock=False, fixture_id=None, locator_id=None,
        start=None, end=None, remark="CT = 0 — excluded", order_completion_date=date(2026, 1, 10),
        order_completion_shift="second",
    )


def test_scheduled_row_gets_real_timestamps():
    print("\n=== Scheduled row: TimePoint converted to a real TIMESTAMP via the fixed shift convention ===")
    oracle_row = to_oracle_row(scheduled_row(), run_id="RUN-1", generated_at=datetime(2026, 1, 1, 12, 0))
    check("START_TIMESTAMP == 08:30 (first shift 07:00 + 90 min)", oracle_row["START_TIMESTAMP"] == datetime(2026, 1, 5, 8, 30))
    check("END_TIMESTAMP == 09:30 (07:00 + 150 min)", oracle_row["END_TIMESTAMP"] == datetime(2026, 1, 5, 9, 30))
    check("IS_SAFETY_STOCK == 'N'", oracle_row["IS_SAFETY_STOCK"] == "N")
    check("LINE_NO == 1", oracle_row["LINE_NO"] == 1)
    check("RUN_ID passed through", oracle_row["RUN_ID"] == "RUN-1")
    check("WORK_CENTER/SHIFT/SCHEDULED_DATE populated", oracle_row["WORK_CENTER"] == "2PMC16" and oracle_row["SHIFT"] == "first")


def test_excluded_row_has_null_machine_and_timestamps():
    print("\n=== Excluded row: NULL machine/shift/date/timestamps, REMARK present ===")
    oracle_row = to_oracle_row(excluded_row(), run_id="RUN-1", generated_at=datetime(2026, 1, 1, 12, 0))
    check("WORK_CENTER is None", oracle_row["WORK_CENTER"] is None)
    check("SHIFT is None", oracle_row["SHIFT"] is None)
    check("SCHEDULED_DATE is None", oracle_row["SCHEDULED_DATE"] is None)
    check("START_TIMESTAMP is None", oracle_row["START_TIMESTAMP"] is None)
    check("END_TIMESTAMP is None", oracle_row["END_TIMESTAMP"] is None)
    check("REMARK present", oracle_row["REMARK"] == "CT = 0 — excluded")
    check("ORDER_COMPLETION_DATE still populated (order-level, not row-level)", oracle_row["ORDER_COMPLETION_DATE"] == date(2026, 1, 10))


def test_to_oracle_rows_shares_one_run_id_and_generated_at():
    print("\n=== to_oracle_rows: one RUN_ID/GENERATED_AT shared across every row ===")
    run_id, rows = to_oracle_rows([scheduled_row(), excluded_row()])
    check("2 rows produced", len(rows) == 2)
    check("both rows share the same RUN_ID", rows[0]["RUN_ID"] == rows[1]["RUN_ID"] == run_id)
    check("both rows share the same GENERATED_AT", rows[0]["GENERATED_AT"] == rows[1]["GENERATED_AT"])
    check("run_id is a non-empty string", isinstance(run_id, str) and len(run_id) > 0)


if __name__ == "__main__":
    test_scheduled_row_gets_real_timestamps()
    test_excluded_row_has_null_machine_and_timestamps()
    test_to_oracle_rows_shares_one_run_id_and_generated_at()
    print("\n[OK] FinalRow -> Oracle row conversion behaves per CLAUDE.md §E.14.")
