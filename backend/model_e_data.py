"""
model_e_data.py — Engine 1 Model E: adapts live Oracle DataFrames (from
db.py's 6 read-only ERP views) into the dispatch engine's plain input types
(dispatch_orders.RawWipRow/RoutingIndex/FixtureIndex, a DevicePool, and an
AvailabilityFn). This is the ONLY module that touches pandas/Oracle on the
Model E side — everything downstream of here is pure Python.

Key-matching note: SIZE_INCH is NUMBER (int64 in pandas) consistently across
MCH_WIP/MCH_MACHINE_PRIORITY/MCH_ITEMWISE_FIXTURE_LOCATOR in the live data —
no float/int string-formatting mismatch risk observed. CLASS/MOC/DESIGN/TASK
are plain strings (MOC is a full word like "Carbon Steel", not a short code).
Every key-building str() cast here mirrors that real shape; if this ever
drifts (e.g. SIZE_INCH becomes float in some view), `_s()` is the one place
to fix it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import pandas as pd

from dispatch_orders import FixtureIndex, FixtureTiming, RawWipRow, RoutingIndex
from dispatch_pool import DevicePool


def _s(value) -> str:
    """Consistent string-cast for every join-key column (see module docstring)."""
    return str(value).strip()


def build_raw_wip_rows(wip_df: pd.DataFrame) -> list[RawWipRow]:
    """
    One RawWipRow per MCH_WIP row, balance_qty precomputed (CLAUDE.md:
    QUANTITY_ORDERED - QUANTITY_COMPLETED - QUANTITY_REJECTED, nullable
    REJECTED treated as 0). Every row is included — filtering/classification
    is dispatch_orders/dispatch_scope's job, not this adapter's (CLAUDE.md
    §E.12: every eligible order-operation always produces a row).
    """
    balance = wip_df["QUANTITY_ORDERED"] - wip_df["QUANTITY_COMPLETED"] - wip_df["QUANTITY_REJECTED"].fillna(0)
    rows: list[RawWipRow] = []
    for (_, row), bq in zip(wip_df.iterrows(), balance):
        cdd = None if pd.isna(row["CDD"]) else row["CDD"].date()
        order_dt = None if pd.isna(row["PRODUCTION_START_DATE_AND_TIME"]) else row["PRODUCTION_START_DATE_AND_TIME"].to_pydatetime()
        order_status = _s(row["ORDER_STATUS"])

        if order_status == "Planned" and order_dt is None:
            raise ValueError(
                f"PRODUCTION_ORDER {row['PRODUCTION_ORDER']!r} is 'Planned' but has no "
                "PRODUCTION_START_DATE_AND_TIME — cannot compute its material-arrival "
                "earliest-start (CLAUDE.md §E.1/§E.8). This is a data integrity issue, "
                "not something safe to silently default."
            )

        rows.append(
            RawWipRow(
                production_order=_s(row["PRODUCTION_ORDER"]),
                operation_no=float(row["OPERATION"]),
                task=_s(row["TASK"]),
                work_center=None if pd.isna(row["WORK_CENTER"]) else _s(row["WORK_CENTER"]),
                size_inch=_s(row["SIZE_INCH"]),
                class_val=_s(row["CLASS"]),
                moc=_s(row["MOC"]),
                design=_s(row["DESIGN"]),
                cycle_time=float(row["CYCLE_TIME"]),
                balance_qty=int(bq),
                cdd=cdd,
                order_date=order_dt,
                order_status=order_status,
                production_start_date=order_dt.date() if order_dt is not None else date.today(),
            )
        )
    return rows


def build_routing_index(routing_df: pd.DataFrame) -> RoutingIndex:
    by_combo: dict[tuple[str, str, str, str, str], list[tuple[str, int]]] = {}
    tasks: set[str] = set()
    for _, row in routing_df.iterrows():
        task = _s(row["TASK"])
        tasks.add(task)
        key = (_s(row["SIZE_INCH"]), _s(row["CLASS"]), _s(row["MOC"]), _s(row["DESIGN"]), task)
        by_combo.setdefault(key, []).append((_s(row["WORK_CENTER"]), int(row["MACHINE_PRIORITY"])))
    return RoutingIndex(by_combo=by_combo, tasks_with_any_routing=frozenset(tasks))


def build_fixture_index(fixture_df: pd.DataFrame) -> FixtureIndex:
    by_combo_machine: dict[tuple[str, str, str, str, str, str], FixtureTiming] = {}
    for _, row in fixture_df.iterrows():
        key = (
            _s(row["SIZE_INCH"]), _s(row["CLASS"]), _s(row["MOC"]), _s(row["DESIGN"]),
            _s(row["TASK"]), _s(row["WORK_CENTER"]),
        )
        by_combo_machine[key] = FixtureTiming(
            fixture=_s(row["FIXTURE"]),
            locator=_s(row["LOCATOR"]),
            fixture_change_time=0.0 if pd.isna(row["FIXTURE_CHANGE_TIME"]) else float(row["FIXTURE_CHANGE_TIME"]),
            locator_change_time=0.0 if pd.isna(row["LOCATOR_CHANGE_TIME"]) else float(row["LOCATOR_CHANGE_TIME"]),
            load_unload_time=0.0 if pd.isna(row["LOAD_UNLOAD_TIME"]) else float(row["LOAD_UNLOAD_TIME"]),
        )
    return FixtureIndex(by_combo_machine=by_combo_machine)


def build_device_pool(inventory_df: pd.DataFrame) -> DevicePool:
    quantities = {_s(row["DEVICE_NAME"]): int(row["QUANTITY"]) for _, row in inventory_df.iterrows()}
    return DevicePool(quantities=quantities)


@dataclass
class ResolvedAvailability:
    """
    CLAUDE.md's capacity_resolved merge (MCH_MACHINE_AVAILABILITY_BY_DATE
    override onto MCH_MACHINE_AVAILABILITY baseline, with the confirmed
    company holiday calendar — L750.TCCCP019 — forcing every shift of every
    machine closed on top of both), as a plain, picklable callable —
    deliberately a dataclass, NOT a closure, because this object travels as
    an argument into `dispatch_parallel`'s worker processes for the §E.10
    speculative check, and closures cannot be pickled.

    Precedence, checked in this order: holiday (always wins, every machine,
    every shift) -> machine_daily override -> machine_master baseline.
    """

    baseline: dict[tuple[str, str], float] = field(default_factory=dict)
    overrides: dict[tuple[str, str, date], float] = field(default_factory=dict)
    holidays: frozenset[date] = frozenset()

    def __call__(self, machine: str, day: date, shift: str) -> float:
        if day in self.holidays:
            return 0.0
        override_key = (machine, shift, day)
        if override_key in self.overrides:
            return self.overrides[override_key]
        return self.baseline.get((machine, shift), 0.0)  # no baseline row at all -> treat as closed, never as unlimited


def build_availability(
    machine_master_df: pd.DataFrame,
    machine_daily_df: pd.DataFrame,
    holiday_df: Optional[pd.DataFrame] = None,
) -> ResolvedAvailability:
    baseline: dict[tuple[str, str], float] = {}
    for _, row in machine_master_df.iterrows():
        baseline[(_s(row["WORK_CENTER"]), _s(row["SHIFT"]).lower())] = float(row["AVAILABLE_MINS"])

    overrides: dict[tuple[str, str, date], float] = {}
    for _, row in machine_daily_df.iterrows():
        working_date = row["WORKING_DATE"]
        d = working_date.date() if hasattr(working_date, "date") else working_date
        overrides[(_s(row["WORK_CENTER"]), _s(row["SHIFT"]).lower(), d)] = float(row["AVAILABLE_MINS"])

    holidays: frozenset = frozenset()
    if holiday_df is not None and not holiday_df.empty:
        holidays = frozenset(
            (d.date() if hasattr(d, "date") else d) for d in holiday_df["DATE_1"]
        )

    return ResolvedAvailability(baseline=baseline, overrides=overrides, holidays=holidays)
