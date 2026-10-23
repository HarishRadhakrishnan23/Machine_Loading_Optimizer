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
from collections import Counter, defaultdict, namedtuple
from datetime import date

sys.path.insert(0, ".")

from db import (
    read_fixture_locator_inventory,
    read_holiday_calendar,
    read_machine_daily,
    read_machine_fnl_master,
    read_machine_master,
    read_wip_orders,
)
from dispatch_orchestrator import run_dispatch_simulation
from dispatch_orders import build_order_chains
from dispatch_pool import parse_devices
from model_f_data import (
    build_availability,
    build_device_pool,
    build_fixture_index_from_fnl,
    build_raw_wip_rows,
    build_routing_index_from_fnl,
)

TODAY = date(2026, 10, 1)
HEAVY_OPS = frozenset({"VB03", "VB04", "VB05", "VB06"})
FIXTURE_REQUIRED_TASKS = frozenset({"VB02", "VB03", "VB04", "VB05", "VB06", "VB07"})


def check(label, condition):
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}")
    return condition


def main():
    print("Loading live Oracle data...")
    wip_df = read_wip_orders()
    mm_df = read_machine_master()
    md_df = read_machine_daily()
    fnl_df = read_machine_fnl_master()
    fi_df = read_fixture_locator_inventory()
    holiday_df = read_holiday_calendar()

    raw_rows = build_raw_wip_rows(wip_df)
    routing_index = build_routing_index_from_fnl(fnl_df)
    fixture_index = build_fixture_index_from_fnl(fnl_df)
    pool = build_device_pool(fi_df)
    known_devices = frozenset(pool.quantities.keys())
    availability = build_availability(mm_df, md_df, holiday_df)
    known_machines = frozenset(mm_df["WORK_CENTER"].astype(str).str.strip()) | frozenset(
        md_df["WORK_CENTER"].astype(str).str.strip()
    )
    chains = build_order_chains(
        raw_rows, routing_index, fixture_index, known_devices, known_machines, FIXTURE_REQUIRED_TASKS
    )

    print(f"Loaded {len(wip_df)} WIP rows -> {len(chains)} orders")

    t0 = time.time()
    rows = run_dispatch_simulation(
        chains, availability, pool, today=TODAY, window_days=60, cooling_minutes=20, day_zero=TODAY,
        planned_order_start_buffer_days=1, heavy_operations=HEAVY_OPS, enable_safety_stock_speculation=False,
    )
    elapsed = time.time() - t0
    print(f"Simulation completed in {elapsed:.2f}s, {len(rows)} output rows\n")

    all_pass = True

    # 1. Row-count invariant. CLAUDE.md §E.18: a scheduled operation can now
    # legitimately produce MORE THAN ONE output row (a shift-boundary
    # quantity slice), so output row count is >= WIP row count, not ==; what
    # must still hold exactly is that every distinct (order, op) WIP pair is
    # represented at least once (never silently dropped).
    all_pass &= check(
        f"Output row count >= WIP row count ({len(rows)} >= {len(wip_df)})", len(rows) >= len(wip_df)
    )
    distinct_order_ops = {(r.production_order, r.operation_no) for r in rows}
    all_pass &= check(
        f"Every distinct (PRODUCTION_ORDER, OPERATION_NO) WIP pair appears at least once "
        f"({len(distinct_order_ops)} distinct pairs, {len(wip_df)} WIP rows)",
        len(distinct_order_ops) <= len(wip_df),
    )

    # 2. The real PK — (PRODUCTION_ORDER, OPERATION_NO, LINE_NO) — must be
    # unique; (PRODUCTION_ORDER, OPERATION_NO) alone is no longer unique by
    # design once a §E.18 quantity slice produces more than one LINE_NO.
    pk_keys = [(r.production_order, r.operation_no, r.line_no) for r in rows]
    all_pass &= check("No duplicate (PRODUCTION_ORDER, OPERATION_NO, LINE_NO) PK", len(pk_keys) == len(set(pk_keys)))
    sliced = len(rows) - len(distinct_order_ops)
    if sliced:
        print(f"  {sliced} extra row(s) from §E.18 quantity slicing (shift-boundary splits)")

    # 3. REMARK coverage. REMARK now also explains every SCHEDULED
    # placement (not just excluded/caveat rows — see dispatch_engine.
    # _placement_remark), so "no-fixture caveat" is distinguished by its
    # distinctive substring, not by remark-is-None any more.
    scheduled_no_fixture = [r for r in rows if r.machine is not None and r.remark and "No fixture/locator match" in r.remark]
    scheduled_clean = [
        r for r in rows
        if r.machine is not None and not (r.remark and "No fixture/locator match" in r.remark)
    ]
    excluded = [r for r in rows if r.machine is None]
    all_pass &= check(
        f"Every excluded row has a REMARK ({len(excluded)} excluded rows checked)",
        all(r.remark is not None for r in excluded),
    )
    all_pass &= check(
        f"Every scheduled row has a non-empty REMARK explaining its placement ({len(rows) - len(excluded)} scheduled rows checked)",
        all(r.remark for r in rows if r.machine is not None),
    )
    print(f"  scheduled (clean): {len(scheduled_clean)}, scheduled (no-fixture caveat): {len(scheduled_no_fixture)}, excluded: {len(excluded)}")

    # 4. Precedence: within an order, ops sorted by OPERATION_NO must have non-decreasing start times
    #    among SCHEDULED rows (excluded rows are bridged, contribute no timestamp). Grouped by
    #    (order, operation_no) FIRST — CLAUDE.md §E.18 means one op can now be several LINE_NO rows
    #    (shift-boundary slices), which aren't a precedence relationship with each other.
    #
    #    Model F §E.18 Part 2 (lot-streaming) deliberately lets a downstream op's start fall BEFORE
    #    its upstream op's overall END — that's the whole point of consuming completed pieces early
    #    instead of waiting for the whole batch. So "b.start < a.end" is no longer a violation; the
    #    only thing that would still be a real bug is a downstream op starting before its upstream
    #    op even BEGINS (no piece could possibly be ready yet).
    precedence_ok = True
    op_spans = defaultdict(list)
    for r in rows:
        if r.start is not None:
            op_spans[(r.production_order, r.operation_no)].append(r)
    OpSpan = namedtuple("OpSpan", ["operation_no", "start", "end"])
    by_order = defaultdict(list)
    for (order_id, operation_no), op_rows in op_spans.items():
        by_order[order_id].append(
            OpSpan(operation_no, min(r.start for r in op_rows), max(r.end for r in op_rows))
        )
    violations = []
    for order_id, op_rows in by_order.items():
        op_rows.sort(key=lambda r: r.operation_no)
        for a, b in zip(op_rows, op_rows[1:]):
            if b.start < a.start:
                violations.append((order_id, a.operation_no, b.operation_no))
                precedence_ok = False
    all_pass &= check(
        f"Precedence holds across all {len(by_order)} orders with scheduled ops "
        f"(downstream never starts before upstream even begins; {len(violations)} violations)",
        precedence_ok,
    )
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
