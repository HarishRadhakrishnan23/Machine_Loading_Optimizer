#!/usr/bin/env python3
"""Clean database and run fresh schedule to verify the fix."""

import oracledb, os, json, sys
from pathlib import Path
from datetime import date
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# Step 1: Clear the database
print("Step 1: Clearing MCH_SCHEDULE_OUTPUT...")
con = oracledb.connect(
    user=os.getenv("ORACLE_USER"),
    password=os.getenv("ORACLE_PASSWORD"),
    dsn=os.getenv("ORACLE_DSN")
)
cur = con.cursor()
cur.execute("SELECT COUNT(*) FROM MCH_SCHEDULE_OUTPUT")
old_count = cur.fetchone()[0]
if old_count > 0:
    cur.execute("DELETE FROM MCH_SCHEDULE_OUTPUT")
    con.commit()
    print(f"  Deleted {old_count} old rows")
else:
    print("  Table already empty")
con.close()

# Step 2: Run Engine 1
print("\nStep 2: Running Engine 1 fresh...")
sys.path.insert(0, str(Path(__file__).parent))

from db import read_wip_orders, read_machine_master, read_machine_daily, read_routing_master
from preprocess import filter_wip_orders, compute_horizon, build_scheduler_input
from pipeline import schedule_all_orders
from models import Config

with open('config.json') as f:
    config = Config(**json.load(f))

result = schedule_all_orders(config, date.today())

# Step 3: Verify database
print("\nStep 3: Verifying database state...")
con = oracledb.connect(
    user=os.getenv("ORACLE_USER"),
    password=os.getenv("ORACLE_PASSWORD"),
    dsn=os.getenv("ORACLE_DSN")
)
cur = con.cursor()

cur.execute("SELECT COUNT(*) FROM MCH_SCHEDULE_OUTPUT")
new_count = cur.fetchone()[0]
print(f"  New total rows: {new_count}")

cur.execute("SELECT COUNT(DISTINCT RUN_ID) FROM MCH_SCHEDULE_OUTPUT")
run_id_count = cur.fetchone()[0]
print(f"  Distinct RUN_IDs: {run_id_count}")

# Check for duplicates
cur.execute("""
    SELECT COUNT(*) FROM (
        SELECT PRODUCTION_ORDER, OPERATION_NO, WORK_CENTER, SHIFT, SCHEDULED_DATE, BALANCE_QTY,
               COUNT(*) as dup_count
        FROM MCH_SCHEDULE_OUTPUT
        GROUP BY PRODUCTION_ORDER, OPERATION_NO, WORK_CENTER, SHIFT, SCHEDULED_DATE, BALANCE_QTY
        HAVING COUNT(*) > 1
    )
""")
dup_count = cur.fetchone()[0]
print(f"  Duplicate rows: {dup_count}")

if dup_count == 0 and run_id_count == 1:
    print("\n[SUCCESS] Fresh schedule written successfully with no duplicates!")
else:
    print(f"\n[ERROR] Unexpected state: {run_id_count} RUN_IDs, {dup_count} duplicate rows")

con.close()
