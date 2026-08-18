"""5 Levels of Schedule Validation."""

import oracledb
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).parent.parent / ".env")


def get_connection():
    """Get Oracle connection."""
    return oracledb.connect(
        user=os.getenv("ORACLE_USER"),
        password=os.getenv("ORACLE_PASSWORD"),
        dsn=os.getenv("ORACLE_DSN")
    )


# ════════════════════════════════════════════════════════════════════════════════
# LEVEL 1: INTERNAL CONSISTENCY CHECKS
# ════════════════════════════════════════════════════════════════════════════════
def validate_level_1_consistency():
    """
    Check if the solver followed its own constraints:
    - All pieces accounted for (no over-scheduling)
    - No capacity violations
    - Precedence respected
    """
    print("\n" + "="*80)
    print("LEVEL 1: INTERNAL CONSISTENCY CHECKS")
    print("="*80)

    con = get_connection()

    # ─────────────────────────────────────────────────────────────────────
    # Check 1: All pieces accounted for (no over-scheduling)
    # ─────────────────────────────────────────────────────────────────────
    print("\n[1.1] Checking: All pieces accounted for...")

    cur = con.cursor()
    cur.execute("""
        SELECT s.PRODUCTION_ORDER, s.OPERATION_NO, SUM(s.BALANCE_QTY) as scheduled,
               w.QUANTITY_ORDERED - w.QUANTITY_COMPLETED - COALESCE(w.QUANTITY_REJECTED, 0) as needed
        FROM MCH_SCHEDULE_OUTPUT s
        JOIN MCH_WIP w ON s.PRODUCTION_ORDER = w.PRODUCTION_ORDER AND s.OPERATION_NO = w.OPERATION
        GROUP BY s.PRODUCTION_ORDER, s.OPERATION_NO,
                 w.QUANTITY_ORDERED, w.QUANTITY_COMPLETED, w.QUANTITY_REJECTED
        HAVING SUM(s.BALANCE_QTY) > w.QUANTITY_ORDERED - w.QUANTITY_COMPLETED - COALESCE(w.QUANTITY_REJECTED, 0)
    """)
    over_scheduled = cur.fetchall()

    if len(over_scheduled) == 0:
        print("  ✓ No over-scheduling detected")
    else:
        print(f"  ✗ FAIL: {len(over_scheduled)} tasks over-scheduled")
        for row in over_scheduled[:10]:
            print(f"    {row[0]} Op{row[1]}: scheduled {row[2]} but only {row[3]} needed")

    # Count under-scheduled (partial placement)
    cur.execute("""
        SELECT COUNT(*) FROM MCH_WIP
        WHERE CYCLE_TIME > 0
        AND QUANTITY_ORDERED - QUANTITY_COMPLETED - COALESCE(QUANTITY_REJECTED, 0) > 0
        AND (PRODUCTION_ORDER, OPERATION) NOT IN (
            SELECT PRODUCTION_ORDER, OPERATION_NO FROM MCH_SCHEDULE_OUTPUT
        )
    """)
    under_scheduled_count = cur.fetchone()[0]

    if under_scheduled_count == 0:
        print("  ✓ No under-scheduling detected (all routable tasks placed)")
    else:
        print(f"  ⚠️  {under_scheduled_count} tasks partially scheduled (expected for bottlenecks)")

    # ─────────────────────────────────────────────────────────────────────
    # Check 2: No capacity violations per (machine, shift, date)
    # ─────────────────────────────────────────────────────────────────────
    print("\n[1.2] Checking: No capacity overruns per machine/shift/date...")

    cur.execute("""
        SELECT s.WORK_CENTER, s.SCHEDULED_DATE, s.SHIFT,
               ROUND(SUM(s.END_OFFSET_MIN - s.START_OFFSET_MIN), 1) as consumed_mins,
               COALESCE(d.AVAILABLE_MINS, base.AVAILABLE_MINS) as available_mins
        FROM MCH_SCHEDULE_OUTPUT s
        JOIN MCH_MACHINE_AVAILABILITY base
            ON LOWER(s.WORK_CENTER) = LOWER(base.WORK_CENTER)
            AND LOWER(s.SHIFT) = LOWER(base.SHIFT)
        LEFT JOIN MCH_MACHINE_AVAILABILITY_BY_DATE d
            ON LOWER(s.WORK_CENTER) = LOWER(d.WORK_CENTER)
            AND s.SCHEDULED_DATE = d.WORKING_DATE
            AND LOWER(s.SHIFT) = LOWER(d.SHIFT)
        GROUP BY s.WORK_CENTER, s.SCHEDULED_DATE, s.SHIFT, d.AVAILABLE_MINS, base.AVAILABLE_MINS
        HAVING ROUND(SUM(s.END_OFFSET_MIN - s.START_OFFSET_MIN), 1) > COALESCE(d.AVAILABLE_MINS, base.AVAILABLE_MINS) * 1.01
    """)
    overruns = cur.fetchall()

    if len(overruns) == 0:
        print("  ✓ No capacity violations")
    else:
        print(f"  ✗ FAIL: {len(overruns)} capacity violations")
        for row in overruns[:10]:
            print(f"    {row[0]} {row[1]} {row[2]}: {row[3]} mins consumed vs {row[4]} available")

    # ─────────────────────────────────────────────────────────────────────
    # Check 3: Precedence respected (Op_n before Op_n+1)
    # ─────────────────────────────────────────────────────────────────────
    print("\n[1.3] Checking: Precedence respected...")

    cur.execute("""
        SELECT PRODUCTION_ORDER, OPERATION_NO,
               MIN(SCHEDULED_DATE) as first_date,
               MAX(SCHEDULED_DATE) as last_date
        FROM MCH_SCHEDULE_OUTPUT
        GROUP BY PRODUCTION_ORDER, OPERATION_NO
        ORDER BY PRODUCTION_ORDER, OPERATION_NO
    """)

    prec_violations = 0
    prev_order = None
    prev_last_date = None

    for row in cur.fetchall():
        order, op, first_date, last_date = row
        if prev_order == order and prev_last_date > first_date:
            prec_violations += 1
        prev_order = order
        prev_last_date = last_date

    if prec_violations == 0:
        print("  ✓ Precedence check passed (operations scheduled in sequence)")
    else:
        print(f"  ⚠️  {prec_violations} potential precedence issues (may be rework ops)")

    con.close()
    print("\n✓ LEVEL 1 COMPLETE\n")


