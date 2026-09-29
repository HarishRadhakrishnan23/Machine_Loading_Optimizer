"""
test_dispatch_consolidation_window.py — CLAUDE.md §E.6 consolidation window,
now a hard rule with no override.

Run: backend/venv/Scripts/python.exe backend/testing/test_dispatch_consolidation_window.py
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dispatch_consolidation_window import is_consolidation_eligible, is_within_window

TODAY = date(2026, 1, 1)
WINDOW_DAYS = 60


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def test_within_window_is_eligible():
    print("\n=== Within window: eligible ===")
    cdd = TODAY + timedelta(days=30)
    check("is_within_window == True", is_within_window(cdd, TODAY, WINDOW_DAYS))
    check("is_consolidation_eligible == True", is_consolidation_eligible(cdd, TODAY, WINDOW_DAYS))


def test_beyond_window_is_never_eligible_no_override():
    print("\n=== Beyond window: never eligible — no escape hatch ===")
    cdd = TODAY + timedelta(days=200)
    check("is_within_window == False", not is_within_window(cdd, TODAY, WINDOW_DAYS))
    check("is_consolidation_eligible == False", not is_consolidation_eligible(cdd, TODAY, WINDOW_DAYS))


def test_safety_stock_always_beyond_window():
    print("\n=== Safety stock (CDD = NULL): always beyond window ===")
    check("is_within_window(None, ...) == False", not is_within_window(None, TODAY, WINDOW_DAYS))
    check("is_consolidation_eligible(None, ...) == False", not is_consolidation_eligible(None, TODAY, WINDOW_DAYS))


def test_exactly_on_boundary_is_within_window():
    print("\n=== Exactly window_days out: inclusive boundary ===")
    cdd = TODAY + timedelta(days=WINDOW_DAYS)
    check(f"CDD exactly {WINDOW_DAYS} days out is within window", is_within_window(cdd, TODAY, WINDOW_DAYS))


def test_window_size_is_just_a_number_you_can_change():
    print("\n=== Window size is a plain parameter — changing it needs no code change ===")
    cdd = TODAY + timedelta(days=75)
    check("beyond a 60-day window", not is_within_window(cdd, TODAY, 60))
    check("within a 90-day window (same CDD, different config value)", is_within_window(cdd, TODAY, 90))


if __name__ == "__main__":
    test_within_window_is_eligible()
    test_beyond_window_is_never_eligible_no_override()
    test_safety_stock_always_beyond_window()
    test_exactly_on_boundary_is_within_window()
    test_window_size_is_just_a_number_you_can_change()
    print("\n[OK] Consolidation window behaves as a hard rule per CLAUDE.md §E.6.")
