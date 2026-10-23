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

from dataclasses import dataclass, field, replace
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
    ChargeType,
    FixtureLocatorTimes,
    compute_batch_duration,
    run_priority_walk,
)
from dispatch_consolidation_window import is_consolidation_eligible as window_eligible
from dispatch_consolidation_window import is_soft_eligible as soft_window_eligible
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
    # CLAUDE.md §E.18 — increments only when this ONE (production_order,
    # operation_no)'s own balance is genuinely split into more than one row:
    # a shift-boundary quantity slice (§E.18) or the pre-existing rare
    # machine-handoff case. 1 for every row that isn't split, same as today.
    line_no: int = 1


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


def _slice_by_shift_raw(
    qty: int,
    one_time: float,
    per_piece: float,
    start: TimePoint,
    machine: str,
    availability: AvailabilityFn,
    piece_ready_at: Optional[list[TimePoint]] = None,
) -> tuple[list[tuple[int, TimePoint, TimePoint]], list[TimePoint]]:
    """
    CLAUDE.md §E.18 — splits one placement into quantity slices at SHIFT
    boundaries: "6 of 10 pieces done by the end of shift 1, the other 4 in
    shift 2" becomes two slices, `[(6, shift1_start, shift1_end_instant),
    (4, shift1_end_instant, shift2_end_instant)]`, instead of one (10, start,
    end) block. `one_time` (a fixture/locator change, or 0 for plain
    routing) is paid once, before the first piece; only `per_piece` is
    walked piece by piece to find where a shift boundary falls.

    Pieces are grouped by the `(day, shift_index)` of their OWN completion
    instant — a piece that finishes in the same shift as the slice's first
    piece stays in that slice; the instant a piece's completion lands in a
    different shift, the previous slice closes (ending exactly where the
    next piece's clock started, i.e. no gap) and a new one opens with that
    piece. A run that never crosses a shift boundary yields exactly one
    slice spanning the whole quantity — byte-identical to pre-§E.18 output.

    `piece_ready_at` (§E.18 Part 2 — lot-streaming consumption): piece `k`'s
    own earliest start, from the UPSTREAM operation's own per-piece
    completion times (this function's own `piece_completions` return value,
    one call earlier in the chain) — never the upstream op's single overall
    `ready_at`. `None` (the default — the first operation in a chain, or any
    caller not yet threading this through) means every piece is gated only
    by the machine's own cursor, identical to pre-Part-2 behavior. A piece
    index beyond `len(piece_ready_at)` (balance_qty mismatch between this op
    and its upstream — a real, if rare, ERP bookkeeping possibility) falls
    back to ungated (cursor-only) rather than guessing.

    Returns `(slices, piece_completions)` — `piece_completions[k]` is piece
    `k`'s own completion TimePoint, meant to become the NEXT operation's
    `piece_ready_at` in turn.
    """
    cursor = advance_clock(start, one_time, machine, availability)
    slices: list[tuple[int, TimePoint, TimePoint]] = []
    piece_completions: list[TimePoint] = []
    # The FIRST slice's reported start is `start` (the row's true start,
    # including the one-time setup charge in its span) — NOT piece 0's own
    # (post-charge) start, which would silently drop the setup time from the
    # row's reported duration. Only a LATER slice (opened because a shift
    # boundary was crossed) starts exactly at its first piece's own start.
    slice_start: TimePoint = start
    slice_qty = 0
    slice_shift_key: Optional[tuple] = None
    for k in range(qty):
        ready_k = piece_ready_at[k] if piece_ready_at is not None and k < len(piece_ready_at) else None
        piece_start = max(cursor, ready_k) if ready_k is not None else cursor
        piece_end = advance_clock(piece_start, per_piece, machine, availability)
        piece_completions.append(piece_end)
        key = (piece_end.day, piece_end.shift_index)
        if slice_shift_key is None:
            slice_shift_key = key
        elif key != slice_shift_key:
            slices.append((slice_qty, slice_start, cursor))
            slice_start = piece_start
            slice_qty = 0
            slice_shift_key = key
        slice_qty += 1
        cursor = piece_end
    slices.append((slice_qty, slice_start, cursor))
    return slices, piece_completions


