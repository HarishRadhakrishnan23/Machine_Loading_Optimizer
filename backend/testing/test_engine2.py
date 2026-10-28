"""
Engine 2 (what-if) Phase 1 tests — synthetic data only, never Oracle.

Run: backend/venv/Scripts/python.exe -m pytest backend/testing/test_engine2.py -q
"""

import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from dispatch_orchestrator import run_dispatch_simulation
from dispatch_orders import RawWipRow, build_order_chains
from dispatch_pool import DevicePool
from engine2 import metrics, service
from engine2.runner import simulate
from engine2.scenarios import AddedMachine, ElevateOrders, MachinePriority, ScenarioError
from engine2.snapshot import Snapshot, real_cdd_by_order
from model_f_data import ResolvedAvailability, build_fixture_index_from_fnl, build_routing_index_from_fnl

RUN_DATE = date(2026, 10, 1)
SHIFTS = ("first", "second", "third")

CONFIG = {
    "fixture_required_tasks": ["VB04"],
    "batch_consolidation_window_days": 60,
    "cooling_minutes": 20,
    "planned_order_start_buffer_days": 1,
    "heavy_operations": [],
    "risk_safe_threshold_days": 5,
    "allow_soft_consolidation_beyond_window": False,
    "soft_consolidation_max_extra_days": 0,
}

# (size, class, moc, design) per order — distinct combos AND distinct fixtures, so each order is its
# own fixture run and the two compete purely for machine time.
COMBO = {"A": ("8", "150", "CS", "STD"), "B": ("10", "150", "CS", "STD")}
FIXTURE = {"A": "FX_A", "B": "FX_B"}


def wip_row(order, cdd, task="VB04", qty=10, ct=120.0, combo=None):
    size, cls, moc, design = combo or COMBO[order]
    return RawWipRow(
        production_order=order, operation_no=10, task=task, work_center=None, size_inch=size, class_val=cls,
        moc=moc, design=design, cycle_time=ct, balance_qty=qty, cdd=cdd, order_date=datetime(2026, 9, 1),
        order_status="Active", production_start_date=date(2026, 9, 1),
    )


def fnl_rows(order, machines, task="VB04"):
    """machines: [(work_center, priority)] for this order's combo."""
    size, cls, moc, design = COMBO[order]
    return [
        {
            "COMPANY": 1, "SIZE_INCH": size, "CLASS": cls, "MOC": moc, "DESIGN": design, "COMBINATION": "x",
            "TASK": task, "PRIORITY": prio, "WORK_CENTER": wc, "FIXTURE_ID": FIXTURE[order], "LOCATOR_ID": f"LC_{order}",
            "FIXTURE_CHANGE_TIME": 30.0, "LOCATOR_CHANGE_TIME": 10.0, "LOAD_UNLOAD_TIME": 2.0,
        }
        for wc, prio in machines
    ]


def make_snapshot(cdds, machines_for=None, extra_known=()):
    """cdds: {order: cdd}. machines_for: {order: [(wc, prio)]}, default one machine M1."""
    machines_for = machines_for or {o: [("M1", 1)] for o in cdds}
    rows = [wip_row(o, c) for o, c in cdds.items()]
    fnl = pd.DataFrame([r for o in cdds for r in fnl_rows(o, machines_for[o])])
    all_machines = {wc for ms in machines_for.values() for wc, _ in ms} | set(extra_known)
    availability = ResolvedAvailability(baseline={(m, s): 450.0 for m in all_machines for s in SHIFTS})
    return Snapshot(
        loaded_at=datetime.now(),  # fresh, so service.get_snapshot() never reloads from Oracle in tests
        run_date=RUN_DATE, config=dict(CONFIG), raw_rows=rows, fnl_df=fnl,
        pool=DevicePool(quantities={"FX_A": 1, "FX_B": 1, "LC_A": 1, "LC_B": 1}), availability=availability,
        known_machines=frozenset(all_machines), real_cdd=real_cdd_by_order(rows),
    )


def completion(rows, order):
    return next(r.order_completion_date for r in rows if r.production_order == order and r.order_completion_date)


