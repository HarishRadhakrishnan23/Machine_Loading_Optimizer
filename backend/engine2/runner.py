"""
runner.py — simulate one input set with Engine 1's own simulator, unchanged.

This mirrors the input construction and the `run_dispatch_simulation` call of
`model_f_pipeline.generate_schedule` (minus the Oracle write) so an unedited
Engine 2 run reproduces the real schedule. That mirror is the one place Engine 2
could silently drift from Engine 1, so it is guarded two ways: the unit test
`test_engine2.py::test_runner_matches_engine1_direct_call`, and the service's
"baseline vs the saved MCH_SCHEDULE_OUTPUT" note shown to the planner.
"""

from __future__ import annotations

from dispatch_orchestrator import FinalRow, run_dispatch_simulation
from dispatch_orders import RawWipRow, build_order_chains
from engine2.snapshot import Snapshot
from model_f_data import build_fixture_index_from_fnl, build_routing_index_from_fnl


def build_chains(snap: Snapshot, raw_rows: list[RawWipRow] | None = None, fnl_df=None) -> list:
    """Classified, precedence-bridged OrderChains for `raw_rows`/`fnl_df` (default: the snapshot's own)."""
    raw_rows = snap.raw_rows if raw_rows is None else raw_rows
    fnl_df = snap.fnl_df if fnl_df is None else fnl_df
    return build_order_chains(
        raw_rows,
        build_routing_index_from_fnl(fnl_df),
        build_fixture_index_from_fnl(fnl_df),
        frozenset(snap.pool.quantities.keys()),
        snap.known_machines,
        frozenset(snap.config["fixture_required_tasks"]),
    )


def simulate(snap: Snapshot, raw_rows: list[RawWipRow] | None = None, fnl_df=None) -> list[FinalRow]:
    """Run Engine 1's dispatch simulation on `raw_rows`/`fnl_df` (default: the snapshot's own)."""
    config = snap.config
    pool = snap.pool.clone()  # the simulator reserves into the pool — never touch the snapshot's own
    chains = build_chains(snap, raw_rows, fnl_df)
    return run_dispatch_simulation(
        chains,
        snap.availability,
        pool,
        today=snap.run_date,
        window_days=config["batch_consolidation_window_days"],
        cooling_minutes=config["cooling_minutes"],
        day_zero=snap.run_date,
        planned_order_start_buffer_days=config["planned_order_start_buffer_days"],
        heavy_operations=frozenset(config["heavy_operations"]),
        enable_safety_stock_speculation=True,
        allow_soft_consolidation_beyond_window=config.get("allow_soft_consolidation_beyond_window", False),
        soft_consolidation_max_extra_days=config.get("soft_consolidation_max_extra_days", 0),
    )