def _slice_by_shift(
    op: ScheduledOperation,
    charge_type: ChargeType,
    candidate,
    start: TimePoint,
    machine: str,
    availability: AvailabilityFn,
    piece_ready_at: Optional[list[TimePoint]] = None,
) -> tuple[list[tuple[int, TimePoint, TimePoint]], list[TimePoint]]:
    """Fixture-run case of `_slice_by_shift_raw`: the one-time charge comes
    from `charge_type` (§E.3's fixture/locator change economics), per-piece
    time is `LOAD_UNLOAD_TIME + CYCLE_TIME`."""
    times = FixtureLocatorTimes(candidate.fixture_change_time, candidate.locator_change_time, candidate.load_unload_time)
    one_time = compute_batch_duration(charge_type, 0, times)
    per_piece = candidate.load_unload_time + op.cycle_time
    return _slice_by_shift_raw(op.balance_qty, one_time, per_piece, start, machine, availability, piece_ready_at)


def _simulate_run_timing(
    op_by_id: dict[str, ScheduledOperation],
    steps: list[ChargedStep],
    machine: str,
    run_start: TimePoint,
    ready_at: dict[str, TimePoint],
    availability: AvailabilityFn,
    piece_ready_at: Optional[dict[str, list[TimePoint]]] = None,
) -> tuple[list[tuple[ChargedStep, int, TimePoint, TimePoint]], TimePoint, TimePoint, dict[str, list[TimePoint]]]:
    """
    Pure timing simulation for one fixture run on `machine`, attempting to
    start at `run_start` (the machine/pool-side earliest attempt only — never
    a sibling step's own readiness baked in here by the caller).

    `steps` is the FORMATION order (§E.4's priority walk) — who's in the run
    and what each one's charge_type assumes about the member immediately
    before it. EXECUTION order can differ from it: at each point, if the
    next-in-formation-order step isn't ready yet and placing it would leave
    the machine idle, this looks ahead for any OTHER not-yet-placed member
    that IS ready now and runs it instead — the original member keeps its
    place in line and resumes it the moment it's actually ready, so this
    never lets a later-priority member permanently cut ahead of an earlier
    one, only temporarily fill a gap the earlier one isn't ready to use yet.
    `charge_type` is recomputed live from the ACTUAL execution sequence
    (first step placed is always "fixture_change"; each one after compares
    its locator to whatever the PREVIOUS ACTUALLY-PLACED step's locator was,
    same logic `_recompute_charges` already uses for a reordered/filtered
    sequence) — never trusted from `steps`' original formation-order value,
    since reordering can change who's actually mounted immediately before
    whom.

    This is also the fix for a real bug found via live-data E-6 validation:
    a run used to compute one upfront `max()` over every member's ready_at
    before placing anything, pinning the WHOLE run's start — and leaving the
    machine fully idle the entire time — to whichever single member happened
    to become ready last. That fix (per-step ready_at, no longer a run-wide
    max) stopped an EARLY member from being held hostage by a LATE one, but
    left the mirror case unfixed: a late-to-ready member partway through a
    long formation-order sequence still blocked every member sequenced AFTER
    it, even ones that had been ready for days — confirmed on live data
    (2PMH19's AT18/200-450 run: a 13-day gap where several already-ready
    members sat behind one that wasn't ready yet). The lookahead above is
    what actually closes that gap, while CLAUDE.md §E.7's "no artificial
    gate, only real availability governs timing" still holds.

    `piece_ready_at` (§E.18 Part 2 — lot-streaming consumption): order_id ->
    that order's UPSTREAM operation's own per-piece completion times, gating
    each of THIS step's pieces individually instead of only its overall
    start — see `_slice_by_shift_raw`'s own docstring. `None`/missing entry
    means every piece is gated only by the machine's cursor, identical to
    pre-Part-2 behavior.

    No pool mutation. Returns (per-step results, the run's actual start —
    the FIRST step's own actual start, which may be later than `run_start`
    if even the first-ready member wasn't ready yet — the run's end, and a
    `{order_id: [piece_completion, ...]}` map — one entry per step actually
    placed here — for the caller to hand to THIS order's next operation in
    turn, once this result is accepted; never mutated into `piece_ready_at`
    directly here, since a rejected pool-capacity retry (see
    `_find_feasible_fixture_run`) must not leave stale data behind).
    """
    def _effective_ready(order_id: str) -> TimePoint:
        # §E.18 Part 2: if this order's upstream op already reported its own
        # per-piece completions, the TRUE earliest this order can start here
        # is its upstream's FIRST piece completing — not the upstream's full
        # `ready_at` (its LAST piece) — so lot-streaming actually changes
        # when a step both becomes eligible for the run and where its own
        # piece loop starts. Falls back to the scalar `ready_at` whenever
        # there's no per-piece data (first op in a chain, or piece_ready_at
        # not tracked at all) — identical to pre-Part-2 behavior.
        pieces = piece_ready_at.get(order_id) if piece_ready_at else None
        return pieces[0] if pieces else ready_at[order_id]

    remaining = list(steps)
    cursor = run_start
    results: list[tuple[ChargedStep, int, TimePoint, TimePoint]] = []
    piece_completions_by_order: dict[str, list[TimePoint]] = {}
    current_locator: Optional[str] = None
    while remaining:
        ready_idx = next((i for i, s in enumerate(remaining) if _effective_ready(s.order_id) <= cursor), None)
        # Nothing ready yet at `cursor` — the machine must wait regardless,
        # so wait for whichever member gets there soonest (minimizes the
        # unavoidable idle time) rather than always the formation-order head.
        idx = ready_idx if ready_idx is not None else min(range(len(remaining)), key=lambda i: _effective_ready(remaining[i].order_id))
        step = remaining.pop(idx)
        op = op_by_id[step.order_id]
        candidate = next(c for c in op.candidates if c.machine == machine)
        charge_type: ChargeType = "fixture_change" if current_locator is None else ("none" if step.locator == current_locator else "locator_change")
        step_start = max(cursor, _effective_ready(step.order_id))
        charged_step = step if charge_type == step.charge_type else ChargedStep(step.order_id, step.fixture, step.locator, charge_type, step.run_index)
        upstream_pieces = piece_ready_at.get(step.order_id) if piece_ready_at else None
        # §E.18: one step can yield several (sub_qty, start, end) slices when
        # its placement spans a shift boundary — each becomes its own output
        # row (LINE_NO) downstream. A step that stays within one shift still
        # yields exactly one slice covering its whole balance_qty.
        slices, piece_completions = _slice_by_shift(op, charge_type, candidate, step_start, machine, availability, upstream_pieces)
        for sub_qty, slice_start, slice_end in slices:
            results.append((charged_step, sub_qty, slice_start, slice_end))
        piece_completions_by_order[step.order_id] = piece_completions
        current_locator = step.locator
        cursor = results[-1][3]
    run_start_actual = results[0][2] if results else run_start
    return results, run_start_actual, cursor, piece_completions_by_order