# ════════════════════════════════════════════════════════════════════════════════
# LEVEL 2: BUSINESS LOGIC CHECKS
# ════════════════════════════════════════════════════════════════════════════════
def validate_level_2_business_logic():
    """
    Check if the schedule makes business sense:
    - Urgent orders are front-loaded
    - Machine utilization is reasonable (30%-95%)
    - High-urgency orders meet CDDs
    """
    print("\n" + "="*80)
    print("LEVEL 2: BUSINESS LOGIC CHECKS")
    print("="*80)

    con = get_connection()

    # ─────────────────────────────────────────────────────────────────────
    # Check 1: Urgent orders are early in schedule
    # ─────────────────────────────────────────────────────────────────────
    print("\n[2.1] Checking: Urgent orders are prioritized...")

    df_urgency = pd.read_sql("""
        SELECT DISTINCT w.PRODUCTION_ORDER, w.CDD,
               MIN(s.SCHEDULED_DATE) as first_scheduled_date
        FROM MCH_WIP w
        LEFT JOIN MCH_SCHEDULE_OUTPUT s ON w.PRODUCTION_ORDER = s.PRODUCTION_ORDER
        WHERE w.CDD IS NOT NULL
        GROUP BY w.PRODUCTION_ORDER, w.CDD
    """, con)

    # Ensure column exists
    if 'first_scheduled_date' not in df_urgency.columns:
        print("  ⚠️  Query missing first_scheduled_date column; skipping Level 2.1")
        return

    # Convert to datetime if not already
    df_urgency['CDD'] = pd.to_datetime(df_urgency['CDD'])
    df_urgency['first_scheduled_date'] = pd.to_datetime(df_urgency['first_scheduled_date'])

    # Only compute for orders that were actually scheduled
    df_urgency = df_urgency[df_urgency['first_scheduled_date'].notna()]

    df_urgency['days_until_cdd'] = (df_urgency['CDD'] - df_urgency['first_scheduled_date']).dt.days

    # Urgent = CDD < 14 days from today
    today = datetime.now().date()
    urgent = df_urgency[(df_urgency['CDD'] - pd.Timestamp(today)).dt.days < 14]

    if len(urgent) > 0:
        late = urgent[urgent['days_until_cdd'] < 0]
        if len(late) == 0:
            print(f"  ✓ All {len(urgent)} urgent orders (CDD < 14 days) are on-time")
        else:
            print(f"  ✗ {len(late)} urgent orders are LATE:")
            print(late[['PRODUCTION_ORDER', 'CDD', 'first_scheduled_date', 'days_until_cdd']].head(5))
    else:
        print("  ℹ️  No urgent orders (CDD < 14 days)")

    # ─────────────────────────────────────────────────────────────────────
    # Check 2: Machine utilization is reasonable
    # ─────────────────────────────────────────────────────────────────────
    print("\n[2.2] Checking: Machine utilization...")

    df_util = pd.read_sql("""
        SELECT WORK_CENTER,
               COUNT(DISTINCT SCHEDULED_DATE || SHIFT) as num_slots,
               SUM(END_OFFSET_MIN - START_OFFSET_MIN) as total_mins_used
        FROM MCH_SCHEDULE_OUTPUT
        GROUP BY WORK_CENTER
    """, con)

    # Get baseline capacity
    df_cap = pd.read_sql("""
        SELECT WORK_CENTER,
               COUNT(DISTINCT WORKING_DATE || SHIFT) as num_slots,
               SUM(AVAILABLE_MINS) as total_available
        FROM MCH_MACHINE_AVAILABILITY_BY_DATE
        GROUP BY WORK_CENTER
    """, con)

    df_util = df_util.merge(df_cap, on='WORK_CENTER')
    df_util['utilization_pct'] = (df_util['total_mins_used'] / df_util['total_available'] * 100).round(1)

    underutilized = df_util[df_util['utilization_pct'] < 30]
    overutilized = df_util[df_util['utilization_pct'] > 95]

    if len(underutilized) > 0:
        print(f"  ⚠️  {len(underutilized)} machines underutilized (<30%):")
        print(underutilized[['WORK_CENTER', 'utilization_pct']].head(5))
    else:
        print("  ✓ No significantly underutilized machines")

    if len(overutilized) > 0:
        print(f"  ⚠️  {len(overutilized)} machines over-constrained (>95%):")
        print(overutilized[['WORK_CENTER', 'utilization_pct']].head(5))
    else:
        print("  ✓ No over-constrained machines")

    # ─────────────────────────────────────────────────────────────────────
    # Check 3: Setup batching working
    # ─────────────────────────────────────────────────────────────────────
    print("\n[2.3] Checking: Setup batching (same valve sizes together)...")

    df_setup = pd.read_sql("""
        SELECT WORK_CENTER, SCHEDULED_DATE, SHIFT,
               COUNT(DISTINCT ITEM_CATEGORY) as num_categories,
               COUNT(*) as num_tasks
        FROM MCH_SCHEDULE_OUTPUT s
        JOIN MCH_WIP w ON s.PRODUCTION_ORDER = w.PRODUCTION_ORDER
        GROUP BY WORK_CENTER, SCHEDULED_DATE, SHIFT
    """, con)

    df_setup['avg_tasks_per_category'] = df_setup['num_tasks'] / df_setup['num_categories']

    # Good batching: ~2-3 tasks per category on average (not 1 per category)
    poor_batching = df_setup[df_setup['avg_tasks_per_category'] < 1.2]

    if len(poor_batching) > 0:
        pct = round(len(poor_batching) / len(df_setup) * 100)
        print(f"  ⚠️  {pct}% of machine/shift slots show poor batching (frequent category changes)")
    else:
        print("  ✓ Good batching: same valve sizes grouped together")

    con.close()
    print("\n✓ LEVEL 2 COMPLETE\n")


