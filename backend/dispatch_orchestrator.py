"""
dispatch_orchestrator.py — Engine 1 Model E final assembly, layer 2 (outer
loop): drives placement across every order's full schedulable chain, handles
Planned-order release gating (§E.1/§E.8), and assembles the final per-row
output shape — REMARK passthrough for excluded rows,
`ORDER_COMPLETION_DATE`/`ORDER_COMPLETION_SHIFT` stamped across every row of
an order (CLAUDE.md §E.14).

Round mechanics: operations are processed by "depth" — an order's 1st
schedulable operation, then its 2nd, and so on — not by raw OPERATION_NO,
since precedence-bridging means different orders' Nth schedulable operation
can sit at different OPERATION_NO values. Within one depth round, operations
are grouped by TASK, and the batching DECISION for that (depth, task) pool —
which orders group into which fixture runs — is precomputed once via
`dispatch_engine.plan_task_pool` (deterministic: depends only on priority
ranks, fixture/locator identity, and §E.6's window, never on timeline/pool
STATE). Each individual run (or no-fixture op) is then its own `WorkItem`,
placed in order; `ready_at` for an order's NEXT round is simply the `end` of
wherever it was placed (or, for a Planned order's very first round, its
material-arrival earliest-start — computed once, up front).

Run-granular WorkItems (not whole-task-pool) are what make the §E.10
speculative check correct: resuming a continuation only at the next TASK
pool would miss a SECOND run within the SAME pool competing for a machine
the first run just occupied — a completely realistic scenario, and exactly
the kind of downstream effect the check exists to catch. This was a real gap
caught while building this module, not a hypothetical one.

§E.10 full speculative check — how it's wired here: when
`enable_safety_stock_speculation` is on, every WorkItem gets a fresh
`resolve_safety_stock` closure bound to its own index into the flat
`work_items` list and to the (still-mutable, live) `ready_at`/
`machine_free_at`/`pool`/`placed_by_order` state as it stands at that exact
point — `dispatch_engine._place_fixture_run` only actually calls it when a
heavy-op run mixes committed and safety-stock members. When called, the
closure:

  1. Forks state (dict `.copy()` for the plain dicts, `DevicePool.clone()`
     for the pool) into two independent copies.
  2. Places THIS run once on each fork — as originally sequenced for
     "append", split into committed-only + solo safety-stock runs for
     "defer" (the same consequence a machine-selection dropout gets).
  3. Continues EACH fork through every remaining WorkItem
     (`_continuation_verdict`, single-level speculation — no further
     heavy-op branching inside a branch, which would blow up
     combinatorially and isn't needed to answer "would THIS decision hurt a
     committed order downstream") to get each branch's final set of
     committed orders that end up completing after their own CDD.
  4. Runs both continuations in parallel via
     `dispatch_parallel.evaluate_branches_in_parallel` (separate OS
     processes — CLAUDE.md is explicit this must not be threads, since
     CPython's GIL buys no speedup on this CPU-bound work).
  5. Accepts "append" iff it introduces no committed breach beyond what
     "defer" already has (`append_breaches <= defer_breaches`) — not simply
     "append has zero breaches", since some breach elsewhere in the plant
     might be unrelated to this decision entirely.

Also not yet covered: a real Oracle TIMESTAMP for START_TIMESTAMP/
END_TIMESTAMP. `TimePoint` (day, shift, minute-into-shift) is this layer's
internal clock, deliberately — the ERP views give shift DURATIONS
(WORKING_MINS), never a shift's clock-time-of-day start, so there is
currently no data anywhere that would let this layer (or any layer) produce
a real wall-clock TIMESTAMP. Converting `TimePoint` -> an actual TIMESTAMP
needs a shift-start-time mapping that doesn't exist yet in any read-only
view; flagging this now so it isn't silently invented later. `scheduled_date`/
`shift` here are exactly `end.day` / `end.shift`, per CLAUDE.md's "Gantt
shows completion" convention.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from dispatch_batching import ChargedStep
from dispatch_engine import PlacedOperation, SafetyStockResolver, _place_fixture_run, _place_no_fixture_op, plan_task_pool
from dispatch_orders import ExcludedOperation, OrderChain, ScheduledOperation
from dispatch_parallel import Branch, evaluate_branches_in_parallel
from dispatch_planned_release import ORDER_STATUS_PLANNED, compute_planned_earliest_start
from dispatch_pool import DevicePool
from dispatch_timeline import SHIFT_ORDER, AvailabilityFn, TimePoint, start_of_day


@dataclass(frozen=True)
class WorkItem:
    """
    One individually-placeable unit: either a fixture run (`run_steps` set,
    `no_fixture_op` None) or a single no-fixture-match operation (the
    reverse). `op_by_id` is the (depth, task) pool's shared lookup — the same
    dict object is referenced by every WorkItem of that pool.
    """

    depth: int
    task: str
    op_by_id: dict[str, ScheduledOperation]
    run_steps: Optional[list[ChargedStep]]
    no_fixture_op: Optional[ScheduledOperation]


@dataclass(frozen=True)
class FinalRow:
    """One MCH_SCHEDULE_OUTPUT row's worth of data (CLAUDE.md §E.14) — Oracle
    write formatting (TIMESTAMP conversion, RUN_ID/GENERATED_AT stamping,
    LINE_NO beyond the default of 1) is the caller's job, not this layer's."""

    production_order: str
    operation_no: float
    task: Optional[str]
    machine: Optional[str]
    shift: Optional[str]
    scheduled_date: Optional[date]
    balance_qty: int
    batch_key: Optional[str]
    is_safety_stock: bool
    fixture_id: Optional[str]
    locator_id: Optional[str]
    start: Optional[TimePoint]
    end: Optional[TimePoint]
    remark: Optional[str]
    order_completion_date: Optional[date]
    order_completion_shift: Optional[str]


def _initial_ready_at(first_op: ScheduledOperation, day_zero: date, planned_order_start_buffer_days: int) -> TimePoint:
    if first_op.order_status == ORDER_STATUS_PLANNED:
        return compute_planned_earliest_start(first_op.production_start_date, planned_order_start_buffer_days)
    return start_of_day(day_zero)


def _build_work_items(order_chains: list[OrderChain], today: date, window_days: int) -> list[WorkItem]:
    """
    Flattens every (depth, task) pool into individually-resumable WorkItems.
    Entirely deterministic and precomputable up front — see `WorkItem` and
    `dispatch_engine.plan_task_pool`'s own docstring for why that's safe.
    """
    max_depth = max((len(chain.schedulable) for chain in order_chains), default=0)
    items: list[WorkItem] = []
    for depth in range(max_depth):
        by_task: dict[str, list[ScheduledOperation]] = {}
        for chain in order_chains:
            if depth < len(chain.schedulable):
                op = chain.schedulable[depth]
                by_task.setdefault(op.task, []).append(op)
        for task in sorted(by_task):  # deterministic order — required for reproducible runs
            plan = plan_task_pool(by_task[task], today, window_days)
            for run_steps in plan.fixture_runs:
                items.append(WorkItem(depth, task, plan.op_by_id, run_steps, None))
            for op in plan.no_fixture_ops:
                items.append(WorkItem(depth, task, plan.op_by_id, None, op))
    return items


def _place_work_item(
    item: WorkItem,
    ready_at: dict[str, TimePoint],
    machine_free_at: dict[str, TimePoint],
    pool: DevicePool,
    availability: AvailabilityFn,
    day_zero: date,
    cooling_minutes: float,
    heavy_operations: frozenset[str] = frozenset(),
    resolve_safety_stock: Optional[SafetyStockResolver] = None,
) -> list[PlacedOperation]:
    if item.run_steps is not None:
        return _place_fixture_run(
            item.run_steps, item.op_by_id, ready_at, machine_free_at, pool, availability, day_zero, cooling_minutes,
            heavy_operations, resolve_safety_stock,
        )
    return [_place_no_fixture_op(item.no_fixture_op, ready_at, machine_free_at, availability, day_zero, cooling_minutes)]


def _continuation_verdict(
    work_items: list[WorkItem],
    resume_index: int,
    ready_at: dict[str, TimePoint],
    machine_free_at: dict[str, TimePoint],
    pool: DevicePool,
    placed_by_order: dict[str, list[PlacedOperation]],
    cdd_by_order: dict[str, Optional[date]],
    availability: AvailabilityFn,
    day_zero: date,
    cooling_minutes: float,
) -> frozenset[str]:
    """
    Module-level (picklable) continuation used as a speculative branch's
    target function (see module docstring). Continues placing every
    remaining WorkItem against the given, already-forked state, then returns
    the set of COMMITTED order_ids whose final completion ends up after
    their own CDD. Single-level speculation: no resolver is passed to
    `_place_work_item` here, so any further heavy-op mixed run encountered
    during this continuation always appends. Batching membership is already
    fixed (baked into each WorkItem), so `today`/`window_days` play no part
    in placement and aren't needed here at all.
    """
    for i in range(resume_index, len(work_items)):
        for placed in _place_work_item(work_items[i], ready_at, machine_free_at, pool, availability, day_zero, cooling_minutes):
            placed_by_order.setdefault(placed.production_order, []).append(placed)

    breached = set()
    for order_id, cdd in cdd_by_order.items():
        if cdd is None:
            continue
        placements = placed_by_order.get(order_id)
        if not placements:
            continue
        if placements[-1].end.day > cdd:
            breached.add(order_id)
    return frozenset(breached)


def _make_committed_safe_resolver(
    work_items: list[WorkItem],
    current_index: int,
    ready_at: dict[str, TimePoint],
    machine_free_at: dict[str, TimePoint],
    pool: DevicePool,
    placed_by_order: dict[str, list[PlacedOperation]],
    cdd_by_order: dict[str, Optional[date]],
    availability: AvailabilityFn,
    day_zero: date,
    cooling_minutes: float,
) -> SafetyStockResolver:
    """Builds the real §E.10 committed_safe_check for the run currently being
    decided at `work_items[current_index]`. The returned closure runs only in
    THIS (main) process — it never itself crosses a process boundary; only
    `_continuation_verdict` and its plain, picklable args do."""

    def resolve(run_steps: list[ChargedStep], op_by_id: dict, task: str) -> bool:
        committed_steps = [s for s in run_steps if op_by_id[s.order_id].cdd is not None]
        safety_steps = [s for s in run_steps if op_by_id[s.order_id].cdd is None]

        def _fork():
            return (
                dict(ready_at),
                dict(machine_free_at),
                pool.clone(),
                {k: list(v) for k, v in placed_by_order.items()},
            )

        # Branch "append": place run_steps exactly as originally sequenced.
        append_ready, append_machines, append_pool, append_placed = _fork()
        for row in _place_fixture_run(run_steps, op_by_id, append_ready, append_machines, append_pool, availability, day_zero, cooling_minutes):
            append_placed.setdefault(row.production_order, []).append(row)

        # Branch "defer": committed portion now; safety portion becomes its
        # own solo run(s) — identical consequence to a machine-selection dropout.
        defer_ready, defer_machines, defer_pool, defer_placed = _fork()
        defer_rows: list[PlacedOperation] = []
        if committed_steps:
            defer_rows.extend(
                _place_fixture_run(committed_steps, op_by_id, defer_ready, defer_machines, defer_pool, availability, day_zero, cooling_minutes)
            )
        for step in safety_steps:
            solo = ChargedStep(step.order_id, step.fixture, step.locator, "fixture_change", run_index=-1)
            defer_rows.extend(
                _place_fixture_run([solo], op_by_id, defer_ready, defer_machines, defer_pool, availability, day_zero, cooling_minutes)
            )
        for row in defer_rows:
            defer_placed.setdefault(row.production_order, []).append(row)

        branches = [
            Branch(
                "append", _continuation_verdict,
                (work_items, current_index + 1, append_ready, append_machines, append_pool, append_placed,
                 cdd_by_order, availability, day_zero, cooling_minutes),
            ),
            Branch(
                "defer", _continuation_verdict,
                (work_items, current_index + 1, defer_ready, defer_machines, defer_pool, defer_placed,
                 cdd_by_order, availability, day_zero, cooling_minutes),
            ),
        ]
        results = evaluate_branches_in_parallel(branches)
        return results["append"] <= results["defer"]

    return resolve


def run_dispatch_simulation(
    order_chains: list[OrderChain],
    availability: AvailabilityFn,
    pool: DevicePool,
    today: date,
    window_days: int,
    cooling_minutes: float,
    day_zero: date,
    planned_order_start_buffer_days: int,
    heavy_operations: frozenset[str] = frozenset(),
    enable_safety_stock_speculation: bool = False,
) -> list[FinalRow]:
    """
    Produce the full set of output rows for every order in `order_chains` —
    scheduled placements AND excluded rows, per CLAUDE.md §E.12's "every
    eligible order-operation always produces a row" rule.

    `enable_safety_stock_speculation=False` (the default) preserves the
    documented simplification (heavy-op safety stock always appends, exactly
    like a light operation) — existing callers see no behavior change. Set
    it True (with a non-empty `heavy_operations`) to get the real §E.10
    full speculative check described in this module's docstring.
    """
    ready_at: dict[str, TimePoint] = {}
    for chain in order_chains:
        if chain.schedulable:
            ready_at[chain.production_order] = _initial_ready_at(chain.schedulable[0], day_zero, planned_order_start_buffer_days)

    machine_free_at: dict[str, TimePoint] = {}
    placed_by_order: dict[str, list[PlacedOperation]] = {chain.production_order: [] for chain in order_chains}
    cdd_by_order: dict[str, Optional[date]] = {
        chain.production_order: (chain.schedulable[0].cdd if chain.schedulable else None) for chain in order_chains
    }

    work_items = _build_work_items(order_chains, today, window_days)

    for i, item in enumerate(work_items):
        resolver = None
        if enable_safety_stock_speculation and heavy_operations:
            resolver = _make_committed_safe_resolver(
                work_items, i, ready_at, machine_free_at, pool, placed_by_order, cdd_by_order,
                availability, day_zero, cooling_minutes,
            )
        for placed in _place_work_item(
            item, ready_at, machine_free_at, pool, availability, day_zero, cooling_minutes, heavy_operations, resolver
        ):
            placed_by_order[placed.production_order].append(placed)

    return _assemble_final_rows(order_chains, placed_by_order)


def _assemble_final_rows(
    order_chains: list[OrderChain],
    placed_by_order: dict[str, list[PlacedOperation]],
) -> list[FinalRow]:
    rows: list[FinalRow] = []
    for chain in order_chains:
        placements = placed_by_order[chain.production_order]
        if placements:
            last_end = placements[-1].end
            completion_date, completion_shift = last_end.day, SHIFT_ORDER[last_end.shift_index]
        else:
            completion_date = completion_shift = None

        for p in placements:
            rows.append(
                FinalRow(
                    production_order=p.production_order,
                    operation_no=p.operation_no,
                    task=p.task,
                    machine=p.machine,
                    shift=SHIFT_ORDER[p.end.shift_index],
                    scheduled_date=p.end.day,
                    balance_qty=p.balance_qty,
                    batch_key=p.batch_key,
                    is_safety_stock=p.is_safety_stock,
                    fixture_id=p.fixture_id,
                    locator_id=p.locator_id,
                    start=p.start,
                    end=p.end,
                    remark=p.remark,
                    order_completion_date=completion_date,
                    order_completion_shift=completion_shift,
                )
            )

        for exc in chain.excluded:
            rows.append(
                FinalRow(
                    production_order=exc.production_order,
                    operation_no=exc.operation_no,
                    task=exc.task,
                    machine=None,
                    shift=None,
                    scheduled_date=None,
                    balance_qty=exc.balance_qty,
                    batch_key=exc.batch_key,
                    is_safety_stock=exc.is_safety_stock,
                    fixture_id=None,
                    locator_id=None,
                    start=None,
                    end=None,
                    remark=exc.remark,
                    order_completion_date=completion_date,
                    order_completion_shift=completion_shift,
                )
            )

    return rows