def _find_feasible_fixture_run(
    op_by_id: dict[str, ScheduledOperation],
    steps: list[ChargedStep],
    machine: str,
    fixture: str,
    earliest_start: TimePoint,
    ready_at: dict[str, TimePoint],
    availability: AvailabilityFn,
    pool: DevicePool,
    max_attempts: int = 200,
    piece_ready_at: Optional[dict[str, list[TimePoint]]] = None,
) -> tuple[list[tuple[ChargedStep, int, TimePoint, TimePoint]], TimePoint, TimePoint, dict[str, list[TimePoint]]]:
    """
    CLAUDE.md §E.9 — the fixture concurrency retry loop: simulate the run's
    timing at a candidate start, check the fixture has capacity for that
    whole span, and if not, retry from the pool's earliest-release hint
    (never a guess — `has_capacity` is re-checked every attempt, per
    dispatch_pool.py's own contract). `earliest_start` is only the
    machine/pool-side earliest attempt now — each step's own readiness
    (`ready_at`, and per-piece `piece_ready_at` — §E.18 Part 2) is applied
    per-step/per-piece inside `_simulate_run_timing`, never pre-maxed across
    every member here (see that function's docstring for why). Returns
    (per-step results, run_start actually used — the first step's own actual
    start, which can differ from `earliest_start` if even the first-priority
    member wasn't ready yet — run_end, and the accepted attempt's
    `piece_completions_by_order` — a rejected retry's never leaks through).

    Uses the `_multi` pool methods — FIXTURE is never a "+"-joined compound
    in practice, but it CAN be the literal "NA" (confirmed real-data
    convention: some operations need no physical fixture at all), which
    `parse_devices` turns into an empty device list. Routing both fixture
    and locator through the same multi-aware methods means "no device
    needed" is handled uniformly rather than as a special case here.
    """
    candidate_start = earliest_start
    for _ in range(max_attempts):
        results, run_start, run_end, piece_completions_by_order = _simulate_run_timing(
            op_by_id, steps, machine, candidate_start, ready_at, availability, piece_ready_at
        )
        if pool.has_capacity_multi(fixture, run_start, run_end):
            return results, run_start, run_end, piece_completions_by_order
        hint = pool.earliest_release_hint_multi(fixture, run_start)
        if hint is None:
            raise RuntimeError(f"Fixture {fixture!r} reported blocked with no release hint at {run_start}")
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


