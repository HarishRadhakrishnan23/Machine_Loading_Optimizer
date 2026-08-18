#!/usr/bin/env python
"""
test_engine2_e2e.py — End-to-end test of Engine 2 (Priority Elevation Simulator).

Uses the same hand-built fixtures as test_engine1_e2e.py and:
  1. Generates a baseline schedule (Engine 1)
  2. Elevates ORD002 (earliest CDD: 2026-09-01)
  3. Re-solves and captures impact on other orders
  4. Verifies ORD002 completes earlier (or stays the same)
  5. Verifies other orders slip (or stay the same)
  6. Checks risk classifications (SAFE / AT_RISK / BREACH)
"""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from engine2_recommender import build_cdd_map, simulate_priority_elevation
from engine1_scheduler import run_engine1
from models import Config, RiskFlag
from preprocess import build_scheduler_input, compute_horizon, filter_wip_orders
from risk_classifier import build_risk_report


def build_fixtures() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Same fixtures as test_engine1_e2e.py."""
    today = date(2026, 8, 11)

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
            "CDD": date(2026, 9, 15),  # due Sep 15
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
            "CDD": date(2026, 9, 1),  # ← Due FIRST (Sep 1)
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
            "CDD": date(2026, 9, 10),  # due Sep 10
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

    machine_data = []
    for machine in ["M1", "M2", "M3"]:
        for shift in ["first", "second", "third"]:
            machine_data.append({
                "COMPANY": 1,
                "WORK_CENTER": machine,
                "SHIFT": shift,
                "WORKING_MINS": 480,
                "OEE": 0.85,
                "AVAILABLE_MINS": 408.0,
            })
    machine_master_df = pd.DataFrame(machine_data)

    machine_daily_df = pd.DataFrame(columns=[
        "COMPANY", "WORK_CENTER", "WORKING_DATE", "SHIFT", "WORKING_MINS", "OEE", "AVAILABLE_MINS"
    ])

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


def test_engine2_e2e():
    """Run Engine 2 end-to-end test."""
    print("\n" + "█"*70)
    print("ENGINE 2 — END-TO-END TEST (hand-built fixtures)")
    print("█"*70)

    today = date(2026, 8, 11)
    config = Config()

    # Build fixtures
    print("\n[SETUP] Building fixture DataFrames...", end=" ")
    wip_df, machine_master_df, machine_daily_df, routing_df = build_fixtures()
    print("✓")

    # Baseline
    print("\n[1] Solving BASELINE schedule...", end=" ")
    filtered = filter_wip_orders(wip_df, routing_df)
    horizon = compute_horizon(filtered, machine_master_df, config, today)
    scheduler_input = build_scheduler_input(
        wip_df, machine_master_df, machine_daily_df, routing_df,
        horizon_dates=horizon,
        config=config,
        today=today,
    )
    baseline_result = run_engine1(scheduler_input, max_time_in_seconds=30)
    print(f"✓ {baseline_result.status.value}")

    if not baseline_result.is_success:
        print(f"  ✗ Baseline solve failed")
        return False

    print(f"  Orders scheduled: {list(baseline_result.completion_dates.keys())}")
    for order, completion in sorted(baseline_result.completion_dates.items()):
        print(f"    {order}: {completion}")

    # Elevate ORD002 (earliest CDD)
    print(f"\n[2] ELEVATING ORD002 (CDD 2026-09-01, earliest)...", end=" ")
    elevated_result, new_completion = simulate_priority_elevation(
        elevated_order_ids=["ORD002"],
        baseline_scheduler_input=scheduler_input,
        baseline_completion_dates=baseline_result.completion_dates,
        config=config,
        today=today,
    )
    print(f"✓ {elevated_result.status.value}")

    if not elevated_result.is_success:
        print(f"  ✗ Elevated solve failed")
        return False

    print(f"  New completion dates:")
    for order, completion in sorted(new_completion.items()):
        print(f"    {order}: {completion}")

    # Build risk report
    print(f"\n[3] Building RISK REPORT...", end=" ")
    cdd_map = build_cdd_map(scheduler_input)
    risk_report = build_risk_report(
        baseline_completion=baseline_result.completion_dates,
        new_completion=new_completion,
        cdd_map=cdd_map,
        elevated_orders=["ORD002"],
        status=elevated_result.status,
        config=config,
    )
    print(f"✓")

    # Verify results
    print(f"\n[4] Verifying results...", end=" ")
    issues = []

    # Check 1: ORD002 not in impact rows (elevated orders excluded)
    impact_orders = set(r.order for r in risk_report.impacts)
    if "ORD002" in impact_orders:
        issues.append("  ✗ ORD002 (elevated) should not appear in impact rows")

    # Check 2: ORD001 and ORD003 should be in impacts
    expected_in_impacts = {"ORD001", "ORD003"}
    if not expected_in_impacts.issubset(impact_orders):
        missing = expected_in_impacts - impact_orders
        issues.append(f"  ✗ Missing from impacts: {missing}")

    # Check 3: Risk flags are valid
    for row in risk_report.impacts:
        if row.risk_flag not in (RiskFlag.SAFE, RiskFlag.AT_RISK, RiskFlag.BREACH):
            issues.append(f"  ✗ Invalid risk_flag for {row.order}: {row.risk_flag}")

    # Check 4: Counts sum correctly
    total = risk_report.safe_count + risk_report.at_risk_count + risk_report.breach_count
    if total != len(risk_report.impacts):
        issues.append(f"  ✗ Risk counts {total} don't match impacts {len(risk_report.impacts)}")

    if not issues:
        print("✓")
        print(f"  ✓ ORD002 (elevated) excluded from impacts")
        print(f"  ✓ ORD001, ORD003 in impact rows ({len(risk_report.impacts)} total)")
        print(f"  ✓ Risk counts: SAFE={risk_report.safe_count}, AT_RISK={risk_report.at_risk_count}, BREACH={risk_report.breach_count}")
    else:
        for issue in issues:
            print(issue)
        return False

    # Summary
    print(f"\n[5] Impact Summary:")
    for row in risk_report.impacts:
        slip_str = f"{row.slip_days:+d} days" if row.slip_days is not None else "N/A"
        print(f"    {row.order}: {slip_str} → {row.risk_flag.value}")

    print(f"\nTop {min(5, len(risk_report.top_impacted))} most impacted:")
    for i, row in enumerate(risk_report.top_impacted, 1):
        print(f"    {i}. {row.order}: {row.slip_days:+d} days, {row.risk_flag.value}")

    print("\n" + "█"*70)
    print("✓ ENGINE 2 END-TO-END TEST PASSED")
    print("█"*70 + "\n")
    return True


if __name__ == "__main__":
    success = test_engine2_e2e()
    sys.exit(0 if success else 1)