def machine_of(rows, order):
    return next(r.machine for r in rows if r.production_order == order and r.machine)


# ─────────────────────────────────────────────────────────────────────────────
def test_runner_matches_engine1_direct_call():
    """Engine 2's baseline must be exactly what Engine 1's own simulator produces from the same inputs."""
    snap = make_snapshot({"A": date(2026, 10, 5), "B": date(2026, 10, 9)})
    chains = build_order_chains(
        snap.raw_rows, build_routing_index_from_fnl(snap.fnl_df), build_fixture_index_from_fnl(snap.fnl_df),
        frozenset(snap.pool.quantities), snap.known_machines, frozenset(CONFIG["fixture_required_tasks"]),
    )
    direct = run_dispatch_simulation(
        chains, snap.availability, snap.pool.clone(), today=RUN_DATE, window_days=60, cooling_minutes=20,
        day_zero=RUN_DATE, planned_order_start_buffer_days=1, heavy_operations=frozenset(),
        enable_safety_stock_speculation=True, allow_soft_consolidation_beyond_window=False,
        soft_consolidation_max_extra_days=0,
    )
    assert simulate(snap) == direct


def test_simulation_is_deterministic_and_never_mutates_the_snapshot():
    snap = make_snapshot({"A": date(2026, 10, 5), "B": date(2026, 10, 9)})
    fnl_before = snap.fnl_df.copy()
    rows_before = list(snap.raw_rows)
    first, second = simulate(snap), simulate(snap)
    assert first == second
    assert snap.pool._held == {}  # the simulator reserved into a clone, never the snapshot's pool
    assert snap.fnl_df.equals(fnl_before) and snap.raw_rows == rows_before


def test_elevating_the_later_order_moves_it_first_and_delays_the_other():
    base_snap = make_snapshot({"A": date(2026, 10, 3), "B": date(2026, 10, 12)})
    baseline = simulate(base_snap)
    a_done, b_done = completion(baseline, "A"), completion(baseline, "B")
    assert a_done < b_done  # A (earlier CDD) runs first on the single machine

    # A's CDD = exactly when it finishes today, so anything that delays it makes it late.
    snap = make_snapshot({"A": a_done, "B": date(2026, 10, 12)})
    baseline = simulate(snap)
    scenario = ElevateOrders(orders=("B",))
    raw_rows, fnl = scenario.apply(snap)
    result = metrics.compare(baseline, simulate(snap, raw_rows, fnl), snap.real_cdd, scenario.elevated_orders(), 5)

    by_order = {o["order"]: o for o in result["orders"]}
    assert by_order["B"]["target"] and by_order["B"]["slip_days"] < 0  # elevated order finishes earlier
    assert by_order["A"]["slip_days"] > 0  # …and the other one pays for it
    assert by_order["A"]["new_risk"] == "BREACH"  # measured against A's REAL CDD
    assert result["verdict"]["label"] == "HURTS"
    assert result["kpis"]["before"]["late"] == 0 and result["kpis"]["after"]["late"] == 1
    assert [o["order"] for o in result["top_worsened"]] == ["A"]


def test_elevation_overrides_only_the_ranking_cdd_never_the_real_one():
    snap = make_snapshot({"A": date(2026, 10, 3), "B": date(2026, 10, 12)})
    rows, _ = ElevateOrders(orders=("B",)).apply(snap)
    assert next(r for r in rows if r.production_order == "B").cdd < date(2001, 1, 1)
    assert next(r for r in rows if r.production_order == "A").cdd == date(2026, 10, 3)
    assert snap.real_cdd["B"] == date(2026, 10, 12)  # impact is measured against this
    assert next(r for r in snap.raw_rows if r.production_order == "B").cdd == date(2026, 10, 12)  # snapshot untouched


def test_elevate_keeps_the_planners_order_as_priority_order():
    snap = make_snapshot({"A": date(2026, 10, 3), "B": date(2026, 10, 12)})
    rows, _ = ElevateOrders(orders=("B", "A")).apply(snap)
    cdd = {r.production_order: r.cdd for r in rows}
    assert cdd["B"] < cdd["A"]


