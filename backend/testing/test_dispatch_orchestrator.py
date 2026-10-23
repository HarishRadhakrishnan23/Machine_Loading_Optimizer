"""
test_dispatch_orchestrator.py — Engine 1 Model E final assembly, layer 2
(outer loop): drives dispatch_task_pool across full order chains.

Run: backend/venv/Scripts/python.exe backend/testing/test_dispatch_orchestrator.py
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dispatch_orchestrator import run_dispatch_simulation
from dispatch_orders import CandidateMachine, ExcludedOperation, OrderChain, ScheduledOperation
from dispatch_pool import DevicePool
from dispatch_scope import ScopeOutcome
from dispatch_timeline import start_of_day

DAY0 = date(2026, 1, 1)


def availability(machine, day, shift):
    return 100000.0


def sched_op(order_id, operation_no, task, cdd, qty=10, machine="M1", cycle_time=0.0, order_status="Active", batch_key="10~150~CS~DFS"):
    candidates = [
        CandidateMachine(machine=machine, machine_priority=1, fixture="FIX-A", locator="LOC-1", fixture_change_time=30.0, locator_change_time=10.0, load_unload_time=2.0)
    ]
    return ScheduledOperation(
        production_order=order_id, operation_no=operation_no, task=task, batch_key=batch_key,
        balance_qty=qty, cycle_time=cycle_time, cdd=cdd, order_date=None, order_status=order_status,
        production_start_date=DAY0, candidates=candidates, scope_outcome=ScopeOutcome.SCHEDULED, remark=None,
    )


def no_fixture_op(order_id, operation_no, task, cdd, qty=10, machine="M1", cycle_time=5.0):
    """A SCHEDULED_NO_FIXTURE operation — CLAUDE.md §E.11's plain-routing fallback."""
    candidates = [CandidateMachine(machine=machine, machine_priority=1)]
    return ScheduledOperation(
        production_order=order_id, operation_no=operation_no, task=task, batch_key="10~150~CS~DFS",
        balance_qty=qty, cycle_time=cycle_time, cdd=cdd, order_date=None, order_status="Active",
        production_start_date=DAY0, candidates=candidates, scope_outcome=ScopeOutcome.SCHEDULED_NO_FIXTURE,
        remark="No fixture/locator match — scheduled via plain routing",
    )


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def test_single_op_order_gets_completion_stamped():
    print("\n=== Single-op order: completion date/shift stamped on its own row ===")
    op = sched_op("O1", 40, "VB04", cdd=date(2026, 2, 1))
    chain = OrderChain(production_order="O1", schedulable=[op], excluded=[])
    pool = DevicePool(quantities={"FIX-A": 1, "LOC-1": 1})
    rows = run_dispatch_simulation([chain], availability, pool, today=DAY0, window_days=60, cooling_minutes=20, day_zero=DAY0, planned_order_start_buffer_days=1)
    check("exactly 1 row", len(rows) == 1)
    check("completion_date == the row's own end date", rows[0].order_completion_date == rows[0].end.day)
    check("completion_shift == the row's own end shift", rows[0].order_completion_shift == rows[0].shift)


def test_multi_op_order_respects_precedence():
    print("\n=== Multi-op order: op2 never starts before op1 ends ===")
    op1 = sched_op("O1", 10, "VB02", cdd=date(2026, 2, 1))
    op2 = sched_op("O1", 20, "VB04", cdd=date(2026, 2, 1))
    chain = OrderChain(production_order="O1", schedulable=[op1, op2], excluded=[])
    pool = DevicePool(quantities={"FIX-A": 1, "LOC-1": 1})
    rows = run_dispatch_simulation([chain], availability, pool, today=DAY0, window_days=60, cooling_minutes=20, day_zero=DAY0, planned_order_start_buffer_days=1)
    check("2 rows", len(rows) == 2)
    row_10, row_20 = (r for r in rows if r.operation_no == 10), (r for r in rows if r.operation_no == 20)
    r10 = next(row_10)
    r20 = next(row_20)
    check("op20 starts at/after op10 ends", r20.start >= r10.end)
    check("completion == op20's end (the LAST schedulable op)", rows[0].order_completion_date == r20.end.day)


