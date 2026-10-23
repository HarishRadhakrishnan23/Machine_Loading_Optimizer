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

An event-driven, eligibility-queue redesign of this loop was attempted and
fully built (forking the queue state through the admission check, etc.) but
deliberately reverted: on live data it produced identical results to this
simpler flat-list loop, and closer analysis showed the "depth rounds cause
artificial machine idle time" premise that motivated it didn't actually hold
once the real bug (below) was fixed — any two pools genuinely competing for
the same machine reduce to the identical greedy choice under both designs,
and pools that don't share a machine never affected each other's computed
timestamps in the first place, in either design. Keeping the simpler,
proven loop rather than carrying unproven complexity.

Run-granular WorkItems (not whole-task-pool) are what make the §E.10
speculative check correct: resuming a continuation only at the next TASK
pool would miss a SECOND run within the SAME pool competing for a machine
the first run just occupied — a completely realistic scenario, and exactly
the kind of downstream effect the check exists to catch. This was a real gap
caught while building this module, not a hypothetical one.

§E.16 full speculative check — how it's wired here: when
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

from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Optional

from dispatch_batching import ChargedStep
from dispatch_engine import (
    PlacedOperation,
    SafetyStockResolver,
    _place_fixture_run,
    _place_no_fixture_op,
    _recompute_charges,
    plan_task_pool,
)
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
    # Opt-in soft-consolidation-beyond-window candidates for THIS run (see
    # dispatch_engine.TaskPoolPlan.soft_candidates_by_run) — None/empty unless
    # `allow_soft_consolidation_beyond_window` is on. Never set on a
    # no_fixture_op WorkItem (merging only concerns fixture runs).
    soft_merge_candidates: Optional[list[ChargedStep]] = None


@dataclass(frozen=True)
class FinalRow:
    """One MCH_SCHEDULE_OUTPUT row's worth of data (CLAUDE.md §E.14) — Oracle
    write formatting (TIMESTAMP conversion, RUN_ID/GENERATED_AT stamping) is
    the caller's job, not this layer's. `line_no` is now a real, computed
    value (CLAUDE.md §E.18 — a shift-boundary quantity slice), not always 1."""

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
    line_no: int = 1


def _initial_ready_at(first_op: ScheduledOperation, day_zero: date, planned_order_start_buffer_days: int) -> TimePoint:
    if first_op.order_status == ORDER_STATUS_PLANNED:
        return compute_planned_earliest_start(first_op.production_start_date, planned_order_start_buffer_days)
    return start_of_day(day_zero)


def _build_work_items(
    order_chains: list[OrderChain],
    today: date,
    window_days: int,
    allow_soft_consolidation: bool = False,
    soft_max_extra_days: int = 0,
) -> list[WorkItem]:
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
            plan = plan_task_pool(by_task[task], today, window_days, allow_soft_consolidation, soft_max_extra_days)
            for run_idx, run_steps in enumerate(plan.fixture_runs):
                soft_candidates = plan.soft_candidates_by_run.get(run_idx)
                items.append(WorkItem(depth, task, plan.op_by_id, run_steps, None, soft_candidates))
            for op in plan.no_fixture_ops:
                items.append(WorkItem(depth, task, plan.op_by_id, None, op))
    return items


