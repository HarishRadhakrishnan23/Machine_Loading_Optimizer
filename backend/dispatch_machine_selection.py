"""
dispatch_machine_selection.py — Engine 1 Model E: machine selection for a
formed fixture run (CLAUDE.md §E.5).

Given a fixture run (the members one dispatch_batching.run_priority_walk pass
has already grouped into a single run), pick which physical machine actually
runs it:

  1. Intersect the capable-machine sets of every member (from MCH_MACHINE_PRIORITY,
     evaluated per member's own SIZE~CLASS~MOC~DESIGN + TASK).
  2. If the intersection is non-empty: pick the machine free earliest; break
     ties by lowest MACHINE_PRIORITY. Priority is soft — never wait on a
     preferred machine while another capable machine is idle and could take
     the work.
  3. If the intersection is empty (routing genuinely diverges across members —
     rare): keep the run intact on ONE common machine and drop the incapable
     member(s), rather than splitting the whole run apart. The chosen machine
     is the one covering the most members; ties broken the same way (free-
     earliest, then priority). Dropped members are returned separately so the
     caller can schedule them in their own run.

`machine_free_at` is supplied by the caller (the continuous-timeline layer,
a later Model E sub-phase) — this module is pure selection logic, no timeline
of its own. Any orderable value works for "free at" (minutes, a timestamp);
a machine absent from the mapping is treated as free right now.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RunMember:
    order_id: str
    # machine_name -> MACHINE_PRIORITY (1 = most preferred) for THIS member.
    # Only machines capable of this member appear here (from MCH_MACHINE_PRIORITY).
    candidates: dict[str, int]


@dataclass(frozen=True)
class MachineSelection:
    machine: str
    covered_order_ids: list[str]  # members that stay in this run, on `machine`
    dropped_order_ids: list[str]  # members with no capability on `machine` — schedule in their own run


def _worst_priority(machine: str, members: list[RunMember]) -> int:
    """A machine is only as good as its worst-supported member (conservative tie-break)."""
    return max(m.candidates[machine] for m in members if machine in m.candidates)


def select_machine(members: list[RunMember], machine_free_at: dict[str, float]) -> MachineSelection:
    """CLAUDE.md §E.5. Raises ValueError if `members` is empty — nothing to select for."""
    if not members:
        raise ValueError("select_machine requires at least one member")

    def free_at(machine: str) -> float:
        return machine_free_at.get(machine, 0)

    intersection = set(members[0].candidates)
    for m in members[1:]:
        intersection &= set(m.candidates)

    if intersection:
        chosen = min(intersection, key=lambda mach: (free_at(mach), _worst_priority(mach, members)))
        return MachineSelection(
            machine=chosen,
            covered_order_ids=[m.order_id for m in members],
            dropped_order_ids=[],
        )

    # Partial coverage: pick the machine covering the most members.
    all_machines = {mach for m in members for mach in m.candidates}
    coverage = {mach: [m for m in members if mach in m.candidates] for mach in all_machines}
    max_covered = max(len(v) for v in coverage.values())
    best_machines = [mach for mach, v in coverage.items() if len(v) == max_covered]

    chosen = min(best_machines, key=lambda mach: (free_at(mach), _worst_priority(mach, coverage[mach])))
    covered_ids = [m.order_id for m in coverage[chosen]]
    dropped_ids = [m.order_id for m in members if m.order_id not in covered_ids]

    return MachineSelection(machine=chosen, covered_order_ids=covered_ids, dropped_order_ids=dropped_ids)
