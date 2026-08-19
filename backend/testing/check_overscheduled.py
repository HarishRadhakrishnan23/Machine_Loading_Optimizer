#!/usr/bin/env python3
"""Check whether the 15 'over-scheduled' tasks are explained by live WIP drift
since the schedule was generated (production progressed after generation)."""

import oracledb, os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent.parent / ".env")

con = oracledb.connect(
    user=os.getenv("ORACLE_USER"),
    password=os.getenv("ORACLE_PASSWORD"),
    dsn=os.getenv("ORACLE_DSN"),
)
cur = con.cursor()

pairs = [
    ("VN1003258", 80), ("VN1003359", 60), ("VN1003366", 70), ("VN1003384", 65),
    ("VN1003386", 70), ("VN1003405", 50), ("VN1003409", 50), ("VN1003444", 65),
    ("VN1003517", 80), ("VN1003532", 50), ("VN1003535", 20), ("VN1003538", 90),
    ("VN1003576", 20), ("VN1003605", 60), ("VN1003627", 10),
]

# Get the schedule's GENERATED_AT for reference
cur.execute("SELECT MIN(GENERATED_AT), MAX(GENERATED_AT) FROM MCH_SCHEDULE_OUTPUT")
gen_min, gen_max = cur.fetchone()
print(f"Schedule GENERATED_AT range: {gen_min} .. {gen_max}\n")

print(f"{'ORDER':12} {'OP':>4} {'ORDERED':>8} {'COMPLETED':>10} {'REJECTED':>9} {'BALANCE_NOW':>12} {'PLANNED_IN_SCHED':>17}")
for order, op in pairs:
    cur.execute("""
        SELECT QUANTITY_ORDERED, QUANTITY_COMPLETED, QUANTITY_REJECTED, OPERATION_STATUS
        FROM MCH_WIP WHERE PRODUCTION_ORDER = :o AND OPERATION = :op
    """, {"o": order, "op": op})
    wip_row = cur.fetchone()

    cur.execute("""
        SELECT SUM(BALANCE_QTY) FROM MCH_SCHEDULE_OUTPUT
        WHERE PRODUCTION_ORDER = :o AND OPERATION_NO = :op
    """, {"o": order, "op": op})
    planned = cur.fetchone()[0]

    if wip_row:
        ordered, completed, rejected, status = wip_row
        rejected = rejected or 0
        balance_now = ordered - completed - rejected
        print(f"{order:12} {op:>4} {ordered:>8} {completed:>10} {rejected:>9} {balance_now:>12} {planned:>17}  status={status}")
    else:
        print(f"{order:12} {op:>4}  NOT FOUND IN CURRENT MCH_WIP (fully consumed / removed?)  planned_in_sched={planned}")

con.close()
