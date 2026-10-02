"""
test_model_e_data_holidays.py — the confirmed company holiday calendar
(L750.TCCCP019) forces every shift of every machine closed on that date,
taking precedence over both the machine_daily override and the baseline.

Run: backend/venv/Scripts/python.exe backend/testing/test_model_e_data_holidays.py
"""

import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from model_e_data import build_availability

HOLIDAY = date(2026, 10, 2)  # Gandhi Jayanti — a real entry in live L750.TCCCP019
NORMAL_DAY = date(2026, 10, 1)


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def test_holiday_closes_every_shift_for_every_machine():
    print("\n=== A holiday date forces AVAILABLE_MINS = 0 for every machine, every shift ===")
    machine_master_df = pd.DataFrame(
        [{"WORK_CENTER": "M1", "SHIFT": "first", "AVAILABLE_MINS": 480.0},
         {"WORK_CENTER": "M1", "SHIFT": "second", "AVAILABLE_MINS": 480.0},
         {"WORK_CENTER": "M2", "SHIFT": "first", "AVAILABLE_MINS": 480.0}]
    )
    machine_daily_df = pd.DataFrame(columns=["WORK_CENTER", "WORKING_DATE", "SHIFT", "AVAILABLE_MINS"])
    holiday_df = pd.DataFrame([{"CALENDAR_CODE": "VEL", "DATE_1": pd.Timestamp(HOLIDAY), "DESCRIPTION": "Gandhi Jayanti"}])

    availability = build_availability(machine_master_df, machine_daily_df, holiday_df)

    check("M1 first shift closed on the holiday", availability("M1", HOLIDAY, "first") == 0.0)
    check("M1 second shift closed on the holiday", availability("M1", HOLIDAY, "second") == 0.0)
    check("M2 first shift closed on the holiday", availability("M2", HOLIDAY, "first") == 0.0)
    check("M1 first shift normal on a non-holiday day", availability("M1", NORMAL_DAY, "first") == 480.0)


def test_holiday_wins_over_a_machine_daily_override():
    print("\n=== Holiday closure takes precedence even over an explicit machine_daily override ===")
    machine_master_df = pd.DataFrame([{"WORK_CENTER": "M1", "SHIFT": "first", "AVAILABLE_MINS": 480.0}])
    # Suppose the ERP mistakenly (or deliberately) marks M1 as available that day anyway.
    machine_daily_df = pd.DataFrame(
        [{"WORK_CENTER": "M1", "WORKING_DATE": pd.Timestamp(HOLIDAY), "SHIFT": "first", "AVAILABLE_MINS": 300.0}]
    )
    holiday_df = pd.DataFrame([{"CALENDAR_CODE": "VEL", "DATE_1": pd.Timestamp(HOLIDAY), "DESCRIPTION": "Gandhi Jayanti"}])

    availability = build_availability(machine_master_df, machine_daily_df, holiday_df)
    check("holiday wins: still 0, not the override's 300", availability("M1", HOLIDAY, "first") == 0.0)


def test_no_holiday_df_behaves_as_before():
    print("\n=== Omitting the holiday calendar entirely preserves old behavior ===")
    machine_master_df = pd.DataFrame([{"WORK_CENTER": "M1", "SHIFT": "first", "AVAILABLE_MINS": 480.0}])
    machine_daily_df = pd.DataFrame(columns=["WORK_CENTER", "WORKING_DATE", "SHIFT", "AVAILABLE_MINS"])
    availability = build_availability(machine_master_df, machine_daily_df)  # no holiday_df at all
    check("unaffected on what would otherwise be a holiday", availability("M1", HOLIDAY, "first") == 480.0)


if __name__ == "__main__":
    test_holiday_closes_every_shift_for_every_machine()
    test_holiday_wins_over_a_machine_daily_override()
    test_no_holiday_df_behaves_as_before()
    print("\n[OK] Holiday calendar closure behaves correctly and takes precedence as expected.")