def _placement_remark(step: ChargedStep, op: ScheduledOperation, machine: str) -> str:
    """
    REMARK also explains a SCHEDULED row's placement, not just an excluded
    or no-fixture-caveat one: which machine it landed on and why (a fresh
    fixture run vs. riding an existing mount for free), plus whether other
    capable machines existed. Reading every order's REMARK is how a machine
    sitting idle for a shift/day/month gets explained — "why isn't X on
    M2" is answered by seeing M2 listed as a capable-but-not-chosen
    candidate on the orders that landed elsewhere instead. Kept short
    (no machine-name lists) since REMARK is VARCHAR2(200) and device/machine
    names can be long.
    """
    reason = {
        "fixture_change": f"new fixture run on {machine} (fixture {step.fixture})",
        "locator_change": f"new locator sub-batch on {machine} (locator {step.locator})",
        "none": f"appended on {machine}, same fixture+locator already mounted",
    }[step.charge_type]
    candidates = {c.machine for c in op.candidates}
    if len(candidates) > 1:
        reason += f"; {len(candidates)} capable machines, earliest-free chosen"
    return reason


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
    piece_ready_at: Optional[dict[str, list[TimePoint]]] = None,
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

    `piece_ready_at` (§E.18 Part 2): order_id -> that order's UPSTREAM
    operation's own per-piece completion times, read here (passed down into
    the timing simulation) AND written here — once a result is accepted,
    this run's OWN per-piece completions become the entry for whoever places
    THIS order's next operation. Mutated in place; `None` (the default)
    means the caller isn't tracking it, same as before §E.18 Part 2 existed.
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
    selection = select_machine(members, machine_free_at, ready_at)
    machine = selection.machine

    covered_steps = _recompute_charges([s for s in run_steps if s.order_id in selection.covered_order_ids])
    dropped_steps = [s for s in run_steps if s.order_id in selection.dropped_order_ids]

    # Each member's own readiness is now handled PER-STEP inside
    # _find_feasible_fixture_run / _simulate_run_timing — never pre-maxed
    # across every member here. Pre-maxing was a real bug (caught via live-
    # data E-6 diagnosis of a reported idle-machine gap): it pinned the
    # WHOLE run's start, and left the machine fully idle the entire time, to
    # whichever single member happened to become ready last, even when every
    # other member had been sitting ready for days or weeks. See
    # CLAUDE.md §E.7 and _simulate_run_timing's docstring.
    earliest_start = machine_free_at.get(machine, start_of_day(day_zero))

    fixture = covered_steps[0].fixture
    results, run_start, run_end, piece_completions_by_order = _find_feasible_fixture_run(
        op_by_id, covered_steps, machine, fixture, earliest_start, ready_at, availability, pool,
        piece_ready_at=piece_ready_at,
    )
    if piece_ready_at is not None:
        piece_ready_at.update(piece_completions_by_order)

    pool.reserve_multi(fixture, run_start, run_end)
    placed: list[PlacedOperation] = []
    line_no_by_order: dict[str, int] = {}
    for step, sub_qty, start, end in results:
        # per-slice, no retry — see module docstring. reserve_multi (not
        # reserve) because a LOCATOR value can be a "+"-joined compound of
        # two physical devices that must be held together (confirmed real
        # ERP convention, dispatch_pool.py) — FIXTURE never is, so it keeps
        # using the plain single-device reserve() above.
        pool.reserve_multi(step.locator, start, end)
        op = op_by_id[step.order_id]
        # §E.18: a step sliced into N shift-bound pieces produces N rows for
        # the SAME (production_order, operation_no) — LINE_NO distinguishes
        # them, 1 for a step that never splits (the normal case, unchanged
        # from pre-§E.18 output).
        line_no_by_order[step.order_id] = line_no_by_order.get(step.order_id, 0) + 1
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
                balance_qty=sub_qty,
                batch_key=op.batch_key,
                is_safety_stock=op.is_safety_stock,
                remark=_placement_remark(step, op, machine),
                line_no=line_no_by_order[step.order_id],
            )
        )
        ready_at[step.order_id] = end

    machine_free_at[machine] = advance_clock(run_end, cooling_minutes, machine, availability)

    for step in dropped_steps:
        # Re-run each dropped member as its own single-member fixture run —
        # same mechanism, just a pool of one (it pays fixture_change again,
        # since it's no longer riding along with the run it was dropped from).
        solo = ChargedStep(step.order_id, step.fixture, step.locator, "fixture_change", run_index=-1)
        for p in _place_fixture_run(
            [solo], op_by_id, ready_at, machine_free_at, pool, availability, day_zero, cooling_minutes,
            heavy_operations, resolve_safety_stock, piece_ready_at,
        ):
            tag = "; no common machine covered the whole run" if p.production_order == step.order_id else ""
            placed.append(p if not tag else _retagged(p, tag))

    for step in deferred_steps:
        # §E.10: this safety-stock member was deferred rather than riding
        # the committed portion's mount for free — re-run as its own solo
        # fixture run, same consequence as a machine-selection dropout above.
        solo = ChargedStep(step.order_id, step.fixture, step.locator, "fixture_change", run_index=-1)
        for p in _place_fixture_run(
            [solo], op_by_id, ready_at, machine_free_at, pool, availability, day_zero, cooling_minutes,
            heavy_operations, resolve_safety_stock, piece_ready_at,
        ):
            tag = "; safety stock deferred from mixed heavy-op run (would have delayed a committed order)" if p.production_order == step.order_id else ""
            placed.append(p if not tag else _retagged(p, tag))

    return placed


