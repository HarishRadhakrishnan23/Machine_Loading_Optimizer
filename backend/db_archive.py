"""Database archiving logic: move old runs to archive tables."""

import oracledb
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).parent.parent / ".env")


def archive_old_runs(days_to_keep=90):
    """
    Move schedules and simulations older than N days to archive tables.
    Keep only the most recent N days in the live tables for fast queries.
    """
    con = oracledb.connect(
        user=os.getenv("ORACLE_USER"),
        password=os.getenv("ORACLE_PASSWORD"),
        dsn=os.getenv("ORACLE_DSN")
    )
    cur = con.cursor()

    cutoff_date = datetime.now() - timedelta(days=days_to_keep)

    print(f"\n{'='*70}")
    print(f"ARCHIVING RUNS OLDER THAN {cutoff_date.strftime('%Y-%m-%d')}")
    print(f"{'='*70}")

    # ─────────────────────────────────────────────────────────────────────
    # 1. ARCHIVE OLD SCHEDULE RUNS
    # ─────────────────────────────────────────────────────────────────────
    print("\n[1/4] Archiving MCH_SCHEDULE_OUTPUT...")

    # Count old rows
    cur.execute("""
        SELECT COUNT(*) FROM MCH_SCHEDULE_OUTPUT
        WHERE GENERATED_AT < :cutoff
    """, {"cutoff": cutoff_date})
    old_schedule_count = cur.fetchone()[0]

    if old_schedule_count > 0:
        # Insert into archive
        cur.execute("""
            INSERT INTO MCH_SCHEDULE_OUTPUT_ARCHIVE
            SELECT * FROM MCH_SCHEDULE_OUTPUT
            WHERE GENERATED_AT < :cutoff
        """, {"cutoff": cutoff_date})
        con.commit()
        print(f"  ✓ Moved {old_schedule_count} rows to MCH_SCHEDULE_OUTPUT_ARCHIVE")

        # Delete from live
        cur.execute("""
            DELETE FROM MCH_SCHEDULE_OUTPUT
            WHERE GENERATED_AT < :cutoff
        """, {"cutoff": cutoff_date})
        con.commit()
        print(f"  ✓ Deleted {old_schedule_count} rows from MCH_SCHEDULE_OUTPUT")
    else:
        print(f"  ✓ No old schedule runs (all within {days_to_keep} days)")

    # ─────────────────────────────────────────────────────────────────────
    # 2. ARCHIVE OLD SIMULATION RESULTS
    # ─────────────────────────────────────────────────────────────────────
    print("\n[2/4] Archiving MCH_SIM_RESULTS...")

    cur.execute("""
        SELECT COUNT(*) FROM MCH_SIM_RESULTS
        WHERE CREATED_AT < :cutoff
    """, {"cutoff": cutoff_date})
    old_sim_count = cur.fetchone()[0]

    if old_sim_count > 0:
        cur.execute("""
            INSERT INTO MCH_SIM_RESULTS_ARCHIVE
            SELECT * FROM MCH_SIM_RESULTS
            WHERE CREATED_AT < :cutoff
        """, {"cutoff": cutoff_date})
        con.commit()
        print(f"  ✓ Moved {old_sim_count} rows to MCH_SIM_RESULTS_ARCHIVE")

        cur.execute("""
            DELETE FROM MCH_SIM_RESULTS
            WHERE CREATED_AT < :cutoff
        """, {"cutoff": cutoff_date})
        con.commit()
        print(f"  ✓ Deleted {old_sim_count} rows from MCH_SIM_RESULTS")
    else:
        print(f"  ✓ No old simulation runs (all within {days_to_keep} days)")

    # ─────────────────────────────────────────────────────────────────────
    # 3. VERIFY TABLE SIZES
    # ─────────────────────────────────────────────────────────────────────
    print("\n[3/4] Verifying table sizes...")

    cur.execute("SELECT COUNT(*) FROM MCH_SCHEDULE_OUTPUT")
    live_schedule = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM MCH_SCHEDULE_OUTPUT_ARCHIVE")
    archive_schedule = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM MCH_SIM_RESULTS")
    live_sim = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM MCH_SIM_RESULTS_ARCHIVE")
    archive_sim = cur.fetchone()[0]

    print(f"  MCH_SCHEDULE_OUTPUT:         {live_schedule:,} rows (live)")
    print(f"  MCH_SCHEDULE_OUTPUT_ARCHIVE: {archive_schedule:,} rows (archived)")
    print(f"  MCH_SIM_RESULTS:             {live_sim:,} rows (live)")
    print(f"  MCH_SIM_RESULTS_ARCHIVE:     {archive_sim:,} rows (archived)")

    # ─────────────────────────────────────────────────────────────────────
    # 4. SUMMARY
    # ─────────────────────────────────────────────────────────────────────
    print("\n[4/4] Archive complete")
    print(f"\n{'='*70}")
    print(f"Summary:")
    print(f"  Total rows moved: {old_schedule_count + old_sim_count}")
    print(f"  Live tables size: {live_schedule + live_sim} rows (bounded)")
    print(f"  Archive tables size: {archive_schedule + archive_sim} rows (history)")
    print(f"{'='*70}\n")

    con.close()


if __name__ == "__main__":
    # Run archival every time this is called
    archive_old_runs(days_to_keep=90)
