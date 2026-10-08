"""
test_soft_consolidation.py — the opt-in "soft consolidation beyond the §E.6
window" extension: a committed order just past the hard window may ride an
existing in-window run's already-mounted fixture/locator for free, but ONLY
when the full speculative check confirms it delays no committed order
anywhere. §E.6 itself stays a hard rule by default (allow_soft_consolidation_
beyond_window=False) — every existing caller sees zero behavior change.

Run: backend/venv/Scripts/python.exe backend/testing/test_soft_consolidation.py
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dispatch_consolidation_window import is_soft_eligible
from dispatch_orchestrator import run_dispatch_simulation
from dispatch_orders import CandidateMachine, OrderChain, ScheduledOperation
from dispatch_pool import DevicePool
from dispatch_scope import ScopeOutcome

DAY0 = date(2026, 1, 1)


def availability(machine, day, shift):
    return 480.0


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
        production_order=order_id, operation_no=10, task="VB02", batch_key=f"{fixture}~key",
        balance_qty=qty, cycle_time=cycle_time, cdd=cdd, order_date=None, order_status="Active",
        production_start_date=DAY0, candidates=candidates, scope_outcome=ScopeOutcome.SCHEDULED, remark=None,
    )


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def test_is_soft_eligible_pure_logic():
    print("\n=== is_soft_eligible: pure window-math unit checks ===")
    check("within hard window -> not soft-eligible (it's already hard-eligible)", not is_soft_eligible(date(2026, 2, 1), DAY0, 60, 90))
    check("beyond window but within extra bound -> soft-eligible", is_soft_eligible(date(2026, 4, 1), DAY0, 60, 90))
    check("beyond window AND beyond extra bound -> not soft-eligible", not is_soft_eligible(date(2027, 1, 1), DAY0, 60, 90))
    check("safety stock (cdd=None) -> never soft-eligible (own mechanism is §E.10)", not is_soft_eligible(None, DAY0, 60, 90))


def test_hard_rule_unchanged_when_feature_off():
    print("\n=== Feature off (default): far order stays in its own solo run, exactly like before ===")
    near = make_op("NEAR", cdd=date(2026, 1, 20), qty=10, machines="M1", fixture="FIX-A", locator="LOC-1")
    far = make_op("FAR", cdd=date(2026, 4, 1), qty=5, machines="M1", fixture="FIX-A", locator="LOC-1")
    chains = [OrderChain(o.production_order, [o], []) for o in (near, far)]
    pool = DevicePool(quantities={"FIX-A": 2, "LOC-1": 2})
    rows = run_dispatch_simulation(
        chains, availability, pool, today=DAY0, window_days=60, cooling_minutes=20, day_zero=DAY0,
        planned_order_start_buffer_days=1, allow_soft_consolidation_beyond_window=False,
    )
    far_row = next(r for r in rows if r.production_order == "FAR")
    check("FAR still pays its own fixture_change (not riding NEAR's mount)", far_row.end.minute - far_row.start.minute == 30.0 + 10.0 + 2.0 * 5)


def test_soft_merge_accepted_when_safe():
    print("\n=== Feature on, no downstream risk: FAR rides NEAR's mount for free ===")
    near = make_op("NEAR", cdd=date(2026, 1, 20), qty=10, machines="M1", fixture="FIX-A", locator="LOC-1")
    far = make_op("FAR", cdd=date(2026, 4, 1), qty=5, machines="M1", fixture="FIX-A", locator="LOC-1")
    chains = [OrderChain(o.production_order, [o], []) for o in (near, far)]
    pool = DevicePool(quantities={"FIX-A": 2, "LOC-1": 2})
    rows = run_dispatch_simulation(
        chains, availability, pool, today=DAY0, window_days=60, cooling_minutes=20, day_zero=DAY0,
        planned_order_start_buffer_days=1, allow_soft_consolidation_beyond_window=True, soft_consolidation_max_extra_days=90,
    )
    check("exactly one row per order (no duplicate from the merge)", len([r for r in rows if r.production_order == "FAR"]) == 1)
    far_row = next(r for r in rows if r.production_order == "FAR")
    check("FAR appended for free (charge=none -> LUT*qty=10, not a fresh fixture_change)", far_row.end.minute - far_row.start.minute == 10.0)
    check("FAR runs on the same machine as NEAR (actually merged, not just coincidentally placed)", far_row.machine == "M1")


def test_soft_merge_rejected_when_it_would_breach_a_committed_order():
    print("\n=== Feature on, but merging would delay a later committed order: rejected ===")
    # Priority order (lower CDD = higher priority): NEAR, then LATE, then FAR
    # — so the merge DECISION (made at NEAR's turn) happens before LATE is
    # placed, and can still affect it. NEAR anchors FIX-A on M1; FAR
    # (soft-eligible, same FIX-A/LOC-1) is a big batch — merged in at "none"
    # charge it adds 2000 minutes to M1's queue, rolling into the next
    # calendar day. LATE (different fixture, same machine M1) is due THE
    # SAME DAY as NEAR with no slack at all — any rollover breaches it.
    near = make_op("NEAR", cdd=date(2025, 12, 31), qty=10, machines="M1", fixture="FIX-A", locator="LOC-1", cycle_time=1.0)
    far = make_op("FAR", cdd=date(2026, 4, 1), qty=1000, machines="M1", fixture="FIX-A", locator="LOC-1")
    late = make_op("LATE", cdd=DAY0, qty=1, machines="M1", fixture="FIX-B", locator="LOC-2", cycle_time=1.0)
    chains = [OrderChain(o.production_order, [o], []) for o in (near, far, late)]

    pool_naive = DevicePool(quantities={"FIX-A": 2, "LOC-1": 2, "FIX-B": 1, "LOC-2": 1})
    rows_naive = run_dispatch_simulation(
        chains, availability, pool_naive, today=DAY0, window_days=60, cooling_minutes=20, day_zero=DAY0,
        planned_order_start_buffer_days=1, allow_soft_consolidation_beyond_window=False,
    )
    late_naive = next(r for r in rows_naive if r.production_order == "LATE")
    far_naive = next(r for r in rows_naive if r.production_order == "FAR")
    check("baseline (feature off): LATE meets its CDD", late_naive.scheduled_date <= DAY0)

    pool_soft = DevicePool(quantities={"FIX-A": 2, "LOC-1": 2, "FIX-B": 1, "LOC-2": 1})
    rows_soft = run_dispatch_simulation(
        chains, availability, pool_soft, today=DAY0, window_days=60, cooling_minutes=20, day_zero=DAY0,
        planned_order_start_buffer_days=1, allow_soft_consolidation_beyond_window=True, soft_consolidation_max_extra_days=90,
    )
    far_soft = next(r for r in rows_soft if r.production_order == "FAR")
    late_soft = next(r for r in rows_soft if r.production_order == "LATE")
    check("FAR was NOT merged (same placement as the feature-off baseline)", far_soft.start == far_naive.start and far_soft.end == far_naive.end)
    check("LATE still meets its CDD (the speculative check protected it)", late_soft.scheduled_date <= DAY0)


if __name__ == "__main__":
    test_is_soft_eligible_pure_logic()
    test_hard_rule_unchanged_when_feature_off()
    test_soft_merge_accepted_when_safe()
    test_soft_merge_rejected_when_it_would_breach_a_committed_order()
    print("\n[OK] Soft consolidation beyond the window: hard rule stays default, opt-in merge only ever accepted when provably safe.")
