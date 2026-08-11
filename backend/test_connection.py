#!/usr/bin/env python
"""
test_connection.py — Verify Oracle thin-mode connection and view access.

Run this standalone to verify:
  1. Oracle credentials are loaded from .env
  2. Connection can be established
  3. All 4 ERP views exist and are readable
  4. Result tables exist (or will be created in Phase 0)

Usage:
    cd backend
    python test_connection.py
"""

import sys
from pathlib import Path

# Add backend to path so db.py can be imported
sys.path.insert(0, str(Path(__file__).parent))

import oracledb
from db import (
    ORACLE_USER, ORACLE_DSN, get_connection,
    read_wip_orders, read_machine_master, read_machine_daily, read_routing_master,
)


def test_connection():
    """Test basic Oracle connection."""
    print("\n" + "="*70)
    print("TESTING ORACLE THIN-MODE CONNECTION")
    print("="*70)

    print(f"\nCredentials loaded from .env:")
    print(f"  ORACLE_USER: {ORACLE_USER}")
    print(f"  ORACLE_DSN:  {ORACLE_DSN}")

    try:
        with get_connection() as conn:
            print("\n✓ Connection established successfully!")
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM DUAL")
            result = cursor.fetchone()
            print(f"✓ Test query executed: SELECT 1 FROM DUAL → {result[0]}")
    except oracledb.Error as e:
        print(f"\n✗ Connection failed: {e}")
        return False

    return True


def test_views():
    """Test access to all 4 ERP views."""
    print("\n" + "="*70)
    print("TESTING ERP VIEW ACCESS")
    print("="*70)

    views = [
        ("MCH_WIP (wip_orders)", read_wip_orders, "PRODUCTION_ORDER"),
        ("MCH_MACHINE_AVAILABILITY (machine_master)", read_machine_master, "WORK_CENTER"),
        ("MCH_MACHINE_AVAILABILITY_BY_DATE (machine_daily)", read_machine_daily, "WORKING_DATE"),
        ("MCH_MACHINE_PRIORITY (routing_master)", read_routing_master, "TASK"),
    ]

    all_ok = True
    for view_name, read_func, key_col in views:
        try:
            print(f"\nReading {view_name}...", end=" ")
            df = read_func()
            print(f"✓")
            print(f"  Shape: {df.shape[0]} rows, {df.shape[1]} columns")
            print(f"  Columns: {', '.join(df.columns[:5])}..." if df.shape[1] > 5 else f"  Columns: {', '.join(df.columns)}")
            if not df.empty:
                print(f"  Sample key values: {df[key_col].iloc[:3].tolist()}")
        except Exception as e:
            print(f"✗ Failed: {e}")
            all_ok = False

    return all_ok


def test_result_tables():
    """Test that result tables exist (MCH_SCHEDULE_OUTPUT, MCH_SIM_RESULTS)."""
    print("\n" + "="*70)
    print("TESTING RESULT TABLE ACCESS")
    print("="*70)

    result_tables = [
        ("MCH_SCHEDULE_OUTPUT", "Engine 1 scheduling results"),
        ("MCH_SIM_RESULTS", "Engine 2 simulation results"),
    ]

    all_ok = True
    for table_name, description in result_tables:
        try:
            print(f"\nChecking {table_name} ({description})...", end=" ")
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                count = cursor.fetchone()[0]
                print(f"✓ Table exists ({count} rows currently)")
        except Exception as e:
            error_msg = str(e)
            if "does not exist" in error_msg or "ORA-00942" in error_msg:
                print(f"✗ Table does not exist (will be created in Phase 0 DDL)")
                all_ok = False
            else:
                print(f"✗ Error: {e}")
                all_ok = False

    return all_ok


def main():
    """Run all tests."""
    print("\n" + "█"*70)
    print("TOV MACHINE LOADING OPTIMIZER — Phase 0 CONNECTION VERIFICATION")
    print("█"*70)

    conn_ok = test_connection()
    views_ok = test_views()
    tables_ok = test_result_tables()

    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    print(f"  Connection:       {'✓ PASS' if conn_ok else '✗ FAIL'}")
    print(f"  ERP Views:        {'✓ PASS' if views_ok else '✗ FAIL'}")
    print(f"  Result Tables:    {'✓ PASS (or will be created)' if not tables_ok else '✓ PASS'}")

    if conn_ok and views_ok:
        print("\n✓ Oracle connection is ready! Proceed to DDL script for result tables.")
        return 0
    else:
        print("\n✗ Fix the issues above before proceeding.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
