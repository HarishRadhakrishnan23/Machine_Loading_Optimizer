"""
dispatch_engine.py — Engine 1 Model E final assembly, layer 2: the dispatch
step that places one TASK's ready pool onto machines.

This is the piece that actually ties every already-built module together for
ONE round of ONE task: priority ranking (§E.2), the consolidation window
(§E.6, hard rule), the priority-walk batching algorithm (§E.4), machine
selection (§E.5), the continuous timeline with cooling (§E.7), and the
fixture/locator pool constraint (§E.9). `SCHEDULED_NO_FIXTURE` operations
(§E.11's fallback) bypass batching and the pool entirely, exactly as
specified — they're placed individually via plain routing.

Scope deliberately NOT covered here (the outer multi-round loop lives in
dispatch_orchestrator.py):

  - Planned-order release gating (§E.1/§E.8) — the caller must not present an
    order to `dispatch_task_pool` before its earliest-start has passed;
    that's the outer loop's job, not this function's.
  - The §E.10 safety-stock append/defer DECISION itself — this module only
    provides the mechanism (`resolve_safety_stock`, a caller-supplied
    predicate) and the "defer" consequence (a solo re-run, same path as a
    machine-selection dropout). The actual full speculative check — forking
    simulation state and running two continuations in parallel — needs the
    outer loop to exist, since only it can "run the rest of the schedule";
    see dispatch_orchestrator.py.
  - Locator-level pool contention is reserved directly, without the retry
    loop the fixture-level reservation below gets. A locator conflict INSIDE
    an already-accepted fixture run is expected to be rare in practice (the
    same locator being needed by two unrelated fixture runs at once), but
    this is a known simplification, not a proven-safe shortcut — flagging it
    for the validation phase (E-6).

Only the FIXTURE-level pool check gets the full "retry at the next release
hint" loop, because that's the one CLAUDE.md's own worked examples and D.8/
E.9 language center the pool constraint on (a run holds its fixture for its
entire span).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Optional

# Given a mixed heavy-op run's steps and the op lookup, decide whether to
# append the safety-stock portion for free (True) or defer it (False). See
# dispatch_orchestrator.py for the real implementation (the full speculative
# check); None here means "no resolver supplied — always append", which is
# what every existing caller/test still gets by default.
SafetyStockResolver = Callable[[list["ChargedStep"], dict[str, "ScheduledOperation"], str], bool]

from dispatch_batching import (
    BatchableOrder,
    ChargedStep,
    FixtureLocatorTimes,
    compute_batch_duration,
    run_priority_walk,
)
from dispatch_consolidation_window import is_consolidation_eligible as window_eligible
from dispatch_machine_selection import RunMember, select_machine
from dispatch_orders import ScheduledOperation
from dispatch_pool import DevicePool
from dispatch_timeline import AvailabilityFn, TimePoint, advance_clock, next_open_instant, start_of_day


@dataclass(frozen=True)
class PlacedOperation:
    """One schedulable operation, fully placed — the direct precursor of an
    MCH_SCHEDULE_OUTPUT row (REMARK / ORDER_COMPLETION_* are stamped later,
    by the outer loop, once an order's full chain is known)."""

    production_order: str
    operation_no: float
    task: str
    machine: str
    fixture_id: Optional[str]
    locator_id: Optional[str]
    start: TimePoint
    end: TimePoint
    balance_qty: int
    batch_key: str
    is_safety_stock: bool
    remark: Optional[str]


def compute_priority_ranks(ops: list[ScheduledOperation]) -> dict[str, int]:
    """
    CLAUDE.md §E.2: lower CDD -> higher priority; ties broken by ageing
    (older order_date first); safety stock (CDD = NULL) is always lowest
    priority. Returns production_order -> rank (0 = highest priority),
    scoped to just the pool passed in.
    """

    def sort_key(op: ScheduledOperation):
        return (op.cdd is None, op.cdd or date.max, op.order_date or datetime.max)

    ordered = sorted(ops, key=sort_key)
    return {op.production_order: rank for rank, op in enumerate(ordered)}


def _simulate_run_timing(
    op_by_id: dict[str, ScheduledOperation],
    steps: list[ChargedStep],
    machine: str,
    run_start: TimePoint,
    availability: AvailabilityFn,
) -> tuple[list[tuple[ChargedStep, TimePoint, TimePoint]], TimePoint]:
    """Pure timing simulation for one fixture run on `machine`, starting at `run_start`. No pool mutation."""
    cursor = run_start
    results: list[tuple[ChargedStep, TimePoint, TimePoint]] = []
    for step in steps:
        op = op_by_id[step.order_id]
        candidate = next(c for c in op.candidates if c.machine == machine)
        times = FixtureLocatorTimes(candidate.fixture_change_time, candidate.locator_change_time, candidate.load_unload_time)
        duration = compute_batch_duration(step.charge_type, op.balance_qty, times) + op.cycle_time * op.balance_qty
        end = advance_clock(cursor, duration, machine, availability)
        results.append((step, cursor, end))
        cursor = end
    return results, cursor


def _find_feasible_fixture_run(
    op_by_id: dict[str, ScheduledOperation],
    steps: list[ChargedStep],
    machine: str,
    fixture: str,
    earliest_start: TimePoint,
    availability: AvailabilityFn,
    pool: DevicePool,
    max_attempts: int = 200,
) -> tuple[list[tuple[ChargedStep, TimePoint, TimePoint]], TimePoint, TimePoint]:
    """
    CLAUDE.md §E.9 — the fixture concurrency retry loop: simulate the run's
    timing at a candidate start, check the fixture has capacity for that
    whole span, and if not, retry from the pool's earliest-release hint
    (never a guess — `has_capacity` is re-checked every attempt, per
    dispatch_pool.py's own contract). Returns (per-step results, run_start
    actually used, run_end).

    Uses the `_multi` pool methods — FIXTURE is never a "+"-joined compound
    in practice, but it CAN be the literal "NA" (confirmed real-data
    convention: some operations need no physical fixture at all), which
    `parse_devices` turns into an empty device list. Routing both fixture
    and locator through the same multi-aware methods means "no device
    needed" is handled uniformly rather than as a special case here.
    """
    candidate_start = earliest_start
    for _ in range(max_attempts):
        results, run_end = _simulate_run_timing(op_by_id, steps, machine, candidate_start, availability)
        if pool.has_capacity_multi(fixture, candidate_start, run_end):
            return results, candidate_start, run_end
        hint = pool.earliest_release_hint_multi(fixture, candidate_start)
        if hint is None:
            raise RuntimeError(f"Fixture {fixture!r} reported blocked with no release hint at {candidate_start}")
        candidate_start = next_open_instant(hint, machine, availability)
    raise RuntimeError(f"Could not find a feasible start for fixture {fixture!r} within {max_attempts} attempts")


def _recompute_charges(steps: list[ChargedStep]) -> list[ChargedStep]:
    """
    Re-derive charge_type for a surviving sub-sequence of a run after machine
    selection has dropped one or more members (CLAUDE.md §E.5's partial-
    coverage case). A dropped member can invalidate a SURVIVING member's
    original charge_type — e.g. a "none" that assumed a now-dropped
    predecessor's locator was still mounted actually needs "locator_change"
    once that predecessor is gone. Since fixture is constant for the whole
    run, charge_type is purely a function of whether the locator changed
    from the immediately preceding SURVIVING step, so this is always safe
    to recompute fresh from the filtered sequence — never patch just the
    first element and assume the rest still holds.
    """
    if not steps:
        return []
    result = [ChargedStep(steps[0].order_id, steps[0].fixture, steps[0].locator, "fixture_change", steps[0].run_index)]
    current_locator = steps[0].locator
    for s in steps[1:]:
        charge = "none" if s.locator == current_locator else "locator_change"
        result.append(ChargedStep(s.order_id, s.fixture, s.locator, charge, s.run_index))
        current_locator = s.locator
    return result


def _group_steps_by_run(steps: list[ChargedStep]) -> list[list[ChargedStep]]:
    runs: dict[int, list[ChargedStep]] = {}
    for step in steps:
        runs.setdefault(step.run_index, []).append(step)
    return [runs[i] for i in sorted(runs)]


def _place_fixture_run(
    run_steps: list[ChargedStep],
    op_by_id: dict[str, ScheduledOperation],
    ready_at: dict[str, TimePoint],
    machine_free_at: dict[str, TimePoint],
    pool: DevicePool,
    availability: AvailabilityFn,
    day_zero: date,
    cooling_minutes: float,
    heavy_operations: frozenset[str] = frozenset(),
    resolve_safety_stock: Optional[SafetyStockResolver] = None,
) -> list[PlacedOperation]:
    """
    Places one fixture run (possibly after machine selection drops some
    members for lack of a common capable machine — those are re-placed as
    their own single-member runs, recursively, via this same function).

    CLAUDE.md §E.10: if this run's TASK is a heavy operation and it mixes
    committed (CDD not null) and safety-stock (CDD null) members, and a
    resolver is supplied, ask it whether to append the safety-stock portion
    for free or defer it. Deferred safety-stock steps are handled by the
    exact same "solo re-run" path as a machine-selection dropout below —
    CLAUDE.md's "send it to the back of the global queue" becomes, in this
    round-based engine, "it pays its own changeover instead of riding the
    committed portion's mount for free," which is the same real-world
    consequence without needing a genuinely open-ended requeue mechanism.
    """
    deferred_steps: list[ChargedStep] = []
    if resolve_safety_stock is not None and run_steps:
        task = op_by_id[run_steps[0].order_id].task
        if task in heavy_operations:
            committed = [s for s in run_steps if op_by_id[s.order_id].cdd is not None]
            safety = [s for s in run_steps if op_by_id[s.order_id].cdd is None]
            if committed and safety and not resolve_safety_stock(run_steps, op_by_id, task):
                run_steps = committed
                deferred_steps = safety

    members = [
        RunMember(step.order_id, {c.machine: c.machine_priority for c in op_by_id[step.order_id].candidates})
        for step in run_steps
    ]
    # select_machine's own default for a never-seen machine is the bare int 0
    # (documented, type-agnostic — it doesn't know what "time zero" looks
    # like for this caller). This caller uses TimePoint, so a machine that's
    # never been used yet (absent from machine_free_at) must be seeded
    # BEFORE calling select_machine — comparing a TimePoint candidate against
    # a raw 0 default for a sibling candidate raises TypeError otherwise.
    for member in members:
        for machine in member.candidates:
            machine_free_at.setdefault(machine, start_of_day(day_zero))
    selection = select_machine(members, machine_free_at)
    machine = selection.machine

    covered_steps = _recompute_charges([s for s in run_steps if s.order_id in selection.covered_order_ids])
    dropped_steps = [s for s in run_steps if s.order_id in selection.dropped_order_ids]

    earliest_start = machine_free_at.get(machine, start_of_day(day_zero))
    for step in covered_steps:
        earliest_start = max(earliest_start, ready_at[step.order_id])

    fixture = covered_steps[0].fixture
    results, run_start, run_end = _find_feasible_fixture_run(
        op_by_id, covered_steps, machine, fixture, earliest_start, availability, pool
    )

    pool.reserve_multi(fixture, run_start, run_end)
    placed: list[PlacedOperation] = []
    for step, start, end in results:
        # per-slice, no retry — see module docstring. reserve_multi (not
        # reserve) because a LOCATOR value can be a "+"-joined compound of
        # two physical devices that must be held together (confirmed real
        # ERP convention, dispatch_pool.py) — FIXTURE never is, so it keeps
        # using the plain single-device reserve() above.
        pool.reserve_multi(step.locator, start, end)
        op = op_by_id[step.order_id]
        placed.append(
            PlacedOperation(
                production_order=op.production_order,
                operation_no=op.operation_no,
                task=op.task,
                machine=machine,
                fixture_id=step.fixture,
                locator_id=step.locator,
                start=start,
                end=end,
                balance_qty=op.balance_qty,
                batch_key=op.batch_key,
                is_safety_stock=op.is_safety_stock,
                remark=op.remark,
            )
        )
        ready_at[step.order_id] = end

    machine_free_at[machine] = advance_clock(run_end, cooling_minutes, machine, availability)

    for step in dropped_steps + deferred_steps:
        # Re-run each dropped/deferred member as its own single-member
        # fixture run — same mechanism, just a pool of one (it pays
        # fixture_change again, since it's no longer riding along with the
        # run it was dropped/deferred from).
        solo = ChargedStep(step.order_id, step.fixture, step.locator, "fixture_change", run_index=-1)
        placed.extend(
            _place_fixture_run(
                [solo], op_by_id, ready_at, machine_free_at, pool, availability, day_zero, cooling_minutes,
                heavy_operations, resolve_safety_stock,
            )
        )

    return placed


def _place_no_fixture_op(
    op: ScheduledOperation,
    ready_at: dict[str, TimePoint],
    machine_free_at: dict[str, TimePoint],
    availability: AvailabilityFn,
    day_zero: date,
    cooling_minutes: float,
) -> PlacedOperation:
    """CLAUDE.md §E.11 fallback — plain routing, no fixture/locator, no pool involvement."""
    candidates = {c.machine: c.machine_priority for c in op.candidates}
    for machine in candidates:
        machine_free_at.setdefault(machine, start_of_day(day_zero))  # see _place_fixture_run's comment
    selection = select_machine([RunMember(op.production_order, candidates)], machine_free_at)
    machine = selection.machine

    start = max(machine_free_at.get(machine, start_of_day(day_zero)), ready_at[op.production_order])
    duration = op.cycle_time * op.balance_qty
    end = advance_clock(start, duration, machine, availability)
    machine_free_at[machine] = advance_clock(end, cooling_minutes, machine, availability)
    ready_at[op.production_order] = end  # this order's NEXT schedulable op must not start before this one ends

    return PlacedOperation(
        production_order=op.production_order,
        operation_no=op.operation_no,
        task=op.task,
        machine=machine,
        fixture_id=None,
        locator_id=None,
        start=start,
        end=end,
        balance_qty=op.balance_qty,
        batch_key=op.batch_key,
        is_safety_stock=op.is_safety_stock,
        remark=op.remark,
    )


@dataclass(frozen=True)
class TaskPoolPlan:
    """
    The deterministic BATCHING decision for one (depth, task) pool — which
    orders group into which fixture runs, and which no-fixture ops are
    pending, in priority order. Entirely independent of machine/timeline/pool
    STATE (only priority ranks, fixture/locator identity, and the §E.6
    window — never `ready_at`/`machine_free_at`/pool occupancy — decide it),
    so it's always safe to compute up front, before anything is placed.

    Splitting this out from `dispatch_task_pool` is what lets the outer loop
    (dispatch_orchestrator.py) drive placement at individual-RUN granularity
    for the §E.10 speculative check — resuming a continuation only at the
    next whole TASK pool would miss a second run within the SAME pool
    competing for a machine the first run just occupied, which is exactly
    the kind of downstream effect that check exists to catch.
    """

    op_by_id: dict[str, ScheduledOperation]
    fixture_runs: list[list[ChargedStep]]
    no_fixture_ops: list[ScheduledOperation]  # already priority-sorted


def plan_task_pool(ops: list[ScheduledOperation], today: date, window_days: int) -> TaskPoolPlan:
    """Pure planning step — see `TaskPoolPlan`. No machine/timeline/pool involvement."""
    if not ops:
        return TaskPoolPlan({}, [], [])

    op_by_id = {op.production_order: op for op in ops}
    ranks = compute_priority_ranks(ops)

    fixture_ops = [op for op in ops if op.scope_outcome.value == "scheduled"]
    no_fixture_ops = [op for op in ops if op.scope_outcome.value == "scheduled_no_fixture"]

    fixture_runs: list[list[ChargedStep]] = []
    if fixture_ops:
        # run_priority_walk only ever compares BatchableOrder.batch_key for
        # equality (never displays or parses it), so it's safe to carry the
        # already-computed op.batch_key through as `size_inch` alone rather
        # than re-deriving size/class/moc/design, which this layer has no
        # other use for.
        batchable = [
            BatchableOrder(
                order_id=op.production_order,
                priority=ranks[op.production_order],
                size_inch=op.batch_key,
                class_val="",
                moc="",
                design="",
                qty=op.balance_qty,
                fixture=op.candidates[0].fixture,
                locator=op.candidates[0].locator,
            )
            for op in fixture_ops
        ]

        def _is_eligible(o: BatchableOrder) -> bool:
            # CLAUDE.md §E.6 gates committed-vs-committed consolidation across
            # a CDD gap; it does not gate a lowest-priority safety-stock order
            # (cdd is None) riding along AFTER a committed anchor — safety
            # stock can never itself anchor a run ahead of a committed order
            # (priority ranking guarantees that), so it can't "pull anything
            # forward" the way the window is meant to prevent. Whether it
            # actually gets appended is §E.10's call alone (dispatch_engine's
            # resolve_safety_stock hook), not this window check.
            cdd = op_by_id[o.order_id].cdd
            return cdd is None or window_eligible(cdd, today, window_days)

        steps = run_priority_walk(batchable, is_consolidation_eligible=_is_eligible)
        fixture_runs = _group_steps_by_run(steps)

    no_fixture_sorted = sorted(no_fixture_ops, key=lambda o: ranks[o.production_order])
    return TaskPoolPlan(op_by_id, fixture_runs, no_fixture_sorted)


def dispatch_task_pool(
    ops: list[ScheduledOperation],
    ready_at: dict[str, TimePoint],
    machine_free_at: dict[str, TimePoint],
    pool: DevicePool,
    availability: AvailabilityFn,
    today: date,
    window_days: int,
    cooling_minutes: float,
    day_zero: date,
    heavy_operations: frozenset[str] = frozenset(),
    resolve_safety_stock: Optional[SafetyStockResolver] = None,
) -> list[PlacedOperation]:
    """
    Place every operation in `ops` (all sharing one TASK, all already past
    their Planned-release gate — the caller's job) onto machines.

    Mutates `ready_at` (each placed order's entry becomes that operation's
    END — the readiness instant its NEXT schedulable operation should use),
    `machine_free_at`, and `pool` in place; returns the placed rows.

    `heavy_operations`/`resolve_safety_stock`: CLAUDE.md §E.10 — see
    `_place_fixture_run`. Omitting `resolve_safety_stock` (the default)
    means every heavy-op run batches committed and safety-stock together
    unconditionally, same as before this hook existed.

    Thin convenience wrapper around `plan_task_pool` + placement — kept for
    callers (and existing tests) that don't need the orchestrator's
    run-granular resumability.
    """
    plan = plan_task_pool(ops, today, window_days)
    placed: list[PlacedOperation] = []
    for run_steps in plan.fixture_runs:
        placed.extend(
            _place_fixture_run(
                run_steps, plan.op_by_id, ready_at, machine_free_at, pool, availability, day_zero, cooling_minutes,
                heavy_operations, resolve_safety_stock,
            )
        )
    for op in plan.no_fixture_ops:
        placed.append(_place_no_fixture_op(op, ready_at, machine_free_at, availability, day_zero, cooling_minutes))
    return placed
