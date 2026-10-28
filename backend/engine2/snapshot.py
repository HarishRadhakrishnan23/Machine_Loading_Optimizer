"""
snapshot.py — the read-only copy of every input one what-if session works from.

A Snapshot is built once (Oracle reads only), reused for the baseline and every
scenario, and is never mutated: scenarios edit COPIES (see scenarios.py), and
each simulation run clones the fixture/locator pool because the simulator
reserves into it.

`Snapshot` is a plain dataclass so tests can build one from in-memory data
without Oracle.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from dispatch_orders import RawWipRow
from dispatch_pool import DevicePool

CONFIG_FILE = Path(__file__).resolve().parent.parent / "config.json"


@dataclass
class Snapshot:
    loaded_at: datetime
    run_date: date
    config: dict  # same dict shape model_f_pipeline.generate_schedule receives (Config.model_dump())
    raw_rows: list[RawWipRow]
    fnl_df: pd.DataFrame  # MCH_MACHINE_FNL_MASTER, never mutated — scenarios edit a copy
    pool: DevicePool  # never mutated — runner clones per run
    availability: Callable  # picklable (travels into admission-check worker processes)
    known_machines: frozenset[str]
    # production_order -> its REAL CDD (scenarios may override the CDD used for
    # ranking; impact is always measured against this one)
    real_cdd: dict[str, Optional[date]] = field(default_factory=dict)
    # Filled lazily by service.ensure_baseline
    baseline_rows: Optional[list] = None
    baseline_note: Optional[str] = None
    # Phase 2: the formed runs and the unedited replay of them (filled lazily by service.ensure_plan)
    plan: Optional[object] = None
    plan_replay: Optional[object] = None


def load_config_dict() -> dict:
    """Exactly what main.load_config() -> Config -> model_dump() hands the Engine 1 pipeline."""
    from models import Config  # local import: keeps this module importable without pydantic in light tests

    with open(CONFIG_FILE) as f:
        return Config(**json.load(f)).model_dump()


def real_cdd_by_order(raw_rows: list[RawWipRow]) -> dict[str, Optional[date]]:
    out: dict[str, Optional[date]] = {}
    for r in raw_rows:
        out.setdefault(r.production_order, r.cdd)
    return out


def load_snapshot(run_date: Optional[date] = None) -> Snapshot:
    """Read every Engine 1 input from Oracle (read-only), same sources and same
    construction as model_f_pipeline.generate_schedule, minus the write."""
    from db import (
        read_fixture_locator_inventory,
        read_holiday_calendar,
        read_machine_daily,
        read_machine_fnl_master,
        read_machine_master,
        read_wip_orders,
    )
    from model_f_data import build_availability, build_device_pool, build_raw_wip_rows

    run_date = run_date or date.today()
    wip_df = read_wip_orders()
    machine_master_df = read_machine_master()
    machine_daily_df = read_machine_daily()
    fnl_df = read_machine_fnl_master()
    inventory_df = read_fixture_locator_inventory()
    holiday_df = read_holiday_calendar()

    raw_rows = build_raw_wip_rows(wip_df)
    known_machines = frozenset(machine_master_df["WORK_CENTER"].astype(str).str.strip()) | frozenset(
        machine_daily_df["WORK_CENTER"].astype(str).str.strip()
    )
    return Snapshot(
        loaded_at=datetime.now(),
        run_date=run_date,
        config=load_config_dict(),
        raw_rows=raw_rows,
        fnl_df=fnl_df,
        pool=build_device_pool(inventory_df),
        availability=build_availability(machine_master_df, machine_daily_df, holiday_df),
        known_machines=known_machines,
        real_cdd=real_cdd_by_order(raw_rows),
    )
