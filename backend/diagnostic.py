#!/usr/bin/env python3
"""Diagnostic script: show what's scheduled vs what couldn't fit."""

import oracledb, os, pandas as pd
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
load_dotenv(Path(__file__).parent.parent / ".env")

con = oracledb.connect(
    user=os.getenv("ORACLE_USER"),
    password=os.getenv("ORACLE_PASSWORD"),
    dsn=os.getenv("ORACLE_DSN")
)

# What's in the schedule
df_schedule = pd.read_sql("""
    SELECT RUN_ID, COUNT(*) as rows, SUM(BALANCE_QTY) as pieces,
           MIN(SCHEDULED_DATE) as first_date, MAX(SCHEDULED_DATE) as last_date,
           MAX(GENERATED_AT) as generated_at
    FROM MCH_SCHEDULE_OUTPUT
    GROUP BY RUN_ID
    ORDER BY MAX(GENERATED_AT) DESC
""", con)
if not df_schedule.empty:
    df_schedule = df_schedule.head(1)

print("✓ Latest Schedule:")
print(df_schedule.to_string(index=False))

# What tasks are MISSING
# Get the latest RUN_ID
latest_run = pd.read_sql("""
    SELECT DISTINCT RUN_ID FROM MCH_SCHEDULE_OUTPUT
    ORDER BY GENERATED_AT DESC
""", con)

if not latest_run.empty:
    run_id = latest_run.iloc[0]['RUN_ID']
    df_missing = pd.read_sql(f"""
        SELECT PRODUCTION_ORDER, OPERATION_NO, TASK,
               QUANTITY_ORDERED - QUANTITY_COMPLETED - COALESCE(QUANTITY_REJECTED, 0) as balance_qty
        FROM MCH_WIP
        WHERE CYCLE_TIME > 0
        AND QUANTITY_ORDERED - QUANTITY_COMPLETED - COALESCE(QUANTITY_REJECTED, 0) > 0
        AND (PRODUCTION_ORDER, OPERATION_NO) NOT IN (
            SELECT PRODUCTION_ORDER, OPERATION_NO FROM MCH_SCHEDULE_OUTPUT
            WHERE RUN_ID = '{run_id}'
        )
        ORDER BY PRODUCTION_ORDER
    """, con)
else:
    df_missing = pd.DataFrame()

print(f"\n⚠️  {len(df_missing)} tasks NOT scheduled:")
print(df_missing.to_string(index=False))

con.close()