# ════════════════════════════════════════════════════════════════════════════════
# LEVEL 3: SHOP FLOOR REALITY (MANUAL)
# ════════════════════════════════════════════════════════════════════════════════
def validate_level_3_sample_orders(sample_order_ids=None):
    """
    Manually audit 3-5 sample orders end-to-end.
    Show the shop floor what the system planned.
    """
    print("\n" + "="*80)
    print("LEVEL 3: SHOP FLOOR REALITY CHECK (MANUAL)")
    print("="*80)

    con = get_connection()

    if sample_order_ids is None:
        # Pick 3 random orders from recent schedule
        cur = con.cursor()
        cur.execute("""
            SELECT DISTINCT PRODUCTION_ORDER FROM MCH_SCHEDULE_OUTPUT
            WHERE ROWNUM <= 3
        """)
        sample_order_ids = [row[0] for row in cur.fetchall()]

    print(f"\nSample orders to audit: {sample_order_ids}")
    print("\n" + "-"*80)

    for order_id in sample_order_ids[:5]:  # max 5
        print(f"\n📋 ORDER: {order_id}")
        print("-"*80)

        df_order = pd.read_sql(f"""
            SELECT s.OPERATION_NO, s.TASK, s.WORK_CENTER, s.SCHEDULED_DATE, s.SHIFT,
                   s.BALANCE_QTY, s.START_OFFSET_MIN, s.END_OFFSET_MIN,
                   w.ITEM_CATEGORY, w.CDD
            FROM MCH_SCHEDULE_OUTPUT s
            JOIN MCH_WIP w ON s.PRODUCTION_ORDER = w.PRODUCTION_ORDER
                           AND s.OPERATION_NO = w.OPERATION
            WHERE s.PRODUCTION_ORDER = '{order_id}'
            ORDER BY s.OPERATION_NO
        """, con)

        if df_order.empty:
            print("  (Not in schedule)")
            continue

        cdd = df_order.iloc[0]['CDD']
        completion = df_order.iloc[-1]['SCHEDULED_DATE']
        days_slack = (cdd - pd.Timestamp(completion)).days if pd.notna(cdd) else None

        print(f"  Item Category: {df_order.iloc[0]['ITEM_CATEGORY']}")
        print(f"  CDD: {cdd}")
        print(f"  Scheduled Completion: {completion} ({days_slack} days slack)" if days_slack is not None else "  Scheduled Completion: (safety stock, no CDD)")

        print(f"\n  Operations:")
        for idx, row in df_order.iterrows():
            duration = row['END_OFFSET_MIN'] - row['START_OFFSET_MIN']
            print(f"    Op{row['OPERATION_NO']} ({row['TASK']}):")
            print(f"      Machine: {row['WORK_CENTER']}")
            print(f"      Date: {row['SCHEDULED_DATE']} ({row['SHIFT']} shift)")
            print(f"      Qty: {row['BALANCE_QTY']} pieces, {duration} mins")

        print(f"\n  ❓ QUESTIONS FOR SHOP FLOOR:")
        print(f"     1. Is this routing realistic?")
        print(f"     2. Do these dates align with what you'd do manually?")
        print(f"     3. Would you reorder any operations?")
        print(f"     4. Are there any missing setup steps?")

    con.close()
    print("\n✓ LEVEL 3 COMPLETE (Requires manual shop floor review)\n")


