"""
test_dispatch_safety_stock_speculation.py — CLAUDE.md §E.10's full
speculative check, wired end-to-end through run_dispatch_simulation.

Deliberately uses module-level (never closure) helpers only where they cross
into the parallel branch machinery, matching the real constraint documented
in dispatch_parallel.py.

Run: backend/venv/Scripts/python.exe backend/testing/test_dispatch_safety_stock_speculation.py
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dispatch_orchestrator import run_dispatch_simulation
from dispatch_orders import CandidateMachine, OrderChain, ScheduledOperation
from dispatch_pool import DevicePool
from dispatch_scope import ScopeOutcome

DAY0 = date(2026, 1, 1)
HEAVY = frozenset({"VB04"})


def availability(machine, day, shift):
    # Deliberately small (not "unlimited") — the whole point of this test is
    # that extra minutes from appending safety stock can spill into the NEXT
    # calendar day, which is what actually makes a CDD breach possible;
    # unlimited capacity would keep everything on day_zero regardless.
    return 50.0


def make_op(order_id, cdd, qty, machines, fixture, locator, priority=1, cycle_time=0.0):
    if isinstance(machines, str):
        machines = [machines]
    candidates = [
        CandidateMachine(
            machine=m, machine_priority=priority, fixture=fixture, locator=locator,
            fixture_change_time=30.0, locator_change_time=10.0, load_unload_time=2.0,
        )
        for m in machines
    ]
    return ScheduledOperation(
        production_order=order_id, operation_no=40, task="VB04", batch_key=f"{fixture}~key",
        balance_qty=qty, cycle_time=cycle_time, cdd=cdd, order_date=None, order_status="Active",
        production_start_date=DAY0, candidates=candidates, scope_outcome=ScopeOutcome.SCHEDULED, remark=None,
    )


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def test_speculation_defers_safety_stock_when_it_would_breach_a_later_committed_order():
    print("\n=== Same task pool: O1+O2 (heavy, mixed) share a fixture; O3 (committed) waits on the same machine ===")
    # O1: committed, due soon -> highest priority, anchors fixture A on M1.
    # O2: safety stock, same fixture+locator as O1 -> free append costs LUT*qty=100 (qty=50) if appended.
    # O3: committed, different fixture, same machine M1 (its only candidate) -> must wait for M1 to free.
    #     Its CDD is exactly on the edge: safe if O2 is deferred, breached if O2 is appended.
    o1 = make_op("O1", cdd=date(2025, 12, 31), qty=10, machines="M1", fixture="FIX-A", locator="LOC-1")  # earliest CDD -> anchors first, M1 only
    # O2 also has an alternative idle machine M2 -- deferring it should let it
    # escape to M2 instead of still queuing behind O1 on M1. If its only
    # candidate were M1 too, "deferred" would just mean "runs on M1 anyway,
    # after paying its own changeover" -- worse for O3, not better.
    o2 = make_op("O2", cdd=None, qty=50, machines=["M1", "M2"], fixture="FIX-A", locator="LOC-1")
    o3 = make_op("O3", cdd=date(2026, 1, 1), qty=1, machines="M1", fixture="FIX-B", locator="LOC-2")  # waits on M1; the tight deadline under test
    chains = [OrderChain(o.production_order, [o], []) for o in (o1, o2, o3)]

    pool_no_speculation = DevicePool(quantities={"FIX-A": 1, "LOC-1": 1, "FIX-B": 1, "LOC-2": 1})
    rows_naive = run_dispatch_simulation(
        chains, availability, pool_no_speculation, today=DAY0, window_days=60, cooling_minutes=20, day_zero=DAY0,
        planned_order_start_buffer_days=1, heavy_operations=HEAVY, enable_safety_stock_speculation=False,
    )
    o3_naive = next(r for r in rows_naive if r.production_order == "O3")
    check(
        "without speculation, O3 breaches its CDD (baseline the fix must beat)",
        o3_naive.scheduled_date > date(2026, 1, 1),
    )

    pool_speculative = DevicePool(quantities={"FIX-A": 1, "LOC-1": 1, "FIX-B": 1, "LOC-2": 1})
    rows_speculative = run_dispatch_simulation(
        chains, availability, pool_speculative, today=DAY0, window_days=60, cooling_minutes=20, day_zero=DAY0,
        planned_order_start_buffer_days=1, heavy_operations=HEAVY, enable_safety_stock_speculation=True,
    )
    o2_spec = next(r for r in rows_speculative if r.production_order == "O2")
    o3_spec = next(r for r in rows_speculative if r.production_order == "O3")
    check("with speculation, O2 (safety stock) is deferred onto the alternate machine M2 (not batched onto M1)", o2_spec.machine == "M2")
    check("with speculation, O3 stays on M1 and no longer breaches its CDD", o3_spec.machine == "M1" and o3_spec.scheduled_date <= date(2026, 1, 1))


def test_speculation_still_appends_when_no_committed_order_is_at_risk():
    print("\n=== Isolated heavy-op run, nothing downstream to protect: append proceeds normally ===")
    o1 = make_op("O1", cdd=date(2026, 2, 1), qty=10, machines="M1", fixture="FIX-A", locator="LOC-1")
    o2 = make_op("O2", cdd=None, qty=5, machines="M1", fixture="FIX-A", locator="LOC-1")
    chains = [OrderChain(o.production_order, [o], []) for o in (o1, o2)]
    pool = DevicePool(quantities={"FIX-A": 1, "LOC-1": 1})
    rows = run_dispatch_simulation(
        chains, availability, pool, today=DAY0, window_days=60, cooling_minutes=20, day_zero=DAY0,
        planned_order_start_buffer_days=1, heavy_operations=HEAVY, enable_safety_stock_speculation=True,
    )
    o2_row = next(r for r in rows if r.production_order == "O2")
    check("O2 appended for free (charge=none -> LUT*qty=10)", o2_row.end.minute - o2_row.start.minute == 10.0)


if __name__ == "__main__":
    test_speculation_defers_safety_stock_when_it_would_breach_a_later_committed_order()
    test_speculation_still_appends_when_no_committed_order_is_at_risk()
    print("\n[OK] The full speculative check protects committed orders exactly per CLAUDE.md §E.10.")
