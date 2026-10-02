"""
e6_validate_real_data.py — Engine 1 Model E, E-6 validation: run the full
dispatch simulation against LIVE Oracle WIP data and audit the invariants
CLAUDE.md's §E.17-equivalent (D.17 in the historical trace) demands:

  - Every eligible WIP row produces exactly one output row.
  - No (PRODUCTION_ORDER, OPERATION_NO) appears twice.
  - Precedence holds: within an order, op N+1 never starts before op N ends.
  - The fixture/locator pool is never oversubscribed (post-hoc interval audit).
  - REMARK coverage: every excluded row has a REMARK, every scheduled row
    (fixture or plain-routing) has none unless it's the no-fixture caveat.
  - CDD attainment: how many committed orders finish on/before their CDD.

This is a read-only diagnostic script — it does NOT write to Oracle.
"""

import sys
import time
from collections import Counter, defaultdict
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


def check(label, condition):
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}")
    return condition


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
    pool = build_device_pool(fi_df)
    known_devices = frozenset(pool.quantities.keys())
    availability = build_availability(mm_df, md_df, holiday_df)
    chains = build_order_chains(raw_rows, routing_index, fixture_index, known_devices)

    print(f"Loaded {len(wip_df)} WIP rows -> {len(chains)} orders")

    t0 = time.time()
    rows = run_dispatch_simulation(
        chains, availability, pool, today=TODAY, window_days=60, cooling_minutes=20, day_zero=TODAY,
        planned_order_start_buffer_days=1, heavy_operations=HEAVY_OPS, enable_safety_stock_speculation=False,
    )
    elapsed = time.time() - t0
    print(f"Simulation completed in {elapsed:.2f}s, {len(rows)} output rows\n")

    all_pass = True

    # 1. Row-count invariant.
    all_pass &= check(f"Row count matches WIP row count exactly ({len(rows)} == {len(wip_df)})", len(rows) == len(wip_df))

    # 2. No duplicate (order, op) pairs.
    keys = [(r.production_order, r.operation_no) for r in rows]
    all_pass &= check("No duplicate (PRODUCTION_ORDER, OPERATION_NO) pairs", len(keys) == len(set(keys)))

    # 3. REMARK coverage.
    scheduled_clean = [r for r in rows if r.machine is not None and r.remark is None]
    scheduled_no_fixture = [r for r in rows if r.machine is not None and r.remark is not None]
    excluded = [r for r in rows if r.machine is None]
    all_pass &= check(
        f"Every excluded row has a REMARK ({len(excluded)} excluded rows checked)",
        all(r.remark is not None for r in excluded),
    )
    all_pass &= check(
        f"Every scheduled row either has no REMARK or the no-fixture caveat ({len(scheduled_no_fixture)} with caveat)",
        all("No fixture/locator match" in r.remark for r in scheduled_no_fixture),
    )
    print(f"  scheduled (clean): {len(scheduled_clean)}, scheduled (no-fixture caveat): {len(scheduled_no_fixture)}, excluded: {len(excluded)}")

    # 4. Precedence: within an order, ops sorted by OPERATION_NO must have non-decreasing start times
    #    among SCHEDULED rows (excluded rows are bridged, contribute no timestamp).
    precedence_ok = True
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
                precedence_ok = False
    all_pass &= check(f"Precedence holds across all {len(by_order)} orders with scheduled ops ({len(violations)} violations)", precedence_ok)
    if violations[:5]:
        print("    sample violations:", violations[:5])

    # 5. Pool never oversubscribed — independent post-hoc interval audit (not reusing dispatch_pool's
    #    own bookkeeping, to catch a bug in that bookkeeping rather than just confirming it agrees with itself).
    device_intervals = defaultdict(list)
    for r in rows:
        if r.fixture_id:
            for d in parse_devices(r.fixture_id):
                device_intervals[d].append((r.start, r.end))
        if r.locator_id:
            for d in parse_devices(r.locator_id):
                device_intervals[d].append((r.start, r.end))

    pool_ok = True
    worst = []
    for device, intervals in device_intervals.items():
        quantity = pool.quantities.get(device)
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
            worst.append((device, peak, quantity))
    all_pass &= check(f"No device's concurrent usage ever exceeds its QUANTITY ({len(device_intervals)} devices checked)", pool_ok)
    if worst[:5]:
        print("    sample overages (device, peak_concurrent, quantity):", worst[:5])

    # 6. CDD attainment for committed orders.
    completion_by_order = {}
    for r in rows:
        if r.order_completion_date is not None:
            completion_by_order[r.production_order] = r.order_completion_date
    committed_cdd = {c.production_order: c.schedulable[0].cdd for c in chains if c.schedulable and c.schedulable[0].cdd is not None}
    on_time = sum(1 for oid, cdd in committed_cdd.items() if oid in completion_by_order and completion_by_order[oid] <= cdd)
    no_completion = sum(1 for oid in committed_cdd if oid not in completion_by_order)
    print(f"\n  Committed orders with a CDD: {len(committed_cdd)}")
    print(f"  ...completing on/before CDD: {on_time} ({100*on_time/max(1,len(committed_cdd)):.1f}%)")
    print(f"  ...with no completion date at all (fully excluded order): {no_completion}")

    print(f"\n{'='*60}")
    print("ALL CHECKS PASSED" if all_pass else "SOME CHECKS FAILED")
    print(f"{'='*60}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
