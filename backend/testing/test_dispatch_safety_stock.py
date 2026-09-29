"""
test_dispatch_safety_stock.py — CLAUDE.md §E.10 safety-stock policy on heavy
operations.

Run: backend/venv/Scripts/python.exe backend/testing/test_dispatch_safety_stock.py
"""

import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from dispatch_safety_stock import (
    HEAVY_OPERATIONS_DEFAULT,
    decide_safety_stock_placement,
    is_heavy_operation,
    split_committed_and_safety,
)


@dataclass(frozen=True)
class FakeOrder:
    order_id: str
    cdd: Optional[date]


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def _raise_if_called():
    raise AssertionError("committed_safe_check must not be called for a light operation")


def test_is_heavy_operation():
    print("\n=== is_heavy_operation matches CLAUDE.md's list exactly ===")
    for task in ("VB03", "VB04", "VB05", "VB06"):
        check(f"{task} is heavy", is_heavy_operation(task, HEAVY_OPERATIONS_DEFAULT))
    for task in ("VB02", "VB07", "VB09", "R002"):
        check(f"{task} is light", not is_heavy_operation(task, HEAVY_OPERATIONS_DEFAULT))


def test_split_committed_and_safety():
    print("\n=== split_committed_and_safety partitions by CDD ===")
    orders = [
        FakeOrder("O1", date(2026, 5, 1)),
        FakeOrder("O2", None),
        FakeOrder("O3", date(2026, 6, 1)),
        FakeOrder("O4", None),
    ]
    committed, safety = split_committed_and_safety(orders)
    check("committed == [O1, O3]", [o.order_id for o in committed] == ["O1", "O3"])
    check("safety == [O2, O4]", [o.order_id for o in safety] == ["O2", "O4"])


def test_light_operation_always_appends_without_consulting_check():
    print("\n=== Light operation: always append, check never consulted ===")
    decision = decide_safety_stock_placement("VB02", HEAVY_OPERATIONS_DEFAULT, _raise_if_called)
    check("append_now is True", decision.append_now is True)


def test_heavy_operation_appends_when_safe():
    print("\n=== Heavy operation, safe to append: append_now True ===")
    decision = decide_safety_stock_placement("VB04", HEAVY_OPERATIONS_DEFAULT, lambda: True)
    check("append_now is True", decision.append_now is True)


def test_heavy_operation_defers_when_unsafe():
    print("\n=== Heavy operation, would delay a committed order: deferred ===")
    decision = decide_safety_stock_placement("VB05", HEAVY_OPERATIONS_DEFAULT, lambda: False)
    check("append_now is False", decision.append_now is False)
    check("reason mentions deferral", "deferred" in decision.reason)


def test_protect_committed_over_safety_must_be_true():
    print("\n=== protect_committed_over_safety disabled: refuses to run ===")
    try:
        decide_safety_stock_placement("VB03", HEAVY_OPERATIONS_DEFAULT, lambda: True, protect_committed_over_safety=False)
        raised = False
    except ValueError:
        raised = True
    check("ValueError raised", raised)


if __name__ == "__main__":
    test_is_heavy_operation()
    test_split_committed_and_safety()
    test_light_operation_always_appends_without_consulting_check()
    test_heavy_operation_appends_when_safe()
    test_heavy_operation_defers_when_unsafe()
    test_protect_committed_over_safety_must_be_true()
    print("\n[OK] Safety-stock policy behaves per CLAUDE.md §E.10.")
