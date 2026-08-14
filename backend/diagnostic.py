#!/usr/bin/env python3
"""Diagnostic script: show what's scheduled vs what couldn't fit."""

import oracledb, os, pandas as pd

con = oracledb.connect(
    user=os.getenv("ORACLE_USER"),
    password=os.getenv("ORACLE_PASSWORD"),
    dsn=os.getenv("ORACLE_DSN")
)

# What's in the schedule
df_schedule = pd.read_sql("""
    SELECT RUN_ID, COUNT(*) as rows, SUM(BALANCE_QTY) as pieces,
           MIN(SCHEDULED_DATE) as first_date, MAX(SCHEDULED_DATE) as last_date
    FROM MCH_SCHEDULE_OUTPUT
    GROUP BY RUN_ID
    ORDER BY GENERATED_AT DESC
    FETCH FIRST 1 ROW ONLY
""", con)

print("✓ Latest Schedule:")
print(df_schedule.to_string(index=False))

# What tasks are MISSING
df_missing = pd.read_sql("""
    SELECT PRODUCTION_ORDER, OPERATION_NO, TASK,
           QUANTITY_ORDERED - QUANTITY_COMPLETED - COALESCE(QUANTITY_REJECTED, 0) as balance_qty
    FROM MCH_WIP
    WHERE CYCLE_TIME > 0
    AND QUANTITY_ORDERED - QUANTITY_COMPLETED - COALESCE(QUANTITY_REJECTED, 0) > 0
    AND (PRODUCTION_ORDER, OPERATION_NO) NOT IN (
        SELECT DISTINCT PRODUCTION_ORDER, OPERATION_NO FROM MCH_SCHEDULE_OUTPUT
        WHERE RUN_ID = (SELECT RUN_ID FROM MCH_SCHEDULE_OUTPUT ORDER BY GENERATED_AT DESC FETCH FIRST 1 ROW ONLY)
    )
    ORDER BY PRODUCTION_ORDER
""", con)

print(f"\n⚠️  {len(df_missing)} tasks NOT scheduled:")
print(df_missing.to_string(index=False))

con.close()
