"""
db.py — Oracle connection and data access layer for TOV Machine Loading Optimizer.

Uses python-oracledb in THIN MODE (no Oracle Instant Client required).
Credentials loaded from environment variables (.env file).

All functions read from the 4 ERP views (read-only) and write to the 2 result
tables (MCH_SCHEDULE_OUTPUT for Engine 1, MCH_SIM_RESULTS for Engine 2).
"""

import os
from contextlib import contextmanager
from datetime import date, datetime
from typing import Optional

import oracledb
import pandas as pd
from dotenv import load_dotenv

# Load .env file (searched in current directory first, then parent directories)
load_dotenv()

# Read connection credentials from environment
ORACLE_USER = os.getenv("ORACLE_USER")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD")
ORACLE_DSN = os.getenv("ORACLE_DSN")

# Validate required environment variables
if not all([ORACLE_USER, ORACLE_PASSWORD, ORACLE_DSN]):
    raise RuntimeError(
        "Missing Oracle credentials. Set ORACLE_USER, ORACLE_PASSWORD, ORACLE_DSN "
        "in .env file (copy .env.example → .env and fill in your credentials)."
    )


@contextmanager
def get_connection():
    """
    Context manager for Oracle connection (thin mode).

    Usage:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM MCH_WIP")
    """
    conn = None
    try:
        conn = oracledb.connect(
            user=ORACLE_USER,
            password=ORACLE_PASSWORD,
            dsn=ORACLE_DSN,
        )
        yield conn
    finally:
        if conn:
            conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Read functions (ERP views → pandas DataFrames)
# ─────────────────────────────────────────────────────────────────────────────

def read_wip_orders() -> pd.DataFrame:
    """
    Read all pending WIP orders from MCH_WIP view.
    Returns: DataFrame with columns (COMPANY, PRODUCTION_ORDER, PRODUCTION_START_DATE_AND_TIME,
    ORDER_STATUS, ITEM, ITEM_DESCRIPTION, SIZE_INCH, CLASS, MOC, DESIGN, ITEM_CATEGORY,
    REFERENCE, QUANTITY_ORDERED, CDD, OPERATION, OPERATION_STATUS, TASK, WORK_CENTER,
    QUANTITY_COMPLETED, QUANTITY_REJECTED, CYCLE_TIME).
    """
    with get_connection() as conn:
        return pd.read_sql("SELECT * FROM MCH_WIP", conn)


def read_machine_master() -> pd.DataFrame:
    """
    Read baseline machine capacity from MCH_MACHINE_AVAILABILITY view.
    Returns: DataFrame with columns (COMPANY, WORK_CENTER, SHIFT, WORKING_MINS, OEE, AVAILABLE_MINS).
    """
    with get_connection() as conn:
        return pd.read_sql("SELECT * FROM MCH_MACHINE_AVAILABILITY", conn)


def read_machine_daily(target_date: Optional[date] = None) -> pd.DataFrame:
    """
    Read day-specific machine capacity overrides from MCH_MACHINE_AVAILABILITY_BY_DATE view.

    Args:
        target_date: if provided, filter to only this date; otherwise return all rows.

    Returns: DataFrame with columns (COMPANY, WORK_CENTER, WORKING_DATE, SHIFT, WORKING_MINS, OEE, AVAILABLE_MINS).
    """
    with get_connection() as conn:
        if target_date:
            query = """
                SELECT * FROM MCH_MACHINE_AVAILABILITY_BY_DATE
                WHERE WORKING_DATE = TO_DATE(:target_date, 'YYYY-MM-DD')
            """
            return pd.read_sql(query, conn, params={"target_date": target_date.strftime("%Y-%m-%d")})
        else:
            return pd.read_sql("SELECT * FROM MCH_MACHINE_AVAILABILITY_BY_DATE", conn)


def read_routing_master() -> pd.DataFrame:
    """
    Read capability matrix from MCH_MACHINE_PRIORITY view.
    Returns: DataFrame with columns (COMPANY, SIZE_INCH, CLASS, MOC, DESIGN, ITEM_CATEGORY,
    TASK, MACHINE_PRIORITY, WORK_CENTER, SETUP_TIME).
    """
    with get_connection() as conn:
        return pd.read_sql("SELECT * FROM MCH_MACHINE_PRIORITY", conn)


# ─────────────────────────────────────────────────────────────────────────────
# Write functions (pandas DataFrames → result tables)
# ─────────────────────────────────────────────────────────────────────────────

