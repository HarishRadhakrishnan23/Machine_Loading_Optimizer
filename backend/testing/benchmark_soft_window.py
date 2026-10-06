"""
benchmark_soft_window.py — read-only diagnostic for the proposed "soft
consolidation beyond batch_consolidation_window_days" feature. Answers two
questions against LIVE Oracle WIP data before any code gets written:

  1. How many extra fixture runs would even qualify for the new speculative
     check (i.e. how many runs gain an out-of-window member once the window
     is lifted)? -> compares plan_task_pool's batching output with the
     window enforced (today) vs. effectively unlimited.
  2. What does one real speculative fork actually cost on this machine? ->
     times evaluate_branches_in_parallel with the real _continuation_verdict
     target over a real slice of a real run's remaining WorkItems.

Never writes to Oracle. Run: backend/venv/Scripts/python.exe backend/testing/benchmark_soft_window.py
"""

import sys
import time
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
from dispatch_engine import plan_task_pool
from dispatch_orchestrator import _build_work_items, _continuation_verdict, _initial_ready_at, _place_work_item
from dispatch_orders import build_order_chains
from dispatch_parallel import Branch, evaluate_branches_in_parallel
from model_e_data import (
    build_availability,
    build_device_pool,
    build_fixture_index,
    build_raw_wip_rows,
    build_routing_index,
)

TODAY = date(2026, 10, 1)
WINDOW_DAYS = 60
UNLIMITED_WINDOW = 10_000_000


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
    print(f"Loaded {len(wip_df)} WIP rows -> {len(chains)} orders\n")

    # ---- Q1: how many fixture runs would gain an out-of-window member? ----
    print("=" * 70)
    print("Q1 — how many fixture runs change membership if the window is lifted?")
    print("=" * 70)

    max_depth = max((len(c.schedulable) for c in chains), default=0)
    runs_today = 0
    runs_unlimited = 0
    runs_with_new_members = 0
    mixed_runs_with_new_members = 0  # the actual trigger count for the new check

    for depth in range(max_depth):
        by_task = {}
        for c in chains:
            if depth < len(c.schedulable):
                by_task.setdefault(c.schedulable[depth].task, []).append(c.schedulable[depth])

        for task, ops in by_task.items():
            plan_today = plan_task_pool(ops, TODAY, WINDOW_DAYS)
            plan_unlimited = plan_task_pool(ops, TODAY, UNLIMITED_WINDOW)
            runs_today += len(plan_today.fixture_runs)
            runs_unlimited += len(plan_unlimited.fixture_runs)

            # Membership signature per run (set of order_ids), keyed by the
            # run's anchor (first member) so we can match "same run" across
            # the two plans even if later membership differs.
            def sig(run_steps):
                return frozenset(s.order_id for s in run_steps)

            today_sets = [sig(r) for r in plan_today.fixture_runs]
            for run_steps in plan_unlimited.fixture_runs:
                u_set = sig(run_steps)
                # Does this unlimited-plan run correspond to a today-plan run
                # with the SAME anchor (first element) but MORE members?
                if not run_steps:
                    continue
                anchor = run_steps[0].order_id
                match = next((t for t in today_sets if t and next(iter(t)) == anchor), None)
                # anchor-order check is approximate; fall back to any superset match
                match = match or next((t for t in today_sets if t < u_set), None)
                if match is not None and u_set > match:
                    runs_with_new_members += 1
                    has_in_window = any(
                        op.cdd is not None and (op.cdd - TODAY).days <= WINDOW_DAYS
                        for op in ops if op.production_order in match
                    )
                    if has_in_window:
                        mixed_runs_with_new_members += 1

    print(f"  Fixture runs formed today (window={WINDOW_DAYS}d):      {runs_today}")
    print(f"  Fixture runs formed with window lifted entirely:        {runs_unlimited}")
    print(f"  Runs that would gain >=1 new (out-of-window) member:    {runs_with_new_members}")
    print(f"  ...of those, runs that are genuinely MIXED (would need  a speculative check): {mixed_runs_with_new_members}")

    # ---- Q2: real cost of one speculative fork ----
    print("\n" + "=" * 70)
    print("Q2 — real wall-clock cost of one speculative fork on this machine")
    print("=" * 70)

    order_chains_sorted = chains
    work_items = _build_work_items(order_chains_sorted, TODAY, WINDOW_DAYS)
    print(f"  Total WorkItems in a full run: {len(work_items)}")

    cdd_by_order = {c.production_order: (c.schedulable[0].cdd if c.schedulable else None) for c in chains}
    day_zero = TODAY

    def real_state_at(resume_index):
        """Actually places work_items[0:resume_index] sequentially (single
        process, no speculation) to get a REAL ready_at/machine_free_at/
        placed_by_order snapshot — forking from an empty dict is invalid.
        Matches run_dispatch_simulation's own setup: ready_at is pre-seeded
        for every order upfront (not built incrementally), via _initial_ready_at."""
        ready_at = {
            c.production_order: _initial_ready_at(c.schedulable[0], day_zero, 1)
            for c in chains if c.schedulable
        }
        machine_free_at, placed_by_order = {}, {c.production_order: [] for c in chains}
        p = pool.clone()
        for i in range(resume_index):
            for placed in _place_work_item(work_items[i], ready_at, machine_free_at, p, availability, day_zero, 20.0):
                placed_by_order.setdefault(placed.production_order, []).append(placed)
        return ready_at, machine_free_at, p, placed_by_order

    for label, frac in [("25% through", 0.25), ("50% through", 0.50), ("75% through", 0.75)]:
        resume_index = int(len(work_items) * frac)
        ready_at, machine_free_at, p, placed_by_order = real_state_at(resume_index)

        t0 = time.time()
        branches = [
            Branch("a", _continuation_verdict,
                   (work_items, resume_index, dict(ready_at), dict(machine_free_at), p.clone(),
                    {k: list(v) for k, v in placed_by_order.items()}, cdd_by_order, availability, day_zero, 20.0)),
            Branch("b", _continuation_verdict,
                   (work_items, resume_index, dict(ready_at), dict(machine_free_at), p.clone(),
                    {k: list(v) for k, v in placed_by_order.items()}, cdd_by_order, availability, day_zero, 20.0)),
        ]
        results = evaluate_branches_in_parallel(branches)
        elapsed = time.time() - t0
        print(f"  Fork at {label} ({resume_index}/{len(work_items)} items remaining -> {len(work_items) - resume_index}): {elapsed:.2f}s")

    print("\n" + "=" * 70)
    print("EXTRAPOLATION")
    print("=" * 70)
    print(f"  {mixed_runs_with_new_members} runs today would actually trigger the new check.")
    print(f"  Multiply that by whichever fork cost above best matches WHERE in the run")
    print(f"  those 12 runs tend to fall (early runs = more remaining work = costlier fork).")


if __name__ == "__main__":
    main()
