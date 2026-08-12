"""
engine2_recommender.py — Engine 2: Priority Elevation Simulator.

Takes a baseline schedule, elevates one or more orders, re-solves, and produces
a risk report showing impact on other orders' delivery dates.

Process (CLAUDE.md Engine 2 steps):
  1. Read baseline schedule (MCH_SCHEDULE_OUTPUT) → old_completion_dates
  2. Elevate order(s): set urgency_weight = ELEVATED_URGENCY_WEIGHT (999_999)
  3. Re-run Engine 1 CP-SAT with max_time_in_seconds (config.engine2_time_limit_seconds)
  4. Compute slip_days and risk_flag for every OTHER order
  5. Assemble and return RiskReport
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from engine1_scheduler import run_engine1
from models import (
    Config,
    ELEVATED_URGENCY_WEIGHT,
    SchedulableTask,
    SchedulerInput,
    SchedulerResult,
    SolveStatus,
)
from risk_classifier import build_risk_report


def simulate_priority_elevation(
    elevated_order_ids: list[str],
    baseline_scheduler_input: SchedulerInput,
    baseline_completion_dates: dict[str, date],
    config: Config,
    today: date,
) -> tuple[SchedulerResult, dict[str, Optional[date]]]:
    """
    Re-solve Engine 1 with elevated urgency for specified orders.

    Process:
      1. Clone the baseline SchedulerInput
      2. For each elevated order: set urgency_weight = ELEVATED_URGENCY_WEIGHT on ALL its tasks
      3. Re-run Engine 1 with time limit (config.engine2_time_limit_seconds)
      4. Extract new completion dates

    Args:
        elevated_order_ids: list of PRODUCTION_ORDER IDs to elevate (e.g., ["ORD001", "ORD002"]).
        baseline_scheduler_input: the original SchedulerInput from the baseline schedule.
        baseline_completion_dates: dict[order_id] → completion_date from baseline (used later for diffing).
        config: runtime config (provides engine2_time_limit_seconds).
        today: reference date (for logging/context, not used in solve).

    Returns:
        (SchedulerResult, dict[order_id] → new_completion_date)
        where SchedulerResult.status indicates solve success (FEASIBLE/OPTIMAL).
    """
    elevated_set = set(elevated_order_ids)

    # Clone the input and mutate: elevate selected orders' urgency_weight.
    print(f"\n[Engine 2] Elevating {len(elevated_order_ids)} order(s):", ", ".join(sorted(elevated_set)))

    elevated_input = SchedulerInput(
        tasks=[
            _elevate_task(task, elevated_set)
            for task in baseline_scheduler_input.tasks
        ],
        capacity=baseline_scheduler_input.capacity,
        horizon_dates=baseline_scheduler_input.horizon_dates,
        config=baseline_scheduler_input.config,
    )

    # Re-solve with time limit.
    time_limit = config.engine2_time_limit_seconds
    print(f"[Engine 2] Re-solving with {time_limit}s time limit...", end=" ")
    result = run_engine1(elevated_input, max_time_in_seconds=time_limit)
    print(f"{result.status.value}")

    return result, result.completion_dates


def _elevate_task(task: SchedulableTask, elevated_order_ids: set[str]) -> SchedulableTask:
    """
    Clone a task and elevate its urgency_weight if its order is in the elevated set.

    The elevated weight (ELEVATED_URGENCY_WEIGHT = 999_999) is so large that
    CP-SAT floats the order to the top of the scheduling queue.
    """
    if task.production_order in elevated_order_ids:
        # Mutate a copy: elevation only; all other fields unchanged.
        return SchedulableTask(
            production_order=task.production_order,
            operation_no=task.operation_no,
            operation=task.operation,
            item_category=task.item_category,
            balance_qty=task.balance_qty,
            cycle_time=task.cycle_time,
            cdd=task.cdd,
            order_date=task.order_date,
            urgency_weight=ELEVATED_URGENCY_WEIGHT,  # ← The only change
            candidates=task.candidates,
        )
    return task


def build_cdd_map(baseline_scheduler_input: SchedulerInput) -> dict[str, Optional[date]]:
    """
    Extract CDD for each order from the SchedulerInput tasks.
    Assumes all tasks of the same order have the same CDD (true by construction).
    """
    cdd_map: dict[str, Optional[date]] = {}
    for task in baseline_scheduler_input.tasks:
        if task.production_order not in cdd_map:
            cdd_map[task.production_order] = task.cdd
    return cdd_map
