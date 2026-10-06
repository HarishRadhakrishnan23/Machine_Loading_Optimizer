"""
test_dispatch_scope.py — CLAUDE.md §E.11/§E.12 scope filters + REMARK taxonomy.

Run: backend/venv/Scripts/python.exe backend/testing/test_dispatch_scope.py
"""

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dispatch_scope import ScopeCheckInputs, ScopeOutcome, classify_operation, is_excluded, remark_for


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


# A row that would cleanly pass every gate — each test mutates one field off it.
CLEAN = ScopeCheckInputs(
    cycle_time=5.0,
    balance_qty=10,
    work_center="2PMC16",
    class_val="150",
    has_routing_entry=True,
    has_machine_for_combo=True,
    has_fixture_locator_match=True,
)


def test_clean_row_is_scheduled():
    print("\n=== A row passing every gate: SCHEDULED, no REMARK ===")
    outcome = classify_operation(CLEAN)
    check("outcome == SCHEDULED", outcome == ScopeOutcome.SCHEDULED)
    check("REMARK is None", remark_for(outcome) is None)
    check("not excluded", not is_excluded(outcome))


def test_ct_zero_excluded():
    print("\n=== CYCLE_TIME = 0 ===")
    outcome = classify_operation(replace(CLEAN, cycle_time=0.0))
    check("outcome == EXCLUDED_CT_ZERO", outcome == ScopeOutcome.EXCLUDED_CT_ZERO)
    check('REMARK == "CT = 0 - excluded"', remark_for(outcome) == "CT = 0 - excluded")
    check("is excluded", is_excluded(outcome))


def test_balance_zero_excluded():
    print("\n=== Balance Qty <= 0 ===")
    outcome = classify_operation(replace(CLEAN, balance_qty=0))
    check("outcome == EXCLUDED_BALANCE_ZERO", outcome == ScopeOutcome.EXCLUDED_BALANCE_ZERO)
    check("is excluded", is_excluded(outcome))


def test_qainsp_excluded_case_insensitive():
    print("\n=== QAINSP work center (case-insensitive) ===")
    outcome = classify_operation(replace(CLEAN, work_center="3qainsp01"))
    check("outcome == EXCLUDED_QAINSP", outcome == ScopeOutcome.EXCLUDED_QAINSP)
    check("is excluded", is_excluded(outcome))


def test_pn10_excluded():
    print("\n=== CLASS = PN10 ===")
    outcome = classify_operation(replace(CLEAN, class_val="PN10"))
    check("outcome == EXCLUDED_PN10", outcome == ScopeOutcome.EXCLUDED_PN10)
    check("is excluded", is_excluded(outcome))


def test_no_routing_entry_excluded_and_bridged():
    print("\n=== No routing entry for this TASK at all ===")
    outcome = classify_operation(replace(CLEAN, has_routing_entry=False, has_machine_for_combo=False))
    check("outcome == EXCLUDED_NO_ROUTING", outcome == ScopeOutcome.EXCLUDED_NO_ROUTING)
    check("is excluded (bridged in precedence)", is_excluded(outcome))


def test_no_machine_for_exact_combo_gets_its_own_remark():
    print("\n=== TASK routable in general, but not for this exact SCMD (distinct from no-routing) ===")
    outcome = classify_operation(replace(CLEAN, has_machine_for_combo=False))
    check("outcome == EXCLUDED_NO_MACHINE_FOR_COMBO", outcome == ScopeOutcome.EXCLUDED_NO_MACHINE_FOR_COMBO)
    check("distinct REMARK from EXCLUDED_NO_ROUTING", remark_for(outcome) != remark_for(ScopeOutcome.EXCLUDED_NO_ROUTING))
    check("is excluded", is_excluded(outcome))


def test_no_fixture_match_is_scheduled_with_caveat():
    print("\n=== No fixture/locator match: SCHEDULED anyway, REMARK is a caveat not an exclusion ===")
    outcome = classify_operation(replace(CLEAN, has_fixture_locator_match=False))
    check("outcome == SCHEDULED_NO_FIXTURE", outcome == ScopeOutcome.SCHEDULED_NO_FIXTURE)
    check("NOT excluded - this row still gets a machine", not is_excluded(outcome))
    check(
        'REMARK == "No fixture/locator match - scheduled via plain routing"',
        remark_for(outcome) == "No fixture/locator match - scheduled via plain routing",
    )


def test_first_matching_gate_wins_when_multiple_fail():
    print("\n=== Multiple gates fail at once: CT=0 takes precedence over everything else ===")
    outcome = classify_operation(replace(CLEAN, cycle_time=0.0, balance_qty=0, class_val="PN10"))
    check("outcome == EXCLUDED_CT_ZERO (first gate in CLAUDE.md's order)", outcome == ScopeOutcome.EXCLUDED_CT_ZERO)


if __name__ == "__main__":
    test_clean_row_is_scheduled()
    test_ct_zero_excluded()
    test_balance_zero_excluded()
    test_qainsp_excluded_case_insensitive()
    test_pn10_excluded()
    test_no_routing_entry_excluded_and_bridged()
    test_no_machine_for_exact_combo_gets_its_own_remark()
    test_no_fixture_match_is_scheduled_with_caveat()
    test_first_matching_gate_wins_when_multiple_fail()
    print("\n[OK] Scope filters + REMARK taxonomy behave per CLAUDE.md §E.11/§E.12.")
