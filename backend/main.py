"""
main.py — FastAPI Backend for TOV Machine Loading Optimizer.

Exposes 9 REST endpoints orchestrating Engines 1 & 2 and ERP data access.

Endpoints:
  POST   /schedule/generate          Engine 1: generate schedule
  GET    /schedule/current           Read latest MCH_SCHEDULE_OUTPUT
  POST   /priority/simulate          Engine 2: simulate elevation
  GET    /orders/wip                 Read active WIP orders
  GET    /machines/capacity          Read resolved capacity for horizon
  POST   /data/refresh               Re-fetch all ERP views
  GET    /machines/daily             Read MCH_MACHINE_AVAILABILITY_BY_DATE
  GET    /config                     Read config.json
  PUT    /config                     Update config.json
"""

import json
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from db import (
    read_wip_orders,
    read_machine_master,
    read_machine_daily,
    read_routing_master,
    read_fixture_locator_master,
    read_fixture_locator_inventory,
    read_holiday_calendar,
)
from models import (
    Config,
    RiskReport,
    ScheduleGenerateResponse,
    SimulationRequest,
)
from model_e_pipeline import generate_schedule as run_model_e_schedule
from pipeline2 import simulate_elevation

# ─────────────────────────────────────────────────────────────────────────────
# FastAPI Setup
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="TOV Machine Loading Optimizer",
    description="ML-driven scheduling system for TOV valve manufacturing",
    version="1.0.0",
)

# CORS: allow React frontend (localhost:5173 in dev, any origin in prod via env var)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost"],  # dev + prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Config file path
CONFIG_FILE = Path(__file__).parent / "config.json"


def load_config() -> Config:
    """Load runtime config from config.json."""
    try:
        with open(CONFIG_FILE) as f:
            data = json.load(f)
        return Config(**data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load config: {e}")


def save_config(config: Config) -> None:
    """Persist config to config.json."""
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config.model_dump(), f, indent=2)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save config: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Healthcheck
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/health", tags=["Admin"])
def health():
    """Healthcheck endpoint."""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


# ─────────────────────────────────────────────────────────────────────────────
# Configuration Endpoints
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/config", tags=["Config"], response_model=Config)
def get_config():
    """
    Read current runtime configuration.

    Returns:
      - batch_bonus_months: window for upcoming-order bonus (days)
      - batch_bonus_value: urgency bonus value
      - downstream_queue_bonus_value: bonus when other order queued downstream
      - ageing_normalization_days: time window for ageing score normalization
      - machine_priority_epsilon: machine preference penalty weight
      - risk_safe_threshold_days: slack buffer for risk classification
      - engine2_time_limit_seconds: max time for priority elevation simulation
      - scheduling_horizon_safety_factor: horizon sizing multiplier
      - scheduling_horizon_buffer_days: additional days to add to horizon
    """
    return load_config()


@app.put("/config", tags=["Config"], response_model=Config)
def put_config(config: Config):
    """Update runtime configuration. Persists to config.json."""
    save_config(config)
    return config