def _retagged(p: PlacedOperation, suffix: str) -> PlacedOperation:
    """Appends a short suffix to an already-built PlacedOperation's REMARK —
    PlacedOperation is frozen, so this is the one place that rebuilds it
    rather than mutating in place."""
    return replace(p, remark=(p.remark or "") + suffix)


def _place_no_fixture_op(
    op: ScheduledOperation,
    ready_at: dict[str, TimePoint],
    machine_free_at: dict[str, TimePoint],
    availability: AvailabilityFn,
    day_zero: date,
    cooling_minutes: float,
    piece_ready_at: Optional[dict[str, list[TimePoint]]] = None,
) -> list[PlacedOperation]:
    """CLAUDE.md §E.11 fallback — plain routing, no fixture/locator, no pool
    involvement. Returns a list (§E.18): a plain-routing op's `CYCLE_TIME * qty`
    can still span a shift boundary, so it slices exactly like a fixture run
    does, just with no one-time change cost (`one_time=0`, per-piece=`CYCLE_TIME`
    alone — no LOAD_UNLOAD_TIME, which only applies to fixture/locator runs).
    `piece_ready_at` (§E.18 Part 2): read for this op's own per-piece gating,
    then overwritten with THIS op's own per-piece completions for whoever
    places this order's next operation."""
    candidates = {c.machine: c.machine_priority for c in op.candidates}
    for machine in candidates:
        machine_free_at.setdefault(machine, start_of_day(day_zero))  # see _place_fixture_run's comment
    selection = select_machine([RunMember(op.production_order, candidates)], machine_free_at, ready_at)
    machine = selection.machine

    upstream_pieces = piece_ready_at.get(op.production_order) if piece_ready_at else None
    # §E.18 Part 2: floor on the upstream's FIRST piece completing, not its
    # full `ready_at` (last piece) — the per-piece loop below (fed
    # `upstream_pieces`) re-applies the real per-piece gate for pieces 2..N;
    # without this, `cursor` would already sit at the upstream's full
    # completion before the loop even starts, silently defeating Part 2.
    effective_ready = upstream_pieces[0] if upstream_pieces else ready_at[op.production_order]
    start = max(machine_free_at.get(machine, start_of_day(day_zero)), effective_ready)
    slices, piece_completions = _slice_by_shift_raw(op.balance_qty, 0.0, op.cycle_time, start, machine, availability, upstream_pieces)
    if piece_ready_at is not None:
        piece_ready_at[op.production_order] = piece_completions
    end = slices[-1][2]
    machine_free_at[machine] = advance_clock(end, cooling_minutes, machine, availability)
    ready_at[op.production_order] = end  # this order's NEXT schedulable op must not start before this one ends

    # §E.11's own REMARK ("No fixture/locator match - scheduled via plain
    # routing") is a caveat, not an exclusion — still enriched with WHERE it
    # landed, same as a fixture-run placement, so machine-utilization gaps
    # are explainable here too.
    note = f" - on {machine}"
    if len(candidates) > 1:
        note += f"; {len(candidates)} capable machines, earliest-free chosen"
    remark = (op.remark + note) if op.remark else note.lstrip(" -")

    return [
        PlacedOperation(
            production_order=op.production_order,
            operation_no=op.operation_no,
            task=op.task,
            machine=machine,
            fixture_id=None,
            locator_id=None,
            start=slice_start,
            end=slice_end,
            balance_qty=sub_qty,
            batch_key=op.batch_key,
            is_safety_stock=op.is_safety_stock,
            remark=remark,
            line_no=line_no,
        )
        for line_no, (sub_qty, slice_start, slice_end) in enumerate(slices, start=1)
    ]


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
    # Opt-in "soft consolidation beyond the window" (see dispatch_consolidation_
    # window.is_soft_eligible): fixture_runs[i]'s index -> the list of
    # out-of-window-but-soft-eligible solo-run ChargedSteps that SHARE run i's
    # exact (fixture, locator) and could ride its mount for free, pending the
    # full speculative check (dispatch_orchestrator's job — this layer only
    # identifies the candidates, never decides or mutates timeline/pool state).
    # Empty unless `allow_soft_consolidation` was passed to plan_task_pool.
    soft_candidates_by_run: dict[int, list[ChargedStep]] = field(default_factory=dict)


