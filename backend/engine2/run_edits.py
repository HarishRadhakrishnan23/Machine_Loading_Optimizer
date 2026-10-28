"""
run_edits.py — Phase 2 what-ifs on the FORMED fixture runs (CLAUDE.md "Engine 2", K3/K4).

A "run" is one of Engine 1's formed work items, identified by its baseline index (`run id`). Two
edits, composable (several per scenario, applied in order):

  MoveRun(run, machine)            "what if run R ran on machine B?"
  PlaceBefore(run, before, pin)    "what if run X ran before run Y?"

How they work without touching Engine 1:

  * Machine choice is an INPUT: restricting a member's candidate machines to [B] forces B. Because
    Engine 1's selection picks the machine covering the MOST members of a run, a PARTIAL move
    (B cannot take every member) splits the run the way Engine 1 itself splits a dropout: the
    members B can take stay in the run (pinned to B), the others become their own solo runs placed
    right after it. Coverage ("covers 12 of 15") is reported. A moved run's admission-check merges
    are switched off (they could otherwise out-vote the move).
  * Order is the list order of the work items (`engine2.plan.replay` places in list order). Putting
    X before Y moves X to just before Y and PULLS FORWARD every feeder run (the earlier-operation
    runs that produce X's pieces, recursively) that is not already ahead of Y, because a run
    placed before its feeders would start without its inputs. The result lists what was pulled.
    X cannot go before Y if X depends on Y.
  * `pin` (default on): with ordering edits, X and Y keep the machines they have today, so the result
    isolates the effect of ORDER. Turn it off to let the engine re-pick machines.
  * Admission-check merges: a merge candidate whose own run now sits ahead of its host would be
    placed twice; such candidates are dropped from the host (reported).

Everything is validated (ScenarioError, shown to the planner) and the replay it feeds is parity-
gated against Engine 1 (test_engine2_plan.py, engine2_live_parity.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional

from dispatch_batching import ChargedStep
from dispatch_engine import PlacedOperation
from dispatch_orchestrator import WorkItem, _recompute_charges
from engine2.plan import Plan, ReplayResult, replay
from engine2.scenarios import ScenarioError
from engine2.snapshot import Snapshot
from shift_clock import timepoint_to_datetime


# ─────────────────────────────────────────────────────────────────────────────
# Edits
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class MoveRun:
    run: int
    machine: str


@dataclass(frozen=True)
class PlaceBefore:
    run: int
    before: int
    pin: bool = True


@dataclass
class _Entry:
    """One baseline run's slot in the working order, plus the edits that change how it is built."""

    base_id: int
    move_to: Optional[str] = None
    pin: bool = False


@dataclass
class EditOutcome:
    work_items: list[WorkItem]
    spans: dict[int, list[int]]  # base run id -> indexes of its items in `work_items`
    edited: list[int]  # base ids the planner edited
    pulled: list[int]  # base ids pulled forward as feeders
    notes: list[str]
    targets: dict = field(default_factory=dict)  # base id -> machine it was moved to


# ─────────────────────────────────────────────────────────────────────────────
# Baseline structure
# ─────────────────────────────────────────────────────────────────────────────
def _orders_of(base: ReplayResult, i: int) -> list[str]:
    seen: list[str] = []
    for p in base.placed_by_item.get(i, []):
        if p.production_order not in seen:
            seen.append(p.production_order)
    return seen


def _capable(item: WorkItem, order_id: str) -> set[str]:
    if item.run_steps is not None:
        return {c.machine for c in item.op_by_id[order_id].candidates}
    return {c.machine for c in item.no_fixture_op.candidates}


def _members(item: WorkItem) -> list[str]:
    if item.run_steps is not None:
        return [s.order_id for s in item.run_steps]
    return [item.no_fixture_op.production_order]


def _feeders(plan: Plan, base: ReplayResult) -> dict[int, set[int]]:
    """base run id -> ids of the runs that placed each member's PREVIOUS operation."""
    item_of: dict[tuple[str, int], int] = {}
    for i in base.placed_by_item:
        for o in _orders_of(base, i):
            item_of[(o, plan.work_items[i].depth)] = i
    out: dict[int, set[int]] = {}
    for i in base.placed_by_item:
        d = plan.work_items[i].depth
        out[i] = {item_of[(o, d - 1)] for o in _orders_of(base, i) if (o, d - 1) in item_of}
    return out


def _closure(feeders: dict[int, set[int]], x: int) -> set[int]:
    seen: set[int] = set()
    stack = list(feeders.get(x, ()))
    while stack:
        n = stack.pop()
        if n not in seen:
            seen.add(n)
            stack.extend(feeders.get(n, ()))
    return seen


