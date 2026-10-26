"""
report_priority_load_imbalance.py — read-only live-data diagnostic + Excel
report for the "priority-1 machine absorbs all demand for a combo, leaving a
capable priority-2+ machine idle for a shift/day+ at a time" pattern
(confirmed live on 2PMT16/3PMC22 for a VB07 combo — see CLAUDE.md §E.17's own
note on this).

For every (SIZE_INCH, CLASS, MOC, DESIGN, TASK) combo with 2+ candidate
machines in MCH_MACHINE_FNL_MASTER, compares how many scheduled jobs actually
landed on each candidate. If the top-priority machine took the work and a
lower-priority candidate took (near) none AND that lower-priority machine has
at least one idle gap of a shift or more somewhere in its own schedule (proof
it had spare capacity), the combo is flagged with a recommended action.

Writes an .xlsx report. Does NOT write to Oracle.
"""

import sys
import warnings
from collections import Counter, defaultdict
from datetime import date

import pandas as pd

warnings.filterwarnings("ignore")
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
from model_f_data import (
    build_availability,
    build_device_pool,
    build_fixture_index_from_fnl,
    build_raw_wip_rows,
    build_routing_index_from_fnl,
)

TODAY = date.today()
HEAVY_OPS = frozenset({"VB03", "VB04", "VB05", "VB06"})
FIXTURE_REQUIRED_TASKS = frozenset({"VB02", "VB03", "VB04", "VB05", "VB06", "VB07"})

# A gap must be at least this long to count as "spare capacity went unused"
# (CLAUDE.md's own 2PMT16 finding describes gaps measured in shifts/days, not
# minutes) — ~1 working shift.
IDLE_GAP_THRESHOLD_MINUTES = 450.0
# A lower-priority machine must have taken no more than this many jobs for the
# combo to count as "never got a turn" (small tolerance for a handful of
# dropout/overflow placements, not a real share of the work).
NEAR_ZERO_JOB_TOLERANCE = 2
# The dominant machine must have taken at least this many jobs for the combo
# to be a real, meaningful pattern rather than 1-2-job noise.
MIN_DOMINANT_JOBS = 5


def _gap_days(start, end) -> float:
    return (end.day - start.day).days + (end.shift_index - start.shift_index) / 3.0


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

    rows = run_dispatch_simulation(
        chains, availability, pool, today=TODAY, window_days=60, cooling_minutes=20, day_zero=TODAY,
        planned_order_start_buffer_days=1, heavy_operations=HEAVY_OPS, enable_safety_stock_speculation=False,
    )
    scheduled = [r for r in rows if r.machine is not None and r.start is not None]
    print(f"Simulation produced {len(rows)} rows ({len(scheduled)} scheduled)")

    # 1. Per-machine idle gaps >= threshold.
    by_machine = defaultdict(list)
    for r in scheduled:
        by_machine[r.machine].append((r.start, r.end))
    idle_gaps = defaultdict(list)  # machine -> [(gap_start, gap_end, days)]
    for machine, spans in by_machine.items():
        spans.sort(key=lambda s: s[0])
        for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
            if s2 > e1:
                gap_minutes_proxy = _gap_days(e1, s2) * 1440.0
                if gap_minutes_proxy >= IDLE_GAP_THRESHOLD_MINUTES:
                    idle_gaps[machine].append((e1, s2, round(_gap_days(e1, s2), 1)))
    machines_with_spare_capacity = frozenset(idle_gaps.keys())
    print(f"Machines with >=1 idle gap of a shift+: {len(machines_with_spare_capacity)}")

    # 2. Per (combo, task) job counts by machine, from the ACTUAL schedule.
    job_counts = defaultdict(Counter)  # (size, class, moc, design, task) -> Counter(machine -> job count)
    for r in scheduled:
        if not r.batch_key or not r.task:
            continue
        parts = r.batch_key.split("~")
        if len(parts) != 4:
            continue
        key = (parts[0], parts[1], parts[2], parts[3], r.task)
        job_counts[key][r.machine] += 1

    # 3. Flag combos where a lower-priority candidate was starved while a
    #    higher-priority one absorbed the work, AND the starved machine had
    #    spare capacity (an idle gap) elsewhere.
    findings = []
    for (size, cls, moc, design, task), counts in job_counts.items():
        candidates = routing_index.candidates_for(size, cls, moc, design, task)
        if len(candidates) < 2:
            continue
        candidates_sorted = sorted(candidates, key=lambda c: c[1])  # by priority ascending
        top_machine, top_priority = candidates_sorted[0]
        top_jobs = counts.get(top_machine, 0)
        if top_jobs < MIN_DOMINANT_JOBS:
            continue
        for machine, priority in candidates_sorted[1:]:
            starved_jobs = counts.get(machine, 0)
            if starved_jobs > NEAR_ZERO_JOB_TOLERANCE:
                continue
            if machine not in machines_with_spare_capacity:
                continue
            worst_gap = max(idle_gaps[machine], key=lambda g: g[2])
            findings.append({
                "SIZE_INCH": size,
                "CLASS": cls,
                "MOC": moc,
                "DESIGN": design,
                "TASK": task,
                "Dominant machine (higher priority)": top_machine,
                "Dominant machine PRIORITY": top_priority,
                "Dominant machine job count": top_jobs,
                "Starved machine (lower priority)": machine,
                "Starved machine PRIORITY": priority,
                "Starved machine job count": starved_jobs,
                "Starved machine's largest idle gap (days)": worst_gap[2],
                "Idle gap window": f"{worst_gap[0].day} -> {worst_gap[1].day}",
                "Remark / recommended change": (
                    f"{top_machine} (PRIORITY={top_priority}) absorbed all {top_jobs} job(s) for this combo "
                    f"while {machine} (PRIORITY={priority}) — a listed capable machine — received only "
                    f"{starved_jobs} and sat idle for {worst_gap[2]} day(s). Re-rank PRIORITY for this "
                    f"SIZE~CLASS~MOC~DESIGN~TASK in MCH_MACHINE_FNL_MASTER (e.g. make {machine} co-equal or "
                    f"route a portion of new orders to it) to share load and raise aggregate throughput."
                ),
            })

    findings.sort(key=lambda f: f["Starved machine's largest idle gap (days)"], reverse=True)
    print(f"\nFlagged {len(findings)} combo/machine-pair imbalance(s)")

    out_path = "priority_load_imbalance_report.xlsx"
    if findings:
        pd.DataFrame(findings).to_excel(out_path, index=False, sheet_name="Load Imbalance")
    else:
        pd.DataFrame([{"Result": "No priority-load-imbalance combos found with current thresholds"}]).to_excel(
            out_path, index=False
        )
    print(f"Report written: {out_path}")


if __name__ == "__main__":
    main()
