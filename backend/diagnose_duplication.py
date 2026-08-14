#!/usr/bin/env python3
"""Diagnose where the 2x duplication is happening in Engine 1."""

import json
import sys
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
    config_dict = json.load(f)
config = Config(**config_dict)

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
print("[2] Creating scheduler...")
engine = Engine1Scheduler(scheduler_input)

# Check pids_by_machine_slot before build_model
print("[3] Checking pids_by_machine_slot BEFORE build_model()...")
before_dups = 0
for (m, k), pids in sorted(engine.pids_by_machine_slot.items()):
    if pids:
        if len(pids) != len(set(pids)):
            before_dups += 1
            print(f"  ({m}, {k}): {len(pids)} pids, DUPLICATES - Unique count: {len(set(pids))}")
if before_dups == 0:
    print("  [OK] No duplicates BEFORE build_model")

# Build model
print("[4] Building model...")
engine.build_model()

# Check pids_by_machine_slot after build_model
print("[5] Checking pids_by_machine_slot AFTER build_model()...")
dup_count = 0
for (m, k), pids in sorted(engine.pids_by_machine_slot.items()):
    if len(pids) != len(set(pids)):
        dup_count += 1
        unique_pids = set(pids)
        for pid in unique_pids:
            count = pids.count(pid)
            if count > 1:
                print(f"  ({m}, {k}): pid {pid} appears {count} times - DUPLICATE")

if dup_count == 0:
    print("  [OK] No duplicates found in pids_by_machine_slot")
else:
    print(f"  [WARN] {dup_count} (machine, slot) pairs have duplicate pids")

# Diagnose _rows_from_assignment
print("[6] Simulating _rows_from_assignment extraction...")
greedy = engine.greedy_schedule()
get_qty = lambda pid, m, k: greedy.get((pid, m, k), 0)

from datetime import datetime
rows = engine._rows_from_assignment(get_qty, "test-run", datetime.now())

# Count rows per (order, op, machine, shift, date)
row_counts = {}
for row in rows:
    key = (row.production_order, row.operation_no, row.machine_name, row.shift.value, str(row.scheduled_date))
    row_counts[key] = row_counts.get(key, 0) + 1

duplicates = {k: v for k, v in row_counts.items() if v > 1}
if duplicates:
    print(f"  [WARN] Found {len(duplicates)} rows appearing multiple times:")
    for key, count in sorted(duplicates.items())[:10]:
        print(f"    {key}: appears {count} times")
else:
    print("  [OK] No duplicate rows found in extraction output")

print(f"\n{'='*70}")
print(f"Total rows extracted: {len(rows)}")
print(f"Unique (order, op, machine, shift, date) combinations: {len(row_counts)}")
print(f"Rows with duplicates: {len(duplicates)}")
print(f"{'='*70}")
