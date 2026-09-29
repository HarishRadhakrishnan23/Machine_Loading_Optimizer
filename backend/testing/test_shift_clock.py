"""
test_shift_clock.py — the fixed shift clock-time convention.

Run: backend/venv/Scripts/python.exe backend/testing/test_shift_clock.py
"""

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dispatch_timeline import TimePoint
from shift_clock import timepoint_to_datetime

DAY = date(2026, 1, 5)  # a Monday


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def test_first_shift_starts_at_7am():
    check("first shift, minute 0 -> 07:00", timepoint_to_datetime(TimePoint(DAY, 0, 0.0)) == datetime(2026, 1, 5, 7, 0))


def test_second_shift_starts_at_3_30pm():
    check("second shift, minute 0 -> 15:30", timepoint_to_datetime(TimePoint(DAY, 1, 0.0)) == datetime(2026, 1, 5, 15, 30))


def test_third_shift_starts_at_11_30pm():
    check("third shift, minute 0 -> 23:30", timepoint_to_datetime(TimePoint(DAY, 2, 0.0)) == datetime(2026, 1, 5, 23, 30))


def test_minute_offset_maps_linearly():
    check("first shift, 90 min in -> 08:30", timepoint_to_datetime(TimePoint(DAY, 0, 90.0)) == datetime(2026, 1, 5, 8, 30))


def test_third_shift_can_cross_into_the_next_calendar_day():
    # 8 hours (480 min) into third shift (starts 23:30) -> 07:30 the NEXT day.
    check(
        "third shift + 480 min -> 07:30 next day",
        timepoint_to_datetime(TimePoint(DAY, 2, 480.0)) == datetime(2026, 1, 6, 7, 30),
    )


if __name__ == "__main__":
    test_first_shift_starts_at_7am()
    test_second_shift_starts_at_3_30pm()
    test_third_shift_starts_at_11_30pm()
    test_minute_offset_maps_linearly()
    test_third_shift_can_cross_into_the_next_calendar_day()
    print("\n[OK] Shift clock-time conversion matches the fixed convention exactly.")
