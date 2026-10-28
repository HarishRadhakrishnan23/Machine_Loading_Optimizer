"""
plan.py — Phase 2: the formed fixture runs ("the plan") and a replay of them.

Engine 1 decides batching up front (`_build_work_items`: one WorkItem per fixture run, or per
no-fixture op) and then places the work items one by one in list order (the outer loop inside
`run_dispatch_simulation`). To answer "what if run R moved to machine B" or "what if run X ran
before run Y", Engine 2 keeps the runs exactly as Engine 1 formed them, edits only the machine
choice or the list order, and replays placement. `replay` below is Engine 2's OWN copy of that
small outer loop, driving Engine 1's own placement functions (`_place_work_item`, the
admission-check helpers, ...) unchanged.

THE GATE: an unedited replay must reproduce `run_dispatch_simulation` exactly (every row).
`testing/test_engine2_plan.py` checks it on synthetic data and `testing/engine2_live_parity.py`
on live data. If Engine 1's loop ever changes, those fail — that is the tripwire, because this
loop is a deliberate copy (the only way to edit the order without touching Engine 1).

It depends on Engine 1's private helpers (leading underscore), imported by name.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta

from dispatch_engine import PlacedOperation
from dispatch_orchestrator import (
    FinalRow,
    WorkItem,
    _assemble_final_rows,
    _build_work_items,
    _decide_admission,
    _initial_ready_at,
    _make_committed_safe_resolver,
    _place_work_item,
    _recompute_charges,
    _should_skip,
)
from engine2.runner import build_chains
from engine2.snapshot import Snapshot

MERGE_REMARK = "; soft-consolidated beyond window (speculatively confirmed safe)"  # must match Engine 1's text


@dataclass
class Plan:
    """The runs Engine 1 formed for a given input set, in Engine 1's own placement order."""

    chains: list
    work_items: list[WorkItem]


@dataclass
class ReplayResult:
    rows: list[FinalRow]
    # work-item index -> the placements that item produced (a "run's" rows; includes members the
    # engine dropped out/deferred into solo runs, and members merged in by an admission check)
    placed_by_item: dict[int, list[PlacedOperation]]


def build_plan(snap: Snapshot, raw_rows=None, fnl_df=None) -> Plan:
    """Form the runs exactly as Engine 1 does (same function, same inputs)."""
    config = snap.config
    chains = build_chains(snap, raw_rows, fnl_df)
    work_items = _build_work_items(
        chains,
        snap.run_date,
        config["batch_consolidation_window_days"],
        config.get("allow_soft_consolidation_beyond_window", False),
        config.get("soft_consolidation_max_extra_days", 0),
    )
    return Plan(chains=chains, work_items=work_items)


def replay(snap: Snapshot, plan: Plan, work_items: list[WorkItem] | None = None) -> ReplayResult:
    """Place `work_items` (default: the plan's own, unedited) in list order. Mirrors the outer loop of
    `dispatch_orchestrator.run_dispatch_simulation` (with the pipeline's settings:
    enable_safety_stock_speculation=True)."""
    work_items = plan.work_items if work_items is None else work_items
    config = snap.config
    chains = plan.chains
    today = day_zero = snap.run_date
    window_days = config["batch_consolidation_window_days"]
    cooling_minutes = config["cooling_minutes"]
    buffer_days = config["planned_order_start_buffer_days"]
    heavy_operations = frozenset(config["heavy_operations"])
    allow_soft = config.get("allow_soft_consolidation_beyond_window", False)
    availability = snap.availability
    pool = snap.pool.clone()

    ready_at = {
        chain.production_order: _initial_ready_at(chain.schedulable[0], day_zero, buffer_days)
        for chain in chains
        if chain.schedulable
    }
    piece_ready_at: dict = {}
    machine_free_at: dict = {}
    placed_by_order: dict[str, list[PlacedOperation]] = {chain.production_order: [] for chain in chains}
    cdd_by_order = {chain.production_order: (chain.schedulable[0].cdd if chain.schedulable else None) for chain in chains}
    window_cutoff = today + timedelta(days=window_days)
    merged_keys: set[tuple[int, str]] = set()
    placed_by_item: dict[int, list[PlacedOperation]] = {}

    for i, item in enumerate(work_items):
        if _should_skip(item, frozenset(merged_keys)):
            continue

        resolver = None
        if heavy_operations:
            resolver = _make_committed_safe_resolver(
                work_items, i, ready_at, machine_free_at, pool, placed_by_order, cdd_by_order,
                availability, day_zero, cooling_minutes, window_cutoff, piece_ready_at,
            )

        effective_item = item
        merged_order_ids: frozenset[str] = frozenset()
        if allow_soft and item.run_steps is not None and item.soft_merge_candidates:
            accepted = _decide_admission(
                item.run_steps, item.soft_merge_candidates, item.depth, item.op_by_id,
                work_items, i, ready_at, machine_free_at, pool, placed_by_order, cdd_by_order,
                availability, day_zero, cooling_minutes, window_cutoff, piece_ready_at,
            )
            if accepted:
                merged_steps = _recompute_charges(item.run_steps + item.soft_merge_candidates)
                effective_item = WorkItem(item.depth, item.task, item.op_by_id, merged_steps, None, None)
                merged_order_ids = frozenset(s.order_id for s in item.soft_merge_candidates)
                merged_keys.update((item.depth, oid) for oid in merged_order_ids)

        for placed in _place_work_item(
            effective_item, ready_at, machine_free_at, pool, availability, day_zero, cooling_minutes,
            heavy_operations, resolver, piece_ready_at,
        ):
            if placed.production_order in merged_order_ids:
                placed = replace(placed, remark=(placed.remark or "") + MERGE_REMARK)
            placed_by_order[placed.production_order].append(placed)
            placed_by_item.setdefault(i, []).append(placed)

    return ReplayResult(rows=_assemble_final_rows(chains, placed_by_order), placed_by_item=placed_by_item)