# ─────────────────────────────────────────────────────────────────────────────
# Engine 1: Scheduling
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/schedule/generate", tags=["Engine 1"], response_model=ScheduleGenerateResponse)
def generate_schedule(run_date: Optional[date] = Query(None)):
    """
    Generate a schedule for all pending orders using Engine 1 (Model E — the
    deterministic continuous-time dispatch simulator, CLAUDE.md §E.13). CP-SAT
    is not invoked.

    Query parameters:
      - run_date: reference date (default: today)

    Returns:
      - run_id: unique identifier for this scheduling run
      - rows_written: total MCH_SCHEDULE_OUTPUT rows written (== total WIP rows — every
        eligible order-operation always produces a row, scheduled or not, §E.12)
      - scheduled_count / scheduled_no_fixture_count / excluded_count: breakdown

    Writes:
      - MCH_SCHEDULE_OUTPUT table (prior rows deleted first — no historical retention)
    """
    if run_date is None:
        run_date = date.today()

    config = load_config()
    try:
        result = run_model_e_schedule(config.model_dump(), run_date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Schedule generation failed: {e}")
    return ScheduleGenerateResponse(**result.__dict__)


@app.get("/schedule/current", tags=["Engine 1"])
def get_current_schedule():
    """
    Read the latest schedule from MCH_SCHEDULE_OUTPUT.

    Returns:
      - List of ScheduleOutputRow (job assignments)
      - Grouped by order/machine for Gantt rendering
    """
    try:
        import pandas as pd
        from db import get_connection

        with get_connection() as conn:
            df = pd.read_sql(
                """
                SELECT RUN_ID, PRODUCTION_ORDER, OPERATION_NO, LINE_NO, TASK, WORK_CENTER,
                       SHIFT, SCHEDULED_DATE, BALANCE_QTY, BATCH_KEY, IS_SAFETY_STOCK,
                       FIXTURE_ID, LOCATOR_ID, START_TIMESTAMP, END_TIMESTAMP, REMARK,
                       ORDER_COMPLETION_DATE, ORDER_COMPLETION_SHIFT, GENERATED_AT
                FROM MCH_SCHEDULE_OUTPUT
                ORDER BY GENERATED_AT DESC, PRODUCTION_ORDER, OPERATION_NO
                FETCH FIRST 5000 ROWS ONLY
                """,
                conn,
            )

        if df.empty:
            return {"assignments": [], "message": "No schedule generated yet"}

        def _d(v):
            return v.strftime("%Y-%m-%d") if pd.notna(v) else None

        def _ts(v):
            return v.isoformat() if pd.notna(v) else None

        # Convert to response format
        assignments = [
            {
                "production_order": row["PRODUCTION_ORDER"],
                "operation_no": row["OPERATION_NO"],
                "line_no": int(row["LINE_NO"]),
                "task": row["TASK"],
                "machine_name": row["WORK_CENTER"] if pd.notna(row["WORK_CENTER"]) else None,
                "shift": row["SHIFT"] if pd.notna(row["SHIFT"]) else None,
                "scheduled_date": _d(row["SCHEDULED_DATE"]),
                "balance_qty": int(row["BALANCE_QTY"]),
                "batch_key": row["BATCH_KEY"],
                "is_safety_stock": row["IS_SAFETY_STOCK"] == "Y",
                "fixture_id": row["FIXTURE_ID"] if pd.notna(row["FIXTURE_ID"]) else None,
                "locator_id": row["LOCATOR_ID"] if pd.notna(row["LOCATOR_ID"]) else None,
                "start_timestamp": _ts(row["START_TIMESTAMP"]),
                "end_timestamp": _ts(row["END_TIMESTAMP"]),
                "remark": row["REMARK"] if pd.notna(row["REMARK"]) else None,
                "order_completion_date": _d(row["ORDER_COMPLETION_DATE"]),
                "order_completion_shift": row["ORDER_COMPLETION_SHIFT"] if pd.notna(row["ORDER_COMPLETION_SHIFT"]) else None,
            }
            for _, row in df.iterrows()
        ]
        return {"assignments": assignments, "count": len(assignments)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read schedule: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Engine 2: Priority Simulation
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/priority/simulate", tags=["Engine 2"], response_model=RiskReport)
def simulate_priority(request: SimulationRequest, run_date: Optional[date] = Query(None)):
    """
    Simulate elevating one or more orders and measure impact on others.

    Request body:
      - orders: list of PRODUCTION_ORDER IDs to elevate
      - time_limit_seconds: override config.engine2_time_limit_seconds (optional)

    Returns:
      - sim_id: unique simulation identifier
      - elevated_orders: the orders that were elevated
      - impacts: list of SimResultRow (per other order: slip_days, risk_flag)
      - top_impacted: top 5 most-affected orders
      - safe_count, at_risk_count, breach_count: risk counts

    Writes:
      - MCH_SIM_RESULTS table with impact analysis
    """
    if run_date is None:
        run_date = date.today()

    if not request.orders:
        raise HTTPException(status_code=400, detail="orders list cannot be empty")

    config = load_config()
    if request.time_limit_seconds is not None:
        config.engine2_time_limit_seconds = request.time_limit_seconds

    try:
        result = simulate_elevation(request.orders, config, run_date)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Simulation failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Data Access: WIP Orders
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/orders/wip", tags=["Data"])
def get_wip_orders():
    """
    List active WIP orders (CT > 0, routable, non-QA, balance_qty > 0).

    Returns:
      - List of orders with production order, item, CDD, quantity, operations
      - Counts by order and status
    """
    try:
        from preprocess import filter_wip_orders

        wip_df = read_wip_orders()
        routing_df = read_routing_master()
        filtered = filter_wip_orders(wip_df, routing_df)

        if filtered.empty:
            return {"orders": [], "count": 0}

        # Group by production order.
        # NOTE: balance_qty is per-OPERATION (CLAUDE.md: "Balance Qty of Op_n is the
        # max input available for Op_n+1") — operations of the same order are pipeline
        # stages, not independent quantities. balance_qty_total (summed across an
        # order's pending ops) is therefore only comparable to quantity_ordered × the
        # number of pending ops, never to quantity_ordered alone — otherwise a 2-op
        # order can show e.g. "20 / 10 pcs" (200%). Expose that matching denominator
        # explicitly so the UI never has to guess it.
        orders_list = []
        for order_id, group in filtered.groupby("PRODUCTION_ORDER"):
            quantity_ordered = int(group.iloc[0]["QUANTITY_ORDERED"])
            operations = len(group)
            orders_list.append({
                "production_order": order_id,
                "item": group.iloc[0]["ITEM"],
                "cdd": str(group.iloc[0]["CDD"]) if group.iloc[0]["CDD"] else None,
                "quantity_ordered": quantity_ordered,
                "quantity_ordered_total": quantity_ordered * operations,
                "balance_qty_total": int(group["balance_qty"].sum()),
                "operations": operations,
            })

        return {"orders": orders_list, "count": len(orders_list)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read WIP: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Data Access: Machine Capacity
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/machines/capacity", tags=["Data"])
def get_machines_capacity(days: Optional[int] = Query(7)):
    """
    Read resolved capacity (MCH_MACHINE_AVAILABILITY merged with daily overrides).

    Query parameters:
      - days: how many days ahead to return (default: 7)

    Returns:
      - List of capacity slots: (machine, shift, date, available_mins, is_open)
      - Merged from baseline + MCH_MACHINE_AVAILABILITY_BY_DATE
    """
    if days < 1 or days > 90:
        raise HTTPException(status_code=400, detail="days must be 1-90")

    try:
        from datetime import timedelta
        from preprocess import resolve_capacity

        today = date.today()
        machine_master_df = read_machine_master()
        machine_daily_df = read_machine_daily()

        slots_list = []
        for i in range(days):
            target_date = today + timedelta(days=i)
            resolved = resolve_capacity(machine_master_df, machine_daily_df, target_date)

            for _, row in resolved.iterrows():
                slots_list.append({
                    "machine": row["WORK_CENTER"],
                    "shift": row["SHIFT"],
                    "date": str(target_date),
                    "available_mins": float(row["AVAILABLE_MINS"]),
                    "is_open": row["AVAILABLE_MINS"] > 0,
                })

        return {"capacity_slots": slots_list, "count": len(slots_list)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read capacity: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Data Access: Machine Daily Overrides (read-only)
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/machines/daily", tags=["Data"])
def get_machines_daily(start_date: Optional[date] = Query(None), end_date: Optional[date] = Query(None)):
    """
    Read machine capacity overrides (MCH_MACHINE_AVAILABILITY_BY_DATE).

    Query parameters:
      - start_date: filter from date (default: today)
      - end_date: filter to date (default: today + 30 days)

    Returns:
      - List of daily overrides: (machine, date, shift, available_mins)
      - Read-only; set via ERP, not this application

    Note: There is NO PUT endpoint — this view is ERP-prepared and read-only.
    """
    if start_date is None:
        start_date = date.today()
    if end_date is None:
        from datetime import timedelta
        end_date = start_date + timedelta(days=30)

    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date must be ≤ end_date")

    try:
        machine_daily_df = read_machine_daily()

        if machine_daily_df.empty:
            return {"daily_overrides": [], "count": 0}

        # Filter to date range
        daily_df = machine_daily_df[
            (machine_daily_df["WORKING_DATE"] >= start_date)
            & (machine_daily_df["WORKING_DATE"] <= end_date)
        ]

        overrides_list = [
            {
                "machine": row["WORK_CENTER"],
                "date": str(row["WORKING_DATE"]),
                "shift": row["SHIFT"],
                "available_mins": float(row["AVAILABLE_MINS"]),
            }
            for _, row in daily_df.iterrows()
        ]

        return {"daily_overrides": overrides_list, "count": len(overrides_list)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read daily: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Data Refresh
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/data/refresh", tags=["Admin"])
def refresh_all_data():
    """
    Re-fetch all 7 read-only sources from Oracle (cache-busting).

    Reads:
      - MCH_WIP
      - MCH_MACHINE_AVAILABILITY
      - MCH_MACHINE_AVAILABILITY_BY_DATE
      - MCH_MACHINE_PRIORITY
      - MCH_ITEMWISE_FIXTURE_LOCATOR
      - MCH_FIXTURE_LOCATOR
      - L750.TCCCP019 (holiday calendar)

    Returns:
      - Count of rows per source
      - Timestamp of refresh
    """
    try:
        wip = read_wip_orders()
        machines = read_machine_master()
        daily = read_machine_daily()
        routing = read_routing_master()
        fixture_master = read_fixture_locator_master()
        fixture_inventory = read_fixture_locator_inventory()
        holidays = read_holiday_calendar()

        return {
            "wip_orders": len(wip),
            "machine_master": len(machines),
            "machine_daily": len(daily),
            "routing_master": len(routing),
            "fixture_locator_master": len(fixture_master),
            "fixture_locator_inventory": len(fixture_inventory),
            "holiday_calendar": len(holidays),
            "refreshed_at": datetime.now().isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Refresh failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Error Handlers
# ─────────────────────────────────────────────────────────────────────────────
@app.exception_handler(Exception)
async def generic_exception_handler(request, exc):
    """Catch-all error handler. Must return a Response — a bare dict is not callable by Starlette."""
    return JSONResponse(
        status_code=500,
        content={
            "error": str(exc),
            "type": type(exc).__name__,
            "timestamp": datetime.now().isoformat(),
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