def test_excluded_row_carries_batch_key_and_safety_flag_but_no_machine():
    print("\n=== Excluded op: REMARK row, batch_key/is_safety_stock present, no machine/timestamps ===")
    op1 = sched_op("O1", 10, "VB02", cdd=date(2026, 2, 1))
    excluded = ExcludedOperation(
        production_order="O1", operation_no=20, task="VA03", balance_qty=5,
        batch_key="10~150~CS~DFS", is_safety_stock=False,
        scope_outcome=ScopeOutcome.EXCLUDED_CT_ZERO, remark="CT = 0 — excluded",
    )
    chain = OrderChain(production_order="O1", schedulable=[op1], excluded=[excluded])
    pool = DevicePool(quantities={"FIX-A": 1, "LOC-1": 1})
    rows = run_dispatch_simulation([chain], availability, pool, today=DAY0, window_days=60, cooling_minutes=20, day_zero=DAY0, planned_order_start_buffer_days=1)
    exc_row = next(r for r in rows if r.operation_no == 20)
    check("no machine assigned", exc_row.machine is None)
    check("no start/end timestamps", exc_row.start is None and exc_row.end is None)
    check("batch_key still populated", exc_row.batch_key == "10~150~CS~DFS")
    check("REMARK explains the exclusion", exc_row.remark == "CT = 0 — excluded")
    check("completion still stamped from the order's real schedulable op", exc_row.order_completion_date is not None)


def test_order_with_zero_schedulable_ops_has_no_completion():
    print("\n=== Fully-excluded order: no completion date to stamp ===")
    excluded = ExcludedOperation(
        production_order="O1", operation_no=10, task="VB02", balance_qty=0,
        batch_key="10~150~CS~DFS", is_safety_stock=False,
        scope_outcome=ScopeOutcome.EXCLUDED_BALANCE_ZERO, remark="Balance Qty <= 0 — fully accounted for",
    )
    chain = OrderChain(production_order="O1", schedulable=[], excluded=[excluded])
    pool = DevicePool(quantities={"FIX-A": 1, "LOC-1": 1})
    rows = run_dispatch_simulation([chain], availability, pool, today=DAY0, window_days=60, cooling_minutes=20, day_zero=DAY0, planned_order_start_buffer_days=1)
    check("1 row, completion date is None", rows[0].order_completion_date is None)


def test_planned_order_first_op_never_starts_before_earliest_start():
    print("\n=== Planned order: first op honors material-arrival earliest-start ===")
    op = sched_op("O1", 10, "VB02", cdd=date(2026, 3, 1), order_status="Planned")
    chain = OrderChain(production_order="O1", schedulable=[op], excluded=[])
    pool = DevicePool(quantities={"FIX-A": 1, "LOC-1": 1})
    rows = run_dispatch_simulation([chain], availability, pool, today=DAY0, window_days=60, cooling_minutes=20, day_zero=DAY0, planned_order_start_buffer_days=1)
    check("starts on day_zero + 1 (production_start_date + buffer)", rows[0].start.day == date(2026, 1, 2))
    check("first shift", rows[0].start.shift_index == 0)
    check("minute 0", rows[0].start.minute == 0.0)


def test_no_fixture_op_updates_ready_at_for_the_next_op():
    print("\n=== Regression: a SCHEDULED_NO_FIXTURE op must still advance ready_at (real-data bug, E-6) ===")
    # Caught during E-6 validation against live WIP data: _place_no_fixture_op
    # computed `end` but never wrote it back to ready_at, so a second op for
    # the same order could start before the no-fixture op actually finished.
    op1 = no_fixture_op("O1", 10, "VA03", cdd=date(2026, 2, 1), cycle_time=500.0)  # long enough to force a real gap
    op2 = no_fixture_op("O1", 20, "VB12", cdd=date(2026, 2, 1), cycle_time=5.0)
    chain = OrderChain(production_order="O1", schedulable=[op1, op2], excluded=[])
    pool = DevicePool(quantities={})
    rows = run_dispatch_simulation([chain], availability, pool, today=DAY0, window_days=60, cooling_minutes=20, day_zero=DAY0, planned_order_start_buffer_days=1)
    r1 = next(r for r in rows if r.operation_no == 10)
    r2 = next(r for r in rows if r.operation_no == 20)
    check("op20 starts at/after op10 ends", r2.start >= r1.end)


