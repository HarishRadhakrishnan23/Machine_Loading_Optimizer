"""
report_priority_change_impact.py — quantifies the actual impact of changing
MCH_MACHINE_FNL_MASTER.PRIORITY for the 14 combos flagged by
report_priority_load_imbalance.py, by re-running the REAL Model F dispatch
simulation three times (baseline, "tie" scenario, "swap/promote" scenario)
against live Oracle data and diffing the outcomes. Read-only — never writes
to Oracle, never edits MCH_MACHINE_FNL_MASTER; PRIORITY is only changed in an
in-memory copy of the DataFrame for the simulation.

Scenario A ("tie"): starved machine's PRIORITY is set equal to the dominant
machine's, for just the flagged combo+task rows. Per CLAUDE.md §E.19 Rule 3 a
tie breaks on machine name alphabetically, NOT round-robin — this scenario
shows whether a tie alone (today's tie-break rule) is enough to shift any load.

Scenario B ("swap"): the two machines' PRIORITY values are fully swapped for
the flagged combo+task rows (starved machine promoted to the dominant's old
rank, dominant demoted to the starved's old rank) — the real fix CLAUDE.md's
F-6 note recommends asking the planner to make.
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


def _s(v) -> str:
    return str(v).strip()


def _gap_days(start, end) -> float:
    return (end.day - start.day).days + (end.shift_index - start.shift_index) / 3.0


def run_scenario(raw_rows, fnl_df, fixture_index, known_devices, known_machines, availability, pool):
    routing_index = build_routing_index_from_fnl(fnl_df)
    chains = build_order_chains(
        raw_rows, routing_index, fixture_index, known_devices, known_machines, FIXTURE_REQUIRED_TASKS
    )
    rows = run_dispatch_simulation(
        chains, availability, pool.clone(), today=TODAY, window_days=60, cooling_minutes=20, day_zero=TODAY,
        planned_order_start_buffer_days=1, heavy_operations=HEAVY_OPS, enable_safety_stock_speculation=False,
    )
    scheduled = [r for r in rows if r.machine is not None and r.start is not None]

    job_counts = defaultdict(Counter)
    for r in scheduled:
        if not r.batch_key or not r.task:
            continue
        parts = r.batch_key.split("~")
        if len(parts) != 4:
            continue
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

    completion_by_order = {r.production_order: r.order_completion_date for r in rows if r.order_completion_date}
    committed_cdd = {
        c.production_order: c.schedulable[0].cdd
        for c in chains if c.schedulable and c.schedulable[0].cdd is not None
    }
    on_time = {
        oid for oid, cdd in committed_cdd.items() if oid in completion_by_order and completion_by_order[oid] <= cdd
    }
    return {
        "job_counts": job_counts,
        "idle_days_total": idle_days_total,
        "completion_by_order": completion_by_order,
        "committed_cdd": committed_cdd,
        "on_time": on_time,
    }


def main():
    print("Loading live Oracle data...")
    wip_df = read_wip_orders()
    mm_df = read_machine_master()
    md_df = read_machine_daily()
    fnl_df = read_machine_fnl_master()
    fi_df = read_fixture_locator_inventory()
    holiday_df = read_holiday_calendar()

    raw_rows = build_raw_wip_rows(wip_df)
    fixture_index = build_fixture_index_from_fnl(fnl_df)
    pool = build_device_pool(fi_df)
    known_devices = frozenset(pool.quantities.keys())
    availability = build_availability(mm_df, md_df, holiday_df)
    known_machines = frozenset(mm_df["WORK_CENTER"].astype(str).str.strip()) | frozenset(
        md_df["WORK_CENTER"].astype(str).str.strip()
    )

    findings_df = pd.read_excel("priority_load_imbalance_report.xlsx")
    pairs = findings_df.to_dict("records")
    print(f"Loaded {len(pairs)} flagged combo/machine pairs")

    print("Running baseline simulation...")
    baseline = run_scenario(raw_rows, fnl_df, fixture_index, known_devices, known_machines, availability, pool)

    fnl_tie = fnl_df.copy()
    fnl_swap = fnl_df.copy()
    for p in pairs:
        mask = (
            (fnl_df["SIZE_INCH"].astype(str).str.strip() == _s(p["SIZE_INCH"]))
            & (fnl_df["CLASS"].astype(str).str.strip() == _s(p["CLASS"]))
            & (fnl_df["MOC"].astype(str).str.strip() == _s(p["MOC"]))
            & (fnl_df["DESIGN"].astype(str).str.strip() == _s(p["DESIGN"]))
            & (fnl_df["TASK"].astype(str).str.strip() == _s(p["TASK"]))
        )
        dom_mach, starved_mach = p["Dominant machine (higher priority)"], p["Starved machine (lower priority)"]
        dom_pri, starved_pri = int(p["Dominant machine PRIORITY"]), int(p["Starved machine PRIORITY"])
        dom_mask = mask & (fnl_df["WORK_CENTER"].astype(str).str.strip() == _s(dom_mach))
        starved_mask = mask & (fnl_df["WORK_CENTER"].astype(str).str.strip() == _s(starved_mach))

        fnl_tie.loc[starved_mask, "PRIORITY"] = dom_pri  # tie: starved matches dominant's rank

        fnl_swap.loc[starved_mask, "PRIORITY"] = dom_pri  # swap: full promotion/demotion
        fnl_swap.loc[dom_mask, "PRIORITY"] = starved_pri

    print("Running 'tie' scenario simulation...")
    tie = run_scenario(raw_rows, fnl_tie, fixture_index, known_devices, known_machines, availability, pool)
    print("Running 'swap' scenario simulation...")
    swap = run_scenario(raw_rows, fnl_swap, fixture_index, known_devices, known_machines, availability, pool)

    # --- Report 1: per-combo job-count shift ---
    combo_rows = []
    for p in pairs:
        key = (_s(p["SIZE_INCH"]), _s(p["CLASS"]), _s(p["MOC"]), _s(p["DESIGN"]), _s(p["TASK"]))
        dom, starved = p["Dominant machine (higher priority)"], p["Starved machine (lower priority)"]
        for label, result in (("Baseline", baseline), ("Tie (same PRIORITY)", tie), ("Swap (promote starved)", swap)):
            counts = result["job_counts"].get(key, Counter())
            combo_rows.append({
                "SIZE_INCH": p["SIZE_INCH"], "CLASS": p["CLASS"], "MOC": p["MOC"], "DESIGN": p["DESIGN"],
                "TASK": p["TASK"], "Scenario": label,
                f"{dom} jobs": counts.get(dom, 0), f"{starved} jobs": counts.get(starved, 0),
                "Starved machine idle days (all combos)": round(result["idle_days_total"].get(starved, 0.0), 1),
            })
    combo_df = pd.DataFrame(combo_rows)

    # --- Report 2: overall CDD attainment + regression check ---
    def attainment(result):
        n = len(result["committed_cdd"])
        return len(result["on_time"]), n, (100.0 * len(result["on_time"]) / n if n else 0.0)

    summary_rows = []
    for label, result in (("Baseline", baseline), ("Tie (same PRIORITY)", tie), ("Swap (promote starved)", swap)):
        on_time, total, pct = attainment(result)
        summary_rows.append({
            "Scenario": label, "Committed orders": total, "On-time": on_time,
            "CDD attainment %": round(pct, 2),
            "Total idle-days on the 7 starved machines": round(
                sum(result["idle_days_total"].get(_s(p["Starved machine (lower priority)"]), 0.0) for p in pairs), 1
            ),
        })
    summary_df = pd.DataFrame(summary_rows)

    # --- Report 3: any committed order that changes on-time status (newly breaches or newly recovers) ---
    def diff_orders(label, scenario_result):
        rows = []
        for oid, cdd in baseline["committed_cdd"].items():
            was_on_time = oid in baseline["on_time"]
            now_on_time = oid in scenario_result["on_time"]
            if was_on_time != now_on_time:
                rows.append({
                    "Scenario": label, "PRODUCTION_ORDER": oid, "CDD": cdd,
                    "Baseline completion": baseline["completion_by_order"].get(oid),
                    "Scenario completion": scenario_result["completion_by_order"].get(oid),
                    "Change": "NEWLY BREACHES" if was_on_time else "NEWLY ON-TIME",
                })
        return rows

    regression_rows = diff_orders("Tie (same PRIORITY)", tie) + diff_orders("Swap (promote starved)", swap)
    regression_df = pd.DataFrame(regression_rows) if regression_rows else pd.DataFrame(
        [{"Result": "No committed order changed on-time/breach status in either scenario"}]
    )

    out_path = "priority_change_impact_report.xlsx"
    with pd.ExcelWriter(out_path) as writer:
        summary_df.to_excel(writer, sheet_name="Summary", index=False)
        combo_df.to_excel(writer, sheet_name="Per-Combo Job Shift", index=False)
        regression_df.to_excel(writer, sheet_name="Order-Level Changes", index=False)
    print(f"\nReport written: {out_path}")
    print(summary_df.to_string(index=False))


if __name__ == "__main__":
    main()
