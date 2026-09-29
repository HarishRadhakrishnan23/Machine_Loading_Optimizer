"""
test_dispatch_orders.py — Engine 1 Model E final assembly, layer 1: raw WIP
rows -> classified, precedence-bridged OrderChains.

Run: backend/venv/Scripts/python.exe backend/testing/test_dispatch_orders.py
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dispatch_orders import (
    FixtureIndex,
    FixtureTiming,
    RawWipRow,
    RoutingIndex,
    build_order_chains,
)
from dispatch_scope import ScopeOutcome


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def row(production_order, operation_no, task, **overrides):
    defaults = dict(
        production_order=production_order,
        operation_no=operation_no,
        task=task,
        work_center="2PMC16",
        size_inch="10",
        class_val="150",
        moc="CS",
        design="DFS",
        cycle_time=5.0,
        balance_qty=10,
        cdd=date(2026, 6, 1),
        order_date=None,
        order_status="Active",
        production_start_date=date(2026, 1, 1),
    )
    defaults.update(overrides)
    return RawWipRow(**defaults)


COMBO = ("10", "150", "CS", "DFS", "VB04")


def test_clean_row_is_scheduled_with_full_fixture_candidates():
    print("\n=== Clean row: SCHEDULED, candidates carry fixture/locator timing ===")
    routing = RoutingIndex(by_combo={COMBO: [("2PMC16", 1), ("2PMC17", 2)]}, tasks_with_any_routing=frozenset({"VB04"}))
    fixtures = FixtureIndex(
        by_combo_machine={
            (*COMBO, "2PMC16"): FixtureTiming("FIX-A", "LOC-1", 30.0, 10.0, 2.0),
            (*COMBO, "2PMC17"): FixtureTiming("FIX-A", "LOC-1", 30.0, 10.0, 2.5),
        }
    )
    chains = build_order_chains([row("O1", 40, "VB04")], routing, fixtures)
    op = chains[0].schedulable[0]
    check("outcome == SCHEDULED", op.scope_outcome == ScopeOutcome.SCHEDULED)
    check("no excluded rows", chains[0].excluded == [])
    check("both candidates present", {c.machine for c in op.candidates} == {"2PMC16", "2PMC17"})
    check("candidates carry fixture identity", all(c.fixture == "FIX-A" for c in op.candidates))
    check("batch_key == 10~150~CS~DFS", op.batch_key == "10~150~CS~DFS")


def test_ct_zero_is_excluded_not_scheduled():
    print("\n=== CYCLE_TIME = 0: excluded, never enters schedulable chain ===")
    routing = RoutingIndex(by_combo={COMBO: [("2PMC16", 1)]}, tasks_with_any_routing=frozenset({"VB04"}))
    chains = build_order_chains([row("O1", 40, "VB04", cycle_time=0.0)], routing, FixtureIndex())
    check("schedulable is empty", chains[0].schedulable == [])
    check("excluded has 1 row", len(chains[0].excluded) == 1)
    check("outcome == EXCLUDED_CT_ZERO", chains[0].excluded[0].scope_outcome == ScopeOutcome.EXCLUDED_CT_ZERO)


def test_no_machine_for_exact_combo():
    print("\n=== TASK routable elsewhere, but not for this exact SCMD ===")
    routing = RoutingIndex(by_combo={}, tasks_with_any_routing=frozenset({"VB04"}))
    chains = build_order_chains([row("O1", 40, "VB04")], routing, FixtureIndex())
    check("excluded outcome == EXCLUDED_NO_MACHINE_FOR_COMBO", chains[0].excluded[0].scope_outcome == ScopeOutcome.EXCLUDED_NO_MACHINE_FOR_COMBO)


def test_no_fixture_match_anywhere_keeps_all_routing_candidates():
    print("\n=== No candidate has fixture data at all: SCHEDULED_NO_FIXTURE, all routing candidates kept ===")
    routing = RoutingIndex(by_combo={COMBO: [("2PMC16", 1), ("2PMC17", 2)]}, tasks_with_any_routing=frozenset({"VB04"}))
    chains = build_order_chains([row("O1", 40, "VB04")], routing, FixtureIndex())  # empty fixture index
    op = chains[0].schedulable[0]
    check("outcome == SCHEDULED_NO_FIXTURE", op.scope_outcome == ScopeOutcome.SCHEDULED_NO_FIXTURE)
    check("both candidates kept, no fixture info", {c.machine for c in op.candidates} == {"2PMC16", "2PMC17"})
    check("no candidate has fixture identity", all(not c.has_fixture_locator for c in op.candidates))


def test_partial_fixture_coverage_narrows_to_the_matching_machine():
    print("\n=== 2PMC16 has fixture data, 2PMC17 doesn't: SCHEDULED, narrowed to 2PMC16 only ===")
    routing = RoutingIndex(by_combo={COMBO: [("2PMC16", 1), ("2PMC17", 2)]}, tasks_with_any_routing=frozenset({"VB04"}))
    fixtures = FixtureIndex(by_combo_machine={(*COMBO, "2PMC16"): FixtureTiming("FIX-A", "LOC-1", 30.0, 10.0, 2.0)})
    chains = build_order_chains([row("O1", 40, "VB04")], routing, fixtures)
    op = chains[0].schedulable[0]
    check("outcome == SCHEDULED (fixture exists somewhere)", op.scope_outcome == ScopeOutcome.SCHEDULED)
    check("candidates narrowed to just 2PMC16", [c.machine for c in op.candidates] == ["2PMC16"])


def test_precedence_bridges_over_excluded_ops_and_sorts_ascending():
    print("\n=== Multi-op order: excluded op bridged, schedulable chain sorted ascending ===")
    routing = RoutingIndex(
        by_combo={
            ("10", "150", "CS", "DFS", "VB02"): [("2PMC16", 1)],
            ("10", "150", "CS", "DFS", "VB04"): [("2PMC16", 1)],
        },
        tasks_with_any_routing=frozenset({"VB02", "VB04"}),
    )
    fixtures = FixtureIndex()  # no fixture rows -> both ops SCHEDULED_NO_FIXTURE, that's fine for this test
    rows = [
        row("O1", 30, "VB03", work_center="3QAINSP01"),  # QA gate, excluded, sits BETWEEN the two real ops
        row("O1", 10, "VB02"),
        row("O1", 20, "VB04"),  # deliberately out of input order
    ]
    chains = build_order_chains(rows, routing, fixtures)
    chain = chains[0]
    check("schedulable == [op10 (VB02), op20 (VB04)] in ascending order", [op.operation_no for op in chain.schedulable] == [10, 20])
    check("excluded == [op30 (VB03, QAINSP)]", [op.operation_no for op in chain.excluded] == [30])
    check("excluded op's outcome is QAINSP", chain.excluded[0].scope_outcome == ScopeOutcome.EXCLUDED_QAINSP)


def test_multiple_orders_grouped_independently():
    print("\n=== Two different orders never mix chains ===")
    routing = RoutingIndex(by_combo={COMBO: [("2PMC16", 1)]}, tasks_with_any_routing=frozenset({"VB04"}))
    fixtures = FixtureIndex()
    rows = [row("O1", 40, "VB04"), row("O2", 40, "VB04")]
    chains = build_order_chains(rows, routing, fixtures)
    orders = {c.production_order for c in chains}
    check("both orders present", orders == {"O1", "O2"})
    check("each has exactly 1 schedulable op", all(len(c.schedulable) == 1 for c in chains))


if __name__ == "__main__":
    test_clean_row_is_scheduled_with_full_fixture_candidates()
    test_ct_zero_is_excluded_not_scheduled()
    test_no_machine_for_exact_combo()
    test_no_fixture_match_anywhere_keeps_all_routing_candidates()
    test_partial_fixture_coverage_narrows_to_the_matching_machine()
    test_precedence_bridges_over_excluded_ops_and_sorts_ascending()
    test_multiple_orders_grouped_independently()
    print("\n[OK] Order-chain construction behaves per CLAUDE.md §E.11/§E.12 (final assembly, layer 1).")