def test_two_orders_same_depth_and_task_batch_together():
    print("\n=== Two independent orders, same round + TASK: dispatch_task_pool batching kicks in ===")
    op1 = sched_op("O1", 10, "VB02", cdd=date(2026, 2, 1), qty=10)
    op2 = sched_op("O2", 10, "VB02", cdd=date(2026, 2, 2), qty=5)
    chains = [OrderChain("O1", [op1], []), OrderChain("O2", [op2], [])]
    pool = DevicePool(quantities={"FIX-A": 1, "LOC-1": 1})
    rows = run_dispatch_simulation(chains, availability, pool, today=DAY0, window_days=60, cooling_minutes=20, day_zero=DAY0, planned_order_start_buffer_days=1)
    check("2 rows total", len(rows) == 2)
    o1_row = next(r for r in rows if r.production_order == "O1")
    o2_row = next(r for r in rows if r.production_order == "O2")
    check("O2 batched in right after O1 with no gap (charge=none)", o2_row.start == o1_row.end)


def test_lot_streaming_next_op_starts_on_early_pieces_not_whole_batch():
    print("\n=== Model F §E.18 Part 2: next op consumes upstream pieces as they complete, not the whole batch ===")
    # op10: 3 pieces @ 10 min/piece on M1. op20: SAME order, 3 pieces @ 1
    # min/piece on a DIFFERENT machine (M2) — fast enough that M2 is always
    # waiting on op10's NEXT piece. A generous shift (1000 min) keeps this
    # scenario free of shift-boundary slicing entirely, so the only thing
    # under test is whether op20 gates on each of op10's per-piece
    # completions (Part 2) or on op10's single whole-batch completion
    # (pre-Part-2 behavior) — not shift-rollover arithmetic.
    def roomy_shift_availability(machine, day, shift):
        return 1000.0

    op1 = no_fixture_op("O1", 10, "T1", cdd=date(2026, 2, 1), qty=3, machine="M1", cycle_time=10.0)
    op2 = no_fixture_op("O1", 20, "T2", cdd=date(2026, 2, 1), qty=3, machine="M2", cycle_time=1.0)
    chain = OrderChain("O1", [op1, op2], [])
    pool = DevicePool(quantities={})
    rows = run_dispatch_simulation(
        [chain], roomy_shift_availability, pool, today=DAY0, window_days=60, cooling_minutes=20,
        day_zero=DAY0, planned_order_start_buffer_days=1,
    )
    op10_rows = [r for r in rows if r.operation_no == 10]
    op20_rows = [r for r in rows if r.operation_no == 20]

    check("op10 (upstream) is a single row (no shift-boundary slicing in this scenario)", len(op10_rows) == 1)
    check("op10 ends at minute 30 (3 pieces x 10 min)", op10_rows[0].end.shift_index == 0 and op10_rows[0].end.minute == 30.0)

    check("op20 (downstream) is also a single row here", len(op20_rows) == 1)
    check(
        "op20 starts at minute 10 — the instant op10's FIRST piece completes, not op10's full 30-minute batch",
        op20_rows[0].start.shift_index == 0 and op20_rows[0].start.minute == 10.0,
    )
    check(
        "op20 finishes at minute 31 (gated by op10's 3rd-piece completions at 10/20/30, +1 min each) "
        "— earlier than the pre-Part-2 whole-batch-wait result of minute 33 (wait for 30, then run all 3)",
        op20_rows[0].end.shift_index == 0 and op20_rows[0].end.minute == 31.0,
    )


if __name__ == "__main__":
    test_single_op_order_gets_completion_stamped()
    test_multi_op_order_respects_precedence()
    test_excluded_row_carries_batch_key_and_safety_flag_but_no_machine()
    test_order_with_zero_schedulable_ops_has_no_completion()
    test_planned_order_first_op_never_starts_before_earliest_start()
    test_no_fixture_op_updates_ready_at_for_the_next_op()
    test_lot_streaming_next_op_starts_on_early_pieces_not_whole_batch()
    test_two_orders_same_depth_and_task_batch_together()
    print("\n[OK] The outer dispatch loop assembles complete, precedence-correct schedules.")
