#!/usr/bin/env python3
"""Deep diagnostic: show exactly what's in pids_by_machine_slot and how rows are created."""

import json, sys
from pathlib import Path
from datetime import date
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from db import read_wip_orders, read_machine_master, read_machine_daily, read_routing_master
from preprocess import filter_wip_orders, compute_horizon, build_scheduler_input
from engine1_scheduler import Engine1Scheduler
from models import Config

# Load config
with open('config.json') as f:
    config = Config(**json.load(f))

# Fetch and preprocess
print("[1] Fetching and preprocessing...")
wip_df = read_wip_orders()
machine_master_df = read_machine_master()
machine_daily_df = read_machine_daily()
routing_df = read_routing_master()

filtered_wip = filter_wip_orders(wip_df, routing_df)
horizon_dates = compute_horizon(filtered_wip, machine_master_df, config, date.today())

scheduler_input = build_scheduler_input(
    wip_df, machine_master_df, machine_daily_df, routing_df,
    horizon_dates=horizon_dates,
    config=config,
    today=date.today(),
)

# Create scheduler
print("[2] Creating scheduler and building model...")
engine = Engine1Scheduler(scheduler_input)
engine.diagnose_feasibility()
engine.build_model()

# Check pids_by_machine_slot for duplicates AFTER build_model
print("\n[3] Checking pids_by_machine_slot AFTER _create_variables()...")
total_entries = sum(len(pids) for pids in engine.pids_by_machine_slot.values())
total_unique_entries = sum(len(set(pids)) for pids in engine.pids_by_machine_slot.values())
print(f"  Total pid entries: {total_entries}")
print(f"  Total UNIQUE pid entries: {total_unique_entries}")
print(f"  Duplicates exist: {total_entries != total_unique_entries}")

# Find which (m, k) have duplicates
dup_slots = []
for (m, k), pids in engine.pids_by_machine_slot.items():
    if len(pids) != len(set(pids)):
        dup_count = len(pids) - len(set(pids))
        dup_slots.append(((m, k), pids, dup_count))

if dup_slots:
    print(f"\n  FOUND {len(dup_slots)} slots with duplicate pids:")
    for (m, k), pids, dup_count in dup_slots[:10]:
        from collections import Counter
        counts = Counter(pids)
        print(f"    ({m}, {k}): {dup_count} extra entries")
        for pid, count in sorted(counts.items()):
            if count > 1:
                print(f"      -> {pid} appears {count}x")
else:
    print("  [OK] No duplicate pids in pids_by_machine_slot")

# Solve
print("\n[4] Solving CP-SAT model...")
from ortools.sat.python import cp_model
solver = cp_model.CpSolver()
solver.parameters.max_time_in_seconds = 10
solver.parameters.num_workers = 2
status = solver.Solve(engine.model)

print(f"  Status: {status}")

# Extract using CP-SAT solution
print("\n[5] Extracting rows from CP-SAT solution...")
from datetime import datetime
get_qty = lambda pid, m, k: solver.Value(engine.qty.get((pid, m, k), 0)) if (pid, m, k) in engine.qty else 0
rows = engine._rows_from_assignment(get_qty, "test-run-deep", datetime.now())

# Count rows per (order, op, machine, shift, date)
row_counts = {}
for row in rows:
    key = (row.production_order, row.operation_no, row.machine_name, row.shift.value, str(row.scheduled_date))
    row_counts[key] = row_counts.get(key, 0) + 1

# Analyze duplicates
duplicates = {k: v for k, v in row_counts.items() if v > 1}
dup_dist = {}
for count in duplicates.values():
    dup_dist[count] = dup_dist.get(count, 0) + 1

print(f"\n[6] Result Analysis:")
print(f"  Total rows extracted: {len(rows)}")
print(f"  Unique (order, op, machine, shift, date): {len(row_counts)}")
print(f"  Rows with duplicates: {len(duplicates)}")

if duplicates:
    print(f"\n  Duplication distribution:")
    for dup_count in sorted(dup_dist.keys()):
        print(f"    {dup_count}x: {dup_dist[dup_count]} combinations")

    print(f"\n  Sample duplicates (first 5):")
    for key, count in sorted(duplicates.items())[:5]:
        print(f"    {key}: {count}x")

print(f"\n{'='*70}")
print(f"CONCLUSION: Duplication found in extraction: {len(duplicates) > 0}")
print(f"Root cause is likely: pids_by_machine_slot has duplicate pids")
print(f"{'='*70}")
