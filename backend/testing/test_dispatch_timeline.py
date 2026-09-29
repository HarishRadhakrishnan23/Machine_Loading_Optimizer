"""
test_dispatch_timeline.py — CLAUDE.md §E.7 continuous timeline: pause/resume
across shift and day boundaries, closed-shift skipping, and cooling using the
same mechanics as machining time.

Run: backend/venv/Scripts/python.exe backend/testing/test_dispatch_timeline.py
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dispatch_timeline import TimePoint, advance_clock, next_open_instant, start_of_day

DAY1 = date(2026, 1, 1)
DAY2 = DAY1 + timedelta(days=1)

BASELINE = {"first": 200.0, "second": 200.0, "third": 100.0}


def make_availability(overrides: dict[tuple[str, date, str], float] = None):
    overrides = overrides or {}

    def available(machine: str, day: date, shift: str) -> float:
        if (machine, day, shift) in overrides:
            return overrides[(machine, day, shift)]
        return BASELINE[shift]

    return available


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def test_fits_within_current_shift():
    print("\n=== Fits entirely inside the current shift ===")
    available = make_availability()
    end = advance_clock(TimePoint(DAY1, 0, 50), 100, "M1", available)
    check("ends at (DAY1, first, 150)", end == TimePoint(DAY1, 0, 150))


def test_spills_into_next_shift_same_day():
    print("\n=== Spills into the next shift, same day ===")
    available = make_availability()
    # 150 into first shift (cap 200), need 100 -> 50 left in first, 50 into second.
    end = advance_clock(TimePoint(DAY1, 0, 150), 100, "M1", available)
    check("ends at (DAY1, second, 50)", end == TimePoint(DAY1, 1, 50))


def test_skips_a_fully_closed_shift():
    print("\n=== A fully closed shift (breakdown) is skipped as zero time ===")
    available = make_availability({("M1", DAY1, "second"): 0.0})
    # 190 into first shift (10 left), need 30 -> 10 consumed, second closed
    # (skipped entirely), 20 lands into third shift.
    end = advance_clock(TimePoint(DAY1, 0, 190), 30, "M1", available)
    check("ends at (DAY1, third, 20) — second shift skipped", end == TimePoint(DAY1, 2, 20))


def test_rolls_over_to_next_day():
    print("\n=== Rolls over from third shift into next day's first shift ===")
    available = make_availability()
    # 90 into third shift (cap 100, 10 left), need 50 -> 10 consumed, 40 into next day's first shift.
    end = advance_clock(TimePoint(DAY1, 2, 90), 50, "M1", available)
    check("ends at (DAY2, first, 40)", end == TimePoint(DAY2, 0, 40))


def test_cooling_uses_the_same_mechanics_and_can_cross_shift_boundary():
    print("\n=== Cooling (same primitive) can span a shift boundary ===")
    available = make_availability()
    batch_end = TimePoint(DAY1, 0, 195)  # 5 minutes left in first shift
    machine_free_at = advance_clock(batch_end, 20, "M1", available)  # cooling_minutes = 20
    check("cooling spills 15 min into second shift", machine_free_at == TimePoint(DAY1, 1, 15))


def test_next_open_instant_unchanged_when_already_open():
    print("\n=== next_open_instant: no-op when already inside an open shift ===")
    available = make_availability()
    point = TimePoint(DAY1, 0, 50)
    check("returned unchanged", next_open_instant(point, "M1", available) == point)


def test_next_open_instant_skips_multiple_closed_shifts_into_next_day():
    print("\n=== next_open_instant: skips two closed shifts into the next day ===")
    available = make_availability({
        ("M1", DAY1, "second"): 0.0,
        ("M1", DAY1, "third"): 0.0,
    })
    result = next_open_instant(TimePoint(DAY1, 1, 0), "M1", available)
    check("lands on (DAY2, first, 0)", result == TimePoint(DAY2, 0, 0.0))


def test_negative_minutes_rejected():
    print("\n=== advance_clock rejects negative durations ===")
    available = make_availability()
    try:
        advance_clock(start_of_day(DAY1), -5, "M1", available)
        raised = False
    except ValueError:
        raised = True
    check("ValueError raised", raised)


if __name__ == "__main__":
    test_fits_within_current_shift()
    test_spills_into_next_shift_same_day()
    test_skips_a_fully_closed_shift()
    test_rolls_over_to_next_day()
    test_cooling_uses_the_same_mechanics_and_can_cross_shift_boundary()
    test_next_open_instant_unchanged_when_already_open()
    test_next_open_instant_skips_multiple_closed_shifts_into_next_day()
    test_negative_minutes_rejected()
    print("\n[OK] Continuous timeline behaves per CLAUDE.md §E.7.")
