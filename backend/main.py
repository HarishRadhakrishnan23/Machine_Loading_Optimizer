"""
main.py — FastAPI Backend for TOV Machine Loading Optimizer.

Exposes 12 REST endpoints orchestrating Engines 1 & 2 and ERP data access.

Endpoints:
  POST   /schedule/generate          Engine 1: generate schedule
  GET    /schedule/current           Read latest MCH_SCHEDULE_OUTPUT
  GET    /schedule/export            Download current MCH_SCHEDULE_OUTPUT as .xlsx
  GET    /schedule/archive/export    Download one archived RUN_ID's rows as .xlsx
  POST   /schedule/freeze            Archive current MCH_SCHEDULE_OUTPUT into MCH_SCHEDULE_OUTPUT_ARCHIVE
  POST   /priority/simulate          Engine 2: simulate elevation
  GET    /orders/wip                 Read active WIP orders
  GET    /machines/capacity          Read resolved capacity for horizon
  POST   /data/refresh               Re-fetch all ERP views
  GET    /machines/daily             Read MCH_MACHINE_AVAILABILITY_BY_DATE
  GET    /config                     Read config.json
  PUT    /config                     Update config.json
"""

import io
import json
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from db import (
    archive_schedule_output,
    read_archived_run_completions,
    read_live_schedule_completions,
    read_archived_runs,
    read_completed_materials,
    read_wip_orders,
    read_machine_master,
    read_machine_daily,
    read_machine_fnl_master,
    read_fixture_locator_inventory,
    read_holiday_calendar,
)
from models import (
    ActualCompareResponse,
    ActualOrderStatus,
    ArchivedRunsResponse,
    ArchivedRunSummary,
    CompletedMaterialsResponse,
    Config,
    FnlAddMachineRequest,
    FnlPriorityUpdateRequest,
    FreezeScheduleResponse,
    RunCompareOrder,
    RunCompareOrderDiff,
    RunCompareResponse,
    ScheduleGenerateResponse,
)
import fnl_master_admin
from engine2.api import router as engine2_router
from model_f_pipeline import generate_schedule as run_model_e_schedule

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

# Engine 2 (independent what-if engine) — its only touch point in this file
app.include_router(engine2_router)

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
    """Read current runtime configuration (backend/config.json — see CLAUDE.md "Configuration")."""
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