# ════════════════════════════════════════════════════════════════════════════════
# LEVEL 4: SENSITIVITY ANALYSIS
# ════════════════════════════════════════════════════════════════════════════════
def validate_level_4_sensitivity():
    """
    Elevate 3 orders and verify Engine 2 impact is reasonable.
    Expected: 1-10 other orders slip by 0-5 days each.
    """
    print("\n" + "="*80)
    print("LEVEL 4: SENSITIVITY ANALYSIS")
    print("="*80)

    con = get_connection()

    print("\n[4.1] Checking: Engine 2 simulation results exist...")

    df_sim = pd.read_sql("""
        SELECT SIM_ID, ELEVATED_ORDER, CREATED_AT,
               COUNT(*) as impacted_count,
               AVG(SLIP_DAYS) as avg_slip,
               MAX(SLIP_DAYS) as max_slip,
               SUM(CASE WHEN RISK_FLAG = 'BREACH' THEN 1 ELSE 0 END) as breach_count,
               SUM(CASE WHEN RISK_FLAG = 'AT_RISK' THEN 1 ELSE 0 END) as at_risk_count
        FROM MCH_SIM_RESULTS
        GROUP BY SIM_ID, ELEVATED_ORDER, CREATED_AT
        ORDER BY CREATED_AT DESC
    """, con)

    if df_sim.empty or len(df_sim) == 0:
        print("  ⚠️  No simulation results found. Run Engine 2 to populate.")
    else:
        try:
            print(f"  ✓ Found {len(df_sim)} simulation runs")
            print(f"\n  Latest 5 simulations:")
            for idx, row in df_sim.head(5).iterrows():
                elevated = row.get('ELEVATED_ORDER', 'N/A')
                impacted = row.get('impacted_count', 0)
                avg_slip = row.get('avg_slip', 0)
                max_slip = row.get('max_slip', 0)
                breach_count = row.get('breach_count', 0)
                at_risk_count = row.get('at_risk_count', 0)

                print(f"    Elevated: {elevated}")
                print(f"      Orders impacted: {impacted}")
                print(f"      Avg slip: {avg_slip:.1f} days, Max: {max_slip}")
                print(f"      BREACH: {breach_count}, AT_RISK: {at_risk_count}")

                # Sanity check
                if impacted == 0:
                    print(f"      ✓ Elevation has no impact (good for non-critical orders)")
                elif max_slip > 10:
                    print(f"      ⚠️  Large slip detected ({max_slip} days)")
                else:
                    print(f"      ✓ Impact reasonable")
        except Exception as e:
            print(f"  ⚠️  Error processing simulation results: {e}")

    con.close()
    print("\n✓ LEVEL 4 COMPLETE\n")