# ─────────────────────────────────────────────────────────────────────────────
# Building one entry's work items
# ─────────────────────────────────────────────────────────────────────────────
def _restrict(item: WorkItem, machine_by_order: dict[str, str], soft_candidates="keep") -> WorkItem:
    """Copy of `item` with the listed orders' candidate machines restricted to one machine each."""
    soft = item.soft_merge_candidates if soft_candidates == "keep" else soft_candidates
    if item.run_steps is not None:
        ops = dict(item.op_by_id)
        for oid, m in machine_by_order.items():
            if oid not in ops:
                continue
            kept = [c for c in ops[oid].candidates if c.machine == m]
            if kept:
                ops[oid] = replace(ops[oid], candidates=kept)
        return WorkItem(item.depth, item.task, ops, item.run_steps, None, soft)
    op = item.no_fixture_op
    kept = [c for c in op.candidates if c.machine == machine_by_order.get(op.production_order)]
    return WorkItem(item.depth, item.task, item.op_by_id, None, replace(op, candidates=kept) if kept else op, None)


def _build_entry(plan: Plan, base: ReplayResult, e: _Entry, notes: list[str]) -> list[WorkItem]:
    item = plan.work_items[e.base_id]

    if e.move_to is not None:
        machine = e.move_to
        members = _members(item)
        capable_ids = [o for o in members if machine in _capable(item, o)]
        if not capable_ids:
            raise ScenarioError(f"No order in run {e.base_id} can run on {machine}.")
        coverage = f"covers {len(capable_ids)} of {len(members)} orders"
        if item.run_steps is None:  # a single no-fixture op
            notes.append(f"Run {e.base_id} moved to {machine} ({coverage}).")
            return [_restrict(item, {capable_ids[0]: machine})]
        incapable = [s for s in item.run_steps if s.order_id not in capable_ids]
        covered = [s for s in item.run_steps if s.order_id in capable_ids]
        if incapable:
            covered = _recompute_charges(covered)  # who is mounted next to whom changed
        host = _restrict(
            WorkItem(item.depth, item.task, item.op_by_id, covered, None, None),
            {o: machine for o in capable_ids},
            soft_candidates=None,  # admission merges could out-vote the move — off for a moved run
        )
        solos = [
            WorkItem(item.depth, item.task, item.op_by_id,
                     [ChargedStep(s.order_id, s.fixture, s.locator, "fixture_change", run_index=-1)], None, None)
            for s in incapable
        ]
        msg = f"Run {e.base_id} moved to {machine} ({coverage})"
        if incapable:
            msg += f"; the other {len(incapable)} order(s) are scheduled as their own runs, picking their normal machine"
        notes.append(msg + ".")
        return [host] + solos

    if e.pin:
        pinned = {}
        for p in base.placed_by_item.get(e.base_id, []):
            pinned.setdefault(p.production_order, p.machine)
        return [_restrict(item, pinned)]
    return [item]


def _shield_moved(items: list[WorkItem], moved: set[tuple[int, str]], notes: list[str]) -> list[WorkItem]:
    """A moved run's orders must stay where the planner put them: remove them from every OTHER run's
    admission-merge candidates (a merge would silently place them on that run's machine)."""
    if not moved:
        return items
    out = list(items)
    removed = 0
    for pos, w in enumerate(items):
        if w.run_steps is None or not w.soft_merge_candidates:
            continue
        keep = [c for c in w.soft_merge_candidates if (w.depth, c.order_id) not in moved]
        if len(keep) != len(w.soft_merge_candidates):
            removed += len(w.soft_merge_candidates) - len(keep)
            out[pos] = WorkItem(w.depth, w.task, w.op_by_id, w.run_steps, None, keep or None)
    if removed:
        notes.append(f"{removed} order(s) of the moved run(s) are no longer eligible to be merged into another run.")
    return out


def _drop_double_placements(items: list[WorkItem], notes: list[str]) -> list[WorkItem]:
    """An admission-merge candidate whose own solo run now sits AHEAD of its host would be placed
    twice (once solo, once merged). Drop such candidates from the host."""
    solo_pos: dict[tuple[int, str], int] = {}
    for pos, w in enumerate(items):
        if w.run_steps is not None and len(w.run_steps) == 1:
            solo_pos.setdefault((w.depth, w.run_steps[0].order_id), pos)
    out = list(items)
    for pos, w in enumerate(items):
        if w.run_steps is None or not w.soft_merge_candidates:
            continue
        keep = [c for c in w.soft_merge_candidates if solo_pos.get((w.depth, c.order_id), 10**9) > pos]
        if len(keep) != len(w.soft_merge_candidates):
            notes.append(
                f"{len(w.soft_merge_candidates) - len(keep)} far-CDD order(s) now run before the run they would "
                "have been merged into, so they are not merged."
            )
            out[pos] = WorkItem(w.depth, w.task, w.op_by_id, w.run_steps, None, keep or None)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Applying a list of edits