def test_elevate_validation():
    snap = make_snapshot({"A": date(2026, 10, 3), "B": date(2026, 10, 12)})
    with pytest.raises(ScenarioError):
        ElevateOrders(orders=()).apply(snap)
    with pytest.raises(ScenarioError):
        ElevateOrders(orders=("NOPE",)).apply(snap)
    with pytest.raises(ScenarioError):
        ElevateOrders(orders=("A", "A")).apply(snap)


def test_machine_priority_reorder_changes_the_chosen_machine():
    snap = make_snapshot({"A": date(2026, 10, 9)}, machines_for={"A": [("M1", 1), ("M2", 2)]})
    baseline = simulate(snap)
    assert machine_of(baseline, "A") == "M1"  # priority 1 wins when both are free

    scenario = MachinePriority("8", "150", "CS", "STD", "VB04", ordering=("M2", "M1"))
    raw_rows, fnl = scenario.apply(snap)
    scenario_rows = simulate(snap, raw_rows, fnl)
    assert machine_of(scenario_rows, "A") == "M2"
    result = metrics.compare(baseline, scenario_rows, snap.real_cdd, frozenset(), 5)
    assert result["placements_changed"] > 0 and result["verdict"]["label"] != "NO_EFFECT"
    assert snap.fnl_df.loc[snap.fnl_df["WORK_CENTER"] == "M1", "PRIORITY"].iloc[0] == 1  # snapshot copy untouched


def test_machine_priority_same_order_is_no_effect():
    snap = make_snapshot({"A": date(2026, 10, 9)}, machines_for={"A": [("M1", 1), ("M2", 2)]})
    baseline = simulate(snap)
    scenario = MachinePriority("8", "150", "CS", "STD", "VB04", ordering=("M1", "M2"))
    raw_rows, fnl = scenario.apply(snap)
    result = metrics.compare(baseline, simulate(snap, raw_rows, fnl), snap.real_cdd, frozenset(), 5)
    assert result["placements_changed"] == 0 and result["verdict"]["label"] == "NO_EFFECT"


def test_machine_priority_added_machine_inherits_fixture_and_wins_at_priority_one():
    snap = make_snapshot({"A": date(2026, 10, 9)}, machines_for={"A": [("M1", 1)]}, extra_known=("M3",))
    baseline = simulate(snap)
    scenario = MachinePriority(
        "8", "150", "CS", "STD", "VB04", ordering=("M3", "M1"), added=(AddedMachine("M3", load_unload_time=1.0),)
    )
    raw_rows, fnl = scenario.apply(snap)
    new_row = fnl[fnl["WORK_CENTER"] == "M3"].iloc[0]
    assert new_row["FIXTURE_ID"] == "FX_A" and new_row["LOAD_UNLOAD_TIME"] == 1.0 and new_row["PRIORITY"] == 1
    assert machine_of(simulate(snap, raw_rows, fnl), "A") == "M3"
    assert (snap.fnl_df["WORK_CENTER"] == "M3").sum() == 0  # snapshot copy untouched


def test_machine_priority_validation():
    snap = make_snapshot({"A": date(2026, 10, 9)}, machines_for={"A": [("M1", 1), ("M2", 2)]}, extra_known=("M3",))
    combo = ("8", "150", "CS", "STD", "VB04")
    with pytest.raises(ScenarioError):  # missing a machine
        MachinePriority(*combo, ordering=("M1",)).apply(snap)
    with pytest.raises(ScenarioError):  # duplicate
        MachinePriority(*combo, ordering=("M1", "M1", "M2")).apply(snap)
    with pytest.raises(ScenarioError):  # extra machine not declared as added
        MachinePriority(*combo, ordering=("M1", "M2", "M3")).apply(snap)
    with pytest.raises(ScenarioError):  # added machine already capable
        MachinePriority(*combo, ordering=("M1", "M2"), added=(AddedMachine("M2"),)).apply(snap)
    with pytest.raises(ScenarioError):  # no availability data in the ERP
        MachinePriority(*combo, ordering=("M1", "M2", "GHOST"), added=(AddedMachine("GHOST"),)).apply(snap)
    with pytest.raises(ScenarioError):  # unknown combo
        MachinePriority("99", "150", "CS", "STD", "VB04", ordering=("M1",)).apply(snap)