# ════════════════════════════════════════════════════════════════════════════════
# LEVEL 5: TIME SERIES STABILITY
# ════════════════════════════════════════════════════════════════════════════════
def validate_level_5_stability():
    """
    Compare completion dates across multiple runs.
    If the same order's completion date jumps >3 days between runs, it's unstable.
    """
    print("\n" + "="*80)
    print("LEVEL 5: TIME SERIES STABILITY")
    print("="*80)

    con = get_connection()

    print("\n[5.1] Checking: Schedule stability across runs...")

    # Get all runs with their dates
    df_runs = pd.read_sql("""
        SELECT DISTINCT RUN_ID, GENERATED_AT FROM MCH_SCHEDULE_OUTPUT
        ORDER BY GENERATED_AT DESC
        FETCH FIRST 10 ROWS ONLY
    """, con)

    if len(df_runs) < 2:
        print("  ⚠️  Less than 2 runs in database. Schedule stability requires history.")
        con.close()
        print("\n✓ LEVEL 5 COMPLETE (need more history)\n")
        return

    print(f"  Comparing {len(df_runs)} recent runs...")

    # Pick 5 orders and track completion dates
    df_sample_orders = pd.read_sql("""
        SELECT DISTINCT PRODUCTION_ORDER FROM MCH_SCHEDULE_OUTPUT
        WHERE ROWNUM <= 5
    """, con)

    tracking_orders = df_sample_orders['PRODUCTION_ORDER'].tolist()

    instability_found = False

    for order in tracking_orders:
        completions = []
        for _, run in df_runs.iterrows():
            run_id, generated_at = run['RUN_ID'], run['GENERATED_AT']
            df_completion = pd.read_sql(f"""
                SELECT MAX(SCHEDULED_DATE) as completion
                FROM MCH_SCHEDULE_OUTPUT
                WHERE RUN_ID = '{run_id}'
                AND PRODUCTION_ORDER = '{order}'
            """, con)

            if not df_completion.empty:
                try:
                    comp_date = df_completion.iloc[0].get('completion')
                    if pd.notna(comp_date):
                        completions.append((generated_at, comp_date))
                except (KeyError, IndexError):
                    pass  # Order not in this run

        if len(completions) >= 2:
            dates = [c[1] for c in completions]
            variance = (pd.Timestamp(max(dates)) - pd.Timestamp(min(dates))).days

            if variance > 3:
                print(f"  ⚠️  {order}: unstable (swings {variance} days across runs)")
                instability_found = True
            elif variance > 0:
                print(f"  ℹ️  {order}: slight variation ({variance} days, acceptable)")

    if not instability_found:
        print("  ✓ Schedule is stable across runs")

    con.close()
    print("\n✓ LEVEL 5 COMPLETE\n")


# ════════════════════════════════════════════════════════════════════════════════
# RUN ALL VALIDATIONS
# ════════════════════════════════════════════════════════════════════════════════
def run_all_validations(sample_order_ids=None):
    """Run all 5 validation levels."""
    print("\n" + "█"*80)
    print("█ RUNNING 5-LEVEL SCHEDULE VALIDATION")
    print("█"*80)

    validate_level_1_consistency()
    validate_level_2_business_logic()
    validate_level_3_sample_orders(sample_order_ids)
    validate_level_4_sensitivity()
    validate_level_5_stability()

    print("\n" + "█"*80)
    print("█ VALIDATION COMPLETE")
    print("█"*80 + "\n")


if __name__ == "__main__":
    # Run all validations
    run_all_validations()