def write_schedule_output(schedule_rows: list[dict], run_id: str) -> int:
    """
    Write Engine 1 scheduling results to MCH_SCHEDULE_OUTPUT.

    Args:
        schedule_rows: list of dicts with keys (PRODUCTION_ORDER, OPERATION_NO, TASK,
                       WORK_CENTER, SHIFT, SCHEDULED_DATE, BALANCE_QTY, START_OFFSET_MIN,
                       END_OFFSET_MIN, BATCH_KEY, IS_SAFETY_STOCK, generated_at).
        run_id: unique identifier for this scheduling run (e.g., UUID).

    Returns: number of rows inserted.
    """
    if not schedule_rows:
        return 0

    with get_connection() as conn:
        cursor = conn.cursor()
        insert_sql = """
            INSERT INTO MCH_SCHEDULE_OUTPUT
            (RUN_ID, PRODUCTION_ORDER, OPERATION_NO, TASK, WORK_CENTER, SHIFT,
             SCHEDULED_DATE, BALANCE_QTY, START_OFFSET_MIN, END_OFFSET_MIN,
             BATCH_KEY, IS_SAFETY_STOCK, GENERATED_AT)
            VALUES (:run_id, :production_order, :operation_no, :task, :work_center,
                    :shift, :scheduled_date, :balance_qty, :start_offset_min,
                    :end_offset_min, :batch_key, :is_safety_stock, :generated_at)
        """

        rows_inserted = 0
        for row in schedule_rows:
            cursor.execute(insert_sql, {
                "run_id": run_id,
                "production_order": row["PRODUCTION_ORDER"],
                "operation_no": row["OPERATION_NO"],
                "task": row.get("TASK"),  # nullable for display
                "work_center": row["WORK_CENTER"],
                "shift": row["SHIFT"],
                "scheduled_date": row["SCHEDULED_DATE"],
                "balance_qty": row["BALANCE_QTY"],
                "start_offset_min": row["START_OFFSET_MIN"],
                "end_offset_min": row["END_OFFSET_MIN"],
                "batch_key": row["BATCH_KEY"],
                "is_safety_stock": row["IS_SAFETY_STOCK"],
                "generated_at": row["generated_at"],
            })
            rows_inserted += 1

        conn.commit()

    return rows_inserted


def write_sim_results(sim_rows: list[dict], sim_id: str) -> int:
    """
    Write Engine 2 simulation results to MCH_SIM_RESULTS.

    Args:
        sim_rows: list of dicts with keys (PRODUCTION_ORDER, OLD_COMPLETION_DATE,
                  NEW_COMPLETION_DATE, SLIP_DAYS, RISK_FLAG, created_at).
        sim_id: unique identifier for this simulation run (e.g., UUID).
        elevated_orders: comma-joined string of elevated PRODUCTION_ORDER(s).

    Returns: number of rows inserted.
    """
    if not sim_rows:
        return 0

    with get_connection() as conn:
        cursor = conn.cursor()
        insert_sql = """
            INSERT INTO MCH_SIM_RESULTS
            (SIM_ID, ELEVATED_ORDER, PRODUCTION_ORDER, OLD_COMPLETION_DATE,
             NEW_COMPLETION_DATE, SLIP_DAYS, RISK_FLAG, CREATED_AT)
            VALUES (:sim_id, :elevated_order, :production_order, :old_completion_date,
                    :new_completion_date, :slip_days, :risk_flag, :created_at)
        """

        rows_inserted = 0
        for row in sim_rows:
            cursor.execute(insert_sql, {
                "sim_id": sim_id,
                "elevated_order": row.get("ELEVATED_ORDER"),  # comma-joined string
                "production_order": row["PRODUCTION_ORDER"],
                "old_completion_date": row.get("OLD_COMPLETION_DATE"),
                "new_completion_date": row.get("NEW_COMPLETION_DATE"),
                "slip_days": row.get("SLIP_DAYS"),
                "risk_flag": row["RISK_FLAG"],
                "created_at": row["created_at"],
            })
            rows_inserted += 1

        conn.commit()

    return rows_inserted


# ─────────────────────────────────────────────────────────────────────────────
# Convenience read-all function (data refresh)
# ─────────────────────────────────────────────────────────────────────────────

def refresh_all_views() -> dict[str, pd.DataFrame]:
    """
    Fetch all 4 ERP views at once (convenience for POST /data/refresh endpoint).

    Returns: dict with keys ('wip_orders', 'machine_master', 'machine_daily', 'routing_master'),
             each mapping to a DataFrame.
    """
    return {
        "wip_orders": read_wip_orders(),
        "machine_master": read_machine_master(),
        "machine_daily": read_machine_daily(),
        "routing_master": read_routing_master(),
    }