@app.get("/schedule/summary", tags=["Engine 1"])
def schedule_summary():
    """
    Order/quantity funnel for the Pending Load Queue page, from current MCH_WIP vs the current
    MCH_SCHEDULE_OUTPUT (read-only). Quantities: WIP = pieces ordered; Scheduled = pieces still to go
    through the final operation; Unscheduled = WIP minus Scheduled, split into pieces already completed
    (QAINSP Completed), pieces of orders waiting on QA inspection (final-op balance) and the rest
    (rejected pieces, rework / non-machine leftovers) so the three parts always add up.
    """
    from db import get_connection, read_wip_orders

    try:
        w = read_wip_orders()
        w["BAL"] = w["QUANTITY_ORDERED"] - w["QUANTITY_COMPLETED"].fillna(0) - w["QUANTITY_REJECTED"].fillna(0)
        w = w.sort_values(["PRODUCTION_ORDER", "OPERATION"])
        last = w.groupby("PRODUCTION_ORDER").last()
        with get_connection() as conn:
            o = pd.read_sql("SELECT DISTINCT PRODUCTION_ORDER FROM MCH_SCHEDULE_OUTPUT WHERE WORK_CENTER IS NOT NULL", conn)
        o.columns = [c.upper() for c in o.columns]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to build summary: {e}")

    all_orders = set(last.index)
    open_orders = set(w[w["BAL"] > 0]["PRODUCTION_ORDER"])
    sched = set(o["PRODUCTION_ORDER"]) & all_orders
    finished = all_orders - open_orders
    open_ops = w[w["BAL"] > 0]
    qa_open = set(open_ops[open_ops["WORK_CENTER"].astype(str).str.contains("QAINSP")]["PRODUCTION_ORDER"])
    qa_wait = (open_orders - sched) & qa_open
    rest = all_orders - sched - finished - qa_wait

    wip_qty = int(last["QUANTITY_ORDERED"].sum())
    sched_qty = int(last.loc[list(sched), "BAL"].clip(lower=0).sum())
    uns_qty = wip_qty - sched_qty
    done_qty = int(last["QUANTITY_COMPLETED"].fillna(0).sum())  # pieces already through the final operation
    qa_qty = int(last.loc[list(qa_wait), "BAL"].clip(lower=0).sum()) if qa_wait else 0
    return {
        "wip": {"orders": len(all_orders), "qty": wip_qty},
        "scheduled": {"orders": len(sched), "qty": sched_qty},
        "unscheduled": {"orders": len(all_orders) - len(sched), "qty": uns_qty},
        "qa_completed": {"orders": len(finished), "qty": done_qty},
        "qa_not_completed": {"orders": len(qa_wait), "qty": qa_qty},
        "rest": {"orders": len(rest), "qty": uns_qty - done_qty - qa_qty},
    }


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
                SELECT o.RUN_ID, o.PRODUCTION_ORDER, o.OPERATION_NO, o.LINE_NO, o.TASK, o.WORK_CENTER,
                       o.SHIFT, o.SCHEDULED_DATE, o.BALANCE_QTY, o.BATCH_KEY, o.IS_SAFETY_STOCK,
                       o.FIXTURE_ID, o.LOCATOR_ID, o.START_TIMESTAMP, o.END_TIMESTAMP, o.REMARK,
                       o.ORDER_COMPLETION_DATE, o.ORDER_COMPLETION_SHIFT, o.GENERATED_AT,
                       w.CDD
                FROM MCH_SCHEDULE_OUTPUT o
                LEFT JOIN (SELECT PRODUCTION_ORDER, MAX(CDD) AS CDD FROM MCH_WIP GROUP BY PRODUCTION_ORDER) w
                       ON w.PRODUCTION_ORDER = o.PRODUCTION_ORDER
                ORDER BY o.PRODUCTION_ORDER, o.OPERATION_NO, o.LINE_NO
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
                "cdd": _d(row["CDD"]),  # from MCH_WIP (read-only); None for safety stock
                "order_completion_date": _d(row["ORDER_COMPLETION_DATE"]),
                "order_completion_shift": row["ORDER_COMPLETION_SHIFT"] if pd.notna(row["ORDER_COMPLETION_SHIFT"]) else None,
                "generated_at": _ts(row["GENERATED_AT"]),
            }
            for _, row in df.iterrows()
        ]
        return {"assignments": assignments, "count": len(assignments)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read schedule: {e}")


_SCHEDULE_COLUMN_RENAME = {
    "PRODUCTION_ORDER": "Production Order", "OPERATION_NO": "Operation No", "LINE_NO": "Line No",
    "TASK": "Task", "WORK_CENTER": "Machine", "SHIFT": "Shift", "SCHEDULED_DATE": "Scheduled Date",
    "BALANCE_QTY": "Balance Qty", "BATCH_KEY": "Batch Key", "IS_SAFETY_STOCK": "Safety Stock",
    "FIXTURE_ID": "Fixture", "LOCATOR_ID": "Locator", "START_TIMESTAMP": "Start Timestamp",
    "END_TIMESTAMP": "End Timestamp", "REMARK": "Remark", "ORDER_COMPLETION_DATE": "Order Completion Date",
    "ORDER_COMPLETION_SHIFT": "Order Completion Shift", "RUN_ID": "Run ID", "GENERATED_AT": "Generated At",
}


def _schedule_rows_to_xlsx_response(df: pd.DataFrame, sheet_name: str, filename: str) -> StreamingResponse:
    """
    Shared MCH_SCHEDULE_OUTPUT-shaped DataFrame -> downloadable .xlsx builder,
    used by both /schedule/export (current live table) and
    /schedule/archive/export (one frozen RUN_ID from the archive table) —
    same 19-column shape in both tables, so one renderer serves both.
    """
    df = df.rename(columns=_SCHEDULE_COLUMN_RENAME)
    for col in ("Scheduled Date", "Order Completion Date"):
        df[col] = pd.to_datetime(df[col]).dt.date
    for col in ("Start Timestamp", "End Timestamp", "Generated At"):
        df[col] = pd.to_datetime(df[col]).dt.tz_localize(None)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=sheet_name, index=False)
        worksheet = writer.sheets[sheet_name]
        for i, col in enumerate(df.columns, start=1):
            width = max(12, min(30, int(df[col].astype(str).str.len().max() or 10) + 2))
            worksheet.column_dimensions[worksheet.cell(row=1, column=i).column_letter].width = width
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/schedule/export", tags=["Engine 1"])
def export_current_schedule():
    """
    Exports the ENTIRE current MCH_SCHEDULE_OUTPUT (no row cap, unlike
    /schedule/current's 5000-row display limit) as a downloadable .xlsx
    workbook — the Overview page's "Export to Excel" button.
    """
    from db import get_connection

    try:
        with get_connection() as conn:
            df = pd.read_sql(
                """
                SELECT PRODUCTION_ORDER, OPERATION_NO, LINE_NO, TASK, WORK_CENTER,
                       SHIFT, SCHEDULED_DATE, BALANCE_QTY, BATCH_KEY, IS_SAFETY_STOCK,
                       FIXTURE_ID, LOCATOR_ID, START_TIMESTAMP, END_TIMESTAMP, REMARK,
                       ORDER_COMPLETION_DATE, ORDER_COMPLETION_SHIFT, RUN_ID, GENERATED_AT
                FROM MCH_SCHEDULE_OUTPUT
                ORDER BY PRODUCTION_ORDER, OPERATION_NO
                """,
                conn,
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read schedule for export: {e}")

    if df.empty:
        raise HTTPException(status_code=404, detail="No schedule generated yet — nothing to export")

    filename = f"schedule_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return _schedule_rows_to_xlsx_response(df, "MCH_SCHEDULE_OUTPUT", filename)


@app.get("/schedule/archive/export", tags=["Engine 1"])
def export_archived_run(run_id: str = Query(...)):
    """
    Download Archive page — scans MCH_SCHEDULE_OUTPUT_ARCHIVE for every row
    matching the given RUN_ID and returns them as a downloadable .xlsx
    workbook. Distinct from /schedule/export: this reads the append-only
    archive table, filtered to one frozen run, never the live table.
    """
    from db import get_connection

    try:
        with get_connection() as conn:
            df = pd.read_sql(
                """
                SELECT PRODUCTION_ORDER, OPERATION_NO, LINE_NO, TASK, WORK_CENTER,
                       SHIFT, SCHEDULED_DATE, BALANCE_QTY, BATCH_KEY, IS_SAFETY_STOCK,
                       FIXTURE_ID, LOCATOR_ID, START_TIMESTAMP, END_TIMESTAMP, REMARK,
                       ORDER_COMPLETION_DATE, ORDER_COMPLETION_SHIFT, RUN_ID, GENERATED_AT
                FROM MCH_SCHEDULE_OUTPUT_ARCHIVE
                WHERE RUN_ID = :run_id
                ORDER BY PRODUCTION_ORDER, OPERATION_NO
                """,
                conn,
                params={"run_id": run_id},
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read archived run for export: {e}")

    if df.empty:
        raise HTTPException(status_code=404, detail=f"No archived rows found for RUN_ID {run_id!r}")

    filename = f"archive_{run_id[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return _schedule_rows_to_xlsx_response(df, "MCH_SCHEDULE_OUTPUT_ARCHIVE", filename)


@app.get("/materials/completed", tags=["Engine 1"], response_model=CompletedMaterialsResponse)
def get_completed_materials(start: date = Query(...), end: date = Query(...), run_id: Optional[str] = Query(None)):
    """
    Planned Completed Materials page — "how many materials completed
    between these two dates" (your manager's ask). `run_id` omitted (or
    "current") reads LIVE MCH_SCHEDULE_OUTPUT; a real RUN_ID instead reads
    that one frozen run from MCH_SCHEDULE_OUTPUT_ARCHIVE — the page's
    "check a particular schedule" picker.
    """
    effective_run_id = None if run_id in (None, "", "current") else run_id
    try:
        order_count, total_quantity = read_completed_materials(start, end, run_id=effective_run_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to compute completed materials: {e}")
    return CompletedMaterialsResponse(start=start, end=end, order_count=order_count, total_quantity=total_quantity)


@app.get("/schedule/archive/runs", tags=["Engine 1"], response_model=ArchivedRunsResponse)
def get_archived_runs():
    """Run Comparison page's run picker — every RUN_ID ever frozen via /schedule/freeze, newest first."""
    try:
        df = read_archived_runs()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list archived runs: {e}")
    runs = [
        ArchivedRunSummary(run_id=row["RUN_ID"], generated_at=row["GENERATED_AT"], row_count=int(row["ROW_COUNT"]))
        for _, row in df.iterrows()
    ]
    return ArchivedRunsResponse(runs=runs)


def _diff_completions(df_a: pd.DataFrame, df_b: pd.DataFrame, run_a: str, run_b: str) -> RunCompareResponse:
    """
    Shared diff core for both /schedule/compare/runs (archived vs. archived)
    and /schedule/compare/runs/to-current (archived vs. the live table) —
    same 2-column (PRODUCTION_ORDER, ORDER_COMPLETION_DATE) shape either way.
    """
    a_by_order = {row["PRODUCTION_ORDER"]: row["ORDER_COMPLETION_DATE"] for _, row in df_a.iterrows()}
    b_by_order = {row["PRODUCTION_ORDER"]: row["ORDER_COMPLETION_DATE"] for _, row in df_b.iterrows()}

    def _d(v):
        return v.date() if pd.notna(v) else None

    orders_a, orders_b = set(a_by_order), set(b_by_order)
    removed = [RunCompareOrder(production_order=o, completion_date=_d(a_by_order[o])) for o in sorted(orders_a - orders_b)]
    added = [RunCompareOrder(production_order=o, completion_date=_d(b_by_order[o])) for o in sorted(orders_b - orders_a)]

    cdd_by_order = {}
    try:
        from db import get_connection

        with get_connection() as conn:
            cdd_df = pd.read_sql("SELECT PRODUCTION_ORDER, MAX(CDD) AS CDD FROM MCH_WIP GROUP BY PRODUCTION_ORDER", conn)
        cdd_df.columns = [c.upper() for c in cdd_df.columns]
        cdd_by_order = {r["PRODUCTION_ORDER"]: _d(r["CDD"]) for _, r in cdd_df.iterrows()}
    except Exception:
        pass  # CDD is a display extra - never fail the comparison over it

    changed = []
    unchanged_count = 0
    for order_id in sorted(orders_a & orders_b):
        old_d, new_d = _d(a_by_order[order_id]), _d(b_by_order[order_id])
        if old_d == new_d:
            unchanged_count += 1
        else:
            delta = (new_d - old_d).days if (old_d is not None and new_d is not None) else None
            changed.append(RunCompareOrderDiff(
                production_order=order_id, old_completion_date=old_d, new_completion_date=new_d, delta_days=delta,
                cdd=cdd_by_order.get(order_id),
            ))

    return RunCompareResponse(
        run_a=run_a, run_b=run_b, added=added, removed=removed, changed=changed, unchanged_count=unchanged_count,
    )


@app.get("/schedule/compare/runs", tags=["Engine 1"], response_model=RunCompareResponse)
def compare_archived_runs(run_a: str = Query(...), run_b: str = Query(...)):
    """
    Run Comparison page — "plan vs. plan": diffs two frozen
    MCH_SCHEDULE_OUTPUT_ARCHIVE snapshots by PRODUCTION_ORDER's
    ORDER_COMPLETION_DATE (e.g. the Oct 1 run vs. the Oct 3 run, after a few
    new orders landed in MCH_WIP in between).
    """
    try:
        df_a = read_archived_run_completions(run_a)
        df_b = read_archived_run_completions(run_b)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read archived runs: {e}")
    return _diff_completions(df_a, df_b, run_a, run_b)


@app.get("/schedule/compare/runs/to-current", tags=["Engine 1"], response_model=RunCompareResponse)
def compare_archived_run_to_current(run_a: str = Query(...)):
    """
    Run Comparison page — Run B = "Current Schedule (live)": diffs one
    archived RUN_ID against the LIVE MCH_SCHEDULE_OUTPUT (not the archive),
    using the exact same completion-date diff as plan-vs-plan — just with
    one side read from the live table instead of a second frozen run.
    """
    try:
        df_a = read_archived_run_completions(run_a)
        df_b = read_live_schedule_completions()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to compare run to current schedule: {e}")
    return _diff_completions(df_a, df_b, run_a, "current")


@app.get("/schedule/compare/actual", tags=["Engine 1"], response_model=ActualCompareResponse)
def compare_archived_run_to_actual(run_id: str = Query(...)):
    """
    Run Comparison page — "plan vs. actual": an archived RUN_ID's planned
    completions against what TODAY's live MCH_WIP actually shows. An order
    from the archived run no longer present in live MCH_WIP is treated as
    completed; one still present reports its current OPERATION/balance as
    real-world progress (CLAUDE.md Run Comparison note — your own call on
    how to read Oct 1's plan against Oct 30's reality).
    """
    try:
        planned_df = read_archived_run_completions(run_id)
        wip_df = read_wip_orders()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to compare run to actual: {e}")

    planned_orders = {row["PRODUCTION_ORDER"]: row["ORDER_COMPLETION_DATE"] for _, row in planned_df.iterrows()}
    live_orders = set(wip_df["PRODUCTION_ORDER"]) if not wip_df.empty else set()

    orders = []
    completed_count = in_progress_count = 0
    for order_id, planned_date in planned_orders.items():
        planned_d = planned_date.date() if pd.notna(planned_date) else None
        if order_id not in live_orders:
            completed_count += 1
            orders.append(ActualOrderStatus(
                production_order=order_id, planned_completion_date=planned_d, status="completed",
            ))
        else:
            in_progress_count += 1
            order_rows = wip_df[wip_df["PRODUCTION_ORDER"] == order_id].sort_values("OPERATION")
            balance = order_rows["QUANTITY_ORDERED"] - order_rows["QUANTITY_COMPLETED"] - order_rows["QUANTITY_REJECTED"].fillna(0)
            pending = order_rows[balance > 0]
            current_row = pending.iloc[0] if not pending.empty else order_rows.iloc[-1]
            current_balance = balance.loc[current_row.name]
            orders.append(ActualOrderStatus(
                production_order=order_id, planned_completion_date=planned_d, status="in_progress",
                current_operation_no=float(current_row["OPERATION"]),
                current_task=current_row["TASK"],
                current_balance_qty=int(current_balance),
            ))

    return ActualCompareResponse(
        run_id=run_id, as_of=date.today(), completed_count=completed_count,
        in_progress_count=in_progress_count, orders=orders,
    )


@app.post("/schedule/freeze", tags=["Engine 1"], response_model=FreezeScheduleResponse)
def freeze_schedule():
    """
    "Freeze schedule" — copies whatever is currently in MCH_SCHEDULE_OUTPUT,
    as-is, into MCH_SCHEDULE_OUTPUT_ARCHIVE for historical backup and
    representation. Triggered by the frontend's "Freeze schedule" button.

    Does NOT touch MCH_SCHEDULE_OUTPUT itself (read-only copy) and does NOT
    re-run Engine 1 — it snapshots the most recent /schedule/generate output.
    The archive is append-only: nothing already archived is ever deleted by
    this application; re-freezing the same RUN_ID just replaces that RUN_ID's
    own archived rows (idempotent), never anyone else's.

    Returns:
      - run_id: the RUN_ID that was archived (None if MCH_SCHEDULE_OUTPUT was empty)
      - rows_archived: number of rows copied
    """
    try:
        run_id, rows_archived = archive_schedule_output()
    except Exception as e:
        if "ORA-00001" in str(e) and "ARCHIVE" in str(e):
            # The archive's primary key lacks RUN_ID, so only the first frozen run fits (see archive_pk_fix.sql).
            raise HTTPException(
                status_code=409,
                detail=(
                    "Freeze schedule failed: MCH_SCHEDULE_OUTPUT_ARCHIVE's primary key does not include RUN_ID, so a "
                    "second run cannot be stored. Run backend/testing/archive_pk_fix.sql once in Oracle, then freeze again."
                ),
            )
        raise HTTPException(status_code=500, detail=f"Freeze schedule failed: {e}")
    return FreezeScheduleResponse(run_id=run_id, rows_archived=rows_archived)


# ─────────────────────────────────────────────────────────────────────────────
# Data Access: Machine Capacity
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/machines/capacity", tags=["Data"])
def get_machines_capacity(days: Optional[int] = Query(7)):
    """
    Resolved machine availability for the next N days — the SAME availability the scheduler uses
    (model_f_data.ResolvedAvailability): holiday calendar first (closes every machine/shift),
    then the MCH_MACHINE_AVAILABILITY_BY_DATE override, then the MCH_MACHINE_AVAILABILITY baseline.

    Query parameters:
      - days: how many days ahead to return (default: 7)

    Returns:
      - List of capacity slots: (machine, shift, date, available_mins, is_open)
    """
    if days < 1 or days > 90:
        raise HTTPException(status_code=400, detail="days must be 1-90")

    try:
        from datetime import timedelta
        from model_f_data import build_availability

        today = date.today()
        availability = build_availability(read_machine_master(), read_machine_daily(), read_holiday_calendar())

        slots_list = []
        for i in range(days):
            target_date = today + timedelta(days=i)
            for machine, shift in availability.baseline:
                mins = float(availability(machine, target_date, shift))
                slots_list.append({
                    "machine": machine,
                    "shift": shift,
                    "date": str(target_date),
                    "available_mins": mins,
                    "is_open": mins > 0,
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
    Re-fetch every read-only source the scheduler uses from Oracle (cache-busting) and report row counts.

    Reads:
      - MCH_WIP
      - MCH_MACHINE_AVAILABILITY
      - MCH_MACHINE_AVAILABILITY_BY_DATE
      - MCH_MACHINE_FNL_MASTER (unified routing + fixture/locator master, CLAUDE.md §E.15)
      - MCH_FIXTURE_LOCATOR (physical inventory)
      - L750.TCCCP019 (holiday calendar)

    Returns:
      - Count of rows per source
      - Timestamp of refresh
    """
    try:
        wip = read_wip_orders()
        machines = read_machine_master()
        daily = read_machine_daily()
        fnl_master = read_machine_fnl_master()
        fixture_inventory = read_fixture_locator_inventory()
        holidays = read_holiday_calendar()

        return {
            "wip_orders": len(wip),
            "machine_master": len(machines),
            "machine_daily": len(daily),
            "machine_fnl_master": len(fnl_master),
            "fixture_locator_inventory": len(fixture_inventory),
            "holiday_calendar": len(holidays),
            "refreshed_at": datetime.now().isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Refresh failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Priority Shuffler — the one sanctioned write path into MCH_MACHINE_FNL_MASTER
# (PRIORITY re-rank + add-machine only; see fnl_master_admin.py / CLAUDE.md).
# Changes apply to the NEXT /schedule/generate, not to the schedule on screen.
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/fnl/master", tags=["Priority Shuffler"])
def get_fnl_master():
    """Every MCH_MACHINE_FNL_MASTER row (key + priority + fixture/locator/timings) plus the machines that may be added."""
    try:
        return {"rows": fnl_master_admin.read_master_for_ui(), "machines": fnl_master_admin.known_machines()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read MCH_MACHINE_FNL_MASTER: {e}")


@app.put("/fnl/priorities", tags=["Priority Shuffler"])
def put_fnl_priorities(req: FnlPriorityUpdateRequest):
    """Re-rank PRIORITY for one combo+task. Atomic and audited."""
    try:
        return fnl_master_admin.update_priorities(
            req.size_inch, req.class_val, req.moc, req.design, req.task,
            [i.model_dump() for i in req.items], req.changed_by,
        )
    except fnl_master_admin.FnlAdminError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Priority update failed: {e}")


@app.post("/fnl/machine", tags=["Priority Shuffler"])
def post_fnl_machine(req: FnlAddMachineRequest):
    """Add a capable machine to an existing combo+task at a chosen priority. Atomic and audited."""
    try:
        return fnl_master_admin.add_machine(
            req.size_inch, req.class_val, req.moc, req.design, req.task,
            req.work_center, req.priority,
            req.fixture_change_time, req.locator_change_time, req.load_unload_time, req.changed_by,
        )
    except fnl_master_admin.FnlAdminError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Add machine failed: {e}")


@app.get("/fnl/audit", tags=["Priority Shuffler"])
def get_fnl_audit(limit: int = Query(200, ge=1, le=2000)):
    try:
        return {"entries": fnl_master_admin.read_audit(limit)}
    except fnl_master_admin.FnlAdminError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read audit log: {e}")


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
