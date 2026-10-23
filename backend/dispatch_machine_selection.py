"""
dispatch_machine_selection.py — Engine 1 Model F: machine selection for a
formed fixture run (CLAUDE.md §E.5, revised under Model F — see also §E.19
Rule 2, the determinism contract's exact statement of this procedure).

Given a fixture run (the members one dispatch_batching.run_priority_walk pass
has already grouped into a single run), pick which physical machine actually
runs it:

  1. Intersect the capable-machine sets of every member (from
     MCH_MACHINE_FNL_MASTER, evaluated per member's own SIZE~CLASS~MOC~DESIGN
     + TASK).
  2. If the intersection is non-empty: walk candidates in ascending `PRIORITY`
     (1 = most preferred) and take the FIRST one that is currently available
     (its `machine_free_at` is at or before this run's own ready instant — the
     earliest any covered member could possibly start). A lower-priority-
     number machine always wins over a higher-priority-number one as long as
     it's actually free, even if the higher-priority machine became idle
     earlier — Model E's rule was the reverse (earliest-free primary, priority
     only a tiebreak on an exact simultaneous free time); Model F deliberately
     flips that. If NO candidate is currently available, there is nothing to
     prefer among — wait for whichever one frees up earliest (tie-break by
     priority, then machine name, for determinism).
  3. If the intersection is empty (routing genuinely diverges across members —
     rare): keep the run intact on ONE common machine and drop the incapable
     member(s), rather than splitting the whole run apart. The chosen machine
     is the one covering the most members, selected by the same priority-
     first procedure as step 2 among the max-coverage candidates. Dropped
     members are returned separately so the caller can schedule them in
     their own run.

`machine_free_at` and `ready_at` are supplied by the caller (the continuous-
timeline layer) — this module is pure selection logic, no timeline of its
own. Any mutually-orderable value works for both (minutes, a TimePoint); a
machine absent from `machine_free_at` is treated as free from the beginning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RunMember:
    order_id: str
    # machine_name -> PRIORITY (1 = most preferred) for THIS member. Only
    # machines capable of this member appear here (from MCH_MACHINE_FNL_MASTER).
    candidates: dict[str, int]


@dataclass(frozen=True)
class MachineSelection:
    machine: str
    covered_order_ids: list[str]  # members that stay in this run, on `machine`
    dropped_order_ids: list[str]  # members with no capability on `machine` — schedule in their own run


def _worst_priority(machine: str, members: list[RunMember]) -> int:
    """A machine is only as good as its worst-supported member (conservative tie-break)."""
    return max(m.candidates[machine] for m in members if machine in m.candidates)


def _pick_priority_first(
    candidates: set[str],
    members: list[RunMember],
    machine_free_at: dict[str, Any],
    run_ready: Any,
) -> str:
    """
    CLAUDE.md §E.5/§E.19 Rule 2: ascending-priority walk, first currently-
    available candidate wins; none available -> earliest to free, tie-broken
    by priority then machine name. `ordered` is sorted once by
    (priority, machine name) — a fixed, deterministic total order — so both
    the "first available" scan and the "none available" fallback's `min()`
    (stable, so ties resolve to the first-encountered = best-priority,
    alphabetically-first candidate) read directly off the same ordering.
    """

    def free_at(machine: str) -> Any:
        return machine_free_at.get(machine, 0)

    ordered = sorted(candidates, key=lambda mach: (_worst_priority(mach, members), mach))
    for mach in ordered:
        if free_at(mach) <= run_ready:
            return mach
    return min(ordered, key=free_at)


def select_machine(
    members: list[RunMember],
    machine_free_at: dict[str, Any],
    ready_at: dict[str, Any],
) -> MachineSelection:
    """
    CLAUDE.md §E.5 (Model F). Raises ValueError if `members` is empty —
    nothing to select for. `ready_at`: order_id -> this member's own earliest
    possible start (its own precedence/readiness, independent of any
    machine) — every member's order_id must have an entry; the run's overall
    ready instant is the earliest (min) among its covered members, since
    that's the earliest point this run could possibly begin on any capable
    machine.
    """
    if not members:
        raise ValueError("select_machine requires at least one member")

    intersection = set(members[0].candidates)
    for m in members[1:]:
        intersection &= set(m.candidates)

    if intersection:
        run_ready = min(ready_at[m.order_id] for m in members)
        chosen = _pick_priority_first(intersection, members, machine_free_at, run_ready)
        return MachineSelection(
            machine=chosen,
            covered_order_ids=[m.order_id for m in members],
            dropped_order_ids=[],
        )

    # Partial coverage: pick the machine covering the most members.
    all_machines = {mach for m in members for mach in m.candidates}
    coverage = {mach: [m for m in members if mach in m.candidates] for mach in all_machines}
    max_covered = max(len(v) for v in coverage.values())
    best_machines = {mach for mach, v in coverage.items() if len(v) == max_covered}

    # Each candidate machine in `best_machines` may cover a different member
    # subset, so there's no single `members` list to prioritize against like
    # the full-intersection case — fold all candidate machines' own covered
    # members together; `_worst_priority`/`run_ready` only ever look up a
    # machine against members that actually list it, so this is safe.
    all_covered_members = [m for v in coverage.values() for m in v]
    run_ready = min(ready_at[m.order_id] for m in all_covered_members)
    chosen = _pick_priority_first(best_machines, all_covered_members, machine_free_at, run_ready)
    covered_ids = [m.order_id for m in coverage[chosen]]
    dropped_ids = [m.order_id for m in members if m.order_id not in covered_ids]

    return MachineSelection(machine=chosen, covered_order_ids=covered_ids, dropped_order_ids=dropped_ids)
