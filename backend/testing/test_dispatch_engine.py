"""
test_dispatch_engine.py — Engine 1 Model E final assembly, layer 2: placing
one TASK's ready pool onto machines (batching + machine selection + timeline
+ pool + consolidation window, wired together).

Run: backend/venv/Scripts/python.exe backend/testing/test_dispatch_engine.py
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dispatch_engine import dispatch_task_pool
from dispatch_orders import CandidateMachine, ScheduledOperation
from dispatch_pool import DevicePool
from dispatch_scope import ScopeOutcome
from dispatch_timeline import TimePoint, start_of_day

DAY0 = date(2026, 1, 1)


def availability(machine, day, shift):
    return 100000.0  # effectively unlimited — these tests focus on batching/pool/readiness, not shift spill


def make_op(
    order_id,
    cdd,
    qty=10,
    machine="M1",
    priority=1,
    fixture="FIX-A",
    locator="LOC-1",
    fct=30.0,
    lct=10.0,
    lut=2.0,
    cycle_time=0.0,  # isolates fixture/locator timing math in these tests; cycle_time itself is tested elsewhere
    task="VB04",
    batch_key="10~150~CS~DFS",
    no_fixture=False,
):
    if no_fixture:
        candidates = [CandidateMachine(machine=machine, machine_priority=priority)]
        outcome = ScopeOutcome.SCHEDULED_NO_FIXTURE
        remark = "No fixture/locator match — scheduled via plain routing"
    else:
        candidates = [
            CandidateMachine(
                machine=machine,
                machine_priority=priority,
                fixture=fixture,
                locator=locator,
                fixture_change_time=fct,
                locator_change_time=lct,
                load_unload_time=lut,
            )
        ]
        outcome = ScopeOutcome.SCHEDULED
        remark = None
    return ScheduledOperation(
        production_order=order_id,
        operation_no=40,
        task=task,
        batch_key=batch_key,
        balance_qty=qty,
        cycle_time=cycle_time,
        cdd=cdd,
        order_date=None,
        order_status="Active",
        production_start_date=DAY0,
        candidates=candidates,
        scope_outcome=outcome,
        remark=remark,
    )


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def test_two_orders_same_batch_key_combine_with_no_extra_charge():
    print("\n=== Two orders, identical SCMD/fixture/locator: second placement is free ===")
    op1 = make_op("O1", cdd=date(2026, 2, 1), qty=10)
    op2 = make_op("O2", cdd=date(2026, 2, 15), qty=5)  # both within the 60-day window — not what this test is about
    ready_at = {"O1": start_of_day(DAY0), "O2": start_of_day(DAY0)}
    machine_free_at = {}
    pool = DevicePool(quantities={"FIX-A": 1, "LOC-1": 1})
    placed = dispatch_task_pool([op1, op2], ready_at, machine_free_at, pool, availability, today=DAY0, window_days=60, cooling_minutes=20, day_zero=DAY0)
    check("2 rows placed", len(placed) == 2)
    check("O1 first (higher priority, earlier CDD)", placed[0].production_order == "O1")
    check("O1 starts at day-zero", placed[0].start == start_of_day(DAY0))
    check("O1 duration = FCT+LCT+LUT*10 = 60", placed[0].end.minute == 60.0)
    check("O2 starts exactly where O1 ended (same run, no gap)", placed[1].start == placed[0].end)
    check("O2 duration = LUT*5 = 10 (free — charge=none)", placed[1].end.minute - placed[1].start.minute == 10.0)
    check("ready_at updated for both orders", ready_at["O1"] == placed[0].end and ready_at["O2"] == placed[1].end)
    check("machine cools 20 min after the run ends", machine_free_at["M1"].minute == 90.0)


def test_no_fixture_op_bypasses_batching_and_pool():
    print("\n=== SCHEDULED_NO_FIXTURE op: plain routing, no pool interaction ===")
    fixture_op = make_op("O1", cdd=date(2026, 3, 1), qty=4)
    plain_op = make_op("O2", cdd=date(2026, 3, 2), qty=3, no_fixture=True, machine="M2", cycle_time=2.0)
    ready_at = {"O1": start_of_day(DAY0), "O2": start_of_day(DAY0)}
    machine_free_at = {}
    pool = DevicePool(quantities={"FIX-A": 1, "LOC-1": 1})
    placed = dispatch_task_pool([fixture_op, plain_op], ready_at, machine_free_at, pool, availability, today=DAY0, window_days=60, cooling_minutes=20, day_zero=DAY0)
    plain_row = next(p for p in placed if p.production_order == "O2")
    check("no-fixture row has no fixture/locator assigned", plain_row.fixture_id is None and plain_row.locator_id is None)
    check("duration == cycle_time * qty (2*3=6)", plain_row.end.minute - plain_row.start.minute == 6.0)
    check("placed on its own machine M2", plain_row.machine == "M2")


def test_run_start_is_per_member_not_gated_by_a_later_members_readiness():
    print("\n=== A later member's own readiness delays only ITS OWN step, not the whole run ===")
    # Real bug, caught via live-data E-6 diagnosis of a reported idle-machine
    # gap (CLAUDE.md §E.7): this run used to compute one upfront max() over
    # EVERY member's ready_at before placing anything, so O1 — ready at
    # day-zero — sat waiting (and the machine sat fully idle) until O2's much
    # later readiness caught up, even though O1 could have started at once.
    # Fixed: each step starts at max(this machine's cursor after the
    # PRECEDING step in the run, that step's OWN ready_at) — O1 is unaffected
    # by O2's late readiness; only O2's own step (and only if the machine
    # would otherwise have been free before O2 is ready) waits.
    op1 = make_op("O1", cdd=date(2026, 3, 1), qty=10)
    op2 = make_op("O2", cdd=date(2026, 3, 2), qty=5)  # same fixture/locator/batch_key -> batches with O1
    late_ready = TimePoint(DAY0, 0, 500.0)
    ready_at = {"O1": start_of_day(DAY0), "O2": late_ready}
    machine_free_at = {}
    pool = DevicePool(quantities={"FIX-A": 1, "LOC-1": 1})
    placed = dispatch_task_pool([op1, op2], ready_at, machine_free_at, pool, availability, today=DAY0, window_days=60, cooling_minutes=20, day_zero=DAY0)
    check("O1 starts immediately at day-zero, NOT deferred by O2's later readiness", placed[0].start == start_of_day(DAY0))
    check("O1 still finishes its normal duration (FCT+LCT+LUT*10=60)", placed[0].end.minute == 60.0)
    check("O2 (appended for free, charge=none) waits for its OWN readiness, not before", placed[1].start == late_ready)
    check("O2 duration = LUT*5 = 10, unaffected by the wait", placed[1].end.minute - placed[1].start.minute == 10.0)


def test_fixture_pool_conflict_defers_a_later_task_pool():
    print("\n=== Shared DevicePool: a busy fixture pushes a later task pool's start out ===")
    pool = DevicePool(quantities={"FIX-A": 1, "LOC-1": 1, "LOC-2": 1})
    machine_free_at = {}

    op_a = make_op("O1", cdd=date(2026, 3, 1), qty=20, machine="M1")
    ready_at_a = {"O1": start_of_day(DAY0)}
    placed_a = dispatch_task_pool([op_a], ready_at_a, machine_free_at, pool, availability, today=DAY0, window_days=60, cooling_minutes=20, day_zero=DAY0)
    first_run_end = placed_a[0].end

    op_b = make_op("O2", cdd=date(2026, 3, 1), qty=5, machine="M2", locator="LOC-2")
    ready_at_b = {"O2": start_of_day(DAY0)}  # would start immediately, but FIX-A is busy
    placed_b = dispatch_task_pool([op_b], ready_at_b, machine_free_at, pool, availability, today=DAY0, window_days=60, cooling_minutes=20, day_zero=DAY0)
    check("O2's run was pushed out to at/after FIX-A's release", placed_b[0].start >= first_run_end)


def test_consolidation_window_hard_rule_prevents_batching_beyond_window():
    print("\n=== An order beyond the consolidation window never joins another's fixture run ===")
    near = make_op("O1", cdd=date(2026, 2, 1), qty=10)  # within 60 days of today
    far = make_op("O2", cdd=date(2027, 1, 1), qty=5)  # far beyond window, same fixture/locator/batch_key
    ready_at = {"O1": start_of_day(DAY0), "O2": start_of_day(DAY0)}
    machine_free_at = {}
    pool = DevicePool(quantities={"FIX-A": 2, "LOC-1": 2})  # 2 units so the pool itself never blocks either
    placed = dispatch_task_pool([near, far], ready_at, machine_free_at, pool, availability, today=DAY0, window_days=60, cooling_minutes=20, day_zero=DAY0)
    far_row = next(p for p in placed if p.production_order == "O2")
    check(
        "O2 paid its own fixture_change (30+10+2*5=50) — never batched in for free",
        far_row.end.minute - far_row.start.minute == 50.0,
    )


def test_partial_coverage_member_is_dropped_and_rescheduled_solo():
    print("\n=== No common machine across a run's members: one dropped, rescheduled on its own ===")
    op1 = make_op("O1", cdd=date(2026, 3, 1), qty=10, machine="M1")
    op2 = make_op("O2", cdd=date(2026, 3, 2), qty=5, machine="M2")  # same fixture/locator/batch_key, no common machine
    ready_at = {"O1": start_of_day(DAY0), "O2": start_of_day(DAY0)}
    machine_free_at = {}
    pool = DevicePool(quantities={"FIX-A": 2, "LOC-1": 2})
    placed = dispatch_task_pool([op1, op2], ready_at, machine_free_at, pool, availability, today=DAY0, window_days=60, cooling_minutes=20, day_zero=DAY0)
    check("both orders placed exactly once", sorted(p.production_order for p in placed) == ["O1", "O2"])
    check("placed on two different machines (no shared capability)", len({p.machine for p in placed}) == 2)
    check(
        "both paid their own fixture_change (each ended up its own run)",
        all((p.end.minute - p.start.minute) == 30.0 + 10.0 + 2.0 * p.balance_qty for p in placed),
    )


def test_heavy_op_resolver_true_appends_safety_stock_for_free():
    print("\n=== Heavy op, resolver says append: safety stock batches in for free ===")
    committed = make_op("O1", cdd=date(2026, 2, 1), qty=10, task="VB04")
    safety = make_op("O2", cdd=None, qty=5, task="VB04")
    ready_at = {"O1": start_of_day(DAY0), "O2": start_of_day(DAY0)}
    machine_free_at = {}
    pool = DevicePool(quantities={"FIX-A": 1, "LOC-1": 1})
    placed = dispatch_task_pool(
        [committed, safety], ready_at, machine_free_at, pool, availability,
        today=DAY0, window_days=60, cooling_minutes=20, day_zero=DAY0,
        heavy_operations=frozenset({"VB04"}), resolve_safety_stock=lambda steps, op_by_id, task: True,
    )
    safety_row = next(p for p in placed if p.production_order == "O2")
    check(
        "safety stock appended for free (charge=none -> LUT*qty=10 only, no change-time)",
        safety_row.end.minute - safety_row.start.minute == 10.0,
    )


def test_heavy_op_resolver_false_defers_safety_stock():
    print("\n=== Heavy op, resolver says defer: safety stock pays its own changeover instead ===")
    committed = make_op("O1", cdd=date(2026, 2, 1), qty=10, task="VB04")
    safety = make_op("O2", cdd=None, qty=5, task="VB04")
    ready_at = {"O1": start_of_day(DAY0), "O2": start_of_day(DAY0)}
    machine_free_at = {}
    pool = DevicePool(quantities={"FIX-A": 2, "LOC-1": 2})
    placed = dispatch_task_pool(
        [committed, safety], ready_at, machine_free_at, pool, availability,
        today=DAY0, window_days=60, cooling_minutes=20, day_zero=DAY0,
        heavy_operations=frozenset({"VB04"}), resolve_safety_stock=lambda steps, op_by_id, task: False,
    )
    safety_row = next(p for p in placed if p.production_order == "O2")
    check(
        "safety stock pays its own full changeover (30+10+2*5=50)",
        safety_row.end.minute - safety_row.start.minute == 50.0,
    )


def test_light_op_never_consults_the_resolver():
    print("\n=== Light op: resolver is never even consulted ===")

    def _raise_if_called(steps, op_by_id, task):
        raise AssertionError("resolver must not be called for a light operation")

    committed = make_op("O1", cdd=date(2026, 2, 1), qty=10, task="VB02")
    safety = make_op("O2", cdd=None, qty=5, task="VB02")
    ready_at = {"O1": start_of_day(DAY0), "O2": start_of_day(DAY0)}
    machine_free_at = {}
    pool = DevicePool(quantities={"FIX-A": 1, "LOC-1": 1})
    placed = dispatch_task_pool(
        [committed, safety], ready_at, machine_free_at, pool, availability,
        today=DAY0, window_days=60, cooling_minutes=20, day_zero=DAY0,
        heavy_operations=frozenset({"VB04"}),  # VB02 is NOT in the heavy set
        resolve_safety_stock=_raise_if_called,
    )
    check("both placed without raising", len(placed) == 2)


if __name__ == "__main__":
    test_two_orders_same_batch_key_combine_with_no_extra_charge()
    test_no_fixture_op_bypasses_batching_and_pool()
    test_run_start_is_per_member_not_gated_by_a_later_members_readiness()
    test_fixture_pool_conflict_defers_a_later_task_pool()
    test_consolidation_window_hard_rule_prevents_batching_beyond_window()
    test_partial_coverage_member_is_dropped_and_rescheduled_solo()
    test_heavy_op_resolver_true_appends_safety_stock_for_free()
    test_heavy_op_resolver_false_defers_safety_stock()
    test_light_op_never_consults_the_resolver()
    print("\n[OK] dispatch_task_pool wires batching + machine selection + timeline + pool + window correctly.")