def _should_skip(item: WorkItem, skip_keys: frozenset[tuple[int, str]]) -> bool:
    """
    True iff every member of this WorkItem has already been placed as part
    of an earlier admission-check merge (see `_decide_admission`) — its
    rows already exist under the run it was merged into, so placing it again
    here would duplicate output. Never true for a no_fixture_op WorkItem
    (merging only ever concerns fixture runs) or when the feature is off
    (`skip_keys` empty, the default — zero behavior change).

    Keyed on (depth, order_id), NOT order_id alone: the same order appears
    in a SEPARATE, unrelated WorkItem at every depth (its op10, op20, ...),
    and a merge at one depth must never cause a later depth's legitimate
    placement of that same order to be skipped too — that was a real bug
    caught by live-data E-6 validation (the row count came up short).
    """
    if not skip_keys or item.run_steps is None:
        return False
    return all((item.depth, s.order_id) in skip_keys for s in item.run_steps)


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
    piece_ready_at: Optional[dict[str, list[TimePoint]]] = None,
) -> list[PlacedOperation]:
    if item.run_steps is not None:
        return _place_fixture_run(
            item.run_steps, item.op_by_id, ready_at, machine_free_at, pool, availability, day_zero, cooling_minutes,
            heavy_operations, resolve_safety_stock, piece_ready_at,
        )
    return _place_no_fixture_op(item.no_fixture_op, ready_at, machine_free_at, availability, day_zero, cooling_minutes, piece_ready_at)


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
    window_cutoff: Optional[date] = None,
    skip_keys: frozenset[tuple[int, str]] = frozenset(),
    piece_ready_at: Optional[dict[str, list[TimePoint]]] = None,
) -> frozenset[str]:
    """
    Module-level (picklable) continuation used as a speculative branch's
    target function (see module docstring). Continues placing every
    remaining WorkItem against the given, already-forked state, then returns
    the set of order_ids — restricted to CLAUDE.md §E.16's protected set —
    whose final completion ends up after their own CDD. Single-level
    speculation: no resolver is passed to `_place_work_item` here, so any
    further heavy-op mixed run encountered during this continuation always
    appends. Batching membership is already fixed (baked into each
    WorkItem), so `today`/`window_days` play no part in placement itself.

    `window_cutoff` (Model F §E.16): only an order whose own CDD is on or
    before this date counts as a breach here — CLAUDE.md §E.16's explicit,
    deliberate scope limit: the universal admission check protects near-term
    (≤`batch_consolidation_window_days`) orders only, never every committed
    order regardless of distance. `None` (the default) means "no window
    scoping" — every committed order counts, which is what a caller outside
    the admission-check machinery (there currently are none) would get.

    `skip_keys`: (depth, order_id) pairs whose WorkItem was already placed
    earlier IN THIS SAME BRANCH via an admission-check merge (see
    `_decide_admission`) — this branch's own copy of "already accounted
    for," never the real (main-process) skip set, since each branch forks
    its own.
    """
    for i in range(resume_index, len(work_items)):
        if _should_skip(work_items[i], skip_keys):
            continue
        for placed in _place_work_item(
            work_items[i], ready_at, machine_free_at, pool, availability, day_zero, cooling_minutes,
            piece_ready_at=piece_ready_at,
        ):
            placed_by_order.setdefault(placed.production_order, []).append(placed)

    breached = set()
    for order_id, cdd in cdd_by_order.items():
        if cdd is None:
            continue
        if window_cutoff is not None and cdd > window_cutoff:
            continue
        placements = placed_by_order.get(order_id)
        if not placements:
            continue
        if placements[-1].end.day > cdd:
            breached.add(order_id)
    return frozenset(breached)


