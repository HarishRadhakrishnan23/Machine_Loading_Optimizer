"""
engine1_scheduler.py — Engine 1: CP-SAT Scheduling Optimizer (Model C).

Logging: Every solve run writes to backend/logs/engine1_YYYYMMDD_HHMMSS.log
This log shows:
  - Pre-solve diagnostics (work vs capacity, routing coverage, precedence depth)
  - Greedy fallback placement trace (which tasks placed where, which failed and why)
  - CP-SAT status and objective value
  - Final schedule summary or failure diagnosis

    "PRODUCTION SHOULD NEVER STOP. Maximise machine + manpower utilisation.
     Setup time is always a cost when a new valve size joins a machine."

Model C — Flexible Per-Slot Routing
───────────────────────────────────
The scheduler allocates integer QUANTITIES of pieces into (machine, slot)
capacity buckets. There is NO continuous minute clock, NO single-machine lock,
and NO contiguity constraint. Time is a discrete lattice of `(date, shift)`
slots addressed by one global, machine-independent slot index:

    slot_index(date, shift) = 3 × day_pos(date) + shift_pos(shift)

A larger slot index is always later in real time for EVERY machine — that is
what makes cross-machine precedence and CDD-relative tardiness well-defined.

Why Model C (and why the old "Model B" was wrong)
─────────────────────────────────────────────────
The previous model assigned each batch to ONE machine for its whole operation
(`AddExactlyOne(assign[t,m])`) and then FORCED every open slot between the
batch's start and end to be occupied (a circular contiguity reification). When
that one machine hit a closed slot (breakdown / maintenance), the batch had
nowhere to go → the model went INFEASIBLE even at ~5% capacity utilisation.

Model C deletes both mechanisms. A task's pieces may be placed on ANY capable
machine in ANY open slot. Consequences that fall out for free:

  • Auto-route on breakdown — a closed slot simply has no variable, so pieces
    flow to the next open (machine, slot). Production never stops.
  • Split across machines — allowed; each machine that newly sees a valve size
    pays that size's SETUP_TIME (charged inside the capacity bucket).
  • Stay on one machine across a shift boundary (shift 3 → shift 1 next day) —
    emergent, because setup is WAIVED when the size carried over from that
    machine's previous OPEN slot, so continuing is free while switching costs.
  • Priority-1 preferred but never idle — earliest slot wins; MACHINE_PRIORITY
    is only a tiny objective tiebreaker, so a priority-2 machine that is free
    sooner beats a priority-1 machine that is down.

Guaranteed feasibility
──────────────────────
A fast greedy pass (earliest-slot, carryover-aware) always produces a valid
schedule. It is used two ways: as a CP-SAT warm-start hint (so the solver has
an optimal-or-near point immediately) and as a FALLBACK returned verbatim if
CP-SAT cannot improve on it within the time budget. Either way, /schedule
always returns a runnable plan.

ALL real-world times (CYCLE_TIME, SETUP_TIME, AVAILABLE_MINS) are in minutes.
CP-SAT is integer-only, so minutes are scaled by TIME_SCALE and the float
urgency weights by URGENCY_SCALE.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

from ortools.sat.python import cp_model

from models import (
    Config,
    SchedulableTask,
    ScheduleOutputRow,
    SchedulerInput,
    SchedulerResult,
    SHIFT_ORDER,
    SolveStatus,
)

# ─────────────────────────────────────────────────────────────────────────────
# Integer scaling constants (CLAUDE.md "Integer scaling")
# ─────────────────────────────────────────────────────────────────────────────
TIME_SCALE = 100       # 2-decimal precision on minutes (AVAILABLE_MINS can be e.g. 365.5)
URGENCY_SCALE = 1000   # 3-decimal precision on objective weights (captures ε = 0.001)

# Loose upper bound for the tardiness IntVar domain (days). Only a domain bound.
_TARDINESS_UPPER_BOUND = 100_000

# Task key = (PRODUCTION_ORDER, OPERATION_NO). Used as dict keys throughout.
TaskKey = tuple[str, float]


def _setup_logging() -> logging.Logger:
    """Create a logger for this solve run, write to backend/logs/."""
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"engine1_{timestamp}.log"

    logger = logging.getLogger(f"engine1_{timestamp}")
    logger.setLevel(logging.DEBUG)
    # File handler: UTF-8, write everything
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(file_handler)
    # Console handler: ASCII-safe (replace unicode with ASCII equivalents)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(console)
    return logger


class Engine1Scheduler:
    """Builds and solves the Model C CP-SAT scheduling model."""

    def __init__(self, scheduler_input: SchedulerInput, logger: Optional[logging.Logger] = None):
        self.input = scheduler_input
        self.config: Config = scheduler_input.config
        self.model = cp_model.CpModel()
        self.logger = logger or logging.getLogger("engine1_noop")

        # Decision variables.
        self.qty: dict[tuple[TaskKey, str, int], cp_model.IntVar] = {}
        self.occ: dict[tuple[TaskKey, str, int], cp_model.IntVar] = {}
        self.occ_any: dict[tuple[TaskKey, int], cp_model.IntVar] = {}
        self.start_slot: dict[TaskKey, cp_model.IntVar] = {}
        self.end_slot: dict[TaskKey, cp_model.IntVar] = {}

        # Setup-carryover bookkeeping, keyed (item_category, machine, slot).
        self.cat_present: dict[tuple[str, str, int], cp_model.IntVar] = {}
        self.carried: dict[tuple[str, str, int], cp_model.IntVar] = {}

        self._index_horizon()
        self._index_capacity()
        self._index_tasks()

    # ── indexing (data only — no CP-SAT variables yet) ──────────────────────
    def _index_horizon(self) -> None:
        """D = horizon_dates (already sorted+deduped by SchedulerInput). K = |D|×3."""
        self.D: list[date] = list(self.input.horizon_dates)
        self.date_to_day_pos: dict[date, int] = {d: i for i, d in enumerate(self.D)}
        self.K: int = len(self.D) * 3

    def _index_capacity(self) -> None:
        """
        cap_s[m, k] = round(AVAILABLE_MINS × TIME_SCALE) for every (machine, slot).
        open_slots[m] = sorted slots with cap_s > 0 (closed slots get NO variables,
        which is a stronger, cheaper guarantee than a constraint forcing qty to 0).
        prev_open[(m, k)] = the machine's previous OPEN slot (for setup carryover).
        """
        self.cap_s: dict[tuple[str, int], int] = {}
        for slot in self.input.capacity:
            day_pos = self.date_to_day_pos.get(slot.slot_date)
            if day_pos is None:
                continue  # capacity row outside the horizon window
            shift_pos = SHIFT_ORDER.index(slot.shift)
            k = 3 * day_pos + shift_pos
            self.cap_s[(slot.machine_name, k)] = round(slot.available_mins * TIME_SCALE)

        self.open_slots: dict[str, list[int]] = {}
        for (m, k), cap in self.cap_s.items():
            if cap > 0:
                self.open_slots.setdefault(m, []).append(k)
        for k_list in self.open_slots.values():
            k_list.sort()

        # prev_open lookup: for each machine, map every open slot to the one before it.
        self.prev_open: dict[tuple[str, int], Optional[int]] = {}
        for m, k_list in self.open_slots.items():
            prev = None
            for k in k_list:
                self.prev_open[(m, k)] = prev
                prev = k

    def _index_tasks(self) -> None:
        """Candidate machines, scaled cycle/setup times, priorities, order grouping."""
        self.task_of: dict[TaskKey, SchedulableTask] = {}
        self.candidates: dict[TaskKey, list[str]] = {}
        self.machine_priority: dict[tuple[TaskKey, str], int] = {}
        self.ct_s: dict[TaskKey, int] = {}
        # SETUP_TIME is "per machine per ITEM_CATEGORY change" ⇒ keyed (category, machine).
        self.setup_s: dict[tuple[str, str], int] = {}
        self.categories_for_machine: dict[str, set[str]] = {}
        self.tasks_for_cat_machine: dict[tuple[str, str], list[TaskKey]] = {}
        self.tasks_by_order: dict[str, list[SchedulableTask]] = {}

        # Reverse index: which task keys have a (qty, occ) var at a given (machine, slot).
        # Use set (converted to list for iteration) to prevent duplicate pids.
        self.pids_by_machine_slot: dict[tuple[str, int], set[TaskKey]] = {}

        # Batch grouping: orders with the same SIZE~CLASS~DESIGN (excluding MOC) going
        # through the SAME operation (TASK code) are queued together on one machine to
        # minimise setups — mirrors how the production planning engineer clubs orders
        # before Op10 and every operation thereafter. Safety-stock orders (CDD=NULL)
        # are included in their batch like any other order (flagged separately downstream).
        self.batch_key_of: dict[TaskKey, str] = {}
        self.batch_group_of: dict[TaskKey, tuple[str, str]] = {}
        self.tasks_in_batch_group: dict[tuple[str, str], list[TaskKey]] = {}

        for task in self.input.tasks:
            pid: TaskKey = (task.production_order, task.operation_no)
            self.task_of[pid] = task
            self.ct_s[pid] = round(task.cycle_time * TIME_SCALE)
            # Deduplicate candidate machines (routing may have duplicates)
            self.candidates[pid] = list(dict.fromkeys(c.machine_name for c in task.candidates))
            self.tasks_by_order.setdefault(task.production_order, []).append(task)

            # task.batch_key is computed straight from the typed SIZE_INCH/CLASS/DESIGN
            # WIP columns in preprocess.py — NOT parsed from item_category, whose segment
            # order shifts when DESIGN is blank in the ERP data.
            batch_key = task.batch_key
            group_key = (batch_key, task.operation)
            self.batch_key_of[pid] = batch_key
            self.batch_group_of[pid] = group_key
            self.tasks_in_batch_group.setdefault(group_key, []).append(pid)

            for c in task.candidates:
                self.machine_priority[(pid, c.machine_name)] = c.machine_priority
                setup_key = (task.item_category, c.machine_name)
                if setup_key not in self.setup_s:
                    self.setup_s[setup_key] = round(c.setup_time * TIME_SCALE)
                self.categories_for_machine.setdefault(c.machine_name, set()).add(task.item_category)
                self.tasks_for_cat_machine.setdefault((task.item_category, c.machine_name), []).append(pid)

    # ── diagnostics ──────────────────────────────────────────────────────────
    def diagnose_feasibility(self) -> None:
        """Print pre-solve diagnostics: work vs capacity, routing coverage, chain depth."""
        self.logger.info("\n" + "=" * 70)
        self.logger.info("PRE-SOLVE DIAGNOSTICS (Model C — flexible routing)")
        self.logger.info("=" * 70)

        total_work_mins = sum(t.balance_qty * t.cycle_time for t in self.input.tasks)
        self.logger.info("\nWork load:")
        self.logger.info(f"  Total piece-minutes: {total_work_mins:,.0f} min ({total_work_mins/60/480:.1f} machine-days)")

        total_available = sum(self.cap_s.values()) / TIME_SCALE
        ratio = total_work_mins / total_available if total_available else float("inf")
        self.logger.info(f"  Total available:     {total_available:,.0f} min ({total_available/60/480:.1f} machine-days)")
        self.logger.info(f"  Capacity ratio:      {ratio:.1%} (1.0 = perfectly tight)")

        unroutable = [
            t for t in self.input.tasks
            if not self.candidates.get((t.production_order, t.operation_no), [])
        ]
        if unroutable:
            self.logger.warning(f"\n⚠️  {len(unroutable)} tasks have no capable machines:")
            for t in unroutable[:5]:
                self.logger.warning(f"     {t.production_order} Op{t.operation_no}: TASK={t.operation}")
        else:
            self.logger.info(f"\n✓ All {len(self.input.tasks)} tasks have routing coverage")

        max_ops = max((len(v) for v in self.tasks_by_order.values()), default=0)
        self.logger.info("\nOrders:")
        self.logger.info(f"  Unique production orders: {len(self.tasks_by_order)}")
        self.logger.info(f"  Max operations per order: {max_ops}")
        self.logger.info(f"  Total tasks:              {len(self.input.tasks)}")
        self.logger.info(f"\nCategories (valve sizes): {len({t.item_category for t in self.input.tasks})}")
        self.logger.info("=" * 70 + "\n")

    # ── model construction ───────────────────────────────────────────────────
    def build_model(self) -> None:
        """Assemble the full CP-SAT model. Order matters: variables, then constraints."""
        self._create_variables()
        self._add_batch_continuity()
        self._add_capacity_and_setup()
        self._add_precedence()
        self._build_objective()

    def _create_variables(self) -> None:
        """
        Per task t: qty[t,m,k]/occ[t,m,k] for every OPEN slot k of every capable
        machine m; occ_any[t,k] = OR over machines; start/end slot via Min/Max over
        k·occ_any; completeness Σ qty = balance_qty (every piece scheduled somewhere).
        """
        model = self.model
        for pid, task in self.task_of.items():
            q_t = task.balance_qty
            machines = self.candidates[pid]
            tag = f"{pid[0]}|{pid[1]}"

            achievable = sorted({k for m in machines for k in self.open_slots.get(m, [])})
            if not achievable:
                raise ValueError(
                    f"Task {pid} has no open (machine, slot) within the horizon on any "
                    f"candidate machine {machines} — horizon too short or all candidates closed."
                )

            for m in machines:
                for k in self.open_slots.get(m, []):
                    qv = model.NewIntVar(0, q_t, f"qty[{tag}|{m}|{k}]")
                    ov = model.NewBoolVar(f"occ[{tag}|{m}|{k}]")
                    self.qty[(pid, m, k)] = qv
                    self.occ[(pid, m, k)] = ov
                    model.Add(qv <= q_t * ov)  # occ = 0 ⇒ qty = 0
                    model.Add(ov <= qv)        # qty = 0 ⇒ occ = 0
                    self.pids_by_machine_slot.setdefault((m, k), set()).add(pid)

            # Every piece of the batch is scheduled somewhere (production never stops).
            model.Add(
                sum(self.qty[(pid, m, k)] for m in machines for k in self.open_slots.get(m, [])) == q_t
            )

            # occ_any[t,k] = OR over candidate machines of occ[t,m,k].
            for k in achievable:
                relevant = [self.occ[(pid, m, k)] for m in machines if (pid, m, k) in self.occ]
                if len(relevant) == 1:
                    oa = relevant[0]
                else:
                    oa = model.NewBoolVar(f"occ_any[{tag}|{k}]")
                    model.AddMaxEquality(oa, relevant)
                self.occ_any[(pid, k)] = oa

            # start_slot / end_slot via Min/MaxEquality over k·occ_any.
            lo, hi = achievable[0], achievable[-1]
            big = hi + 1  # sentinel > any real slot index in this task's range
            end_v = model.NewIntVar(lo, hi, f"end_slot[{tag}]")
            start_v = model.NewIntVar(lo, hi, f"start_slot[{tag}]")
            model.AddMaxEquality(end_v, [k * self.occ_any[(pid, k)] for k in achievable])
            model.AddMinEquality(
                start_v,
                [k * self.occ_any[(pid, k)] + big * (1 - self.occ_any[(pid, k)]) for k in achievable],
            )
            self.end_slot[pid] = end_v
            self.start_slot[pid] = start_v

    def _add_batch_continuity(self) -> None:
        """
        Hard constraint: for each batch group — orders sharing SIZE~CLASS~DESIGN
        (excluding MOC) at the SAME operation (TASK code) — all member tasks must
        place every piece on ONE common machine. Overflow to further OPEN slots on
        that same machine is unrestricted (batch continuity across shift/day
        boundaries), matching how a production planning engineer queues similar
        valves through an operation together to avoid repeated setups.

        Only machines capable of EVERY task in the group are eligible. If the group
        has no common machine (routing diverges — typically because MOC affects
        candidate machines even though MOC is excluded from the batch key), no
        constraint is added for that group: those tasks fall back to fully flexible
        per-slot routing rather than blocking production over a batching preference.

        A batch may split after this operation — grouping is evaluated independently
        per (batch_key, TASK), not carried across an order's whole routing.
        """
        model = self.model
        for group_key, pids in self.tasks_in_batch_group.items():
            if len(pids) <= 1:
                continue  # single order at this batch+operation — nothing to consolidate

            # Common machines = intersection of every member task's candidate machines.
            common = set(self.candidates[pids[0]])
            for pid in pids[1:]:
                common &= set(self.candidates[pid])
            if not common:
                continue  # divergent routing (e.g. MOC-specific machines) — stay flexible

            common = sorted(common)
            batch_key, task_code = group_key
            tag = f"batch[{batch_key}|{task_code}]"

            chosen = {m: model.NewBoolVar(f"{tag}|chosen|{m}") for m in common}
            model.Add(sum(chosen.values()) == 1)

            for pid in pids:
                for m in common:
                    for k in self.open_slots.get(m, []):
                        if (pid, m, k) in self.occ:
                            model.Add(self.occ[(pid, m, k)] <= chosen[m])

    def _add_capacity_and_setup(self) -> None:
        """
        Per (machine, slot): Σ qty·CT + Σ setup ≤ AVAILABLE_MINS.
        Setup is charged once per distinct ITEM_CATEGORY a machine touches in a
        slot, WAIVED when that size carried over from the machine's previous OPEN
        slot (so continuing on one machine across a shift boundary is free, while
        introducing a new size — or switching machines — costs a setup).
        """
        model = self.model
        for m, k_list in self.open_slots.items():
            categories = self.categories_for_machine.get(m, set())

            # cat_present[c,m,k] = OR of occ[t,m,k] over tasks t of category c.
            for c in categories:
                pids_c = self.tasks_for_cat_machine.get((c, m), [])
                for k in k_list:
                    relevant = [self.occ[(pid, m, k)] for pid in pids_c if (pid, m, k) in self.occ]
                    if not relevant:
                        continue
                    if len(relevant) == 1:
                        cp_var = relevant[0]
                    else:
                        cp_var = model.NewBoolVar(f"cat_present[{c}|{m}|{k}]")
                        model.AddMaxEquality(cp_var, relevant)
                    self.cat_present[(c, m, k)] = cp_var

            # setup_charged[c,m,k] = cat_present AND NOT carried-from-prev-open-slot.
            for k in k_list:
                prev_k = self.prev_open[(m, k)]
                setup_terms = []
                for c in categories:
                    cp_var = self.cat_present.get((c, m, k))
                    if cp_var is None:
                        continue
                    setup_minutes = self.setup_s.get((c, m), 0)
                    if setup_minutes == 0:
                        continue
                    prev_cp = self.cat_present.get((c, m, prev_k)) if prev_k is not None else None
                    if prev_cp is not None:
                        carried = model.NewBoolVar(f"carried[{c}|{m}|{k}]")
                        # carried = cp_var AND prev_cp
                        model.Add(carried <= cp_var)
                        model.Add(carried <= prev_cp)
                        model.Add(carried >= cp_var + prev_cp - 1)
                        self.carried[(c, m, k)] = carried
                        setup_terms.append(setup_minutes * (cp_var - carried))
                    else:
                        setup_terms.append(setup_minutes * cp_var)

                work_terms = [
                    self.qty[(pid, m, k)] * self.ct_s[pid]
                    for pid in self.pids_by_machine_slot.get((m, k), set())
                ]
                model.Add(sum(work_terms) + sum(setup_terms) <= self.cap_s[(m, k)])

    def _add_precedence(self) -> None:
        """
        Within each order, sort ops ascending OPERATION_NO and chain
        start_slot[next] ≥ end_slot[prev]. Non-routed ops were already dropped in
        preprocessing, so consecutive survivors chain directly.
        """
        model = self.model
        for tasks in self.tasks_by_order.values():
            ordered = sorted(tasks, key=lambda t: t.operation_no)
            for prev_t, next_t in zip(ordered, ordered[1:]):
                prev_pid: TaskKey = (prev_t.production_order, prev_t.operation_no)
                next_pid: TaskKey = (next_t.production_order, next_t.operation_no)
                model.Add(self.start_slot[next_pid] >= self.end_slot[prev_pid])

    def _build_objective(self) -> None:
        """
        Minimize:
            Σ_order urgency_s × tardiness_days                      (primary)
          + Σ_{c,m,k} setup_penalty_s × setup_charged[c,m,k]       (mild: batch same sizes)
          + Σ_{t,m}   eps_s × (MACHINE_PRIORITY − 1) × used[t,m]   (tiny: prefer priority-1)

        completion_day[order] = end_slot[last op] // 3. Safety-stock orders
        (CDD = NULL, urgency_weight = 0) contribute 0 tardiness regardless.
        The setup and priority terms are scaled far below one tardy day, so they
        only break ties — they never delay a delivery to save a setup or honour a
        machine preference.
        """
        model = self.model
        terms = []

        # Primary — weighted tardiness per order.
        for tasks in self.tasks_by_order.values():
            last_t = max(tasks, key=lambda t: t.operation_no)
            last_pid: TaskKey = (last_t.production_order, last_t.operation_no)
            end_v = self.end_slot[last_pid]

            completion_day = model.NewIntVar(0, len(self.D) - 1, f"completion_day[{last_pid[0]}]")
            model.AddDivisionEquality(completion_day, end_v, 3)

            if last_t.cdd is not None:
                pdd_day = (last_t.cdd - self.D[0]).days
            else:
                pdd_day = len(self.D) + 1  # sentinel; urgency_weight = 0 anyway

            tardiness = model.NewIntVar(0, _TARDINESS_UPPER_BOUND, f"tardiness[{last_pid[0]}]")
            model.AddMaxEquality(tardiness, [0, completion_day - pdd_day])

            urgency_s = round(last_t.urgency_weight * URGENCY_SCALE)
            if urgency_s:
                terms.append(urgency_s * tardiness)

        # Secondary — mild setup-count penalty (emergent batching / utilisation).
        setup_pen_s = round(self.config.setup_penalty_weight * URGENCY_SCALE)
        if setup_pen_s:
            for (c, m, k), cp_var in self.cat_present.items():
                if self.setup_s.get((c, m), 0) == 0:
                    continue
                carried = self.carried.get((c, m, k))
                # setup event = cat_present − carried  (∈ {0,1})
                terms.append(setup_pen_s * (cp_var if carried is None else (cp_var - carried)))

        # Tertiary — machine-priority tiebreaker (prefer priority-1 machines).
        eps_s = round(self.config.machine_priority_epsilon * URGENCY_SCALE)
        if eps_s:
            for pid, machines in self.candidates.items():
                tag = f"{pid[0]}|{pid[1]}"
                for m in machines:
                    priority = self.machine_priority[(pid, m)]
                    if priority <= 1:
                        continue
                    slots = [self.occ[(pid, m, k)] for k in self.open_slots.get(m, []) if (pid, m, k) in self.occ]
                    if not slots:
                        continue
                    used = model.NewBoolVar(f"used[{tag}|{m}]")
                    model.AddMaxEquality(used, slots)
                    terms.append(eps_s * (priority - 1) * used)

        model.Minimize(sum(terms) if terms else 0)

    # ── greedy warm-start / fallback (guarantees production never stops) ──────
    def greedy_schedule(self) -> dict[tuple[TaskKey, str, int], int]:
        """
        Earliest-slot, carryover-aware, BATCH-aware greedy. Places every piece of
        every task on a capable machine's open slot, preferring (in order): the
        EARLIEST slot, then a slot where the size is already set up (setup = 0),
        then the lowest MACHINE_PRIORITY. Respects precedence (an op cannot start
        before the previous op of its order has finished). Always returns a full
        assignment as long as total open capacity ≥ total work (the horizon
        guarantees this).

        Batch continuity: the FIRST task processed within a batch group (same
        SIZE~CLASS~DESIGN, same TASK code, across different orders — see
        _index_tasks) picks a machine normally; every OTHER task in that group is
        then FORCED onto that same machine (overflowing to further open slots on
        it as needed), so the whole batch queues through one setup instead of
        fragmenting across machines. If the forced machine genuinely has no room
        left for a later member (precedence floor pushed it too late, or capacity
        ran out), that member falls back to a free machine search for its
        remainder — production is never blocked to preserve a batching preference.

        Returned dict: {(task_key, machine, slot) -> pieces}. Used as a CP-SAT hint
        AND as the fallback schedule if CP-SAT finds nothing better in time.
        """
        self.logger.info("\n" + "=" * 70)
        self.logger.info("GREEDY WARM-START / FALLBACK PASS (batch-aware)")
        self.logger.info("=" * 70)

        remaining = {(m, k): self.cap_s[(m, k)] for m, ks in self.open_slots.items() for k in ks}
        cat_here: dict[tuple[str, int], set[str]] = defaultdict(set)  # (m,k) -> sizes set up
        assign: dict[tuple[TaskKey, str, int], int] = defaultdict(int)
        failed_tasks: list[tuple[TaskKey, int, str]] = []  # (task_key, balance_qty, reason)

        # Sticky machine choice per batch group: (batch_key, TASK) -> machine name.
        # Set once, by whichever order in the group is placed first.
        batch_machine: dict[tuple[str, str], str] = {}
        batch_overrides = 0

        # Process orders clustered by batch (same SIZE~CLASS~DESIGN), most-urgent
        # batch first, then most-urgent order within that batch; ops within an
        # order ascending. An order's batch_key is constant across all of its own
        # operations (SIZE/CLASS/DESIGN are valve attributes, not per-operation),
        # so clustering here keeps every family member's placement close together
        # in the walk — closing the window for an unrelated order to consume the
        # batch's chosen machine capacity in between (see Phase 3b discussion).
        order_batch_key: dict[str, str] = {}
        for order, tasks in self.tasks_by_order.items():
            sample_pid: TaskKey = (tasks[0].production_order, tasks[0].operation_no)
            order_batch_key[order] = self.batch_key_of.get(sample_pid, "")

        batch_max_urgency: dict[str, float] = defaultdict(float)
        for order, tasks in self.tasks_by_order.items():
            bkey = order_batch_key[order]
            batch_max_urgency[bkey] = max(batch_max_urgency[bkey], max(t.urgency_weight for t in tasks))

        orders_sorted = sorted(
            self.tasks_by_order.items(),
            key=lambda kv: (
                -batch_max_urgency[order_batch_key[kv[0]]],
                -max(t.urgency_weight for t in kv[1]),
            ),
        )
        for _order, tasks in orders_sorted:
            ordered = sorted(tasks, key=lambda t: t.operation_no)
            prev_end_k = 0  # this op may not start before the previous op's last slot
            for t in ordered:
                pid: TaskKey = (t.production_order, t.operation_no)
                c = t.item_category
                ct = self.ct_s[pid]
                machines = self.candidates[pid]
                remaining_pieces = t.balance_qty
                op_end_k = prev_end_k

                group_key = self.batch_group_of.get(pid)
                is_batched = group_key is not None and len(self.tasks_in_batch_group.get(group_key, [])) > 1

                def find_best(search_machines):
                    best = None  # (sort_key, m, k, setup_cost)
                    for m in search_machines:
                        prio = self.machine_priority[(pid, m)]
                        for k in self.open_slots.get(m, []):
                            if k < prev_end_k:
                                continue
                            cap_here = remaining[(m, k)]
                            if c in cat_here[(m, k)]:
                                setup_cost = 0
                            else:
                                pk = self.prev_open[(m, k)]
                                carried = pk is not None and c in cat_here[(m, pk)]
                                setup_cost = 0 if carried else self.setup_s.get((c, m), 0)
                            if cap_here - setup_cost >= ct:  # room for ≥ 1 piece
                                sort_key = (k, 0 if setup_cost == 0 else 1, prio)
                                if best is None or sort_key < best[0]:
                                    best = (sort_key, m, k, setup_cost)
                                break  # smallest usable k for this machine
                    return best

                guard = 0
                placed_on_op = 0
                while remaining_pieces > 0:
                    guard += 1
                    if guard > 500_000:
                        failed_tasks.append((pid, t.balance_qty, "guard limit (likely no open capacity)"))
                        break

                    forced_m = batch_machine.get(group_key) if is_batched else None
                    if forced_m is not None and forced_m in machines:
                        best = find_best([forced_m])
                        if best is None:
                            # Batch machine has no room right now — deviate for this
                            # remainder only; the group's sticky choice is unchanged.
                            best = find_best(machines)
                            if best is not None:
                                batch_overrides += 1
                                self.logger.debug(
                                    f"  Batch override: {pid[0]} Op{pid[1]} "
                                    f"({group_key}) deviates from {forced_m} -> {best[1]}"
                                )
                    else:
                        best = find_best(machines)
                        if best is not None and is_batched and group_key not in batch_machine:
                            batch_machine[group_key] = best[1]

                    if best is None:
                        reason = f"no capable machine has open slot ≥ slot {prev_end_k} with {ct} mins free"
                        failed_tasks.append((pid, remaining_pieces, reason))
                        break
                    _, m, k, setup_cost = best
                    if c not in cat_here[(m, k)]:
                        remaining[(m, k)] -= setup_cost
                        cat_here[(m, k)].add(c)
                    place = min(remaining_pieces, max(0, remaining[(m, k)] // ct))
                    if place <= 0:
                        # setup consumed all/most capacity; move on to next slot
                        continue
                    assign[(pid, m, k)] += place
                    remaining[(m, k)] -= place * ct
                    remaining_pieces -= place
                    placed_on_op += place
                    op_end_k = max(op_end_k, k)
                if placed_on_op > 0:
                    self.logger.debug(f"  {pid[0]} Op{pid[1]}: placed {placed_on_op}/{t.balance_qty} pieces")
                prev_end_k = op_end_k

        # Summary
        placed_total = sum(assign.values())
        needed_total = sum(t.balance_qty for t in self.input.tasks)
        self.logger.info(f"\nGreedy result: {placed_total}/{needed_total} pieces placed")
        self.logger.info(f"Batch groups formed: {len(batch_machine)} (each consolidated onto one machine)")
        if batch_overrides:
            self.logger.info(f"Batch overrides (capacity/timing forced a deviation): {batch_overrides}")
        if failed_tasks:
            self.logger.info(f"Failed to place {len(failed_tasks)} tasks:")
            for pid, remaining, reason in failed_tasks[:10]:  # show first 10
                self.logger.info(f"  {pid[0]} Op{pid[1]}: {remaining} pieces left — {reason}")
            if len(failed_tasks) > 10:
                self.logger.info(f"  ... and {len(failed_tasks) - 10} more")
        self.logger.info("=" * 70)

        return dict(assign)

    # ── solve + extract ──────────────────────────────────────────────────────
    def solve(self, max_time_in_seconds: Optional[float] = None) -> SchedulerResult:
        """
        Warm-start CP-SAT from the greedy schedule, solve, and return the best of
        {CP-SAT solution, greedy fallback}. A schedule is ALWAYS returned as long
        as the greedy pass placed every piece — production never stalls waiting on
        the solver.
        """
        greedy = self.greedy_schedule()
        greedy_complete = self._greedy_is_complete(greedy)

        # Warm-start hint: tell CP-SAT the greedy placement so it starts feasible.
        if greedy_complete:
            for (pid, m, k), q in greedy.items():
                if (pid, m, k) in self.qty:
                    self.model.AddHint(self.qty[(pid, m, k)], q)
                    self.model.AddHint(self.occ[(pid, m, k)], 1)

        solver = cp_model.CpSolver()
        time_limit = (
            max_time_in_seconds if max_time_in_seconds is not None
            else self.config.solver_time_limit_seconds
        )
        solver.parameters.max_time_in_seconds = time_limit
        # Use ALL CPU cores unless a specific count is configured.
        solver.parameters.num_workers = (
            self.config.solver_workers if self.config.solver_workers > 0 else (os.cpu_count() or 8)
        )

        status = solver.Solve(self.model)
        run_id = uuid.uuid4().hex
        generated_at = datetime.now()

        status_map = {
            cp_model.OPTIMAL: SolveStatus.OPTIMAL,
            cp_model.FEASIBLE: SolveStatus.FEASIBLE,
            cp_model.INFEASIBLE: SolveStatus.INFEASIBLE,
            cp_model.MODEL_INVALID: SolveStatus.MODEL_INVALID,
        }
        solve_status = status_map.get(status, SolveStatus.UNKNOWN)

        # Prefer the CP-SAT solution; otherwise fall back to the greedy schedule.
        if solve_status in (SolveStatus.OPTIMAL, SolveStatus.FEASIBLE):
            self.logger.info(f"✓ CP-SAT: {solve_status.value} (objective: {solver.ObjectiveValue():.0f})")
            get_qty = lambda pid, m, k: solver.Value(self.qty[(pid, m, k)])  # noqa: E731
            result = SchedulerResult(
                run_id=run_id,
                generated_at=generated_at,
                status=solve_status,
                objective_value=solver.ObjectiveValue(),
            )
            result.assignments = self._rows_from_assignment(get_qty, run_id, generated_at)
            result.completion_dates = self._completion_from_assignment(get_qty)
            self.logger.info(f"✓ Extracted {len(result.assignments)} assignment rows from CP-SAT solution")
            return result

        # Always use greedy as fallback, whether complete or not (it's guaranteed correct by construction)
        get_qty = lambda pid, m, k: greedy.get((pid, m, k), 0)  # noqa: E731
        greedy_pieces = sum(greedy.values())
        needed_pieces = sum(t.balance_qty for t in self.input.tasks)

        if greedy_complete:
            self.logger.info(f"✓ CP-SAT {solve_status.value}, but greedy fallback is COMPLETE: using greedy ({greedy_pieces}/{needed_pieces} pieces)")
        else:
            self.logger.warning(f"⚠️  CP-SAT {solve_status.value} and greedy INCOMPLETE: returning partial schedule ({greedy_pieces}/{needed_pieces} pieces, {100*greedy_pieces/needed_pieces:.1f}%)")

        result = SchedulerResult(
            run_id=run_id,
            generated_at=generated_at,
            status=SolveStatus.FEASIBLE if greedy_pieces > 0 else SolveStatus.INFEASIBLE,
            objective_value=None,
        )
        if greedy_pieces > 0:
            result.assignments = self._rows_from_assignment(get_qty, run_id, generated_at)
            result.completion_dates = self._completion_from_assignment(get_qty)
            self.logger.info(f"✓ Extracted {len(result.assignments)} assignment rows from greedy")
        else:
            self.logger.error(f"✗ Greedy placed 0 pieces — no schedule possible")

        return result

    def _greedy_is_complete(self, greedy: dict[tuple[TaskKey, str, int], int]) -> bool:
        """True iff the greedy pass placed every piece of every task."""
        placed: dict[TaskKey, int] = defaultdict(int)
        for (pid, _m, _k), q in greedy.items():
            placed[pid] += q
        for pid, task in self.task_of.items():
            if placed.get(pid, 0) != task.balance_qty:
                return False
        return True

    # ── unified extraction (works for both CP-SAT and greedy results) ────────
    def _rows_from_assignment(
        self,
        get_qty: Callable[[TaskKey, str, int], int],
        run_id: str,
        generated_at: datetime,
    ) -> list[ScheduleOutputRow]:
        """
        Turn a {(task, machine, slot) -> pieces} assignment into schedule rows.
        Within each (machine, slot) the assigned tasks are laid back-to-back from
        offset 0, grouped by ITEM_CATEGORY, with a setup block preceding a size's
        first appearance on that machine in that slot (waived if the size carried
        over from the machine's previous open slot). Offsets are DISPLAY ONLY.
        """
        # Which sizes are present on each (machine, slot), for carryover-aware setup.
        cat_present: dict[tuple[str, int], set[str]] = defaultdict(set)
        for m, k_list in self.open_slots.items():
            for k in k_list:
                for pid in self.pids_by_machine_slot.get((m, k), set()):
                    if get_qty(pid, m, k) > 0:
                        cat_present[(m, k)].add(self.task_of[pid].item_category)

        rows: list[ScheduleOutputRow] = []
        for m, k_list in self.open_slots.items():
            for k in k_list:
                pids_here = [
                    pid for pid in self.pids_by_machine_slot.get((m, k), set())
                    if get_qty(pid, m, k) > 0
                ]
                if not pids_here:
                    continue

                by_category: dict[str, list[TaskKey]] = defaultdict(list)
                for pid in pids_here:
                    by_category[self.task_of[pid].item_category].append(pid)

                shift = SHIFT_ORDER[k % 3]
                scheduled_date = self.D[k // 3]
                prev_k = self.prev_open[(m, k)]
                running = 0.0

                for cat in sorted(by_category):
                    carried = prev_k is not None and cat in cat_present[(m, prev_k)]
                    if not carried:
                        running += self.setup_s.get((cat, m), 0) / TIME_SCALE
                    for pid in sorted(by_category[cat]):
                        task = self.task_of[pid]
                        qty_val = get_qty(pid, m, k)
                        start = running
                        running += qty_val * task.cycle_time
                        rows.append(
                            ScheduleOutputRow(
                                production_order=task.production_order,
                                operation_no=task.operation_no,
                                machine_name=m,
                                shift=shift,
                                scheduled_date=scheduled_date,
                                balance_qty=qty_val,
                                start_offset_min=int(round(start)),
                                end_offset_min=int(round(running)),
                                batch_key=task.batch_key,
                                is_safety_stock=task.cdd is None,
                                run_id=run_id,
                                generated_at=generated_at,
                            )
                        )
        return rows

    def _completion_from_assignment(
        self,
        get_qty: Callable[[TaskKey, str, int], int],
    ) -> dict[str, date]:
        """completion_date[order] = D[(last op's max occupied slot) // 3]."""
        completion: dict[str, date] = {}
        for order, tasks in self.tasks_by_order.items():
            last_t = max(tasks, key=lambda t: t.operation_no)
            last_pid: TaskKey = (last_t.production_order, last_t.operation_no)
            max_k = -1
            for m in self.candidates[last_pid]:
                for k in self.open_slots.get(m, []):
                    if (last_pid, m, k) in self.qty and get_qty(last_pid, m, k) > 0:
                        max_k = max(max_k, k)
            if max_k >= 0:
                completion[order] = self.D[max_k // 3]
        return completion


# ─────────────────────────────────────────────────────────────────────────────
# Module-level convenience entry point (used by FastAPI POST /schedule/generate
# and by engine2_recommender.simulate_priority_elevation)
# ─────────────────────────────────────────────────────────────────────────────
def run_engine1(
    scheduler_input: SchedulerInput,
    max_time_in_seconds: Optional[float] = None,
) -> SchedulerResult:
    """Build + solve in one call. Engine 2 reuses this with a shorter time limit."""
    logger = _setup_logging()
    engine = Engine1Scheduler(scheduler_input, logger=logger)
    engine.diagnose_feasibility()
    engine.build_model()
    return engine.solve(max_time_in_seconds=max_time_in_seconds)
