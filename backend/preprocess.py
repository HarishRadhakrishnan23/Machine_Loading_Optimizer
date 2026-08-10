"""
preprocess.py — Data pipeline that runs before every engine call.

Turns the 4 raw Oracle DataFrames (wip_orders, machine_master, machine_daily,
routing_master) into a single validated `SchedulerInput` for the CP-SAT engine.

Pipeline order (locked in CLAUDE.md):
    1. Drop CYCLE_TIME == 0 rows            (external vendors / missing CT / non-machine)
    2. Keep only routable OPERATION codes   (present in routing_master; v1 = 7 ops)
    3. Drop QA rows                         (WORK_CENTER contains 'QAINSP')
    4. balance_qty = ORDERED − COMPLETED − REJECTED; drop rows where ≤ 0
    5. Normalize shift names → lowercase    (machine_master + machine_daily)
    6. resolve_capacity()                   (machine_daily override on machine_master)
    7. Use AVAILABLE_MINS column directly when populated

This module is pure pandas + python. It never touches Oracle directly — db.py
hands it DataFrames, so tests can pass hand-built fixtures instead of a live DB.

ALL times are in minutes. No conversion, ever.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import pandas as pd

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
    Merge the day-specific machine_daily override onto the machine_master baseline
    for a single date. Returns a resolved capacity frame (in memory only — never
    written to Oracle).

    machine_master  : baseline capacity per machine per shift (all machines, all shifts).
    machine_daily   : overrides for a specific machine+shift+date (breakdown, maintenance).

    Rule: start from the baseline; for every machine_daily row matching this date,
    overwrite AVAILABLE_MINS for that machine+shift. If no daily row exists for a
    machine+shift, the baseline stands. AVAILABLE_MINS == 0 ⇒ machine idle that slot.

    Faithful to the reference implementation in CLAUDE.md.
    """
    resolved = machine_master_df.copy()
    resolved["working_shift"] = resolved["working_shift"].str.lower()

    daily = machine_daily_df[machine_daily_df["date"] == target_date].copy()
    if not daily.empty:
        daily["working_shift"] = daily["working_shift"].str.lower()
        for _, row in daily.iterrows():
            mask = (
                (resolved["machine_name"] == row["machine_name"])
                & (resolved["working_shift"] == row["working_shift"])
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
    """
    slots: list[CapacitySlot] = []
    for d in horizon_dates:
        resolved = resolve_capacity(machine_master_df, machine_daily_df, d)
        for _, row in resolved.iterrows():
            slots.append(
                CapacitySlot(
                    machine_name=str(row["machine_name"]),
                    shift=Shift(str(row["working_shift"]).lower()),
                    slot_date=d,
                    available_mins=float(row["AVAILABLE_MINS"]),
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

    # 2. Keep only operations that have a routing_master entry (v1: 7 ops; extensible).
    routable_ops = set(routing_df["OPERATION"].unique())
    df = df[df["OPERATION"].isin(routable_ops)]

    # 3. Drop QA inspection rows (WORK_CENTER contains 'QAINSP'). Case-insensitive.
    df = df[~df["WORK_CENTER"].astype(str).str.upper().str.contains(QA_WORK_CENTER_TOKEN)]

    # 4. balance_qty = ORDERED − COMPLETED − REJECTED; drop rows ≤ 0.
    df["balance_qty"] = (
        df["QUANTITY_ORDERED"] - df["QUANTITY_COMPLETED"] - df["QUANTITY_REJECTED"]
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

    "Next downstream schedulable operation" = the smallest OPERATION_NO greater
    than this task's OPERATION_NO within the same ITEM_CATEGORY across the
    already-filtered WIP (filtered WIP only contains routable, CT>0, balance>0 rows).
    """
    same_cat = filtered_wip[filtered_wip["ITEM_CATEGORY"] == task_row["ITEM_CATEGORY"]]
    downstream = same_cat[
        (same_cat["OPERATION_NO"] > task_row["OPERATION_NO"])
        & (same_cat["PRODUCTION_ORDER"] != task_row["PRODUCTION_ORDER"])
    ]
    return config.downstream_queue_bonus_value if not downstream.empty else 0.0


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
# Machine candidates from routing_master
# ─────────────────────────────────────────────────────────────────────────────
def build_machine_candidates(
    operation: str,
    item_category: str,
    routing_df: pd.DataFrame,
) -> list[MachineCandidate]:
    """
    All machines capable of `operation` for `item_category`, from routing_master.
    Each becomes an OptionalIntervalVar candidate in the CP-SAT model.
    """
    rows = routing_df[
        (routing_df["OPERATION"] == operation)
        & (routing_df["ITEM_CATEGORY"] == item_category)
    ]
    return [
        MachineCandidate(
            machine_name=str(r["machine_name"]),
            setup_time=float(r["SETUP_TIME"]),
            machine_priority=int(r["MACHINE_PRIORITY"]),
        )
        for _, r in rows.iterrows()
    ]


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

    # Build one SchedulableTask per (PRODUCTION_ORDER, OPERATION_NO) survivor.
    tasks: list[SchedulableTask] = []
    for _, row in filtered.iterrows():
        candidates = build_machine_candidates(
            operation=row["OPERATION"],
            item_category=row["ITEM_CATEGORY"],
            routing_df=routing_df,
        )
        if not candidates:
            # Routable per the OPERATION filter but no machine matches this exact
            # ITEM_CATEGORY — cannot schedule; skip (surfaced to UI as unroutable).
            continue

        cdd = row.get("CDD")
        cdd = cdd.date() if isinstance(cdd, (datetime, pd.Timestamp)) and not pd.isna(cdd) else (None if pd.isna(cdd) else cdd)
        order_dt = row.get("PRODUCTION_START_DATE_AND_TIME")
        order_dt = None if pd.isna(order_dt) else (order_dt.to_pydatetime() if isinstance(order_dt, pd.Timestamp) else order_dt)

        tasks.append(
            SchedulableTask(
                production_order=str(row["PRODUCTION_ORDER"]),
                operation_no=float(row["OPERATION_NO"]),
                operation=str(row["OPERATION"]),
                item_category=str(row["ITEM_CATEGORY"]),
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