# ─────────────────────────────────────────────────────────────────────────────
def apply_edits(plan: Plan, base: ReplayResult, edits: tuple) -> EditOutcome:
    valid = set(base.placed_by_item)
    feeders = _feeders(plan, base)
    entries = [_Entry(i) for i in range(len(plan.work_items))]
    by_id = {e.base_id: e for e in entries}
    edited: list[int] = []
    pulled: list[int] = []
    notes: list[str] = []

    def check(run: int) -> None:
        if run not in valid:
            raise ScenarioError(
                f"Run {run} is not a run in the current schedule (unknown id, or its orders were merged into another run)."
            )

    for edit in edits:
        if isinstance(edit, MoveRun):
            check(edit.run)
            by_id[edit.run].move_to = edit.machine
            edited.append(edit.run)
        elif isinstance(edit, PlaceBefore):
            check(edit.run)
            check(edit.before)
            if edit.run == edit.before:
                raise ScenarioError("A run cannot be placed before itself.")
            x, y = edit.run, edit.before
            needs = _closure(feeders, x)
            if y in needs:
                raise ScenarioError(f"Run {x} needs the output of run {y} first, so it cannot run before it.")
            pos = {e.base_id: p for p, e in enumerate(entries)}
            moving_ids = [i for i in ({x} | needs) if pos[i] >= pos[y]]
            if not moving_ids:
                notes.append(f"Run {x} already runs before run {y}.")
            moving = sorted(moving_ids, key=lambda i: pos[i])
            rest = [e for e in entries if e.base_id not in set(moving)]
            at = next(p for p, e in enumerate(rest) if e.base_id == y)
            entries = rest[:at] + [by_id[i] for i in moving] + rest[at:]
            extra = [i for i in moving if i != x]
            if extra:
                pulled.extend(i for i in extra if i not in pulled)
                shown = sorted(extra)
                listed = ", ".join(map(str, shown[:8])) + (f" (+{len(shown) - 8} more)" if len(shown) > 8 else "")
                notes.append(
                    f"Run {x} placed before run {y}; its {len(shown)} feeder run(s) ({listed}) were pulled "
                    "forward with it so it does not start without its inputs."
                )
            for i in (x, y):
                if edit.pin:
                    by_id[i].pin = True
            edited.extend(i for i in (x,) if i not in edited)
        else:
            raise ScenarioError(f"Unknown edit: {edit!r}")

    flat: list[WorkItem] = []
    spans: dict[int, list[int]] = {}
    for e in entries:
        built = _build_entry(plan, base, e, notes) if (e.move_to is not None or e.pin) else [plan.work_items[e.base_id]]
        spans[e.base_id] = list(range(len(flat), len(flat) + len(built)))
        flat.extend(built)
    moved = {(plan.work_items[e.base_id].depth, o) for e in entries if e.move_to is not None
             for o in _members(plan.work_items[e.base_id])}
    flat = _shield_moved(flat, moved, notes)
    flat = _drop_double_placements(flat, notes)
    return EditOutcome(flat, spans, edited, pulled, notes, targets={e.base_id: e.move_to for e in entries if e.move_to})


# ─────────────────────────────────────────────────────────────────────────────
# Scenario
# ─────────────────────────────────────────────────────────────────────────────
def _summ(placements: list[PlacedOperation]) -> dict:
    if not placements:
        return {"machines": [], "start": None, "end": None}
    start = min(timepoint_to_datetime(p.start) for p in placements)
    end = max(timepoint_to_datetime(p.end) for p in placements)
    return {
        "machines": sorted({p.machine for p in placements}),
        "start": start.isoformat(timespec="minutes"),
        "end": end.isoformat(timespec="minutes"),
    }


@dataclass(frozen=True)
class RunEdits:
    edits: tuple
    kind: str = "runs"

    def describe(self) -> str:
        parts = []
        for e in self.edits:
            if isinstance(e, MoveRun):
                parts.append(f"run {e.run} to {e.machine}")
            else:
                parts.append(f"run {e.run} before run {e.before}")
        text = "Runs: " + "; ".join(parts)
        return text if len(text) <= 190 else text[:187] + "..."

    def elevated_orders(self) -> frozenset:
        return frozenset()

    def run(self, snap: Snapshot, plan: Plan, base: ReplayResult) -> tuple[list, dict]:
        if not self.edits:
            raise ScenarioError("Add at least one edit.")
        outcome = apply_edits(plan, base, self.edits)
        result = replay(snap, plan, outcome.work_items)

        every = [p for placements in result.placed_by_item.values() for p in placements]
        changed = []
        notes = list(outcome.notes)
        for run_id in list(dict.fromkeys(outcome.edited + outcome.pulled)):
            before_pl = base.placed_by_item.get(run_id, [])
            orders = {p.production_order for p in before_pl}
            opnos = {p.operation_no for p in before_pl}
            after = [p for p in every if p.production_order in orders and p.operation_no in opnos]
            target = outcome.targets.get(run_id)
            if target and target not in {p.machine for p in after}:
                notes.append(f"Run {run_id} did not end up on {target}; it ran on {sorted({p.machine for p in after})}.")
            item = plan.work_items[run_id]
            changed.append(
                {
                    "run": run_id,
                    "task": item.task,
                    "orders": len(_orders_of(base, run_id)),
                    "role": "pulled forward" if run_id in outcome.pulled and run_id not in outcome.edited else "edited",
                    "before": _summ(base.placed_by_item.get(run_id, [])),
                    "after": _summ(after),
                }
            )
        return result.rows, {"notes": notes, "runs_changed": changed}