def _compute_soft_candidates(
    fixture_runs: list[list[ChargedStep]],
    op_by_id: dict[str, ScheduledOperation],
    today: date,
    window_days: int,
    soft_max_extra_days: int,
) -> dict[int, list[ChargedStep]]:
    """
    Identifies which solo fixture runs are out-of-window-but-soft-eligible
    candidates, and which OTHER (genuinely committed, in-window) run in the
    same pool they could ride for free. Every out-of-window order is
    ALWAYS alone in its own run under the hard §E.6 walk (the window's
    `is_consolidation_eligible` filter removes it from every append pool
    regardless of whose run is anchoring — not just the run it would have
    matched — so it can never be appended to ANYTHING today, including
    another out-of-window order's run). That's what makes "solo run" a
    reliable signal for "this is a candidate," not just a coincidence.

    Matches on the target run's own FIRST step's (fixture, locator) — the
    exact same identity rule §E.4 steps (a)/(b) already use for a free
    append — so a candidate only ever gets offered a merge it would have
    qualified for on its own terms, had the window not blocked it.
    A target that is itself a solo soft-candidate is skipped (this links
    candidates to an established committed run, not to each other).
    """
    soft_candidates_by_run: dict[int, list[ChargedStep]] = {}
    claimed: set[str] = set()
    for run_idx, run_steps in enumerate(fixture_runs):
        if len(run_steps) != 1:
            continue
        step = run_steps[0]
        if step.order_id in claimed:
            continue
        cdd = op_by_id[step.order_id].cdd
        if not soft_window_eligible(cdd, today, window_days, soft_max_extra_days):
            continue
        for target_idx, target_steps in enumerate(fixture_runs):
            if target_idx == run_idx or not target_steps:
                continue
            if len(target_steps) == 1:
                target_cdd = op_by_id[target_steps[0].order_id].cdd
                if soft_window_eligible(target_cdd, today, window_days, soft_max_extra_days):
                    continue  # don't link one candidate to another candidate
            anchor = target_steps[0]
            if anchor.fixture == step.fixture and anchor.locator == step.locator:
                soft_candidates_by_run.setdefault(target_idx, []).append(step)
                claimed.add(step.order_id)
                break
    return soft_candidates_by_run