def _evaluate_two_options(
    option_a_calls: list[list[ChargedStep]],
    option_b_calls: list[list[ChargedStep]],
    skip_for_a: frozenset[tuple[int, str]],
    skip_for_b: frozenset[tuple[int, str]],
    op_by_id: dict,
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
    window_cutoff: Optional[date] = None,
    piece_ready_at: Optional[dict[str, list[TimePoint]]] = None,
) -> bool:
    """
    CLAUDE.md §E.16 — the universal admission check's fork/place-once/
    continue/compare core. One mechanism, used for every beyond-window or
    safety-stock candidate wanting to join a cluster (`_decide_admission`
    below) — not two separately-maintained mechanisms the way Model E had it
    (a heavy-op safety-stock resolver and a soft-consolidation merge, kept
    here only as the historical names `_make_committed_safe_resolver`/
    `_decide_soft_merge` used to go by). Each option is a list of
    `_place_fixture_run` calls (one run each — a deferred safety-stock
    decision needs several: the committed portion plus one solo run per
    deferred member; an admission decision is just one call for each side).
    Forks state twice, places option_a/option_b once each into its own fork,
    continues each fork through every remaining WorkItem (skipping
    `skip_for_a`/`skip_for_b` in that branch's own continuation — members
    already placed by this decision, never to be placed again later), runs
    both continuations in parallel processes, and accepts option_a iff it
    introduces no NEW breach among §E.16's protected (≤`window_cutoff`)
    orders beyond option_b's own (`a_breaches <= b_breaches`, not "a has zero
    breaches" — a breach elsewhere in the plant might be unrelated to this
    decision entirely).
    """

    def _fork():
        return (
            dict(ready_at),
            dict(machine_free_at),
            pool.clone(),
            {k: list(v) for k, v in placed_by_order.items()},
            {k: list(v) for k, v in piece_ready_at.items()} if piece_ready_at is not None else None,
        )

    def _run_calls(calls, ready, machines, forked_pool, placed, forked_pieces):
        for steps in calls:
            for row in _place_fixture_run(
                steps, op_by_id, ready, machines, forked_pool, availability, day_zero, cooling_minutes,
                piece_ready_at=forked_pieces,
            ):
                placed.setdefault(row.production_order, []).append(row)

    a_ready, a_machines, a_pool, a_placed, a_pieces = _fork()
    _run_calls(option_a_calls, a_ready, a_machines, a_pool, a_placed, a_pieces)

    b_ready, b_machines, b_pool, b_placed, b_pieces = _fork()
    _run_calls(option_b_calls, b_ready, b_machines, b_pool, b_placed, b_pieces)

    branches = [
        Branch(
            "a", _continuation_verdict,
            (work_items, current_index + 1, a_ready, a_machines, a_pool, a_placed,
             cdd_by_order, availability, day_zero, cooling_minutes, window_cutoff, skip_for_a, a_pieces),
        ),
        Branch(
            "b", _continuation_verdict,
            (work_items, current_index + 1, b_ready, b_machines, b_pool, b_placed,
             cdd_by_order, availability, day_zero, cooling_minutes, window_cutoff, skip_for_b, b_pieces),
        ),
    ]
    results = evaluate_branches_in_parallel(branches)
    return results["a"] <= results["b"]


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
    window_cutoff: Optional[date] = None,
    piece_ready_at: Optional[dict[str, list[TimePoint]]] = None,
) -> SafetyStockResolver:
    """Builds the §E.16 admission check for the heavy-op run currently being
    decided at `work_items[current_index]` — append this run's safety-stock
    portion for free, or defer it. The returned closure runs only in THIS
    (main) process — it never itself crosses a process boundary; only
    `_continuation_verdict` and its plain, picklable args do."""

    def resolve(run_steps: list[ChargedStep], op_by_id: dict, task: str) -> bool:
        committed_steps = [s for s in run_steps if op_by_id[s.order_id].cdd is not None]
        safety_steps = [s for s in run_steps if op_by_id[s.order_id].cdd is None]

        # "append": run_steps exactly as originally sequenced.
        option_a_calls = [run_steps]
        # "defer": committed portion now; each safety-stock member becomes
        # its own solo run — identical consequence to a machine-selection dropout.
        option_b_calls = [committed_steps] if committed_steps else []
        option_b_calls += [
            [ChargedStep(s.order_id, s.fixture, s.locator, "fixture_change", run_index=-1)] for s in safety_steps
        ]

        return _evaluate_two_options(
            option_a_calls, option_b_calls, frozenset(), frozenset(),
            op_by_id, work_items, current_index, ready_at, machine_free_at, pool, placed_by_order, cdd_by_order,
            availability, day_zero, cooling_minutes, window_cutoff, piece_ready_at,
        )

    return resolve


