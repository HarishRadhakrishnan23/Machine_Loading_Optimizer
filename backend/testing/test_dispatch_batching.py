"""
test_dispatch_batching.py — reproduces the two worked batching examples from
`Chat Reports/Engine1_Finallogic(BOSS)` EXACTLY, as CLAUDE.md's Model E §E.4
requires ("implementers should validate against both exactly before considering
the batching engine correct").

Run: backend/venv/Scripts/python.exe backend/testing/test_dispatch_batching.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dispatch_batching import BatchableOrder, run_priority_walk


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


# ─────────────────────────────────────────────────────────────────────────────
# Example 1 — no MOC column given in the source doc; treat MOC as constant ("-")
# for every row, so batch_key equality collapses to SIZE~CLASS~DESIGN, matching
# the doc's own worked reasoning (which never distinguishes on MOC in this example).
# ─────────────────────────────────────────────────────────────────────────────
def test_example_1():
    print("\n=== Example 1 ===")
    orders = [
        BatchableOrder("P2", 2, "10", "150", "-", "DFS", 5, "A", "1"),
        BatchableOrder("P1", 1, "10", "300", "-", "DFS", 4, "A", "2"),
        BatchableOrder("P5", 5, "8", "150", "-", "LUG", 10, "A", "3"),
        BatchableOrder("P6", 6, "12", "150", "-", "DFS", 10, "A", "4"),
        BatchableOrder("P4", 4, "10", "150", "-", "DFS", 21, "A", "1"),
        BatchableOrder("P3", 3, "24", "150", "-", "LUG", 8, "D", "2"),
    ]
    steps = run_priority_walk(orders)
    got = [(s.order_id, s.charge_type, s.run_index) for s in steps]
    expected = [
        ("P1", "fixture_change", 0),
        ("P2", "locator_change", 0),
        ("P4", "none", 0),
        ("P5", "locator_change", 0),
        ("P6", "locator_change", 0),
        ("P3", "fixture_change", 1),
    ]
    check(f"placement sequence == {expected}", got == expected)
    print("  got:", got)


# ─────────────────────────────────────────────────────────────────────────────
# Example 2 — full SIZE~CLASS~MOC~DESIGN key, tests the MOC-aware intra-batching
# nuance explicitly (step (a) vs (b) — exact SCMD match still wins over pure
# priority when both share the active fixture+locator).
# ─────────────────────────────────────────────────────────────────────────────
def test_example_2():
    print("\n=== Example 2 ===")
    orders = [
        BatchableOrder("P1", 1, "10", "150", "CS", "DFS", 10, "A", "1"),
        BatchableOrder("P4", 4, "12", "150", "SS", "DFS", 2, "A", "3"),
        BatchableOrder("P6", 6, "10", "150", "CS", "DFS", 10, "A", "1"),
        BatchableOrder("P5", 5, "10", "150", "SS", "DFS", 12, "A", "1"),
        BatchableOrder("P2", 2, "10", "150", "CS", "LUG", 5, "A", "2"),
        BatchableOrder("P3", 3, "10", "150", "SS", "LUG", 5, "A", "2"),
        BatchableOrder("P7", 7, "12", "150", "CS", "DFS", 2, "A", "3"),
        BatchableOrder("P8", 8, "40", "300", "SD", "DFL", 9, "G", "4"),
    ]
    steps = run_priority_walk(orders)
    got = [(s.order_id, s.charge_type, s.run_index) for s in steps]
    expected = [
        ("P1", "fixture_change", 0),
        ("P6", "none", 0),           # exact SCMD match beats P5/P2 despite lower priority rank
        ("P5", "none", 0),           # same fixture+locator, different SCMD -> still free
        ("P2", "locator_change", 0),
        ("P3", "none", 0),
        ("P4", "locator_change", 0),
        ("P7", "none", 0),
        ("P8", "fixture_change", 1),
    ]
    check(f"placement sequence == {expected}", got == expected)
    print("  got:", got)


if __name__ == "__main__":
    test_example_1()
    test_example_2()
    print("\n[OK] Both worked examples reproduced exactly.")
