"""
pipeline2.py — Engine 2 Orchestration: Simulate Priority Elevation.

Entry point: simulate_elevation(elevated_orders, config, today)

Orchestrates:
  1. Fetch baseline schedule (MCH_SCHEDULE_OUTPUT) + ERP data
  2. Build SchedulerInput (same as Phase 1)
  3. Call Engine 2: re-solve with elevated urgency
  4. Build risk report (slip/risk classification)
  5. Persist results to MCH_SIM_RESULTS

Returns: RiskReport with per-order impact and top-5 most affected.
"""

import sys
from datetime import date, datetime
from typing import Optional

from db import (
    read_wip_orders,
    read_machine_master,
    read_machine_daily,
    read_routing_master,
    write_sim_results,
)

# See pipeline.py: forces UTF-8 stdout so the ✓/═ progress logs below don't crash
# under launchers that attach a cp1252 console (NSSM, some IDE runners).
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
from engine2_recommender import (
    build_cdd_map,
    simulate_priority_elevation,
)
from models import Config, RiskReport, SolveStatus
from pipeline import schedule_all_orders
from preprocess import build_scheduler_input, compute_horizon, filter_wip_orders
from risk_classifier import build_risk_report


def simulate_elevation(
    elevated_orders: list[str],
    config: Config,
    today: Optional[date] = None,
) -> RiskReport:
    """
    Simulate elevating one or more orders and return a risk report showing impact.

    Args:
        elevated_orders: list of PRODUCTION_ORDER IDs to elevate (e.g., ["ORD001"]).
        config: runtime config (batch_bonus_months, threshold_days, time limits, etc.).
        today: reference date for urgency scoring (default: today).

    Returns:
        RiskReport with:
          - sim_id: unique identifier for this simulation
          - elevated_orders: the orders that were elevated
          - impacts: list of SimResultRow per OTHER order (slip_days, risk_flag)
          - top_impacted: sorted list of 5 most-affected orders
          - safe_count, at_risk_count, breach_count: counts by risk category
    """
    if today is None:
        today = date.today()

    print(f"\n{'='*70}")
    print(f"ENGINE 2 — PRIORITY ELEVATION SIMULATOR")
    print(f"{'='*70}")
    print(f"Simulation date: {today}")
    print(f"Elevated orders: {', '.join(elevated_orders)}")

    # ──────────────────────────────────────────────────────────────────
    # Step 1: Fetch baseline schedule + ERP data
    # ──────────────────────────────────────────────────────────────────
    print(f"\n[1/5] Fetching baseline schedule & ERP data...", end=" ")
    wip_df = read_wip_orders()
    machine_master_df = read_machine_master()
    machine_daily_df = read_machine_daily()
    routing_df = read_routing_master()
    print(f"✓")

    # ──────────────────────────────────────────────────────────────────
    # Step 2: Build SchedulerInput (same as Phase 1)
    # ──────────────────────────────────────────────────────────────────
    print(f"[2/5] Building SchedulerInput...", end=" ")
    filtered_wip = filter_wip_orders(wip_df, routing_df)
    horizon_dates = compute_horizon(filtered_wip, machine_master_df, config, today)
    scheduler_input = build_scheduler_input(
        wip_df, machine_master_df, machine_daily_df, routing_df,
        horizon_dates=horizon_dates,
        config=config,
        today=today,
    )
    print(f"✓")
    print(f"      {len(scheduler_input.tasks)} tasks, {len(horizon_dates)} days")

    # ──────────────────────────────────────────────────────────────────
    # Step 3: Baseline schedule (Phase 1 result)
    # ──────────────────────────────────────────────────────────────────
    print(f"[3/5] Solving baseline (Phase 1)...", end=" ")
    baseline_result = schedule_all_orders(config, today, max_time_in_seconds=None)
    if not baseline_result.is_success:
        print(f"\n✗ Baseline solve failed: {baseline_result.status.value}")
        # Return empty report on failure
        return RiskReport(
            sim_id="",
            created_at=datetime.now(),
            elevated_orders=elevated_orders,
            status=baseline_result.status,
            impacts=[],
            top_impacted=[],
            safe_count=0,
            at_risk_count=0,
            breach_count=0,
        )
    print(f"✓ Status: {baseline_result.status.value}")
    print(f"      {len(baseline_result.completion_dates)} orders scheduled")

    # ──────────────────────────────────────────────────────────────────
    # Step 4: Elevated schedule (Engine 2: re-solve with elevated urgency)
    # ──────────────────────────────────────────────────────────────────
    print(f"[4/5] Re-solving with elevated urgency...", end=" ")
    elevated_result, new_completion_dates = simulate_priority_elevation(
        elevated_order_ids=elevated_orders,
        baseline_scheduler_input=scheduler_input,
        baseline_completion_dates=baseline_result.completion_dates,
        config=config,
        today=today,
    )

    if not elevated_result.is_success:
        print(f"\n✗ Elevated solve failed: {elevated_result.status.value}")
        return RiskReport(
            sim_id="",
            created_at=datetime.now(),
            elevated_orders=elevated_orders,
            status=elevated_result.status,
            impacts=[],
            top_impacted=[],
            safe_count=0,
            at_risk_count=0,
            breach_count=0,
        )

    # ──────────────────────────────────────────────────────────────────
    # Step 5: Build risk report + persist
    # ──────────────────────────────────────────────────────────────────
    print(f"[5/5] Building risk report...", end=" ")
    cdd_map = build_cdd_map(scheduler_input)
    risk_report = build_risk_report(
        baseline_completion=baseline_result.completion_dates,
        new_completion=new_completion_dates,
        cdd_map=cdd_map,
        elevated_orders=elevated_orders,
        status=elevated_result.status,
        config=config,
        now=datetime.now(),
    )
    print(f"✓")

    # Persist to MCH_SIM_RESULTS
    print(f"      Writing MCH_SIM_RESULTS ({len(risk_report.impacts)} rows)...", end=" ")
    rows_dicts = [
        {
            "ELEVATED_ORDER": sim_row.elevated_order,
            "PRODUCTION_ORDER": sim_row.order,
            "OLD_COMPLETION_DATE": sim_row.old_completion_date,
            "NEW_COMPLETION_DATE": sim_row.new_completion_date,
            "SLIP_DAYS": sim_row.slip_days,
            "RISK_FLAG": sim_row.risk_flag.value,
            "created_at": sim_row.created_at,
        }
        for sim_row in risk_report.impacts
    ]
    write_sim_results(rows_dicts, risk_report.sim_id)
    print(f"✓")

    # ──────────────────────────────────────────────────────────────────
    # Summary
    # ──────────────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"✓ SIMULATION COMPLETE")
    print(f"{'='*70}")
    print(f"Simulation ID: {risk_report.sim_id}")
    print(f"Elevated:      {', '.join(risk_report.elevated_orders)}")
    print(f"Impact:        {len(risk_report.impacts)} other orders")
    print(f"Risk Summary:")
    print(f"  SAFE:       {risk_report.safe_count}")
    print(f"  AT_RISK:    {risk_report.at_risk_count}")
    print(f"  BREACH:     {risk_report.breach_count}")
    if risk_report.top_impacted:
        print(f"\nTop {min(5, len(risk_report.top_impacted))} most impacted orders (by slip_days):")
        for i, row in enumerate(risk_report.top_impacted, 1):
            print(f"  {i}. {row.order}: {row.slip_days:+d} days, {row.risk_flag.value}")
    print(f"{'='*70}\n")

    return risk_report
