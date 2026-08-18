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

# Check: How many machines have NO entries in MCH_MACHINE_AVAILABILITY_BY_DATE?
cur.execute("""
    SELECT COUNT(DISTINCT WORK_CENTER) FROM MCH_MACHINE_AVAILABILITY
    WHERE WORK_CENTER NOT IN (
        SELECT DISTINCT WORK_CENTER FROM MCH_MACHINE_AVAILABILITY_BY_DATE
    )
""")
missing_machines = cur.fetchone()[0]
print(f"Machines with NO daily overrides: {missing_machines}")

# Check: Sample of over-scheduled tasks
cur.execute("""
    SELECT DISTINCT PRODUCTION_ORDER, OPERATION FROM MCH_WIP
    WHERE (PRODUCTION_ORDER, OPERATION) IN (
        SELECT PRODUCTION_ORDER, OPERATION_NO FROM MCH_SCHEDULE_OUTPUT
    )
    AND QUANTITY_ORDERED - QUANTITY_COMPLETED - COALESCE(QUANTITY_REJECTED, 0) = 0
    FETCH FIRST 10 ROWS ONLY
""")
print(f"\nTasks scheduled but already complete (balance_qty=0):")
for row in cur.fetchall():
    print(f"  {row[0]} Op{row[1]}")

con.close()