def _decide_admission(
    run_steps: list[ChargedStep],
    candidates: list[ChargedStep],
    depth: int,
    op_by_id: dict,
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
    window_cutoff: Optional[date],
    piece_ready_at: Optional[dict[str, list[TimePoint]]] = None,
) -> bool:
    """
    CLAUDE.md §E.16 — the universal admission check for a beyond-window or
    safety-stock candidate wanting to join an in-window cluster (this module
    used to call this `_decide_soft_merge`, scoped only to Model E's narrow
    soft-consolidation-beyond-window case; Model F uses the same mechanism
    for every such candidate, not just that one historical trigger).
    "Include": the run extended with `candidates` (charges recomputed fresh —
    appending changes what charge_type the appended members, and possibly
    the existing tail, actually owe). "Exclude": the run unchanged; each
    candidate's own already-built solo WorkItem (further down `work_items`)
    places it normally later. Accepts "include" iff it introduces no new
    breach among §E.16's protected (≤`window_cutoff`) orders beyond leaving
    the candidates out.
    """
    merged_steps = _recompute_charges(run_steps + candidates)
    skip_keys = frozenset((depth, s.order_id) for s in candidates)
    return _evaluate_two_options(
        [merged_steps], [run_steps], skip_keys, frozenset(),
        op_by_id, work_items, current_index, ready_at, machine_free_at, pool, placed_by_order, cdd_by_order,
        availability, day_zero, cooling_minutes, window_cutoff, piece_ready_at,
    )


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
    allow_soft_consolidation_beyond_window: bool = False,
    soft_consolidation_max_extra_days: int = 0,
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

    `allow_soft_consolidation_beyond_window=False` (the default) preserves
    §E.6 as a pure hard rule with zero behavior change for every existing
    caller. Set it True (with `soft_consolidation_max_extra_days > 0`) to
    let a committed order just beyond the window ride an existing in-window
    run's already-mounted fixture/locator for free, but ONLY when the full
    speculative check (CLAUDE.md §E.16, see `_decide_admission`) confirms
    doing so introduces no new breach among orders within the window.

    §E.18 Part 2 (lot-streaming): `piece_ready_at` tracks, per order, its
    most-recently-placed operation's own per-piece completion times — each
    operation's placement reads it (gating its own pieces individually
    instead of waiting for the whole upstream batch) and overwrites it with
    its own per-piece completions for whoever places this order's NEXT
    operation in turn. Unconditional, same as Part 1 — no config flag, since
    it degrades to exactly today's single-instant `ready_at` behavior for
    any order whose upstream operation never spans a shift boundary (the
    common case).
    """
    ready_at: dict[str, TimePoint] = {}
    for chain in order_chains:
        if chain.schedulable:
            ready_at[chain.production_order] = _initial_ready_at(chain.schedulable[0], day_zero, planned_order_start_buffer_days)

    piece_ready_at: dict[str, list[TimePoint]] = {}
    machine_free_at: dict[str, TimePoint] = {}
    placed_by_order: dict[str, list[PlacedOperation]] = {chain.production_order: [] for chain in order_chains}
    cdd_by_order: dict[str, Optional[date]] = {
        chain.production_order: (chain.schedulable[0].cdd if chain.schedulable else None) for chain in order_chains
    }
    # CLAUDE.md §E.16: the universal admission check protects orders whose
    # own CDD falls within this cutoff only — never every committed order
    # regardless of distance. Same boundary §E.6 already uses for ordinary
    # (non-admission-check) consolidation eligibility.
    window_cutoff = today + timedelta(days=window_days)

    work_items = _build_work_items(
        order_chains, today, window_days, allow_soft_consolidation_beyond_window, soft_consolidation_max_extra_days
    )

    # Real (main-process) record of which (depth, order_id) WorkItems got
    # placed early via an admission-check merge — their own later solo
    # WorkItem must then be skipped, never placed twice. Keyed on
    # (depth, order_id), NOT order_id alone: the same order has a SEPARATE,
    # unrelated WorkItem at every depth (its op10, op20, ...), and a merge at
    # one depth must never skip a later depth's legitimate placement of that
    # same order — a real bug caught by live-data E-6 validation (short row
    # count). Always empty when the feature is off.
    merged_keys: set[tuple[int, str]] = set()

    for i, item in enumerate(work_items):
        if _should_skip(item, frozenset(merged_keys)):
            continue

        resolver = None
        if enable_safety_stock_speculation and heavy_operations:
            resolver = _make_committed_safe_resolver(
                work_items, i, ready_at, machine_free_at, pool, placed_by_order, cdd_by_order,
                availability, day_zero, cooling_minutes, window_cutoff, piece_ready_at,
            )

        effective_item = item
        merged_order_ids_this_item: frozenset[str] = frozenset()
        if allow_soft_consolidation_beyond_window and item.run_steps is not None and item.soft_merge_candidates:
            accepted = _decide_admission(
                item.run_steps, item.soft_merge_candidates, item.depth, item.op_by_id,
                work_items, i, ready_at, machine_free_at, pool, placed_by_order, cdd_by_order,
                availability, day_zero, cooling_minutes, window_cutoff, piece_ready_at,
            )
            if accepted:
                merged_steps = _recompute_charges(item.run_steps + item.soft_merge_candidates)
                effective_item = WorkItem(item.depth, item.task, item.op_by_id, merged_steps, None, None)
                merged_order_ids_this_item = frozenset(s.order_id for s in item.soft_merge_candidates)
                merged_keys.update((item.depth, oid) for oid in merged_order_ids_this_item)

        for placed in _place_work_item(
            effective_item, ready_at, machine_free_at, pool, availability, day_zero, cooling_minutes, heavy_operations, resolver,
            piece_ready_at,
        ):
            # REMARK: flag an admission-checked member distinctly from an
            # ordinary in-window append — "why is this far-CDD order on the
            # same machine as a near one" is otherwise non-obvious.
            if placed.production_order in merged_order_ids_this_item:
                placed = replace(placed, remark=(placed.remark or "") + "; soft-consolidated beyond window (speculatively confirmed safe)")
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
                    line_no=p.line_no,
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
