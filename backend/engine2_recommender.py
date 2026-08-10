"""
engine2_recommender.py — Engine 2: Recommendation / Impact Simulator.

Answers the planner's "what if I push these order(s) to the front?" question.
It reuses the Engine 1 CP-SAT foundation verbatim: rather than mutating a built
solver model (not supported by OR-Tools), it rebuilds the SchedulerInput with the
elevated orders' urgency_weight forced to ELEVATED_URGENCY_WEIGHT (999999) and
re-runs run_engine1() under a short time limit. Elevating the weight IS the
priority mechanism specified in CLAUDE.md — the solver then floats those orders
to the top of the queue while still weighing everyone else's tardiness.

Flow (CLAUDE.md Engine 2):
    1. Baseline snapshot        → old_completion_date per order (from Engine 1)
    2. Elevate selected orders  → urgency_weight = 999999
    3. Re-run Engine 1          → max_time_in_seconds = config.engine2_time_limit_seconds
    4. Diff completion dates    → slip_days, slack   (risk_classifier.py)
    5. Classify SAFE/AT_RISK/BREACH
    6. Write sim_results
    7. Return top-5 impacted    → RiskReport

Like Phase 1, the DB is injected (write_sim_results callback), so this module is
fully testable with hand-built SchedulerInput fixtures and no live Oracle.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

from engine1_scheduler import run_engine1
from models import (
    ELEVATED_URGENCY_WEIGHT,
    Config,
    RiskReport,
    SchedulableTask,
    SchedulerInput,
    SchedulerResult,
    SimResultRow,
    SimulationRequest,
)
from risk_classifier import build_risk_report

# A sink that persists the simulation's rows to the sim_results table. Injected so
# tests can pass a no-op / list-collector instead of a live Oracle connection.
SimResultWriter = Callable[[list[SimResultRow]], None]


def simulate_priority_elevation(
    baseline_input: SchedulerInput,
    elevated_orders: list[str],
    config: Config,
    time_limit_seconds: Optional[float] = None,
) -> SchedulerResult:
    """
    Step 2 + 3: rebuild the input with elevated urgency weights and re-solve.

    Every SchedulableTask whose PRODUCTION_ORDER is in `elevated_orders` has its
    urgency_weight overridden to ELEVATED_URGENCY_WEIGHT; all other tasks and the
    capacity/horizon/config are carried over unchanged. The re-run uses the Engine
    2 time limit (default config.engine2_time_limit_seconds) so the UI stays snappy.

    Returns the simulated SchedulerResult (its .completion_dates is the "new" side
    of the diff).
    """
    elevated_set = set(elevated_orders)
    limit = time_limit_seconds if time_limit_seconds is not None else config.engine2_time_limit_seconds

    elevated_tasks: list[SchedulableTask] = [
        task.model_copy(update={"urgency_weight": ELEVATED_URGENCY_WEIGHT})
        if task.production_order in elevated_set
        else task
        for task in baseline_input.tasks
    ]

    elevated_input = baseline_input.model_copy(update={"tasks": elevated_tasks})
    return run_engine1(elevated_input, max_time_in_seconds=limit)


def engine2_main(
    baseline_input: SchedulerInput,
    request: SimulationRequest,
    config: Config,
    baseline_result: Optional[SchedulerResult] = None,
    write_sim_results: Optional[SimResultWriter] = None,
    now: Optional[datetime] = None,
) -> RiskReport:
    """
    Orchestrate the full simulation and return the RiskReport (top-5 impacted).

    Parameters
    ----------
    baseline_input   : the current SchedulerInput (from preprocess.build_scheduler_input).
    request          : which orders to elevate (+ optional per-run time limit).
    config           : runtime config (thresholds, time limit).
    baseline_result  : the current Engine 1 result. If omitted, a fresh baseline
                       solve is run so old_completion_date is always well-defined.
    write_sim_results: sink that persists rows to the sim_results table. If None,
                       nothing is written (useful for tests / dry-runs).
    now              : timestamp injected for deterministic tests.

    Raises
    ------
    ValueError : if an elevated order is not present in the current schedulable set.
    """
    now = now or datetime.now()

    # Validate the elevation targets exist among schedulable orders.
    known_orders = {t.production_order for t in baseline_input.tasks}
    unknown = [o for o in request.orders if o not in known_orders]
    if unknown:
        raise ValueError(f"Cannot elevate unknown / unschedulable order(s): {unknown}")

    # Step 1: baseline snapshot (old_completion_date per order).
    if baseline_result is None:
        baseline_result = run_engine1(baseline_input)

    # Steps 2 + 3: elevate and re-solve.
    sim_result = simulate_priority_elevation(
        baseline_input=baseline_input,
        elevated_orders=request.orders,
        config=config,
        time_limit_seconds=request.time_limit_seconds,
    )

    # Steps 4 + 5: diff + classify.
    cdd_map = {t.production_order: t.cdd for t in baseline_input.tasks}
    report = build_risk_report(
        baseline_completion=baseline_result.completion_dates,
        new_completion=sim_result.completion_dates,
        cdd_map=cdd_map,
        elevated_orders=request.orders,
        status=sim_result.status,
        config=config,
        now=now,
    )

    # Step 6: persist all rows (top-5 is a view over the same rows).
    if write_sim_results is not None:
        write_sim_results(report.impacts)

    # Step 7: return the report (API returns report.top_impacted to the UI).
    return report
