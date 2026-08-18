#!/usr/bin/env python3
"""Test batch continuity fix: verify tasks don't split across machines unnecessarily."""

import oracledb, os, json
from pathlib import Path
from datetime import date
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# Step 1: Clear database
print("Step 1: Clearing MCH_SCHEDULE_OUTPUT...")
con = oracledb.connect(
    user=os.getenv("ORACLE_USER"),
    password=os.getenv("ORACLE_PASSWORD"),
    dsn=os.getenv("ORACLE_DSN")
)
cur = con.cursor()
cur.execute("DELETE FROM MCH_SCHEDULE_OUTPUT")
con.commit()
con.close()
print("  [OK] Database cleared")

# Step 2: Run Engine 1
print("\nStep 2: Running Engine 1 with batch continuity fix...")
from db import read_wip_orders, read_machine_master, read_machine_daily, read_routing_master
from preprocess import filter_wip_orders, compute_horizon, build_scheduler_input
from pipeline import schedule_all_orders
from models import Config

with open('config.json') as f:
    config = Config(**json.load(f))

result = schedule_all_orders(config, date.today())

# Step 3: Check if batches stayed together
print("\nStep 3: Analyzing batch cohesion...")
con = oracledb.connect(
    user=os.getenv("ORACLE_USER"),
    password=os.getenv("ORACLE_PASSWORD"),
    dsn=os.getenv("ORACLE_DSN")
)
cur = con.cursor()

# Count how many tasks are split across machines
cur.execute("""
    SELECT PRODUCTION_ORDER, OPERATION_NO, COUNT(DISTINCT WORK_CENTER) as num_machines
    FROM MCH_SCHEDULE_OUTPUT
    GROUP BY PRODUCTION_ORDER, OPERATION_NO
    HAVING COUNT(DISTINCT WORK_CENTER) > 1
    ORDER BY num_machines DESC, PRODUCTION_ORDER
""")

splits = cur.fetchall()
if splits:
    print(f"  {len(splits)} tasks split across multiple machines:")
    for order, op, num_machines in splits[:10]:
        cur.execute("""
            SELECT DISTINCT WORK_CENTER FROM MCH_SCHEDULE_OUTPUT
            WHERE PRODUCTION_ORDER = :order AND OPERATION_NO = :op
            ORDER BY WORK_CENTER
        """, {"order": order, "op": op})
        machines = [r[0] for r in cur.fetchall()]
        print(f"    {order} Op{op}: {num_machines} machines - {', '.join(machines[:3])}...")
else:
    print("  [SUCCESS] All tasks stay on ONE machine! Batch continuity working!")

# Step 4: Compare to previous expectation (QS1000575 Op10)
print("\nStep 4: Checking QS1000575 Op10 (the problem order)...")
cur.execute("""
    SELECT WORK_CENTER, COUNT(*) as qty
    FROM MCH_SCHEDULE_OUTPUT
    WHERE PRODUCTION_ORDER = 'QS1000575' AND OPERATION_NO = 10
    GROUP BY WORK_CENTER
    ORDER BY qty DESC
""")

machines_for_op10 = cur.fetchall()
if machines_for_op10:
    if len(machines_for_op10) == 1:
        machine, qty = machines_for_op10[0]
        print(f"  [EXCELLENT] QS1000575 Op10: ALL {qty} pieces on {machine}")
    else:
        print(f"  [PARTIAL] QS1000575 Op10 split across {len(machines_for_op10)} machines:")
        total_qty = 0
        for machine, qty in machines_for_op10:
            print(f"    → {machine}: {qty} pieces")
            total_qty += qty
        print(f"    Total: {total_qty} pieces")

con.close()

print(f"\n{'='*70}")
print(f"Fix Status: Batch continuity constraint is active")
print(f"Next: Run validation_scheduler.py to check improvement")
print(f"{'='*70}\n")
