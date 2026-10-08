"""
e6_validate_soft_consolidation.py — E-6-style live-data validation for the
soft-consolidation-beyond-window feature (allow_soft_consolidation_beyond_window
/ soft_consolidation_max_extra_days). Runs the full dispatch simulation
against LIVE Oracle WIP data TWICE — feature off (today's default/baseline)
and feature on — and checks:

  - Every invariant e6_validate_real_data.py already checks (row count,
    no duplicate keys, precedence, pool never oversubscribed) STILL holds
    with the feature on — this is not a separate, looser bar.
  - Zero committed orders go from "meets CDD" to "breaches CDD" when the
    feature is turned on (the whole point of the speculative check).
  - The utilization gain: how many committed-vs-committed merges actually
    happened, and the CDD-attainment delta.
  - Runtime delta (the extra forks cost real wall-clock time — measured,
    not assumed).

Read-only — never writes to Oracle.
"""

import sys
import time
from collections import defaultdict
from datetime import date

sys.path.insert(0, ".")

from db import (
    read_fixture_locator_inventory,
    read_fixture_locator_master,
    read_holiday_calendar,
    read_machine_daily,
    read_machine_master,
    read_routing_master,
    read_wip_orders,
)
from dispatch_orchestrator import run_dispatch_simulation
from dispatch_orders import build_order_chains
from dispatch_pool import parse_devices
from model_e_data import (
    build_availability,
    build_device_pool,
    build_fixture_index,
    build_raw_wip_rows,
    build_routing_index,
)

TODAY = date(2026, 10, 1)
HEAVY_OPS = frozenset({"VB03", "VB04", "VB05", "VB06"})
SOFT_MAX_EXTRA_DAYS = 90


def check(label, condition):
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}")
    return condition


def audit_invariants(label, rows, wip_count, pool_quantities):
    """The same structural invariants e6_validate_real_data.py checks, run
    against this run's own rows — the feature must never weaken these."""
    all_pass = True
    all_pass &= check(f"[{label}] row count matches WIP row count ({len(rows)} == {wip_count})", len(rows) == wip_count)

    keys = [(r.production_order, r.operation_no) for r in rows]
    all_pass &= check(f"[{label}] no duplicate (PRODUCTION_ORDER, OPERATION_NO) pairs", len(keys) == len(set(keys)))

    by_order = defaultdict(list)
    for r in rows:
        if r.start is not None:
            by_order[r.production_order].append(r)
    violations = []
    for order_id, op_rows in by_order.items():
        op_rows.sort(key=lambda r: r.operation_no)
        for a, b in zip(op_rows, op_rows[1:]):
            if b.start < a.end:
                violations.append((order_id, a.operation_no, b.operation_no))
    all_pass &= check(f"[{label}] precedence holds across all {len(by_order)} orders ({len(violations)} violations)", len(violations) == 0)

    device_intervals = defaultdict(list)
    for r in rows:
        if r.fixture_id:
            for d in parse_devices(r.fixture_id):
                device_intervals[d].append((r.start, r.end))
        if r.locator_id:
            for d in parse_devices(r.locator_id):
                device_intervals[d].append((r.start, r.end))
    pool_ok = True
    for device, intervals in device_intervals.items():
        quantity = pool_quantities.get(device)
        if quantity is None:
            continue
        events = []
        for s, e in intervals:
            events.append((s, 1))
            events.append((e, -1))
        events.sort(key=lambda ev: (ev[0], ev[1]))
        running = peak = 0
        for _, delta in events:
            running += delta
            peak = max(peak, running)
        if peak > quantity:
            pool_ok = False
    all_pass &= check(f"[{label}] no device's concurrent usage ever exceeds its QUANTITY ({len(device_intervals)} devices)", pool_ok)

    return all_pass


def cdd_attainment(rows, chains):
    completion_by_order = {}
    for r in rows:
        if r.order_completion_date is not None:
            completion_by_order[r.production_order] = r.order_completion_date
    committed_cdd = {c.production_order: c.schedulable[0].cdd for c in chains if c.schedulable and c.schedulable[0].cdd is not None}
    on_time = {oid for oid, cdd in committed_cdd.items() if oid in completion_by_order and completion_by_order[oid] <= cdd}
    breached = set(committed_cdd) - on_time
    return committed_cdd, on_time, breached


