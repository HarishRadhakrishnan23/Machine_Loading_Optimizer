"""
test_dispatch_pool.py — CLAUDE.md §E.9 fixture/locator pool constraint.

Run: backend/venv/Scripts/python.exe backend/testing/test_dispatch_pool.py
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dispatch_pool import DevicePool
from dispatch_timeline import TimePoint

DAY = date(2026, 1, 1)


def tp(minute: float, shift: int = 0) -> TimePoint:
    return TimePoint(DAY, shift, minute)


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def test_single_unit_device_blocks_overlap():
    print("\n=== QUANTITY=1: a second overlapping reservation is blocked ===")
    pool = DevicePool(quantities={"FIXTURE-A": 1})
    check("free before any reservation", pool.has_capacity("FIXTURE-A", tp(0), tp(50)))
    pool.reserve("FIXTURE-A", tp(0), tp(50))
    check("overlapping request blocked", not pool.has_capacity("FIXTURE-A", tp(20), tp(70)))
    check("non-overlapping request (starts exactly at release) is fine", pool.has_capacity("FIXTURE-A", tp(50), tp(80)))


def test_two_unit_device_allows_two_concurrent_then_blocks_third():
    print("\n=== QUANTITY=2: two concurrent holds OK, a third overlapping one is not ===")
    pool = DevicePool(quantities={"LOCATOR-B": 2})
    pool.reserve("LOCATOR-B", tp(0), tp(100))
    check("second overlapping reservation fits (1 of 2 used)", pool.has_capacity("LOCATOR-B", tp(10), tp(90)))
    pool.reserve("LOCATOR-B", tp(10), tp(90))
    check("third overlapping reservation blocked (2 of 2 used)", not pool.has_capacity("LOCATOR-B", tp(20), tp(30)))
    # Outside both existing windows entirely -> fine even though quantity is exhausted elsewhere.
    check("a request after both release is fine", pool.has_capacity("LOCATOR-B", tp(100), tp(110)))


def test_reservations_dont_leak_across_devices():
    print("\n=== Reservations on one device never affect another ===")
    pool = DevicePool(quantities={"FIXTURE-A": 1, "FIXTURE-B": 1})
    pool.reserve("FIXTURE-A", tp(0), tp(50))
    check("FIXTURE-B is untouched", pool.has_capacity("FIXTURE-B", tp(0), tp(50)))


def test_unknown_device_raises():
    print("\n=== Unknown device (not in MCH_FIXTURE_LOCATOR inventory): raises ===")
    pool = DevicePool(quantities={"FIXTURE-A": 1})
    try:
        pool.has_capacity("NOT-A-REAL-DEVICE", tp(0), tp(10))
        raised = False
    except KeyError:
        raised = True
    check("KeyError raised", raised)


def test_earliest_release_hint():
    print("\n=== earliest_release_hint gives a retry starting point, not a guarantee ===")
    pool = DevicePool(quantities={"FIXTURE-A": 1})
    check("no holds -> None (free right away)", pool.earliest_release_hint("FIXTURE-A", tp(0)) is None)
    pool.reserve("FIXTURE-A", tp(10), tp(60))
    check("hint == the reservation's end", pool.earliest_release_hint("FIXTURE-A", tp(0)) == tp(60))
    check("ignores reservations that already ended before not_before", pool.earliest_release_hint("FIXTURE-A", tp(70)) is None)


def test_clone_is_an_independent_fork():
    print("\n=== clone(): independent fork for speculative branches ===")
    pool = DevicePool(quantities={"FIXTURE-A": 1})
    pool.reserve("FIXTURE-A", tp(0), tp(50))
    cloned = pool.clone()
    check("clone starts with the same reservation", not cloned.has_capacity("FIXTURE-A", tp(10), tp(20)))

    cloned.reserve("FIXTURE-A", tp(50), tp(100))
    check("clone reflects its own new reservation", not cloned.has_capacity("FIXTURE-A", tp(60), tp(70)))
    check("original pool is untouched by the clone's new reservation", pool.has_capacity("FIXTURE-A", tp(60), tp(70)))


def test_invalid_interval_rejected():
    print("\n=== reserve() rejects a non-positive-duration interval ===")
    pool = DevicePool(quantities={"FIXTURE-A": 1})
    try:
        pool.reserve("FIXTURE-A", tp(50), tp(50))
        raised = False
    except ValueError:
        raised = True
    check("ValueError raised for start == end", raised)


if __name__ == "__main__":
    test_single_unit_device_blocks_overlap()
    test_two_unit_device_allows_two_concurrent_then_blocks_third()
    test_reservations_dont_leak_across_devices()
    test_unknown_device_raises()
    test_earliest_release_hint()
    test_clone_is_an_independent_fork()
    test_invalid_interval_rejected()
    print("\n[OK] Fixture/locator pool constraint behaves per CLAUDE.md §E.9.")
