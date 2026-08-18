#!/usr/bin/env python3
"""Simple check: what's in the database?"""

import oracledb, os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

con = oracledb.connect(
    user=os.getenv("ORACLE_USER"),
    password=os.getenv("ORACLE_PASSWORD"),
    dsn=os.getenv("ORACLE_DSN")
)
cur = con.cursor()

# Check if table exists
print("=== Checking MCH_SCHEDULE_OUTPUT ===")
try:
    cur.execute("SELECT COUNT(*) FROM MCH_SCHEDULE_OUTPUT")
    count = cur.fetchone()[0]
    print(f"✓ Table exists. Rows: {count}")
except Exception as e:
    print(f"✗ Error: {e}")
    con.close()
    exit(1)

# Get latest run
print("\n=== Latest Schedule Run ===")
cur.execute("""
    SELECT RUN_ID, COUNT(*) as cnt, SUM(BALANCE_QTY) as pieces
    FROM MCH_SCHEDULE_OUTPUT
    GROUP BY RUN_ID
    ORDER BY RUN_ID DESC
""")
rows = cur.fetchall()
if rows:
    run_id, cnt, pieces = rows[0]
    print(f"RUN_ID: {run_id}")
    print(f"Rows: {cnt}")
    print(f"Pieces: {pieces}")
else:
    print("No data found")
    con.close()
    exit(0)

# Check missing tasks
print("\n=== Tasks NOT in Schedule ===")
cur.execute("""
    SELECT COUNT(*) FROM MCH_WIP
    WHERE CYCLE_TIME > 0
    AND QUANTITY_ORDERED - QUANTITY_COMPLETED - COALESCE(QUANTITY_REJECTED, 0) > 0
    AND (PRODUCTION_ORDER, OPERATION) NOT IN (
        SELECT PRODUCTION_ORDER, OPERATION_NO FROM MCH_SCHEDULE_OUTPUT
        WHERE RUN_ID = :run_id
    )
""", {"run_id": run_id})
missing_count = cur.fetchone()[0]
print(f"Missing tasks: {missing_count}")

# Show first 10 missing
print("\nFirst 10 missing tasks:")
cur.execute("""
    SELECT PRODUCTION_ORDER, OPERATION, TASK,
           QUANTITY_ORDERED - QUANTITY_COMPLETED - COALESCE(QUANTITY_REJECTED, 0) as balance_qty
    FROM MCH_WIP
    WHERE CYCLE_TIME > 0
    AND QUANTITY_ORDERED - QUANTITY_COMPLETED - COALESCE(QUANTITY_REJECTED, 0) > 0
    AND (PRODUCTION_ORDER, OPERATION) NOT IN (
        SELECT PRODUCTION_ORDER, OPERATION_NO FROM MCH_SCHEDULE_OUTPUT
        WHERE RUN_ID = :run_id
    )
    ORDER BY PRODUCTION_ORDER
""", {"run_id": run_id})

for row in cur.fetchall()[:10]:
    order, op, task, qty = row
    print(f"  {order} Op{op} ({task}): {qty} pieces")

con.close()
print("\n✓ Diagnostic complete")
