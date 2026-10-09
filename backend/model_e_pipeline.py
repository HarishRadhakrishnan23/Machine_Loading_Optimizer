"""
model_e_pipeline.py — Engine 1 Model E: the single entry point tying
together data load (model_e_data.py) -> classification (dispatch_orders.py)
-> simulation (dispatch_orchestrator.py) -> write (dispatch_writer.py +
db.py). This is what `main.py`'s `POST /schedule/generate` calls — CP-SAT
(engine1_scheduler.py / preprocess.py / pipeline.py) is no longer invoked by
the live API; those modules remain only as Model C's historical record.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from db import (
    delete_schedule_output,
    read_fixture_locator_inventory,
    read_fixture_locator_master,
    read_holiday_calendar,
    read_machine_daily,
    read_machine_master,
    read_routing_master,
    read_wip_orders,
    write_schedule_output,
)
from dispatch_orchestrator import run_dispatch_simulation
from dispatch_orders import build_order_chains
from dispatch_writer import to_oracle_rows
from model_e_data import (
    build_availability,
    build_device_pool,
    build_fixture_index,
    build_raw_wip_rows,
    build_routing_index,
)


@dataclass(frozen=True)
class ScheduleGenerateResult:
    run_id: str
    rows_written: int
    scheduled_count: int
    scheduled_no_fixture_count: int
    excluded_count: int


def generate_schedule(config: dict, run_date: date) -> ScheduleGenerateResult:
    """
    Runs the full Model E pipeline against live Oracle data and writes the
    result to MCH_SCHEDULE_OUTPUT (deleting prior rows first — no historical
    retention, CLAUDE.md).
    """
    wip_df = read_wip_orders()
    machine_master_df = read_machine_master()
    machine_daily_df = read_machine_daily()
    routing_df = read_routing_master()
    fixture_df = read_fixture_locator_master()
    inventory_df = read_fixture_locator_inventory()
    holiday_df = read_holiday_calendar()

    raw_rows = build_raw_wip_rows(wip_df)
    routing_index = build_routing_index(routing_df)
    fixture_index = build_fixture_index(fixture_df)
    pool = build_device_pool(inventory_df)
    known_devices = frozenset(pool.quantities.keys())
    availability = build_availability(machine_master_df, machine_daily_df, holiday_df)

    # Machines with zero rows anywhere in MCH_MACHINE_AVAILABILITY /
    # MCH_MACHINE_AVAILABILITY_BY_DATE are permanently "closed" per
    # ResolvedAvailability's own honest default (0.0 — it can't invent an
    # open shift the ERP never reported). A routing candidate pointing at
    # one of those is a dead end the engine can never place — confirmed live
    # (machine 2PBX74: a routing entry, zero availability rows, which made
    # next_open_instant's open-shift search run forever and crash with
    # "date value out of range"). Excluding it up front (see dispatch_scope.
    # EXCLUDED_NO_AVAILABILITY_DATA) turns that crash into a normal REMARK.
    known_machines = frozenset(machine_master_df["WORK_CENTER"].astype(str).str.strip()) | frozenset(
        machine_daily_df["WORK_CENTER"].astype(str).str.strip()
    )

    order_chains = build_order_chains(raw_rows, routing_index, fixture_index, known_devices, known_machines)

    final_rows = run_dispatch_simulation(
        order_chains,
        availability,
        pool,
        today=run_date,
        window_days=config["batch_consolidation_window_days"],
        cooling_minutes=config["cooling_minutes"],
        day_zero=run_date,
        planned_order_start_buffer_days=config["planned_order_start_buffer_days"],
        heavy_operations=frozenset(config["heavy_operations"]),
        enable_safety_stock_speculation=True,
        allow_soft_consolidation_beyond_window=config.get("allow_soft_consolidation_beyond_window", False),
        soft_consolidation_max_extra_days=config.get("soft_consolidation_max_extra_days", 0),
    )

    run_id, oracle_rows = to_oracle_rows(final_rows)

    delete_schedule_output()
    rows_written = write_schedule_output(oracle_rows)

    # "No-fixture caveat" is identified by fixture_id being None, not by
    # remark being None — every SCHEDULED row carries a non-empty REMARK now
    # (placement reasoning, see dispatch_engine._placement_remark), so a
    # remark-is-None check would misclassify every clean scheduled row as
    # excluded/caveat (a real bug: it made this exact stat report 0
    # scheduled on a fully-working schedule). fixture_id is None only for
    # the §E.11 no-fixture/locator-match fallback — a real fixture run's
    # fixture_id is always set, even to the literal "NA" sentinel (no
    # physical device needed, still a genuine fixture/locator match).
    scheduled = sum(1 for r in final_rows if r.machine is not None and r.fixture_id is not None)
    scheduled_no_fixture = sum(1 for r in final_rows if r.machine is not None and r.fixture_id is None)
    excluded = sum(1 for r in final_rows if r.machine is None)

    return ScheduleGenerateResult(
        run_id=run_id,
        rows_written=rows_written,
        scheduled_count=scheduled,
        scheduled_no_fixture_count=scheduled_no_fixture,
        excluded_count=excluded,
    )