def main():
    print("Loading live Oracle data...")
    wip_df = read_wip_orders()
    mm_df = read_machine_master()
    md_df = read_machine_daily()
    rt_df = read_routing_master()
    fl_df = read_fixture_locator_master()
    fi_df = read_fixture_locator_inventory()
    holiday_df = read_holiday_calendar()

    raw_rows = build_raw_wip_rows(wip_df)
    routing_index = build_routing_index(rt_df)
    fixture_index = build_fixture_index(fl_df)
    pool_off = build_device_pool(fi_df)
    pool_on = build_device_pool(fi_df)
    known_devices = frozenset(pool_off.quantities.keys())
    availability = build_availability(mm_df, md_df, holiday_df)
    chains = build_order_chains(raw_rows, routing_index, fixture_index, known_devices)
    print(f"Loaded {len(wip_df)} WIP rows -> {len(chains)} orders\n")

    print("=" * 70)
    print("BASELINE — allow_soft_consolidation_beyond_window=False (today's default)")
    print("=" * 70)
    t0 = time.time()
    rows_off = run_dispatch_simulation(
        chains, availability, pool_off, today=TODAY, window_days=60, cooling_minutes=20, day_zero=TODAY,
        planned_order_start_buffer_days=1, heavy_operations=HEAVY_OPS, enable_safety_stock_speculation=True,
        allow_soft_consolidation_beyond_window=False,
    )
    elapsed_off = time.time() - t0
    print(f"Completed in {elapsed_off:.2f}s, {len(rows_off)} output rows")
    all_pass = audit_invariants("OFF", rows_off, len(wip_df), pool_off.quantities)
    cdd_off, on_time_off, breached_off = cdd_attainment(rows_off, chains)

    print("\n" + "=" * 70)
    print(f"FEATURE ON — allow_soft_consolidation_beyond_window=True, max_extra_days={SOFT_MAX_EXTRA_DAYS}")
    print("=" * 70)
    t0 = time.time()
    rows_on = run_dispatch_simulation(
        chains, availability, pool_on, today=TODAY, window_days=60, cooling_minutes=20, day_zero=TODAY,
        planned_order_start_buffer_days=1, heavy_operations=HEAVY_OPS, enable_safety_stock_speculation=True,
        allow_soft_consolidation_beyond_window=True, soft_consolidation_max_extra_days=SOFT_MAX_EXTRA_DAYS,
    )
    elapsed_on = time.time() - t0
    print(f"Completed in {elapsed_on:.2f}s, {len(rows_on)} output rows")
    all_pass &= audit_invariants("ON", rows_on, len(wip_df), pool_on.quantities)
    cdd_on, on_time_on, breached_on = cdd_attainment(rows_on, chains)

    print("\n" + "=" * 70)
    print("SAFETY CHECK — no committed order goes from meeting its CDD to breaching it")
    print("=" * 70)
    newly_breached = breached_on - breached_off
    all_pass &= check(
        f"zero orders newly breached by turning the feature on ({len(newly_breached)} found)",
        len(newly_breached) == 0,
    )
    if newly_breached:
        print("    newly breached orders:", sorted(newly_breached)[:10])

    print("\n" + "=" * 70)
    print("UTILIZATION GAIN")
    print("=" * 70)
    print(f"  Committed orders with a CDD: {len(cdd_off)}")
    print(f"  On-time OFF: {len(on_time_off)} ({100*len(on_time_off)/max(1,len(cdd_off)):.1f}%)")
    print(f"  On-time ON:  {len(on_time_on)} ({100*len(on_time_on)/max(1,len(cdd_off)):.1f}%)")
    newly_on_time = on_time_on - on_time_off
    print(f"  Orders newly meeting CDD (improved by the merge): {len(newly_on_time)}")
    if newly_on_time:
        print("    e.g.:", sorted(newly_on_time)[:10])

    # Fixture-change-event count as a cheap utilization proxy: fewer distinct
    # (fixture, start-timestamp) mounts for the same device = fewer changeovers.
    def mount_count(rows):
        mounts = set()
        for r in rows:
            if r.fixture_id and r.start is not None:
                mounts.add((r.fixture_id, r.start))
        return len(mounts)

    print(f"  Distinct fixture mounts OFF: {mount_count(rows_off)}")
    print(f"  Distinct fixture mounts ON:  {mount_count(rows_on)}")

    print("\n" + "=" * 70)
    print("RUNTIME")
    print("=" * 70)
    print(f"  OFF: {elapsed_off:.2f}s")
    print(f"  ON:  {elapsed_on:.2f}s")
    print(f"  Delta: {elapsed_on - elapsed_off:+.2f}s ({100*(elapsed_on-elapsed_off)/max(0.01,elapsed_off):+.1f}%)")

    print(f"\n{'='*70}")
    print("ALL CHECKS PASSED" if all_pass else "SOME CHECKS FAILED")
    print(f"{'='*70}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
