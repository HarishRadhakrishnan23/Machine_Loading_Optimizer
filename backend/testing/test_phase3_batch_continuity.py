#!/usr/bin/env python3
"""
Test Phase 3: Engine 1 Batch Continuity (CP-SAT hard constraint + batch-aware greedy)

This clears MCH_SCHEDULE_OUTPUT, runs Engine 1 fresh, and checks:
1. No duplicate rows (regression check from earlier fixes)
2. Each (batch_key, TASK) group is consolidated onto ONE machine
3. QS1000575's Op10 batch specifically (the original reported problem)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import oracledb, os, json
from datetime import date
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

# Step 1: Clear database
print("Step 1: Clearing MCH_SCHEDULE_OUTPUT...")
con = oracledb.connect(
    user=os.getenv("ORACLE_USER"),
    password=os.getenv("ORACLE_PASSWORD"),
    dsn=os.getenv("ORACLE_DSN"),
)
cur = con.cursor()
cur.execute("DELETE FROM MCH_SCHEDULE_OUTPUT")
con.commit()
con.close()
print("  [OK] Database cleared")

# Step 2: Run Engine 1
print("\nStep 2: Running Engine 1 with batch continuity...")
from pipeline import schedule_all_orders
from models import Config

with open("config.json") as f:
    config = Config(**json.load(f))

result = schedule_all_orders(config, date.today())

# Step 3: Regression check - no duplicates
print("\nStep 3: Regression check - duplicate rows...")
con = oracledb.connect(
    user=os.getenv("ORACLE_USER"),
    password=os.getenv("ORACLE_PASSWORD"),
    dsn=os.getenv("ORACLE_DSN"),
)
cur = con.cursor()

cur.execute("SELECT COUNT(*) FROM MCH_SCHEDULE_OUTPUT")
total_rows = cur.fetchone()[0]

cur.execute("""
    SELECT COUNT(*) FROM (
        SELECT PRODUCTION_ORDER, OPERATION_NO, WORK_CENTER, SHIFT, SCHEDULED_DATE, BALANCE_QTY
        FROM MCH_SCHEDULE_OUTPUT
        GROUP BY PRODUCTION_ORDER, OPERATION_NO, WORK_CENTER, SHIFT, SCHEDULED_DATE, BALANCE_QTY
        HAVING COUNT(*) > 1
    )
""")
dup_count = cur.fetchone()[0]
print(f"  Total rows: {total_rows}")
print(f"  Duplicate rows: {dup_count}")
if dup_count == 0:
    print("  [OK] No duplicates")
else:
    print("  [FAIL] Duplicates present - regression!")

# Step 4: Check batch consolidation - how many machines does each (batch, operation) use?
print("\nStep 4: Batch consolidation check (across ALL batch groups)...")
cur.execute("""
    SELECT sched.PRODUCTION_ORDER, sched.OPERATION_NO, sched.WORK_CENTER,
           wip.SIZE_INCH, wip.CLASS, wip.DESIGN, wip.TASK
    FROM MCH_SCHEDULE_OUTPUT sched
    JOIN MCH_WIP wip
      ON sched.PRODUCTION_ORDER = wip.PRODUCTION_ORDER
     AND sched.OPERATION_NO = wip.OPERATION
""")
rows = cur.fetchall()
con.close()

from collections import defaultdict
group_machines = defaultdict(set)
group_orders = defaultdict(set)
for order, op_no, machine, size, cls, design, task_code in rows:
    batch_key = f"{size}~{cls}~{design}"
    group_key = (batch_key, task_code)
    group_machines[group_key].add(machine)
    group_orders[group_key].add(order)

multi_order_groups = {g: m for g, m in group_machines.items() if len(group_orders[g]) > 1}
consolidated = {g: m for g, m in multi_order_groups.items() if len(m) == 1}
split = {g: m for g, m in multi_order_groups.items() if len(m) > 1}

print(f"  Batch groups with >1 order: {len(multi_order_groups)}")
print(f"  Consolidated onto ONE machine: {len(consolidated)}")
print(f"  Still split across multiple machines: {len(split)}")
if split:
    print("\n  Groups still split (should be rare - capacity/routing override cases):")
    for g, m in list(split.items())[:10]:
        print(f"    {g}: {len(m)} machines -> {sorted(m)} ({len(group_orders[g])} orders)")

# Step 5: QS1000575 specific check
print("\nStep 5: QS1000575 Op10 check (the original reported problem)...")
con = oracledb.connect(
    user=os.getenv("ORACLE_USER"),
    password=os.getenv("ORACLE_PASSWORD"),
    dsn=os.getenv("ORACLE_DSN"),
)
cur = con.cursor()
cur.execute("""
    SELECT WORK_CENTER, SUM(BALANCE_QTY) as qty
    FROM MCH_SCHEDULE_OUTPUT
    WHERE PRODUCTION_ORDER = 'QS1000575' AND OPERATION_NO = 10
    GROUP BY WORK_CENTER
    ORDER BY qty DESC
""")
qs_op10 = cur.fetchall()
con.close()

if qs_op10:
    if len(qs_op10) == 1:
        machine, qty = qs_op10[0]
        print(f"  [SUCCESS] QS1000575 Op10: ALL {qty} pieces on ONE machine ({machine})")
    else:
        print(f"  [STILL SPLIT] QS1000575 Op10 across {len(qs_op10)} machines:")
        for machine, qty in qs_op10:
            print(f"    {machine}: {qty} pieces")
else:
    print("  [WARN] QS1000575 Op10 not found in schedule (may have failed to place)")

print(f"\n{'='*70}")
print("PHASE 3 TEST COMPLETE")
print(f"{'='*70}\n")
