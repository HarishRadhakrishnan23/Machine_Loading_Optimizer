"""
report_rebalancing_recommendations.py — one combined action list for the
planner/ERP team: for each "other work" combo occupying 3PMH91/2PMC17 (the two
machines confirmed busy-but-idle in report_priority_change_impact.py), name
every capable alternate machine with its current load/idle-days so a shift
target can be picked; plus the ~8 combos from the earlier swap simulation that
actually DID move jobs when PRIORITY was re-ranked, folded into the same list
so both kinds of change land in one sheet. Read-only, live Oracle data.
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
IDLE_GAP_THRESHOLD_MINUTES = 450.0
BUSY_MACHINES = ["3PMH91", "2PMC17"]
# The combos confirmed, by actually re-simulating, to shift jobs under a
# PRIORITY swap (from report_priority_change_impact.py's output).
PARTIAL_SHIFT_COMBOS = [
    ("12", "150", "Stainless Steel", "LUG", "VB07", "2PMT16", "2PMT13"),
    ("10", "150", "Carbon Steel", "DFS", "VB07", "3PMC22", "2PMT16"),
    ("6", "150", "Stainless Steel", "LUG", "VB07", "2PML29", "3PMC22"),
    ("4", "300", "Carbon Steel", "LUG", "VB07", "2PML29", "3PMC22"),
    ("10", "150", "Carbon Steel", "DFS", "VB04", "2PMH19", "3PMH91"),
]


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
    rows = run_dispatch_simulation(
        chains, availability, pool, today=TODAY, window_days=60, cooling_minutes=20, day_zero=TODAY,
        planned_order_start_buffer_days=1, heavy_operations=HEAVY_OPS, enable_safety_stock_speculation=False,
    )
    scheduled = [r for r in rows if r.machine is not None and r.start is not None]

    job_counts = defaultdict(Counter)
    for r in scheduled:
        if not r.batch_key or not r.task:
            continue
        parts = r.batch_key.split("~")
        if len(parts) == 4:
            job_counts[(parts[0], parts[1], parts[2], parts[3], r.task)][r.machine] += 1

    by_machine = defaultdict(list)
    for r in scheduled:
        by_machine[r.machine].append((r.start, r.end))
    idle_days_total = defaultdict(float)
    for machine, spans in by_machine.items():
        spans.sort(key=lambda s: s[0])
        for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
            if s2 > e1:
                d = _gap_days(e1, s2)
                if d * 1440.0 >= IDLE_GAP_THRESHOLD_MINUTES:
                    idle_days_total[machine] += d

    out_rows = []

    # Part 1: other-work combos on the busy-but-idle machines.
    for busy_mach in BUSY_MACHINES:
        combos_here = [key for key, counts in job_counts.items() if counts.get(busy_mach, 0) > 0]
        for size, cls, moc, design, task in combos_here:
            candidates = routing_index.candidates_for(size, cls, moc, design, task)
            for alt_mach, alt_priority in sorted(candidates, key=lambda c: c[1]):
                if alt_mach == busy_mach:
                    continue
                out_rows.append({
                    "Action type": "Move other-work combo off busy machine",
                    "SIZE_INCH": size, "CLASS": cls, "MOC": moc, "DESIGN": design, "TASK": task,
                    "Currently on": busy_mach,
                    "Currently on - job count": job_counts[(size, cls, moc, design, task)].get(busy_mach, 0),
                    "Currently on - idle days": round(idle_days_total.get(busy_mach, 0.0), 1),
                    "Alternate capable machine": alt_mach,
                    "Alternate PRIORITY": alt_priority,
                    "Alternate - job count": job_counts[(size, cls, moc, design, task)].get(alt_mach, 0),
                    "Alternate - idle days": round(idle_days_total.get(alt_mach, 0.0), 1),
                    "Recommendation": (
                        f"Consider moving some {task} volume for this combo from {busy_mach} "
                        f"(busy, {idle_days_total.get(busy_mach, 0.0):.1f} idle-days elsewhere) to {alt_mach} "
                        f"(PRIORITY={alt_priority}, {idle_days_total.get(alt_mach, 0.0):.1f} idle-days) to free "
                        f"capacity for the starved combos it should be absorbing instead."
                    ),
                })

    # Part 2: confirmed-effective PRIORITY re-ranks (actually moved jobs under simulation).
    for size, cls, moc, design, task, dominant, starved in PARTIAL_SHIFT_COMBOS:
        counts = job_counts.get((size, cls, moc, design, task), Counter())
        out_rows.append({
            "Action type": "Re-rank PRIORITY (confirmed by simulation to shift load)",
            "SIZE_INCH": size, "CLASS": cls, "MOC": moc, "DESIGN": design, "TASK": task,
            "Currently on": dominant,
            "Currently on - job count": counts.get(dominant, 0),
            "Currently on - idle days": round(idle_days_total.get(dominant, 0.0), 1),
            "Alternate capable machine": starved,
            "Alternate PRIORITY": "promote to tie/above dominant",
            "Alternate - job count": counts.get(starved, 0),
            "Alternate - idle days": round(idle_days_total.get(starved, 0.0), 1),
            "Recommendation": (
                f"Promote {starved}'s PRIORITY to match/beat {dominant}'s for this combo in "
                f"MCH_MACHINE_FNL_MASTER — re-simulation confirmed this actually shifts jobs onto {starved} "
                f"(unlike most flagged combos, where a priority change alone made no difference)."
            ),
        })

    out_path = "rebalancing_recommendations_report.xlsx"
    pd.DataFrame(out_rows).to_excel(out_path, index=False, sheet_name="Recommendations")
    print(f"Report written: {out_path} ({len(out_rows)} rows)")


if __name__ == "__main__":
    main()
