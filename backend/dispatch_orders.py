"""
dispatch_orders.py — Engine 1 Model E final assembly, layer 1: turning raw
MCH_WIP + MCH_MACHINE_PRIORITY + MCH_ITEMWISE_FIXTURE_LOCATOR rows into one
ordered "operation chain" per PRODUCTION_ORDER.

Everything downstream (the event-driven dispatch loop — not yet built) will
consume `OrderChain` objects produced here, never raw WIP rows directly. This
module's job, and only this module's job:

  1. Classify every WIP operation row via `dispatch_scope.classify_operation`
     (CT=0 / balance<=0 / QAINSP / PN10 / no-routing / no-machine-for-combo /
     no-fixture-match / clean).
  2. Bridge precedence over every excluded row (CLAUDE.md §E.12): an order's
     `schedulable` chain contains ONLY its non-excluded operations, in
     ascending OPERATION_NO order — an excluded row still produces its own
     `ExcludedOperation` (REMARK explains why) but is never part of the chain
     precedence keys off.
  3. Resolve each schedulable operation's real candidate-machine list,
     including the §E.11 fixture/locator narrowing rule: CLAUDE.md's exact
     wording is "no row ... for ANY candidate machine" triggers the no-fixture
     fallback — meaning if even ONE candidate machine has a fixture/locator
     row, the fallback does NOT apply, and candidates that lack their OWN row
     (their own change-times, which are machine-specific even though
     FIXTURE/LOCATOR identity is constant across machines) are simply not
     usable for this operation — they're dropped from the candidate list
     rather than left in without the timing data needed to schedule them.

No Oracle/pandas dependency — this module accepts plain dataclasses so it
stays unit-testable; the real orchestrator adapts pandas DataFrames into
`RawWipRow`/`RoutingIndex`/`FixtureIndex` once (see their docstrings for the
exact shape expected).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from batch_grouping import compute_batch_key
from dispatch_scope import ScopeCheckInputs, ScopeOutcome, classify_operation, is_excluded, remark_for


# ─────────────────────────────────────────────────────────────────────────────
# Inputs
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class RawWipRow:
    """One MCH_WIP operation row, with balance_qty already computed upstream
    (QUANTITY_ORDERED - QUANTITY_COMPLETED - QUANTITY_REJECTED(null->0))."""

    production_order: str
    operation_no: float
    task: str
    work_center: Optional[str]  # ERP-assigned, informational; only used for the QAINSP check
    size_inch: str
    class_val: str
    moc: str
    design: str
    cycle_time: float
    balance_qty: int
    cdd: Optional[date]
    order_date: Optional[datetime]
    order_status: str  # 'Active' | 'Planned'
    production_start_date: date  # PRODUCTION_START_DATE_AND_TIME, date part


@dataclass(frozen=True)
class RoutingIndex:
    """
    Built once from MCH_MACHINE_PRIORITY.

    by_combo: (size, class, moc, design, task) -> [(machine, machine_priority), ...]
    tasks_with_any_routing: every TASK that appears anywhere in the table at
    all — distinct from "has a row for this exact combo" (see EXCLUDED_NO_ROUTING
    vs. EXCLUDED_NO_MACHINE_FOR_COMBO in dispatch_scope.py).
    """

    by_combo: dict[tuple[str, str, str, str, str], list[tuple[str, int]]] = field(default_factory=dict)
    tasks_with_any_routing: frozenset[str] = frozenset()

    def candidates_for(self, size_inch: str, class_val: str, moc: str, design: str, task: str) -> list[tuple[str, int]]:
        return self.by_combo.get((size_inch, class_val, moc, design, task), [])

    def has_routing_entry(self, task: str) -> bool:
        return task in self.tasks_with_any_routing


@dataclass(frozen=True)
class FixtureTiming:
    fixture: str
    locator: str
    fixture_change_time: float
    locator_change_time: float
    load_unload_time: float


@dataclass(frozen=True)
class FixtureIndex:
    """Built once from MCH_ITEMWISE_FIXTURE_LOCATOR: (size, class, moc, design, task, machine) -> FixtureTiming."""

    by_combo_machine: dict[tuple[str, str, str, str, str, str], FixtureTiming] = field(default_factory=dict)

    def lookup(self, size_inch: str, class_val: str, moc: str, design: str, task: str, machine: str) -> Optional[FixtureTiming]:
        return self.by_combo_machine.get((size_inch, class_val, moc, design, task, machine))


# ─────────────────────────────────────────────────────────────────────────────
# Outputs
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class CandidateMachine:
    """One machine capable of running a specific schedulable operation."""

    machine: str
    machine_priority: int
    fixture: Optional[str] = None
    locator: Optional[str] = None
    fixture_change_time: Optional[float] = None
    locator_change_time: Optional[float] = None
    load_unload_time: Optional[float] = None

    @property
    def has_fixture_locator(self) -> bool:
        return self.fixture is not None


@dataclass(frozen=True)
class ScheduledOperation:
    """
    One WIP operation row that survived classification as SCHEDULED or
    SCHEDULED_NO_FIXTURE — it participates in the dispatch simulation
    (batching, machine selection, timeline, pool).
    """

    production_order: str
    operation_no: float
    task: str
    batch_key: str  # SIZE_INCH~CLASS~MOC~DESIGN
    balance_qty: int
    cycle_time: float
    cdd: Optional[date]
    order_date: Optional[datetime]
    order_status: str
    production_start_date: date
    candidates: list[CandidateMachine]
    scope_outcome: ScopeOutcome  # SCHEDULED or SCHEDULED_NO_FIXTURE
    remark: Optional[str]

    @property
    def is_safety_stock(self) -> bool:
        return self.cdd is None


@dataclass(frozen=True)
class ExcludedOperation:
    """
    A WIP row classified as excluded — never enters the simulation, but still
    produces its own MCH_SCHEDULE_OUTPUT row (WORK_CENTER/SHIFT/SCHEDULED_DATE/
    fixture fields NULL, REMARK explains why — CLAUDE.md §E.12).

    batch_key and is_safety_stock are informational — both are derivable
    straight from the WIP row regardless of scheduling outcome, so an
    excluded row still carries them (a planner scanning the output shouldn't
    have to guess whether an excluded row was safety stock).
    """

    production_order: str
    operation_no: float
    task: str
    balance_qty: int
    batch_key: str
    is_safety_stock: bool
    scope_outcome: ScopeOutcome
    remark: str


@dataclass(frozen=True)
class OrderChain:
    """
    One PRODUCTION_ORDER's full operation list, split into the schedulable
    chain (ascending operation_no; precedence is defined ONLY over this list,
    bridging transparently over every excluded operation) and the excluded
    rows (still emitted as output rows, never simulated).
    """

    production_order: str
    schedulable: list[ScheduledOperation]
    excluded: list[ExcludedOperation]


# ─────────────────────────────────────────────────────────────────────────────
# Builder
# ─────────────────────────────────────────────────────────────────────────────
def _build_candidates(
    row: RawWipRow,
    routing_candidates: list[tuple[str, int]],
    fixture_index: FixtureIndex,
    outcome: ScopeOutcome,
) -> list[CandidateMachine]:
    if outcome == ScopeOutcome.SCHEDULED_NO_FIXTURE:
        # No candidate has fixture data at all (§E.11 fallback) — every
        # routing-listed machine remains eligible, plain routing only.
        return [CandidateMachine(machine=m, machine_priority=p) for m, p in routing_candidates]

    # outcome == SCHEDULED: at least one candidate has fixture data. Narrow
    # to exactly those candidates — a machine missing its own row can't be
    # scheduled here (its change-times are unknown), even though the same
    # FIXTURE/LOCATOR identity would conceptually apply to it too.
    candidates = []
    for machine, priority in routing_candidates:
        timing = fixture_index.lookup(row.size_inch, row.class_val, row.moc, row.design, row.task, machine)
        if timing is not None:
            candidates.append(
                CandidateMachine(
                    machine=machine,
                    machine_priority=priority,
                    fixture=timing.fixture,
                    locator=timing.locator,
                    fixture_change_time=timing.fixture_change_time,
                    locator_change_time=timing.locator_change_time,
                    load_unload_time=timing.load_unload_time,
                )
            )
    return candidates


def build_order_chains(
    rows: list[RawWipRow],
    routing_index: RoutingIndex,
    fixture_index: FixtureIndex,
) -> list[OrderChain]:
    """Classify and group every raw WIP row into one OrderChain per PRODUCTION_ORDER."""
    chains: dict[str, tuple[list[ScheduledOperation], list[ExcludedOperation]]] = {}

    for row in rows:
        schedulable_list, excluded_list = chains.setdefault(row.production_order, ([], []))

        routing_candidates = routing_index.candidates_for(row.size_inch, row.class_val, row.moc, row.design, row.task)
        has_fixture_match = any(
            fixture_index.lookup(row.size_inch, row.class_val, row.moc, row.design, row.task, m) is not None
            for m, _ in routing_candidates
        )

        inputs = ScopeCheckInputs(
            cycle_time=row.cycle_time,
            balance_qty=row.balance_qty,
            work_center=row.work_center,
            class_val=row.class_val,
            has_routing_entry=routing_index.has_routing_entry(row.task),
            has_machine_for_combo=len(routing_candidates) > 0,
            has_fixture_locator_match=has_fixture_match,
        )
        outcome = classify_operation(inputs)

        if is_excluded(outcome):
            excluded_list.append(
                ExcludedOperation(
                    production_order=row.production_order,
                    operation_no=row.operation_no,
                    task=row.task,
                    balance_qty=row.balance_qty,
                    batch_key=compute_batch_key(row.size_inch, row.class_val, row.moc, row.design),
                    is_safety_stock=row.cdd is None,
                    scope_outcome=outcome,
                    remark=remark_for(outcome),
                )
            )
            continue

        schedulable_list.append(
            ScheduledOperation(
                production_order=row.production_order,
                operation_no=row.operation_no,
                task=row.task,
                batch_key=compute_batch_key(row.size_inch, row.class_val, row.moc, row.design),
                balance_qty=row.balance_qty,
                cycle_time=row.cycle_time,
                cdd=row.cdd,
                order_date=row.order_date,
                order_status=row.order_status,
                production_start_date=row.production_start_date,
                candidates=_build_candidates(row, routing_candidates, fixture_index, outcome),
                scope_outcome=outcome,
                remark=remark_for(outcome),
            )
        )

    return [
        OrderChain(
            production_order=order,
            schedulable=sorted(sched, key=lambda op: op.operation_no),
            excluded=sorted(exc, key=lambda op: op.operation_no),
        )
        for order, (sched, exc) in chains.items()
    ]
