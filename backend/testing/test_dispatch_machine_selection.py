"""
test_dispatch_machine_selection.py — CLAUDE.md §E.5 machine selection
(Model F: priority-first, not earliest-free-first — see §E.19 Rule 2).

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


def test_preferred_machine_wins_even_though_a_lower_priority_one_is_free_sooner():
    print("\n=== Model F: priority wins over earliest-free when both are currently available ===")
    members = [
        RunMember("O1", {"M1": 1, "M2": 2}),
        RunMember("O2", {"M1": 1, "M2": 2}),
    ]
    # M2 (priority 2) freed up earlier (10) than M1 (priority 1, free at 100) —
    # but both are available at/before this run's own ready instant (200), so
    # Model F picks M1 (better priority), reversing Model E's earliest-free rule.
    result = select_machine(members, machine_free_at={"M1": 100, "M2": 10}, ready_at={"O1": 200, "O2": 200})
    check("chosen machine == M1 (better priority, both currently available)", result.machine == "M1")
    check("no members dropped", result.dropped_order_ids == [])
    check("both members covered", set(result.covered_order_ids) == {"O1", "O2"})


def test_never_waits_on_a_busy_preferred_machine():
    print("\n=== Model F: a busy preferred machine is skipped, never waited on ===")
    members = [
        RunMember("O1", {"M1": 1, "M2": 2}),
        RunMember("O2", {"M1": 1, "M2": 2}),
    ]
    # M1 (priority 1) isn't free until 100 — AFTER this run's ready instant
    # (50) — so it's not "currently available"; M2 (priority 2, free at 10)
    # is available and wins. Matches both models' "never wait on a preferred
    # machine while another capable one is idle" principle.
    result = select_machine(members, machine_free_at={"M1": 100, "M2": 10}, ready_at={"O1": 50, "O2": 50})
    check("chosen machine == M2 (M1 not yet available)", result.machine == "M2")


def test_none_available_falls_back_to_earliest_to_free():
    print("\n=== Model F: nothing currently available -> wait for the earliest one, tie-break by priority ===")
    members = [
        RunMember("O1", {"M1": 2, "M2": 1}),
        RunMember("O2", {"M1": 2, "M2": 1}),
    ]
    # Neither machine is free by this run's ready instant (0) — M1 frees at
    # 10, M2 at 10 too (tie) -> tie-break by priority -> M2 (priority 1).
    result = select_machine(members, machine_free_at={"M1": 10, "M2": 10}, ready_at={"O1": 0, "O2": 0})
    check("chosen machine == M2 (tied earliest-to-free, better priority)", result.machine == "M2")


def test_partial_coverage_drops_incapable_member():
    print("\n=== Partial coverage: keep run on max-coverage machine, drop the rest ===")
    members = [
        RunMember("O1", {"M1": 1, "M2": 1}),
        RunMember("O2", {"M1": 1, "M2": 1}),
        RunMember("O3", {"M3": 1}),  # only capable on M3 -> no full intersection
    ]
    result = select_machine(
        members, machine_free_at={"M1": 5, "M2": 5, "M3": 0}, ready_at={"O1": 100, "O2": 100, "O3": 100}
    )
    check("chosen machine covers the 2-member majority (M1 or M2)", result.machine in ("M1", "M2"))
    check("O3 dropped", result.dropped_order_ids == ["O3"])
    check("O1, O2 covered", set(result.covered_order_ids) == {"O1", "O2"})


if __name__ == "__main__":
    test_preferred_machine_wins_even_though_a_lower_priority_one_is_free_sooner()
    test_never_waits_on_a_busy_preferred_machine()
    test_none_available_falls_back_to_earliest_to_free()
    test_partial_coverage_drops_incapable_member()
    print("\n[OK] Machine selection behaves per CLAUDE.md §E.5 (Model F).")
