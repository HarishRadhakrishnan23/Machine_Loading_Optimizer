"""
dispatch_batching.py — Engine 1 Model E: the priority-walk batching algorithm
(CLAUDE.md "Engine 1 — Model E" §E.2–E.4).

Scope of this module ONLY: given a pool of same-TASK order-operations that are
already fixture/locator-matched (one FIXTURE + one LOCATOR per order, looked up
from MCH_ITEMWISE_FIXTURE_LOCATOR upstream of this module), decide the order they
are pipelined in and what each one costs to place — fixture change, locator
change, or free (load/unload only).

Deliberately NOT in scope here (later Model E sub-phases, layered on top):
  - machine selection / free-earliest-machine picking (§E.5)
  - the 60-day consolidation window + committed-order-safe override (§E.6)
  - the continuous machine timeline, shift pause/resume, cooling (§E.7)
  - Planned-order release gating (§E.8)
  - the fixture/locator pool concurrency cap (§E.9)
  - safety-stock / heavy-ops policy (§E.10)
  - the no-fixture-match fallback and REMARK taxonomy (§E.11/§E.12)

This module is pure, deterministic, and has no I/O — it operates on plain
dataclasses so it can be unit-tested against the two worked examples in
`Chat Reports/Engine1_Finallogic(BOSS)` without touching Oracle, machine
timelines, or the fixture/locator pool. See
backend/testing/test_dispatch_batching.py, which reproduces both examples
exactly — CLAUDE.md requires this before the batching engine is considered
correct.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Optional

from batch_grouping import compute_batch_key

ChargeType = Literal["fixture_change", "locator_change", "none"]


@dataclass(frozen=True)
class BatchableOrder:
    """
    One order-operation eligible for the priority-walk. Already fixture/locator-
    matched (§E.11's no-match fallback is handled upstream, before this module —
    an order with no fixture/locator match never enters this pool at all).

    `priority` is a rank, not a raw value to compare across pools — lower means
    higher priority (CLAUDE.md §E.2: lower CDD first, ties by ageing). Building
    that rank (from CDD + order_date) is the caller's job, not this module's.
    """

    order_id: str
    priority: int
    size_inch: str
    class_val: str
    moc: str
    design: str
    qty: int
    fixture: str
    locator: str

    @property
    def batch_key(self) -> str:
        return compute_batch_key(self.size_inch, self.class_val, self.moc, self.design)


@dataclass(frozen=True)
class ChargedStep:
    """One order placed into a fixture run, with the charge applied to place it."""

    order_id: str
    fixture: str
    locator: str
    charge_type: ChargeType
    run_index: int  # 0-based — which fixture run this step belongs to


@dataclass(frozen=True)
class FixtureLocatorTimes:
    """Per-(SCMD, TASK, machine) timing constants from MCH_ITEMWISE_FIXTURE_LOCATOR."""

    fixture_change_time: float
    locator_change_time: float
    load_unload_time: float


def compute_batch_duration(charge_type: ChargeType, qty: int, times: FixtureLocatorTimes) -> float:
    """
    CLAUDE.md §E.3/§E.4 timing formula. Excludes CYCLE_TIME — that is per-piece
    machining time added on top by the caller, unaffected by fixture/locator logic.
    """
    if charge_type == "fixture_change":
        return times.fixture_change_time + times.locator_change_time + times.load_unload_time * qty
    if charge_type == "locator_change":
        return times.locator_change_time + times.load_unload_time * qty
    if charge_type == "none":
        return times.load_unload_time * qty
    raise ValueError(f"Unknown charge_type: {charge_type!r}")


def run_priority_walk(
    orders: list[BatchableOrder],
    is_consolidation_eligible: Callable[[BatchableOrder], bool] = lambda order: True,
) -> list[ChargedStep]:
    """
    CLAUDE.md §E.4 — the priority-walk batching algorithm.

    Walk a same-TASK pool of fixture/locator-matched orders in strict priority
    order. For the fixture run currently being built, search the remaining pool
    in this preference order for the next order to append:

      a. exact same batch_key (SIZE~CLASS~MOC~DESIGN), same fixture AND same
         locator already active                              -> "none"
      b. else same fixture + same locator combo (any batch_key)  -> "none"
      c. else same fixture, different locator, highest priority
         among same-fixture candidates                        -> "locator_change"
         (starts a new locator sub-batch; (a)/(b) are retried inside it before
         falling back to (c) again)
      d. nothing left shares this fixture -> close the run, start a new one
         from the next-highest-priority remaining order        -> "fixture_change"

    `is_consolidation_eligible` is CLAUDE.md §E.6's consolidation window — a
    HARD rule, no override (see dispatch_consolidation_window.py). It is
    consulted only when searching for an order to APPEND to an already-
    started run (steps a/b/c); starting a fresh run of one's own (step 1, the
    "anchor") is never gated by the window — that isn't consolidation, it's
    just this order taking its own priority turn. An order beyond the window
    is simply skipped for this search — it stays in the pool and gets its own
    anchor turn later (it is never dropped, just never consolidated with
    something further out than the window allows). Defaults to "always
    eligible" so callers that don't care about the window (e.g. the
    worked-example tests) see unchanged behaviour.

    Returns the full placement sequence as a flat list of ChargedStep, in the
    order each order was pipelined (not necessarily priority order, once inside
    a run — batching deliberately pulls a lower-priority same-fixture/locator
    match ahead of a higher-priority order on a different fixture).
    """
    remaining = sorted(orders, key=lambda o: o.priority)
    steps: list[ChargedStep] = []
    run_index = -1

    while remaining:
        run_index += 1
        anchor = remaining.pop(0)
        steps.append(ChargedStep(anchor.order_id, anchor.fixture, anchor.locator, "fixture_change", run_index))

        current_fixture = anchor.fixture
        current_locator = anchor.locator
        current_batch_key = anchor.batch_key

        while True:
            pool = [o for o in remaining if is_consolidation_eligible(o)]

            # (a) exact same batch_key, same fixture + locator already active.
            match = next(
                (
                    o
                    for o in pool
                    if o.fixture == current_fixture
                    and o.locator == current_locator
                    and o.batch_key == current_batch_key
                ),
                None,
            )
            charge: ChargeType = "none"

            # (b) same fixture + locator combo, any batch_key (still free).
            if match is None:
                match = next(
                    (o for o in pool if o.fixture == current_fixture and o.locator == current_locator),
                    None,
                )

            # (c) same fixture, different locator — highest priority among
            # same-fixture candidates (list is priority-sorted, so first hit wins).
            if match is None:
                match = next((o for o in pool if o.fixture == current_fixture), None)
                if match is not None:
                    charge = "locator_change"

            if match is None:
                break  # (d) nothing eligible left shares this fixture — close the run.

            remaining.remove(match)
            steps.append(ChargedStep(match.order_id, match.fixture, match.locator, charge, run_index))
            current_locator = match.locator
            current_batch_key = match.batch_key

    return steps
