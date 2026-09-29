"""
test_dispatch_machine_selection.py — CLAUDE.md §E.5 machine selection.

Run: backend/venv/Scripts/python.exe backend/testing/test_dispatch_machine_selection.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dispatch_machine_selection import RunMember, select_machine


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def test_full_intersection_picks_free_earliest():
    print("\n=== Full intersection: free-earliest wins over priority ===")
    members = [
        RunMember("O1", {"M1": 1, "M2": 2}),
        RunMember("O2", {"M1": 1, "M2": 2}),
    ]
    # M2 is lower priority (2) but frees up earlier than M1 -> M2 must win.
    result = select_machine(members, machine_free_at={"M1": 100, "M2": 10})
    check("chosen machine == M2 (free earliest, despite worse priority)", result.machine == "M2")
    check("no members dropped", result.dropped_order_ids == [])
    check("both members covered", set(result.covered_order_ids) == {"O1", "O2"})


def test_full_intersection_tiebreak_by_priority():
    print("\n=== Full intersection: tie on free-at broken by priority ===")
    members = [
        RunMember("O1", {"M1": 2, "M2": 1}),
        RunMember("O2", {"M1": 2, "M2": 1}),
    ]
    result = select_machine(members, machine_free_at={"M1": 10, "M2": 10})
    check("chosen machine == M2 (same free-at, better priority)", result.machine == "M2")


def test_partial_coverage_drops_incapable_member():
    print("\n=== Partial coverage: keep run on max-coverage machine, drop the rest ===")
    members = [
        RunMember("O1", {"M1": 1, "M2": 1}),
        RunMember("O2", {"M1": 1, "M2": 1}),
        RunMember("O3", {"M3": 1}),  # only capable on M3 -> no full intersection
    ]
    result = select_machine(members, machine_free_at={"M1": 5, "M2": 5, "M3": 0})
    check("chosen machine covers the 2-member majority (M1 or M2)", result.machine in ("M1", "M2"))
    check("O3 dropped", result.dropped_order_ids == ["O3"])
    check("O1, O2 covered", set(result.covered_order_ids) == {"O1", "O2"})


if __name__ == "__main__":
    test_full_intersection_picks_free_earliest()
    test_full_intersection_tiebreak_by_priority()
    test_partial_coverage_drops_incapable_member()
    print("\n[OK] Machine selection behaves per CLAUDE.md §E.5.")
