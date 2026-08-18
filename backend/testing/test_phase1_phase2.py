#!/usr/bin/env python3
"""
Test Phase 1 + Phase 2: Database Schema & Batch Grouping Logic

This script:
1. Verifies batch_grouping.py functions work correctly
2. Fetches real WIP data and shows batch groupings
3. Displays which orders are safety stock
4. Shows examples of batches that will be scheduled together
"""

import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from batch_grouping import (
    compute_batch_key,
    is_safety_stock,
    group_orders_by_batch,
    build_batch_task_map,
    build_safety_stock_map,
)

from db import read_wip_orders
from preprocess import filter_wip_orders
from models import Config
import json

print("=" * 80)
print("PHASE 1 + PHASE 2 TEST: Database Schema & Batch Grouping")
print("=" * 80)

# Test 1: Batch key computation
print("\n[TEST 1] Batch Key Computation")
print("-" * 80)
test_cases = [
    (("3", "300", "DFS"), "3~300~DFS"),
    (("10", "150", "CS"), "10~150~CS"),
    (("8", "300", "LUG"), "8~300~LUG"),
]
for (size, cls, design), expected in test_cases:
    result = compute_batch_key(size, cls, design)
    status = "[OK]" if result == expected else "[FAIL]"
    print(f"  {status} compute_batch_key('{size}', '{cls}', '{design}') = '{result}'")
    assert result == expected

# Test 2: Safety stock detection
print("\n[TEST 2] Safety Stock Detection")
print("-" * 80)
print(f"  [OK] is_safety_stock(None) = {is_safety_stock(None)}")
print(f"  [OK] is_safety_stock('2026-08-27') = {is_safety_stock('2026-08-27')}")
assert is_safety_stock(None) == True
assert is_safety_stock("2026-08-27") == False

# Test 3: Real WIP data batch grouping
print("\n[TEST 3] Real WIP Data - Batch Grouping")
print("-" * 80)
print("Fetching WIP data from Oracle...")
wip_df = read_wip_orders()
print(f"  Total WIP rows: {len(wip_df)}")

# Load config for filtering
with open("config.json") as f:
    config = Config(**json.load(f))

# Filter (CT > 0, routable, etc.)
print("Filtering WIP data (CT > 0, routable operations)...")
from db import read_routing_master
routing_df = read_routing_master()
filtered_wip = filter_wip_orders(wip_df, routing_df)
print(f"  Filtered WIP rows: {len(filtered_wip)}")

# Build batch groupings
print("\nBuilding batch groupings...")
batch_groups = group_orders_by_batch(filtered_wip)
print(f"  Total unique batches: {len(batch_groups)}")

# Show top 10 batches by size
print("\n  Top 10 batches by number of orders:")
sorted_batches = sorted(batch_groups.items(), key=lambda x: len(x[1]), reverse=True)
for i, (batch_key, tasks) in enumerate(sorted_batches[:10], 1):
    orders = set(task[0] for task in tasks)
    pieces = sum(filtered_wip[filtered_wip["PRODUCTION_ORDER"].isin(orders)]["QUANTITY_ORDERED"].sum() for o in orders)
    print(f"    {i}. {batch_key}: {len(orders)} orders, {len(tasks)} operations")

# Test 4: Build task-to-batch mapping
print("\n[TEST 4] Task-to-Batch Mapping")
print("-" * 80)
task_to_batch = build_batch_task_map(filtered_wip)
print(f"  Total tasks mapped: {len(task_to_batch)}")
print(f"  Sample mappings (first 5):")
for i, (task, batch) in enumerate(list(task_to_batch.items())[:5], 1):
    print(f"    {i}. {task[0]} Op{int(task[1])}: -> {batch}")

# Test 5: Safety stock detection
print("\n[TEST 5] Safety Stock Orders")
print("-" * 80)
safety_stock_map = build_safety_stock_map(filtered_wip)
safety_stock_orders = [order for order, is_ss in safety_stock_map.items() if is_ss]
print(f"  Total orders: {len(safety_stock_map)}")
print(f"  Safety stock orders (CDD = NULL): {len(safety_stock_orders)}")
if safety_stock_orders:
    print(f"  Sample safety stock orders: {safety_stock_orders[:5]}")

# Test 6: Show batch batching example (QS1000575)
print("\n[TEST 6] Example: QS1000575 Batching")
print("-" * 80)
qs_orders = filtered_wip[filtered_wip["PRODUCTION_ORDER"] == "QS1000575"]
if not qs_orders.empty:
    print(f"  Order QS1000575:")
    for _, row in qs_orders.iterrows():
        batch_key = compute_batch_key(str(row["SIZE_INCH"]), str(row["CLASS"]), str(row["DESIGN"]))
        qty = row["QUANTITY_ORDERED"]
        print(f"    Op{int(row['OPERATION'])}: Qty {qty}, Batch={batch_key}")

    # Show which other orders are in same batches
    print(f"\n  Other orders in same batches as QS1000575:")
    qs_batches = set(task_to_batch.get((task[0], task[1]), None)
                     for task in task_to_batch
                     if task[0] == "QS1000575")
    for batch in qs_batches:
        same_batch_tasks = [t for t, b in task_to_batch.items() if b == batch and t[0] != "QS1000575"]
        if same_batch_tasks:
            same_batch_orders = set(t[0] for t in same_batch_tasks)
            print(f"    Batch {batch}: {len(same_batch_orders)} other orders (sample: {list(same_batch_orders)[:3]})")

print("\n" + "=" * 80)
print("[OK] PHASE 1 + PHASE 2 TESTS PASSED")
print("=" * 80)
print("\nNext Steps:")
print("1. Run the SQL script: phase1_schema_update.sql in Oracle SQL Developer")
print("2. Then proceed to Phase 3: Engine 1 Batch Continuity Constraints")
print("=" * 80 + "\n")
