"""
pipeline.py — Top-level orchestration for Engine 1: data fetch → preprocess → solve → persist.

This is the glue that ties together:
    db.py (read views) → preprocess.py (build SchedulerInput) → engine1_scheduler.py (solve)
    → db.py (write results)

Used by FastAPI endpoint POST /schedule/generate and by engine2_recommender for priority simulations.
"""

from datetime import date, datetime
from typing import Optional

from db import (
    read_wip_orders,
    read_machine_master,
    read_machine_daily,
    read_routing_master,
    write_schedule_output,
)
from engine1_scheduler import run_engine1
from models import Config, ScheduleOutputRow, SchedulerResult
from preprocess import build_scheduler_input, compute_horizon, filter_wip_orders


def schedule_all_orders(
    config: Config,
    today: Optional[date] = None,
    max_time_in_seconds: Optional[float] = None,
) -> SchedulerResult:
    """
    Full scheduling pipeline: fetch ERP views → preprocess → solve → persist.

    Args:
        config: runtime config (batch_bonus_months, horizon sizing, time limits, etc.)
        today: reference date for urgency scoring (default: today; injected for testability)
        max_time_in_seconds: CP-SAT time limit (used by Engine 2; None = solve to optimality)

    Returns:
        SchedulerResult with status, assignments (list of ScheduleOutputRow), and
        completion_dates (dict[PRODUCTION_ORDER] → completion_date).

    Writes MCH_SCHEDULE_OUTPUT on success (FEASIBLE or OPTIMAL status).
    """
    if today is None:
        today = date.today()

    print(f"\n{'='*70}")
    print(f"ENGINE 1 — SCHEDULING OPTIMIZER")
    print(f"{'='*70}")
    print(f"Run date: {today}")
    print(f"Config: batch_bonus_months={config.batch_bonus_months}, "
          f"horizon_safety={config.scheduling_horizon_safety_factor}, "
          f"horizon_buffer={config.scheduling_horizon_buffer_days} days")

    # Step 1: Fetch all 4 ERP views
    print(f"\n[1/4] Fetching ERP views from Oracle...", end=" ")
    wip_df = read_wip_orders()
    machine_master_df = read_machine_master()
    machine_daily_df = read_machine_daily()
    routing_df = read_routing_master()
    print(f"✓")
    print(f"      WIP: {len(wip_df)} rows | Machines: {machine_master_df['WORK_CENTER'].nunique()} | "
          f"Daily: {len(machine_daily_df)} | Routing: {len(routing_df)} rows")

    # Step 2: Preprocess → SchedulerInput
    print(f"[2/4] Preprocessing (CT=0, QA, routable, balance>0)...", end=" ")
    filtered_wip = filter_wip_orders(wip_df, routing_df)
    horizon_dates = compute_horizon(filtered_wip, machine_master_df, config, today)
    scheduler_input = build_scheduler_input(
        wip_df, machine_master_df, machine_daily_df, routing_df,
        horizon_dates=horizon_dates,
        config=config,
        today=today,
    )
    print(f"✓")
    print(f"      Tasks: {len(scheduler_input.tasks)} | Horizon: {len(scheduler_input.horizon_dates)} days "
          f"({scheduler_input.horizon_dates[0]} … {scheduler_input.horizon_dates[-1]})")

    # Step 3: CP-SAT solve
    print(f"[3/4] Building & solving CP-SAT model...", end=" ")
    result = run_engine1(scheduler_input, max_time_in_seconds=max_time_in_seconds)
    print(f"✓")
    print(f"      Status: {result.status.value}")
    if result.objective_value is not None:
        print(f"      Objective: {result.objective_value:.1f}")

    # Step 4: Persist results
    if result.is_success:
        print(f"[4/4] Writing MCH_SCHEDULE_OUTPUT ({len(result.assignments)} rows)...", end=" ")
        _persist_schedule_output(result.assignments, result.run_id)
        print(f"✓")
        print(f"\n{'='*70}")
        print(f"✓ SCHEDULING COMPLETE")
        print(f"  Run ID: {result.run_id}")
        print(f"  Assignments: {len(result.assignments)} rows across {len(result.completion_dates)} orders")
        print(f"  {sum(1 for o in result.completion_dates.values() if o <= today)} orders by today, "
              f"{sum(1 for o in result.completion_dates.values() if o > today)} future")
        print(f"{'='*70}\n")
    else:
        print(f"[4/4] Solve was {result.status.value}; skipping write.")
        print(f"\n{'='*70}")
        print(f"✗ SCHEDULING FAILED: {result.status.value}")
        print(f"{'='*70}\n")

    return result


def _persist_schedule_output(assignments: list[ScheduleOutputRow], run_id: str) -> None:
    """
    Write ScheduleOutputRow list to MCH_SCHEDULE_OUTPUT.

    Args:
        assignments: list of ScheduleOutputRow (from engine1_scheduler.py).
        run_id: unique identifier for this scheduling run (e.g., UUID hex).
    """
    # Convert Pydantic model to dict for db.write_schedule_output
    rows_dicts = [
        {
            "PRODUCTION_ORDER": row.production_order,
            "OPERATION_NO": row.operation_no,
            "TASK": None,  # nullable; could populate from task lookup if needed
            "WORK_CENTER": row.machine_name,
            "SHIFT": row.shift.value,  # Shift enum → string
            "SCHEDULED_DATE": row.scheduled_date,
            "BALANCE_QTY": row.balance_qty,
            "START_OFFSET_MIN": row.start_offset_min,
            "END_OFFSET_MIN": row.end_offset_min,
            "generated_at": row.generated_at,
        }
        for row in assignments
    ]
    write_schedule_output(rows_dicts, run_id)