def plan_task_pool(
    ops: list[ScheduledOperation],
    today: date,
    window_days: int,
    allow_soft_consolidation: bool = False,
    soft_max_extra_days: int = 0,
) -> TaskPoolPlan:
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

    soft_candidates_by_run: dict[int, list[ChargedStep]] = {}
    if fixture_runs and allow_soft_consolidation:
        soft_candidates_by_run = _compute_soft_candidates(fixture_runs, op_by_id, today, window_days, soft_max_extra_days)

    no_fixture_sorted = sorted(no_fixture_ops, key=lambda o: ranks[o.production_order])
    return TaskPoolPlan(op_by_id, fixture_runs, no_fixture_sorted, soft_candidates_by_run)


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
    piece_ready_at: Optional[dict[str, list[TimePoint]]] = None,
) -> list[PlacedOperation]:
    """
    Place every operation in `ops` (all sharing one TASK, all already past
    their Planned-release gate — the caller's job) onto machines.

    Mutates `ready_at` (each placed order's entry becomes that operation's
    END — the readiness instant its NEXT schedulable operation should use),
    `machine_free_at`, `pool`, and `piece_ready_at` (§E.18 Part 2 — see
    `_place_fixture_run`) in place; returns the placed rows.

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
                heavy_operations, resolve_safety_stock, piece_ready_at,
            )
        )
    for op in plan.no_fixture_ops:
        placed.extend(_place_no_fixture_op(op, ready_at, machine_free_at, availability, day_zero, cooling_minutes, piece_ready_at))
    return placed
