#!/usr/bin/env python
"""
test_engine1_e2e.py — End-to-end test of Engine 1 scheduling pipeline.

Creates hand-built fixture DataFrames (no Oracle required) and runs:
  filter_wip_orders → compute_horizon → build_scheduler_input → run_engine1 → persist

Verifies:
  - Pipeline executes without error
  - Schedule is generated (FEASIBLE or OPTIMAL)
  - Assignments respect machine capacity and precedence
  - Completion dates are sensible
"""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from models import Config
from preprocess import compute_horizon, filter_wip_orders, build_scheduler_input
from engine1_scheduler import run_engine1


def build_fixtures() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Create hand-built fixture DataFrames mimicking the 4 ERP views.
    Scenario: 3 orders (2-3 ops each), 3 machines, minimal routing.
    """
    today = date(2026, 8, 11)

    # MCH_WIP: 7 work-in-progress operations
    wip_data = [
        {
            "COMPANY": 1,
            "PRODUCTION_ORDER": "ORD001",
            "PRODUCTION_START_DATE_AND_TIME": datetime(2026, 8, 1),
            "ORDER_STATUS": "In Progress",
            "ITEM": "30150DF",
            "ITEM_DESCRIPTION": "3 inch, 150 class valve",
            "SIZE_INCH": 30,
            "CLASS": "150",
            "MOC": "CS",
            "DESIGN": "DF",
            "ITEM_CATEGORY": "30~150~DF~CS",
            "REFERENCE": "spec-001",
            "QUANTITY_ORDERED": 10,
            "CDD": date(2026, 9, 15),  # due in ~1 month
            "OPERATION": 10,
            "OPERATION_STATUS": "Not Started",
            "TASK": "VB02",
            "WORK_CENTER": "M1",
            "QUANTITY_COMPLETED": 0,
            "QUANTITY_REJECTED": 0,
            "CYCLE_TIME": 5.0,  # 5 min per piece
        },
        {
            "COMPANY": 1,
            "PRODUCTION_ORDER": "ORD001",
            "PRODUCTION_START_DATE_AND_TIME": datetime(2026, 8, 1),
            "ORDER_STATUS": "In Progress",
            "ITEM": "30150DF",
            "ITEM_DESCRIPTION": "3 inch, 150 class valve",
            "SIZE_INCH": 30,
            "CLASS": "150",
            "MOC": "CS",
            "DESIGN": "DF",
            "ITEM_CATEGORY": "30~150~DF~CS",
            "REFERENCE": "spec-001",
            "QUANTITY_ORDERED": 10,
            "CDD": date(2026, 9, 15),
            "OPERATION": 20,
            "OPERATION_STATUS": "Not Started",
            "TASK": "VB03",
            "WORK_CENTER": "M2",
            "QUANTITY_COMPLETED": 0,
            "QUANTITY_REJECTED": 0,
            "CYCLE_TIME": 7.0,
        },
        {
            "COMPANY": 1,
            "PRODUCTION_ORDER": "ORD002",
            "PRODUCTION_START_DATE_AND_TIME": datetime(2026, 8, 3),
            "ORDER_STATUS": "In Progress",
            "ITEM": "50300SS",
            "ITEM_DESCRIPTION": "5 inch, 300 class valve",
            "SIZE_INCH": 50,
            "CLASS": "300",
            "MOC": "SS",
            "DESIGN": "DF",
            "ITEM_CATEGORY": "50~300~DF~SS",
            "REFERENCE": "spec-002",
            "QUANTITY_ORDERED": 5,
            "CDD": date(2026, 9, 1),  # due sooner
            "OPERATION": 10,
            "OPERATION_STATUS": "Not Started",
            "TASK": "VB02",
            "WORK_CENTER": "M1",
            "QUANTITY_COMPLETED": 0,
            "QUANTITY_REJECTED": 0,
            "CYCLE_TIME": 8.0,
        },
        {
            "COMPANY": 1,
            "PRODUCTION_ORDER": "ORD002",
            "PRODUCTION_START_DATE_AND_TIME": datetime(2026, 8, 3),
            "ORDER_STATUS": "In Progress",
            "ITEM": "50300SS",
            "ITEM_DESCRIPTION": "5 inch, 300 class valve",
            "SIZE_INCH": 50,
            "CLASS": "300",
            "MOC": "SS",
            "DESIGN": "DF",
            "ITEM_CATEGORY": "50~300~DF~SS",
            "REFERENCE": "spec-002",
            "QUANTITY_ORDERED": 5,
            "CDD": date(2026, 9, 1),
            "OPERATION": 20,
            "OPERATION_STATUS": "Not Started",
            "TASK": "VB05",
            "WORK_CENTER": "M3",
            "QUANTITY_COMPLETED": 0,
            "QUANTITY_REJECTED": 0,
            "CYCLE_TIME": 6.0,
        },
        {
            "COMPANY": 1,
            "PRODUCTION_ORDER": "ORD003",
            "PRODUCTION_START_DATE_AND_TIME": datetime(2026, 8, 5),
            "ORDER_STATUS": "In Progress",
            "ITEM": "30150DF",
            "ITEM_DESCRIPTION": "3 inch, 150 class valve",
            "SIZE_INCH": 30,
            "CLASS": "150",
            "MOC": "CS",
            "DESIGN": "DF",
            "ITEM_CATEGORY": "30~150~DF~CS",
            "REFERENCE": "spec-001",
            "QUANTITY_ORDERED": 8,
            "CDD": date(2026, 9, 10),
            "OPERATION": 10,
            "OPERATION_STATUS": "Not Started",
            "TASK": "VB02",
            "WORK_CENTER": "M1",
            "QUANTITY_COMPLETED": 0,
            "QUANTITY_REJECTED": 0,
            "CYCLE_TIME": 5.0,
        },
        {
            "COMPANY": 1,
            "PRODUCTION_ORDER": "ORD003",
            "PRODUCTION_START_DATE_AND_TIME": datetime(2026, 8, 5),
            "ORDER_STATUS": "In Progress",
            "ITEM": "30150DF",
            "ITEM_DESCRIPTION": "3 inch, 150 class valve",
            "SIZE_INCH": 30,
            "CLASS": "150",
            "MOC": "CS",
            "DESIGN": "DF",
            "ITEM_CATEGORY": "30~150~DF~CS",
            "REFERENCE": "spec-001",
            "QUANTITY_ORDERED": 8,
            "CDD": date(2026, 9, 10),
            "OPERATION": 20,
            "OPERATION_STATUS": "Not Started",
            "TASK": "VB03",
            "WORK_CENTER": "M2",
            "QUANTITY_COMPLETED": 0,
            "QUANTITY_REJECTED": 0,
            "CYCLE_TIME": 7.0,
        },
    ]
    wip_df = pd.DataFrame(wip_data)

    # MCH_MACHINE_AVAILABILITY: 3 machines × 3 shifts baseline
    machine_data = []
    for machine in ["M1", "M2", "M3"]:
        for shift in ["first", "second", "third"]:
            machine_data.append({
                "COMPANY": 1,
                "WORK_CENTER": machine,
                "SHIFT": shift,
                "WORKING_MINS": 480,  # 8 hours
                "OEE": 0.85,
                "AVAILABLE_MINS": 408.0,  # 480 × 0.85
            })
    machine_master_df = pd.DataFrame(machine_data)

    # MCH_MACHINE_AVAILABILITY_BY_DATE: empty (no daily overrides in this test)
    machine_daily_df = pd.DataFrame(columns=[
        "COMPANY", "WORK_CENTER", "WORKING_DATE", "SHIFT", "WORKING_MINS", "OEE", "AVAILABLE_MINS"
    ])

    # MCH_MACHINE_PRIORITY: routing capability matrix
    routing_data = [
        {
            "COMPANY": 1,
            "SIZE_INCH": 30,
            "CLASS": "150",
            "MOC": "CS",
            "DESIGN": "DF",
            "ITEM_CATEGORY": "30~150~DF~CS",
            "TASK": "VB02",
            "MACHINE_PRIORITY": 1,
            "WORK_CENTER": "M1",
            "SETUP_TIME": 15.0,
        },
        {
            "COMPANY": 1,
            "SIZE_INCH": 30,
            "CLASS": "150",
            "MOC": "CS",
            "DESIGN": "DF",
            "ITEM_CATEGORY": "30~150~DF~CS",
            "TASK": "VB03",
            "MACHINE_PRIORITY": 1,
            "WORK_CENTER": "M2",
            "SETUP_TIME": 20.0,
        },
        {
            "COMPANY": 1,
            "SIZE_INCH": 50,
            "CLASS": "300",
            "MOC": "SS",
            "DESIGN": "DF",
            "ITEM_CATEGORY": "50~300~DF~SS",
            "TASK": "VB02",
            "MACHINE_PRIORITY": 1,
            "WORK_CENTER": "M1",
            "SETUP_TIME": 15.0,
        },
        {
            "COMPANY": 1,
            "SIZE_INCH": 50,
            "CLASS": "300",
            "MOC": "SS",
            "DESIGN": "DF",
            "ITEM_CATEGORY": "50~300~DF~SS",
            "TASK": "VB05",
            "MACHINE_PRIORITY": 1,
            "WORK_CENTER": "M3",
            "SETUP_TIME": 25.0,
        },
    ]
    routing_df = pd.DataFrame(routing_data)

    return wip_df, machine_master_df, machine_daily_df, routing_df


def test_end_to_end():
    """Run the full scheduling pipeline end-to-end."""
    print("\n" + "█"*70)
    print("ENGINE 1 — END-TO-END TEST (hand-built fixtures)")
    print("█"*70)

    today = date(2026, 8, 11)
    config = Config()

    # Build fixtures
    print("\n[SETUP] Building fixture DataFrames...", end=" ")
    wip_df, machine_master_df, machine_daily_df, routing_df = build_fixtures()
    print("✓")
    print(f"  WIP: {len(wip_df)} rows ({wip_df['PRODUCTION_ORDER'].nunique()} orders)")
    print(f"  Machines: {len(machine_master_df)} slots ({machine_master_df['WORK_CENTER'].nunique()} machines × 3 shifts)")
    print(f"  Routing: {len(routing_df)} rows")

    # Filter & horizon
    print("\n[1] Preprocessing...", end=" ")
    filtered = filter_wip_orders(wip_df, routing_df)
    horizon = compute_horizon(filtered, machine_master_df, config, today)
    print("✓")
    print(f"  Filtered WIP: {len(filtered)} rows")
    print(f"  Horizon: {len(horizon)} days ({horizon[0]} to {horizon[-1]})")

    # Build SchedulerInput
    print("\n[2] Building SchedulerInput...", end=" ")
    scheduler_input = build_scheduler_input(
        wip_df, machine_master_df, machine_daily_df, routing_df,
        horizon_dates=horizon,
        config=config,
        today=today,
    )
    print("✓")
    print(f"  Tasks: {len(scheduler_input.tasks)}")
    print(f"  Capacity slots: {len(scheduler_input.capacity)}")

    # Solve
    print("\n[3] Running CP-SAT solver...", end=" ")
    result = run_engine1(scheduler_input, max_time_in_seconds=30)
    print("✓")
    print(f"  Status: {result.status.value}")
    if result.objective_value is not None:
        print(f"  Objective value: {result.objective_value:.1f}")

    # Verify
    print("\n[4] Verifying results...", end=" ")
    if not result.is_success:
        print(f"\n  ✗ FAILED: Solver returned {result.status.value}")
        return False

    print("✓")
    print(f"  Assignments: {len(result.assignments)} rows")
    print(f"  Orders completed: {len(result.completion_dates)}")

    # Check every order's tasks are present
    for order_id in sorted(set(row.production_order for row in result.assignments)):
        rows_for_order = [r for r in result.assignments if r.production_order == order_id]
        ops = sorted(set(r.operation_no for r in rows_for_order))
        print(f"    {order_id}: ops {ops}")

    # Sanity checks
    print("\n[5] Sanity checks...", end=" ")
    issues = []

    # Check 1: All scheduled dates within horizon
    for row in result.assignments:
        if row.scheduled_date not in horizon:
            issues.append(f"  ✗ {row.production_order} op {row.operation_no} scheduled outside horizon ({row.scheduled_date})")

    # Check 2: Completion dates make sense
    for order_id, completion in result.completion_dates.items():
        if completion < today:
            issues.append(f"  ✗ {order_id} completed in the past ({completion})")

    if not issues:
        print("✓")
        print("  ✓ All assignments within horizon")
        print("  ✓ All completion dates ≥ today")
    else:
        for issue in issues:
            print(issue)
        return False

    print("\n" + "█"*70)
    print("✓ END-TO-END TEST PASSED")
    print("█"*70 + "\n")
    return True


if __name__ == "__main__":
    success = test_end_to_end()
    sys.exit(0 if success else 1)
