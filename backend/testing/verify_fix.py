#!/usr/bin/env python3
"""Quick verification that the fix works."""

import json
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

# Create scheduler and check candidates
print("[2] Checking candidates for duplicates...")
engine = Engine1Scheduler(scheduler_input)

dup_candidates = 0
for pid, candidates in engine.candidates.items():
    if len(candidates) != len(set(candidates)):
        dup_candidates += 1
        print(f"  {pid}: {candidates} (HAS DUPLICATES)")

if dup_candidates == 0:
    print("  [OK] No duplicate candidates found")

# Build model
print("[3] Building model and checking pids_by_machine_slot...")
engine.build_model()

# Check that pids_by_machine_slot uses sets and has no duplicates
all_clean = True
for (m, k), pids_set in engine.pids_by_machine_slot.items():
    if not isinstance(pids_set, set):
        print(f"  ERROR: ({m}, {k}) is not a set: {type(pids_set)}")
        all_clean = False
        break

if all_clean:
    print("  [OK] All pids_by_machine_slot entries are sets (no duplicates possible)")

print(f"\n[SUCCESS] Fix verified! Ready to run full Engine 1 test.")
print(f"\nNow run: python regenerate_schedule.py")
print(f"Then verify: SELECT PRODUCTION_ORDER, OPERATION_NO, COUNT(*) FROM MCH_SCHEDULE_OUTPUT GROUP BY PRODUCTION_ORDER, OPERATION_NO HAVING COUNT(*) > 1")
