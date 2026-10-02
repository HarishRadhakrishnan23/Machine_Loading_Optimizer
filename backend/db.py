"""
db.py — Oracle connection and data access layer for TOV Machine Loading Optimizer.

Uses python-oracledb in THIN MODE (no Oracle Instant Client required).
Credentials loaded from environment variables (.env file).

All functions read from the 6 ERP views (read-only) and write to the 2 result
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

    Model D note: SETUP_TIME is IGNORED by Model D (replaced by FIXTURE_CHANGE_TIME /
    LOCATOR_CHANGE_TIME from MCH_ITEMWISE_FIXTURE_LOCATOR). Only used for machine
    capability/priority lookup.
    """
    with get_connection() as conn:
        return pd.read_sql("SELECT * FROM MCH_MACHINE_PRIORITY", conn)


def read_fixture_locator_master() -> pd.DataFrame:
    """
    Read itemwise fixture/locator master from MCH_ITEMWISE_FIXTURE_LOCATOR view.
    Keyed by (SIZE_INCH, CLASS, MOC, DESIGN, TASK, WORK_CENTER) — Model E's batch/
    fixture key (CLAUDE.md "Batch / fixture key — used everywhere, all tables").

    Returns: DataFrame with columns (SIZE_INCH, CLASS, MOC, DESIGN, TASK, WORK_CENTER,
    FIXTURE, LOCATOR, FIXTURE_CHANGE_TIME, LOCATOR_CHANGE_TIME, LOAD_UNLOAD_TIME,
    USER_ID, USER_DATE). Model E §3: change-times apply once (to the batch's first
    piece); LOAD_UNLOAD_TIME is per piece.
    """
    with get_connection() as conn:
        return pd.read_sql("SELECT * FROM MCH_ITEMWISE_FIXTURE_LOCATOR", conn)


def read_fixture_locator_inventory() -> pd.DataFrame:
    """
    Read physical fixture/locator inventory counts from MCH_FIXTURE_LOCATOR view.

    Returns: DataFrame with columns (DEVICE_NAME, DEVICE_TYPE, QUANTITY, USER_ID,
    USER_DATE). DEVICE_TYPE is 'F' (fixture) or 'L' (locator). QUANTITY is a hard,
    plant-wide concurrency cap (Model D D.8) — never a soft/advisory limit.
    """
    with get_connection() as conn:
        return pd.read_sql("SELECT * FROM MCH_FIXTURE_LOCATOR", conn)


def read_holiday_calendar() -> pd.DataFrame:
    """
    Read the confirmed company holiday calendar from L750.TCCCP019 — a
    cross-schema, read-only table (not one of the 6 MCH_* ERP views). On
    every date listed here, AVAILABLE_MINS is forced to 0 for all three
    shifts, for every machine (CLAUDE.md "Shift clock-time convention" —
    holiday closure rule). The table also carries old/historical entries
    (e.g. 2008, 2013, 2014) that are harmless to read — a date that never
    falls inside a schedule's horizon simply never gets looked up.

    Returns: DataFrame with columns (CALENDAR_CODE, DATE_1, DESCRIPTION).
    """
    with get_connection() as conn:
        return pd.read_sql("SELECT * FROM L750.TCCCP019", conn)


# ─────────────────────────────────────────────────────────────────────────────
# Write functions (pandas DataFrames → result tables)
# ─────────────────────────────────────────────────────────────────────────────

def delete_schedule_output() -> int:
    """
    CLAUDE.md: no historical retention — every /schedule/generate run DELETEs
    all existing MCH_SCHEDULE_OUTPUT rows before writing the fresh schedule.
    Returns the number of rows removed.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM MCH_SCHEDULE_OUTPUT")
        removed = cursor.rowcount
        conn.commit()
    return removed


def write_schedule_output(schedule_rows: list[dict]) -> int:
    """
    Write Engine 1 (Model E) scheduling results to MCH_SCHEDULE_OUTPUT.

    Args:
        schedule_rows: list of dicts with the exact Oracle column names as
            keys (RUN_ID, PRODUCTION_ORDER, OPERATION_NO, LINE_NO, TASK,
            WORK_CENTER, SHIFT, SCHEDULED_DATE, BALANCE_QTY, GENERATED_AT,
            BATCH_KEY, IS_SAFETY_STOCK, FIXTURE_ID, LOCATOR_ID,
            START_TIMESTAMP, END_TIMESTAMP, REMARK, ORDER_COMPLETION_DATE,
            ORDER_COMPLETION_SHIFT) — exactly what
            `dispatch_writer.to_oracle_rows` produces. Caller is responsible
            for calling `delete_schedule_output()` first (no historical
            retention) — this function only inserts.

    Returns: number of rows inserted.
    """
    if not schedule_rows:
        return 0

    with get_connection() as conn:
        cursor = conn.cursor()
        insert_sql = """
            INSERT INTO MCH_SCHEDULE_OUTPUT
            (RUN_ID, PRODUCTION_ORDER, OPERATION_NO, LINE_NO, TASK, WORK_CENTER, SHIFT,
             SCHEDULED_DATE, BALANCE_QTY, GENERATED_AT, BATCH_KEY, IS_SAFETY_STOCK,
             FIXTURE_ID, LOCATOR_ID, START_TIMESTAMP, END_TIMESTAMP, REMARK,
             ORDER_COMPLETION_DATE, ORDER_COMPLETION_SHIFT)
            VALUES (:RUN_ID, :PRODUCTION_ORDER, :OPERATION_NO, :LINE_NO, :TASK, :WORK_CENTER, :SHIFT,
                    :SCHEDULED_DATE, :BALANCE_QTY, :GENERATED_AT, :BATCH_KEY, :IS_SAFETY_STOCK,
                    :FIXTURE_ID, :LOCATOR_ID, :START_TIMESTAMP, :END_TIMESTAMP, :REMARK,
                    :ORDER_COMPLETION_DATE, :ORDER_COMPLETION_SHIFT)
        """
        cursor.executemany(insert_sql, schedule_rows)
        conn.commit()

    return len(schedule_rows)


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
    Fetch all 6 ERP views at once (convenience for POST /data/refresh endpoint).

    Returns: dict with keys ('wip_orders', 'machine_master', 'machine_daily',
             'routing_master', 'fixture_locator_master', 'fixture_locator_inventory'),
             each mapping to a DataFrame.
    """
    return {
        "wip_orders": read_wip_orders(),
        "machine_master": read_machine_master(),
        "machine_daily": read_machine_daily(),
        "routing_master": read_routing_master(),
        "fixture_locator_master": read_fixture_locator_master(),
        "fixture_locator_inventory": read_fixture_locator_inventory(),
    }
