"""
preprocess.py — Data pipeline that runs before every engine call.

Turns the 4 raw Oracle DataFrames — read from the ERP views MCH_WIP,
MCH_MACHINE_AVAILABILITY, MCH_MACHINE_AVAILABILITY_BY_DATE, MCH_MACHINE_PRIORITY
(aliased below as wip_df, machine_master_df, machine_daily_df, routing_df) —
into a single validated `SchedulerInput` for the CP-SAT engine.

Real ERP column names used here (see CLAUDE.md "Column-name changes"):
    WORK_CENTER   = machine identifier (all 4 views)
    SHIFT         = shift name (machine_master_df + machine_daily_df)
    WORKING_DATE  = the override date (machine_daily_df only)
    TASK          = operation/task code, e.g. VB02, R002 (wip_df + routing_df) —
                    routing capability is matched on TASK, not on the sequence number.
    OPERATION     = the ascending operation-SEQUENCE number (wip_df only) — this is
                    what the rest of the codebase calls `operation_no`.

Pipeline order (locked in CLAUDE.md):
    1. Drop CYCLE_TIME == 0 rows            (external vendors / missing CT / non-machine)
    2. Keep only routable TASK codes        (present in MCH_MACHINE_PRIORITY; v1 = 7 tasks)
    3. Drop QA rows                         (WORK_CENTER contains 'QAINSP')
    4. balance_qty = ORDERED − COMPLETED − REJECTED (REJECTED is nullable ⇒ treat NULL as 0); drop rows where ≤ 0
    5. Normalize shift names → lowercase    (machine_master_df + machine_daily_df)
    6. resolve_capacity()                   (machine_daily_df override on machine_master_df)
    7. Use AVAILABLE_MINS column directly (already OEE-adjusted by the views)

This module is pure pandas + python. It never touches Oracle directly — db.py
hands it DataFrames, so tests can pass hand-built fixtures instead of a live DB.

ALL times are in minutes. No conversion, ever.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import pandas as pd

from batch_grouping import compute_batch_key
from models import (
    CapacitySlot,
    Config,
    MachineCandidate,
    SchedulableTask,
    SchedulerInput,
    Shift,
)

# Work centers that are manual QA gates — excluded from CP-SAT (shown in UI only).
QA_WORK_CENTER_TOKEN = "QAINSP"


# ─────────────────────────────────────────────────────────────────────────────
# 5 + 6 + 7 — Capacity resolution
# ─────────────────────────────────────────────────────────────────────────────
def resolve_capacity(
    machine_master_df: pd.DataFrame,
    machine_daily_df: pd.DataFrame,
    target_date: date,
) -> pd.DataFrame:
    """
    Merge the day-specific MCH_MACHINE_AVAILABILITY_BY_DATE override onto the
    MCH_MACHINE_AVAILABILITY baseline for a single date. Returns a resolved
    capacity frame (in memory only — never written to Oracle).

    machine_master_df : baseline capacity per WORK_CENTER per SHIFT (all machines, all shifts).
    machine_daily_df  : overrides for a specific WORK_CENTER+SHIFT+WORKING_DATE (ERP-prepared).

    Rule: start from the baseline; for every machine_daily row matching this date,
    overwrite AVAILABLE_MINS for that WORK_CENTER+SHIFT. If no daily row exists for a
    WORK_CENTER+SHIFT, the baseline stands. AVAILABLE_MINS == 0 ⇒ machine idle that slot.

    Faithful to the reference implementation in CLAUDE.md.
    """
    resolved = machine_master_df.copy()
    resolved["SHIFT"] = resolved["SHIFT"].str.lower()

    daily = machine_daily_df[machine_daily_df["WORKING_DATE"] == target_date].copy()
    if not daily.empty:
        daily["SHIFT"] = daily["SHIFT"].str.lower()
        for _, row in daily.iterrows():
            mask = (
                (resolved["WORK_CENTER"] == row["WORK_CENTER"])
                & (resolved["SHIFT"] == row["SHIFT"])
            )
            resolved.loc[mask, "AVAILABLE_MINS"] = row["AVAILABLE_MINS"]

    return resolved


def build_capacity_slots(
    machine_master_df: pd.DataFrame,
    machine_daily_df: pd.DataFrame,
    horizon_dates: list[date],
) -> list[CapacitySlot]:
    """
    Expand resolved capacity into one CapacitySlot per (machine, shift, date)
    across the scheduling horizon. Slots with available_mins == 0 are kept so the
    solver can *see* that a machine is explicitly closed that slot (vs. absent).

    Equivalent to calling resolve_capacity() per date, but normalizes the baseline
    once and groups machine_daily by date up front — so it is O(horizon × machines)
    plus a single pass over machine_daily, instead of re-scanning the full daily
    frame (and re-lowercasing the baseline) on every horizon day.
    """
    baseline = machine_master_df.copy()
    baseline["SHIFT"] = baseline["SHIFT"].str.lower()

    # (date) → {(work_center, shift): AVAILABLE_MINS} override lookup, built once.
    overrides_by_date: dict[date, dict[tuple[str, str], float]] = {}
    if not machine_daily_df.empty:
        daily = machine_daily_df.copy()
        daily["SHIFT"] = daily["SHIFT"].str.lower()
        for d, group in daily.groupby("WORKING_DATE"):
            overrides_by_date[d] = {
                (row["WORK_CENTER"], row["SHIFT"]): row["AVAILABLE_MINS"]
                for _, row in group.iterrows()
            }

    slots: list[CapacitySlot] = []
    for d in horizon_dates:
        day_overrides = overrides_by_date.get(d, {})
        for _, row in baseline.iterrows():
            work_center = str(row["WORK_CENTER"])
            shift = str(row["SHIFT"]).lower()
            available_mins = day_overrides.get((work_center, shift), row["AVAILABLE_MINS"])
            slots.append(
                CapacitySlot(
                    machine_name=work_center,
                    shift=Shift(shift),
                    slot_date=d,
                    available_mins=float(available_mins),
                )
            )
    return slots


# ─────────────────────────────────────────────────────────────────────────────
# 1–4 — WIP filtering + balance_qty
# ─────────────────────────────────────────────────────────────────────────────
def filter_wip_orders(
    wip_df: pd.DataFrame,
    routing_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Apply the four WIP filters and compute balance_qty. Returns the surviving
    schedulable rows with a new `balance_qty` column. Order-preserving.

    Steps 1–4 of the pipeline. QA rows and CT=0 rows are dropped here (they are
    re-included for UI display elsewhere, not in the scheduling path).
    """
    df = wip_df.copy()

    # 1. Drop CYCLE_TIME == 0 (covers external vendors, missing CT, non-machine work).
    df = df[df["CYCLE_TIME"] > 0]

    # 2. Keep only TASK codes that have an MCH_MACHINE_PRIORITY entry (v1: 7 tasks; extensible).
    routable_tasks = set(routing_df["TASK"].unique())
    df = df[df["TASK"].isin(routable_tasks)]

    # 3. Drop QA inspection rows (WORK_CENTER contains 'QAINSP'). Case-insensitive.
    df = df[~df["WORK_CENTER"].astype(str).str.upper().str.contains(QA_WORK_CENTER_TOKEN)]

    # 4. balance_qty = ORDERED − COMPLETED − REJECTED; QUANTITY_REJECTED is nullable, treat NULL as 0.
    df["balance_qty"] = (
        df["QUANTITY_ORDERED"] - df["QUANTITY_COMPLETED"] - df["QUANTITY_REJECTED"].fillna(0)
    )
    df = df[df["balance_qty"] > 0]

    return df.reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# urgency_weight components
