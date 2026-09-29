"""
dispatch_safety_stock.py — Engine 1 Model E: safety-stock policy on heavy
operations (CLAUDE.md §E.10).

A fixture+locator sub-batch can mix committed (CDD not null) and safety-stock
(CDD null) pieces. What happens to the safety-stock portion depends on the
operation:

  - Light operations (everything not in `heavy_operations`): committed and
    safety-stock pieces batch together normally, no special ordering — the
    marginal cost of including safety stock is only LOAD_UNLOAD_TIME × qty.
  - Heavy operations (`["VB03", "VB04", "VB05", "VB06"]` — Weld Overlay, Stem
    Boring, Cone Finishing, Stem Boring + Cone Finishing): committed pieces
    are scheduled first. The safety-stock pieces are appended immediately
    after (free — same fixture/locator already mounted) ONLY IF doing so
    delays no committed order, anywhere in the plant, past its own CDD. If it
    would, the fixture/locator is released and the safety-stock pieces are
    re-queued at the global tail — picked up later whenever their
    fixture/locator naturally comes free again (never dropped, just deferred).

`protect_committed_over_safety` is absolute — CLAUDE.md is explicit it "must
always be true". There is deliberately no parameter here that can make a
committed-delay-causing append happen anyway; the config flag is accepted only
so a caller passing `False` fails loudly rather than silently disabling the
protection.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional, Protocol


HEAVY_OPERATIONS_DEFAULT: tuple[str, ...] = ("VB03", "VB04", "VB05", "VB06")


class HasCdd(Protocol):
    cdd: Optional[date]


def is_heavy_operation(task: str, heavy_operations: tuple[str, ...] = HEAVY_OPERATIONS_DEFAULT) -> bool:
    return task in heavy_operations


def split_committed_and_safety(orders: list[HasCdd]) -> tuple[list[HasCdd], list[HasCdd]]:
    """
    Split a fixture+locator sub-batch's members into (committed, safety_stock)
    by CDD. Works with any object exposing a `.cdd` attribute (Optional[date]);
    CDD is None -> safety stock (CLAUDE.md: CDD = NULL => safety-stock order).
    """
    committed = [o for o in orders if o.cdd is not None]
    safety_stock = [o for o in orders if o.cdd is None]
    return committed, safety_stock


@dataclass(frozen=True)
class SafetyStockDecision:
    append_now: bool  # True: append the safety-stock portion right after committed, for free
    reason: str


def decide_safety_stock_placement(
    task: str,
    heavy_operations: tuple[str, ...],
    committed_safe_check: Callable[[], bool],
    protect_committed_over_safety: bool = True,
) -> SafetyStockDecision:
    """
    CLAUDE.md §E.10 — the placement decision for a sub-batch's safety-stock
    portion, once its committed portion has already been scheduled first.

    Light operations: always append now — `committed_safe_check` is never
    called (it's the expensive, simulation-backed path, and light operations
    never need it).

    Heavy operations: append now only if `committed_safe_check()` returns
    True — i.e. appending the safety-stock pieces delays no committed order
    anywhere in the plant past its own CDD. Otherwise, defer to the global
    tail (`append_now=False`); this is never dropped, only re-queued for a
    later pass whenever the same fixture/locator is next free.

    Raises ValueError immediately if `protect_committed_over_safety` is not
    True — CLAUDE.md states this must always hold; there is no legitimate
    way to call this function with it disabled.
    """
    if not protect_committed_over_safety:
        raise ValueError(
            "protect_committed_over_safety must always be True (CLAUDE.md §E.10) — "
            "refusing to evaluate safety-stock placement with it disabled"
        )

    if not is_heavy_operation(task, heavy_operations):
        return SafetyStockDecision(append_now=True, reason="light operation — safety stock batches normally")

    if committed_safe_check():
        return SafetyStockDecision(
            append_now=True,
            reason="heavy operation — appending safety stock delays no committed order",
        )

    return SafetyStockDecision(
        append_now=False,
        reason="heavy operation — appending would delay a committed order; deferred to the global tail",
    )
