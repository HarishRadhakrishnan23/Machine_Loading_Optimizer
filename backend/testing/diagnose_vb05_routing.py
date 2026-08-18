#!/usr/bin/env python3
"""
Diagnose: (1) Is VB05 routing actually restricted to 2 machines for small sizes,
or is our batch-continuity code wrongly narrowing candidates?
(2) Are 2PMC16/2PMC17 genuinely oversubscribed plant-wide within the computed
horizon, explaining both the splits and the "no open slot" failures?
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import read_wip_orders, read_routing_master, read_machine_master, read_machine_daily
from preprocess import filter_wip_orders, compute_horizon, build_scheduler_input
from models import Config
import json
from datetime import date

with open("config.json") as f:
    config = Config(**json.load(f))

routing_df = read_routing_master()
wip_df = read_wip_orders()
machine_master_df = read_machine_master()
machine_daily_df = read_machine_daily()

# ── Part 1: Real routing coverage for VB05, by SIZE_INCH ──────────────────
print("=" * 80)
print("PART 1: VB05 routing coverage by SIZE_INCH (from MCH_MACHINE_PRIORITY)")
print("=" * 80)

vb05 = routing_df[routing_df["TASK"].astype(str).str.contains("VB05", na=False)]
print(f"Total VB05 routing rows: {len(vb05)}")
print(f"\nDistinct WORK_CENTERs capable of VB05 (any size): {sorted(vb05['WORK_CENTER'].unique())}")

print("\nBy SIZE_INCH, which machines are listed as capable:")
for size in sorted(vb05["SIZE_INCH"].unique(), key=lambda x: float(x)):
    machines = sorted(vb05[vb05["SIZE_INCH"] == size]["WORK_CENTER"].unique())
    print(f"  Size {size}\": {machines}")

# ── Part 2: Confirm what our code actually computes as "candidates" for a
#    specific task in a split batch, to check for a bug in intersection logic ──
print("\n" + "=" * 80)
print("PART 2: What does OUR preprocessing compute as candidates for a real VB05 task?")
print("=" * 80)

filtered_wip = filter_wip_orders(wip_df, routing_df)
horizon_dates = compute_horizon(filtered_wip, machine_master_df, config, date.today())
scheduler_input = build_scheduler_input(
    wip_df, machine_master_df, machine_daily_df, routing_df,
    horizon_dates=horizon_dates, config=config, today=date.today(),
)

print(f"Horizon: {len(horizon_dates)} days ({horizon_dates[0]} .. {horizon_dates[-1]})")

# Find a task in the 10~150~DFS / VB05 group
sample_tasks = [
    t for t in scheduler_input.tasks
    if t.operation.startswith("VB05") and "~".join(t.item_category.split("~")[:3]) == "10~150~DFS"
]
print(f"\nTasks in batch (10~150~DFS, VB05): {len(sample_tasks)}")
for t in sample_tasks[:5]:
    cand_machines = sorted(c.machine_name for c in t.candidates)
    print(f"  {t.production_order} Op{t.operation_no}: item_category={t.item_category}")
    print(f"    Candidates (our code): {cand_machines}")

# ── Part 3: Plant-wide VB05 demand vs capacity for 2PMC16 / 2PMC17 within horizon ──
print("\n" + "=" * 80)
print("PART 3: Plant-wide VB05 workload vs capacity for 2PMC16 / 2PMC17 (within horizon)")
print("=" * 80)

vb05_tasks = [t for t in scheduler_input.tasks if t.operation.startswith("VB05")]
print(f"Total VB05 tasks (plant-wide): {len(vb05_tasks)}")
total_vb05_pieces = sum(t.balance_qty for t in vb05_tasks)
total_vb05_minutes = sum(t.balance_qty * t.cycle_time for t in vb05_tasks)
print(f"Total VB05 pieces: {total_vb05_pieces}, total minutes: {total_vb05_minutes:,.0f}")

# How many VB05 tasks even have 2PMC16 or 2PMC17 as a candidate?
uses_2pmc16_17 = [
    t for t in vb05_tasks
    if any(c.machine_name in ("2PMC16 (TOV - Miven CNC (1116))", "2PMC17 (TOV - Miven CNC (1117))") for c in t.candidates)
]
print(f"VB05 tasks that CAN use 2PMC16 or 2PMC17: {len(uses_2pmc16_17)}")
minutes_on_2pmc16_17 = sum(t.balance_qty * t.cycle_time for t in uses_2pmc16_17)
print(f"Their total minutes demand: {minutes_on_2pmc16_17:,.0f}")

# Capacity of 2PMC16 + 2PMC17 within the computed horizon
for slot in scheduler_input.capacity:
    pass  # capacity is a flat list; aggregate below

cap_2pmc16 = sum(
    s.available_mins for s in scheduler_input.capacity
    if s.machine_name == "2PMC16 (TOV - Miven CNC (1116))"
)
cap_2pmc17 = sum(
    s.available_mins for s in scheduler_input.capacity
    if s.machine_name == "2PMC17 (TOV - Miven CNC (1117))"
)
print(f"\n2PMC16 total available minutes within horizon: {cap_2pmc16:,.0f}")
print(f"2PMC17 total available minutes within horizon: {cap_2pmc17:,.0f}")
print(f"Combined capacity: {cap_2pmc16 + cap_2pmc17:,.0f}")
print(f"Demand that could route to them: {minutes_on_2pmc16_17:,.0f}")
print(f"Utilization if ALL such demand landed there: {minutes_on_2pmc16_17 / (cap_2pmc16 + cap_2pmc17):.1%}")

# But also: how much OTHER (non-VB05) work competes for these 2 machines?
all_tasks_on_2pmc16_17 = [
    t for t in scheduler_input.tasks
    if any(c.machine_name in ("2PMC16 (TOV - Miven CNC (1116))", "2PMC17 (TOV - Miven CNC (1117))") for c in t.candidates)
]
total_all_minutes = sum(t.balance_qty * t.cycle_time for t in all_tasks_on_2pmc16_17)
print(f"\nALL tasks (any operation) that can use 2PMC16/17: {len(all_tasks_on_2pmc16_17)}")
print(f"Their total minutes demand: {total_all_minutes:,.0f}")
print(f"Utilization if ALL such demand landed there: {total_all_minutes / (cap_2pmc16 + cap_2pmc17):.1%}")

print("\n" + "=" * 80)
print("DIAGNOSIS COMPLETE")
print("=" * 80)