# ─────────────────────────────────────────────────────────────────────────────
def _pdd_score(cdd: Optional[date], today: date) -> float:
    """1 / max(1, days_until_CDD). Grows sharply as the deadline approaches."""
    if cdd is None:
        return 0.0
    days_until = (cdd - today).days
    return 1.0 / max(1, days_until)


def _ageing_score(order_date: Optional[datetime], today: date, normalization_days: int) -> float:
    """(today − order_date).days / ageing_normalization_days. Normalized ~0→1 over ~6 months."""
    if order_date is None:
        return 0.0
    age_days = (today - order_date.date()).days
    return max(0, age_days) / normalization_days


def _batch_bonus(cdd: Optional[date], today: date, config: Config) -> float:
    """batch_bonus_value if the order is due within batch_bonus_months × 30 days."""
    if cdd is None:
        return 0.0
    days_until = (cdd - today).days
    return config.batch_bonus_value if days_until < config.batch_bonus_months * 30 else 0.0


def _downstream_queue_bonus(
    task_row: pd.Series,
    filtered_wip: pd.DataFrame,
    config: Config,
) -> float:
    """
    downstream_queue_bonus_value if ANOTHER production order of the SAME
    ITEM_CATEGORY has balance_qty > 0 at the next downstream schedulable
    operation — i.e. a machine is already (or about to be) set up for this
    ITEM_CATEGORY downstream, so scheduling this order now saves a setup.

    "Next downstream schedulable operation" = the smallest OPERATION (sequence
    number) greater than this task's OPERATION within the same ITEM_CATEGORY
    across the already-filtered WIP (filtered WIP only contains routable,
    CT>0, balance>0 rows).

    Only an OTHER order queued at THAT specific next operation earns the bonus —
    not merely any order sitting somewhere further downstream. (Same ITEM_CATEGORY
    ⇒ same routing, so the next op number is well-defined across the category.)
    """
    same_cat = filtered_wip[filtered_wip["ITEM_CATEGORY"] == task_row["ITEM_CATEGORY"]]
    downstream = same_cat[same_cat["OPERATION"] > task_row["OPERATION"]]
    if downstream.empty:
        return 0.0

    # The immediate next operation in this category's sequence.
    next_op_no = downstream["OPERATION"].min()
    queued_at_next = downstream[
        (downstream["OPERATION"] == next_op_no)
        & (downstream["PRODUCTION_ORDER"] != task_row["PRODUCTION_ORDER"])
    ]
    return config.downstream_queue_bonus_value if not queued_at_next.empty else 0.0


