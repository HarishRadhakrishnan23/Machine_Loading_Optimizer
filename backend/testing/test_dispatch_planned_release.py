"""
test_dispatch_planned_release.py — CLAUDE.md §E.1/§E.8 Planned-order release
gating.

Run: backend/venv/Scripts/python.exe backend/testing/test_dispatch_planned_release.py
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dispatch_planned_release import compute_planned_earliest_start, is_released
from dispatch_timeline import TimePoint

ARRIVAL = date(2026, 3, 1)
BUFFER_DAYS = 1


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def test_earliest_start_is_arrival_plus_buffer_first_shift():
    print("\n=== Earliest-start = arrival + buffer, first shift, minute 0 ===")
    earliest = compute_planned_earliest_start(ARRIVAL, BUFFER_DAYS)
    check("day == 2026-03-02", earliest.day == date(2026, 3, 2))
    check("shift_index == 0 (first)", earliest.shift_index == 0)
    check("minute == 0", earliest.minute == 0.0)


def test_active_orders_always_released():
    print("\n=== Active orders: always released, no earliest_start needed ===")
    check("released before any given instant", is_released("Active", TimePoint(date(2020, 1, 1), 0, 0)))


def test_planned_order_gated_until_earliest_start():
    print("\n=== Planned order: released only at/after its earliest_start ===")
    earliest = compute_planned_earliest_start(ARRIVAL, BUFFER_DAYS)
    before = TimePoint(ARRIVAL, 2, 50)  # still arrival day, third shift -> before release day
    at_release = earliest
    after = TimePoint(earliest.day, 1, 0)  # same release day, second shift
    check("not released the instant before", not is_released("Planned", before, earliest))
    check("released exactly at earliest_start", is_released("Planned", at_release, earliest))
    check("released after earliest_start", is_released("Planned", after, earliest))


def test_planned_without_earliest_start_raises():
    print("\n=== Planned order missing earliest_start: raises rather than silently passing ===")
    try:
        is_released("Planned", TimePoint(ARRIVAL, 0, 0))
        raised = False
    except ValueError:
        raised = True
    check("ValueError raised", raised)


def test_unknown_status_raises():
    print("\n=== Unknown ORDER_STATUS: raises rather than silently allowing ===")
    try:
        is_released("Cancelled", TimePoint(ARRIVAL, 0, 0))
        raised = False
    except ValueError:
        raised = True
    check("ValueError raised", raised)


if __name__ == "__main__":
    test_earliest_start_is_arrival_plus_buffer_first_shift()
    test_active_orders_always_released()
    test_planned_order_gated_until_earliest_start()
    test_planned_without_earliest_start_raises()
    test_unknown_status_raises()
    print("\n[OK] Planned-order release gating behaves per CLAUDE.md §E.1/§E.8.")