# ─────────────────────────────────────────────────────────────────────────────
def _row(order, completion_date):
    return SimpleNamespace(
        production_order=order, order_completion_date=completion_date, machine=None, start=None, end=None,
        operation_no=10, line_no=1,
    )


def test_risk_classification():
    cdd = date(2026, 10, 10)
    assert metrics.classify_risk(date(2026, 10, 20), cdd, 5) == "BREACH"
    assert metrics.classify_risk(date(2026, 10, 7), cdd, 5) == "AT_RISK"
    assert metrics.classify_risk(date(2026, 10, 1), cdd, 5) == "SAFE"
    assert metrics.classify_risk(None, cdd, 5) == "BREACH"
    assert metrics.classify_risk(date(2026, 10, 20), None, 5) == "SAFE"  # safety stock has no deadline


def test_verdict_rules_in_order():
    cdd = {"X": date(2026, 10, 10), "Y": date(2026, 10, 10)}
    on_time, late = date(2026, 10, 9), date(2026, 10, 12)
    helps = metrics.compare([_row("X", late)], [_row("X", on_time)], cdd, frozenset(), 5)
    assert helps["verdict"]["label"] == "HELPS"
    hurts = metrics.compare([_row("X", on_time)], [_row("X", late)], cdd, frozenset(), 5)
    assert hurts["verdict"]["label"] == "HURTS"
    # same late count, less total lateness -> HELPS (rule 2)
    rule2 = metrics.compare([_row("X", date(2026, 10, 15))], [_row("X", date(2026, 10, 12))], cdd, frozenset(), 5)
    assert rule2["verdict"]["label"] == "HELPS"
    same = metrics.compare([_row("X", on_time)], [_row("X", on_time)], cdd, frozenset(), 5)
    assert same["verdict"]["label"] == "NO_EFFECT"


# ─────────────────────────────────────────────────────────────────────────────
def test_service_run_store_and_save(monkeypatch):
    snap = make_snapshot({"A": date(2026, 10, 3), "B": date(2026, 10, 12)})
    monkeypatch.setattr(service, "_snapshot", snap)
    monkeypatch.setattr(service, "_baseline_vs_saved_note", lambda s: None)  # no Oracle in tests
    monkeypatch.setattr(service, "_results", service.OrderedDict())

    result = service.run_scenario(ElevateOrders(orders=("B",)))
    assert service.get_result(result["id"]) is result
    assert result["elevated_orders"] == ["B"] and result["kind"] == "elevate"
    assert snap.baseline_rows is not None  # computed once, cached on the snapshot

    saved = {}
    import db

    monkeypatch.setattr(db, "write_sim_results", lambda rows, sim_id: saved.update(rows=rows, sim_id=sim_id) or len(rows))
    out = service.save_result(result["id"])
    assert out["rows_written"] == len(saved["rows"]) and saved["sim_id"] == result["id"]
    assert all(r["PRODUCTION_ORDER"] != "B" for r in saved["rows"])  # the elevated order is the cause, not an impacted party
    assert all(len(r["ELEVATED_ORDER"]) <= 200 for r in saved["rows"])
    with pytest.raises(KeyError):
        service.save_result("does-not-exist")


def test_result_store_is_bounded(monkeypatch):
    snap = make_snapshot({"A": date(2026, 10, 3), "B": date(2026, 10, 12)})
    monkeypatch.setattr(service, "_snapshot", snap)
    monkeypatch.setattr(service, "_baseline_vs_saved_note", lambda s: None)
    monkeypatch.setattr(service, "_results", service.OrderedDict())
    monkeypatch.setattr(service, "MAX_RESULTS", 2)
    ids = [service.run_scenario(ElevateOrders(orders=("B",)))["id"] for _ in range(3)]
    assert service.get_result(ids[0]) is None and service.get_result(ids[2]) is not None