def compute_urgency_weight(
    task_row: pd.Series,
    filtered_wip: pd.DataFrame,
    config: Config,
    today: date,
) -> float:
    """
    urgency_weight = pdd_score + ageing_score + batch_bonus + downstream_queue_bonus

    Safety stock (CDD == NULL) ⇒ urgency_weight = 0 (always scheduled last).
    """
    cdd = task_row.get("CDD")
    if pd.isna(cdd):
        return 0.0
    cdd = cdd.date() if isinstance(cdd, (datetime, pd.Timestamp)) else cdd

    order_date = task_row.get("PRODUCTION_START_DATE_AND_TIME")
    if pd.isna(order_date):
        order_date = None

    return (
        _pdd_score(cdd, today)
        + _ageing_score(order_date, today, config.ageing_normalization_days)
        + _batch_bonus(cdd, today, config)
        + _downstream_queue_bonus(task_row, filtered_wip, config)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Machine candidates from MCH_MACHINE_PRIORITY (routing_master)
# ─────────────────────────────────────────────────────────────────────────────
def build_machine_candidates(
    task: str,
    item_category: str,
    routing_df: pd.DataFrame,
) -> list[MachineCandidate]:
    """
    All machines (WORK_CENTER) capable of `task` for `item_category`, from
    MCH_MACHINE_PRIORITY. Each becomes an OptionalIntervalVar candidate in the
    CP-SAT model.
    """
    rows = routing_df[
        (routing_df["TASK"] == task)
        & (routing_df["ITEM_CATEGORY"] == item_category)
    ]
    return [
        MachineCandidate(
            machine_name=str(r["WORK_CENTER"]),
            setup_time=float(r["SETUP_TIME"]),
            machine_priority=int(r["MACHINE_PRIORITY"]),
        )
        for _, r in rows.iterrows()
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Horizon derivation (CLAUDE.md "Horizon derivation")
# ─────────────────────────────────────────────────────────────────────────────
def compute_horizon(
    filtered_wip: pd.DataFrame,
    machine_master_df: pd.DataFrame,
    config: Config,
    today: date,
) -> list[date]:
    """
    Derive the scheduling horizon to guarantee feasibility: long enough that
    every task can fit, or at least scheduled (possibly tardy). Bounded by the
    latest CDD and padded per config.scheduling_horizon_buffer_days.

    Inputs:
        filtered_wip: WIP rows that survived CT>0, routable, non-QA, balance>0.
        machine_master_df: baseline machine capacity (for daily_cap computation).
        config: loaded config with scheduling_horizon_safety_factor and buffer_days.
        today: reference date (scheduling starts today).

    Returns:
        List of consecutive calendar dates from today + computed horizon_days.

    CLAUDE.md formula:
        total_work = Σ_tasks (balance_qty × CYCLE_TIME) + setup allowance
        daily_cap = Σ_machines Σ_shifts AVAILABLE_MINS
        horizon_days = ceil(total_work / daily_cap) × safety_factor
        horizon_days = max(horizon_days, days_until_latest_CDD) + buffer_days
    """
    # Compute total_work (piece-minutes across all tasks).
    total_work = 0.0
    for _, row in filtered_wip.iterrows():
        balance = row.get("balance_qty", 0)
        ct = row.get("CYCLE_TIME", 0)
        total_work += balance * ct

    # Add setup allowance: assume 1 setup per machine per day (rough upper bound).
    num_machines = machine_master_df["WORK_CENTER"].nunique()
    avg_setup = filtered_wip.groupby("ITEM_CATEGORY").size().mean() if not filtered_wip.empty else 0
    # Simplistic: worst case ~1 setup per machine per day for avg_setup categories.
    setup_allowance = num_machines * avg_setup * 30.0  # ~30 min per setup on average

    # Compute daily_cap (sum of all AVAILABLE_MINS per day across all machines × shifts).
    daily_cap = machine_master_df["AVAILABLE_MINS"].sum()
    if daily_cap <= 0:
        daily_cap = 1  # fallback: never divide by zero

    # Horizon sizing.
    import math
    horizon_days_work = math.ceil((total_work + setup_allowance) / daily_cap)
    horizon_days_work *= config.scheduling_horizon_safety_factor

    # Extend to latest CDD.
    cdd_list = [
        row["CDD"].date() if isinstance(row["CDD"], (datetime, pd.Timestamp)) and not pd.isna(row["CDD"]) else None
        for _, row in filtered_wip.iterrows()
    ]
    cdd_list = [d for d in cdd_list if d is not None]
    if cdd_list:
        latest_cdd = max(cdd_list)
        days_until_cdd = (latest_cdd - today).days
        horizon_days_work = max(horizon_days_work, days_until_cdd)

    # Add buffer.
    horizon_days = horizon_days_work + config.scheduling_horizon_buffer_days

    # Cap at 150 days max for solver performance (rolling horizon approach).
    # Balances feasibility (fits all work) vs. speed (keeps solve time ~2-3 min).
    # With 150 days × 3 shifts × 26 machines = ~11,700 solver slots, which is
    # manageable with a 120-second time limit; 90 days was infeasible.
    MAX_HORIZON_DAYS = 150
    horizon_days = min(horizon_days, MAX_HORIZON_DAYS)

    # Ensure at least 1 day (even if nothing is scheduled, have a day to show).
    horizon_days = max(horizon_days, 1)

    # Generate consecutive calendar dates.
    from datetime import timedelta
    return [today + timedelta(days=i) for i in range(horizon_days)]


# ─────────────────────────────────────────────────────────────────────────────
# Top-level pipeline
# ─────────────────────────────────────────────────────────────────────────────
def build_scheduler_input(
    wip_df: pd.DataFrame,
    machine_master_df: pd.DataFrame,
    machine_daily_df: pd.DataFrame,
    routing_df: pd.DataFrame,
    horizon_dates: list[date],
    config: Config,
    today: date,
) -> SchedulerInput:
    """
    Run the full pipeline and assemble the SchedulerInput consumed by Engine 1.

    Parameters
    ----------
    *_df          : raw Oracle tables as pandas DataFrames (exact column names).
    horizon_dates : the calendar dates the scheduler may place work on.
    config        : loaded from backend/config.json.
    today         : reference date for urgency scoring (injected for testability).
    """
    # Steps 1–4: filter + balance_qty.
    filtered = filter_wip_orders(wip_df, routing_df)

    # Dev mode: limit to top-N orders by urgency (soonest CDD + oldest orders).
    # 0 = no limit (production). Set to 100 for rapid dev iteration.
    if config.dev_max_orders > 0:
        filtered = filtered.sort_values(
            by=["CDD", "PRODUCTION_START_DATE_AND_TIME"],
            na_position="last",  # safety stock (NULL CDD) goes last
            ascending=[True, True]  # soonest CDD first, oldest orders first
        )
        top_orders = filtered["PRODUCTION_ORDER"].unique()[:config.dev_max_orders]
        filtered = filtered[filtered["PRODUCTION_ORDER"].isin(top_orders)].reset_index(drop=True)

    # Build one SchedulableTask per (PRODUCTION_ORDER, OPERATION) survivor.
    # Note: wip_df's OPERATION is the ascending sequence number (→ SchedulableTask.operation_no);
    # wip_df's TASK is the operation/task code (→ SchedulableTask.operation).
    tasks: list[SchedulableTask] = []
    for _, row in filtered.iterrows():
        candidates = build_machine_candidates(
            task=row["TASK"],
            item_category=row["ITEM_CATEGORY"],
            routing_df=routing_df,
        )
        if not candidates:
            # Routable per the TASK filter but no machine matches this exact
            # ITEM_CATEGORY — cannot schedule; skip (surfaced to UI as unroutable).
            continue

        cdd = row.get("CDD")
        cdd = cdd.date() if isinstance(cdd, (datetime, pd.Timestamp)) and not pd.isna(cdd) else (None if pd.isna(cdd) else cdd)
        order_dt = row.get("PRODUCTION_START_DATE_AND_TIME")
        order_dt = None if pd.isna(order_dt) else (order_dt.to_pydatetime() if isinstance(order_dt, pd.Timestamp) else order_dt)

        tasks.append(
            SchedulableTask(
                production_order=str(row["PRODUCTION_ORDER"]),
                operation_no=float(row["OPERATION"]),
                operation=str(row["TASK"]),
                item_category=str(row["ITEM_CATEGORY"]),
                # Computed straight from the typed WIP columns, NOT parsed out of
                # item_category — that string's segment order shifts when DESIGN is
                # blank in the ERP data (see SchedulableTask.batch_key docstring).
                batch_key=compute_batch_key(str(row["SIZE_INCH"]), str(row["CLASS"]), str(row["DESIGN"])),
                balance_qty=int(row["balance_qty"]),
                cycle_time=float(row["CYCLE_TIME"]),
                cdd=cdd,
                order_date=order_dt,
                urgency_weight=compute_urgency_weight(row, filtered, config, today),
                candidates=candidates,
            )
        )

    # Steps 5–7: capacity slots across the horizon.
    capacity = build_capacity_slots(machine_master_df, machine_daily_df, horizon_dates)

    return SchedulerInput(
        tasks=tasks,
        capacity=capacity,
        horizon_dates=horizon_dates,
        config=config,
    )
