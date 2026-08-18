#!/usr/bin/env python3
"""Check MCH_SCHEDULE_OUTPUT for duplicates and RUN_ID info."""

import oracledb, os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent / ".env")

con = oracledb.connect(
    user=os.getenv("ORACLE_USER"),
    password=os.getenv("ORACLE_PASSWORD"),
    dsn=os.getenv("ORACLE_DSN")
)
cur = con.cursor()

# Check total rows
cur.execute("SELECT COUNT(*) FROM MCH_SCHEDULE_OUTPUT")
total = cur.fetchone()[0]
print(f"Total rows in MCH_SCHEDULE_OUTPUT: {total}")

# Check RUN_IDs
cur.execute("SELECT RUN_ID FROM (SELECT DISTINCT RUN_ID FROM MCH_SCHEDULE_OUTPUT) t ORDER BY RUN_ID DESC")
run_ids = [r[0] for r in cur.fetchall()]
print(f"RUN_IDs in database ({len(run_ids)} total):")
for i, rid in enumerate(run_ids[:5]):
    cur.execute("SELECT COUNT(*), MIN(GENERATED_AT) FROM MCH_SCHEDULE_OUTPUT WHERE RUN_ID = :id", {"id": rid})
    cnt, gen_time = cur.fetchone()
    print(f"  {i+1}. {rid[:12]}... : {cnt} rows (generated: {gen_time})")

if len(run_ids) > 5:
    print(f"  ... and {len(run_ids)-5} more")

# Check if latest RUN_ID has duplicates
if run_ids:
    latest_run_id = run_ids[0]
    print(f"\nAnalyzing latest RUN_ID: {latest_run_id[:12]}...")
    cur.execute("""
        SELECT PRODUCTION_ORDER, OPERATION_NO, WORK_CENTER, SHIFT, SCHEDULED_DATE, BALANCE_QTY,
               COUNT(*) as dup_count
        FROM MCH_SCHEDULE_OUTPUT
        WHERE RUN_ID = :run_id
        GROUP BY PRODUCTION_ORDER, OPERATION_NO, WORK_CENTER, SHIFT, SCHEDULED_DATE, BALANCE_QTY
        HAVING COUNT(*) > 1
    """, {"run_id": latest_run_id})

    dups = cur.fetchall()
    if dups:
        print(f"  Found {len(dups)} duplicate rows:")
        for order, op, machine, shift, date, qty, count in dups[:5]:
            print(f"    {order} Op{op}: {machine} {shift} {date} qty={qty} appears {count}x")
    else:
        print("  [OK] No duplicates in latest run")

con.close()
