"""
Phase 2 tests: moving / reordering the formed fixture runs (engine2/run_edits.py). Synthetic data only.

Run: backend/venv/Scripts/python.exe -m pytest backend/testing/test_engine2_run_edits.py -q
"""

import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from dispatch_batching import ChargedStep
from dispatch_orchestrator import WorkItem
from dispatch_orders import RawWipRow
from dispatch_pool import DevicePool
from engine2 import metrics
from engine2.plan import build_plan, replay
from engine2.run_edits import MoveRun, PlaceBefore, RunEdits, _drop_double_placements, apply_edits
from engine2.scenarios import ScenarioError
from engine2.snapshot import Snapshot, real_cdd_by_order
from model_f_data import ResolvedAvailability
from test_engine2 import CONFIG, RUN_DATE, SHIFTS

CFG = dict(CONFIG, fixture_required_tasks=["VB04", "VB05"])


def _row(order, op, task, combo, cdd, ct=120.0, qty=10):
    s, c, m, d = combo
    return RawWipRow(
        production_order=order, operation_no=op, task=task, work_center=None, size_inch=s, class_val=c, moc=m,
        design=d, cycle_time=ct, balance_qty=qty, cdd=cdd, order_date=datetime(2026, 9, 1), order_status="Active",
        production_start_date=date(2026, 9, 1),
    )


def _fnl(combo, task, machines, fixture, locator):
    s, c, m, d = combo
    return [
        {"COMPANY": 1, "SIZE_INCH": s, "CLASS": c, "MOC": m, "DESIGN": d, "COMBINATION": "x", "TASK": task,
         "PRIORITY": prio, "WORK_CENTER": wc, "FIXTURE_ID": fixture, "LOCATOR_ID": locator,
         "FIXTURE_CHANGE_TIME": 30.0, "LOCATOR_CHANGE_TIME": 10.0, "LOAD_UNLOAD_TIME": 2.0}
        for wc, prio in machines
    ]


def _snap(rows, fnl_rows, machines, fixtures):
    availability = ResolvedAvailability(baseline={(m, s): 450.0 for m in machines for s in SHIFTS})
    return Snapshot(
        loaded_at=datetime.now(), run_date=RUN_DATE, config=dict(CFG), raw_rows=rows, fnl_df=pd.DataFrame(fnl_rows),
        pool=DevicePool(quantities={f: 1 for f in fixtures}), availability=availability,
        known_machines=frozenset(machines), real_cdd=real_cdd_by_order(rows),
    )


def two_op_snapshot():
    """A (earlier CDD) and B each have op10 (VB04, on M1) then op20 (VB05, on M2); every run is solo."""
    combos = {"A": ("8", "150", "CS", "STD"), "B": ("10", "150", "CS", "STD")}
    cdds = {"A": date(2026, 10, 6), "B": date(2026, 10, 9)}
    rows, fnl, fixtures = [], [], []
    for o, combo in combos.items():
        rows += [_row(o, 10, "VB04", combo, cdds[o]), _row(o, 20, "VB05", combo, cdds[o])]
        for task, mach in (("VB04", "M1"), ("VB05", "M2")):
            fx, lc = f"FX_{o}_{task}", f"LC_{o}_{task}"
            fixtures += [fx, lc]
            fnl += _fnl(combo, task, [(mach, 1)], fx, lc)
    return _snap(rows, fnl, ["M1", "M2"], fixtures)


def shared_fixture_snapshot():
    """A (combo 8) and B (combo 10) share one fixture+locator so they batch into ONE run. A can run on
    M1 or M2; B only on M1 — so a move of that run to M2 is a PARTIAL move (covers 1 of 2)."""
    ca, cb = ("8", "150", "CS", "STD"), ("10", "150", "CS", "STD")
    rows = [_row("A", 10, "VB04", ca, date(2026, 10, 6)), _row("B", 10, "VB04", cb, date(2026, 10, 7))]
    fnl = _fnl(ca, "VB04", [("M1", 1), ("M2", 2)], "FX_S", "LC_S") + _fnl(cb, "VB04", [("M1", 1)], "FX_S", "LC_S")
    return _snap(rows, fnl, ["M1", "M2"], ["FX_S", "LC_S"])


def machine_of(rows, order, op=None):
    return next(r.machine for r in rows if r.production_order == order and r.machine and (op is None or r.operation_no == op))


def start_of(rows, order, op):
    return min(r.start for r in rows if r.production_order == order and r.operation_no == op and r.start)


def run_of(plan, base, order, task):
    return next(i for i in base.placed_by_item if plan.work_items[i].task == task and any(p.production_order == order for p in base.placed_by_item[i]))


# ─────────────────────────────────────────────────────────────────────────────
def test_unedited_pin_reproduces_the_baseline_exactly():
    """Pinning every run to the machine it already has must change nothing."""
    snap = two_op_snapshot()
    plan = build_plan(snap)
    base = replay(snap, plan)
    runs = sorted(base.placed_by_item)
    out = apply_edits(plan, base, (PlaceBefore(runs[0], runs[1], pin=True),))  # already in this order
    assert replay(snap, plan, out.work_items).rows == base.rows
    assert "already runs before" in " ".join(out.notes)


def test_move_forces_the_machine_and_costs_nothing_elsewhere():
    snap = shared_fixture_snapshot()
    plan = build_plan(snap)
    base = replay(snap, plan)
    assert machine_of(base.rows, "A") == "M1" and machine_of(base.rows, "B") == "M1"
    assert len(base.placed_by_item) == 1  # one run holds both


def test_partial_move_splits_off_the_orders_the_target_cannot_take():
    snap = shared_fixture_snapshot()
    plan = build_plan(snap)
    base = replay(snap, plan)
    run = next(iter(base.placed_by_item))
    out = apply_edits(plan, base, (MoveRun(run, "M2"),))
    rows = replay(snap, plan, out.work_items).rows
    assert machine_of(rows, "A") == "M2"  # the order M2 can take moved
    assert machine_of(rows, "B") == "M1"  # B cannot run on M2: its own run, normal machine
    text = " ".join(out.notes)
    assert "covers 1 of 2" in text and "their own runs" in text
    assert len(out.work_items) == 2 and len(out.spans[run]) == 2  # host + one split-off solo run


def test_move_validation():
    snap = shared_fixture_snapshot()
    plan = build_plan(snap)
    base = replay(snap, plan)
    run = next(iter(base.placed_by_item))
    with pytest.raises(ScenarioError, match="can run on"):
        apply_edits(plan, base, (MoveRun(run, "NOPE"),))
    with pytest.raises(ScenarioError, match="not a run"):
        apply_edits(plan, base, (MoveRun(99, "M1"),))


def test_place_before_pulls_the_feeder_run_forward_and_keeps_precedence():
    snap = two_op_snapshot()
    plan = build_plan(snap)
    base = replay(snap, plan)
    a1, a2 = run_of(plan, base, "A", "VB04"), run_of(plan, base, "A", "VB05")
    b1, b2 = run_of(plan, base, "B", "VB04"), run_of(plan, base, "B", "VB05")
    assert a1 < b1  # A (earlier CDD) is ahead in the baseline

    out = apply_edits(plan, base, (PlaceBefore(b2, a1, pin=True),))  # B's second op before A's first op
    assert out.pulled == [b1]  # B's FIRST op must come with it
    assert "feeder" in " ".join(out.notes)
    result = replay(snap, plan, out.work_items)
    # precedence is intact for every order (op20 never starts before op10 starts)
    for o in ("A", "B"):
        assert start_of(result.rows, o, 20) >= start_of(result.rows, o, 10)
    # B's chain now finishes earlier than before, A's later
    before, after = metrics.completion_by_order(base.rows), metrics.completion_by_order(result.rows)
    assert after["B"] <= before["B"] and after["A"] >= before["A"]


def test_place_before_validation():
    snap = two_op_snapshot()
    plan = build_plan(snap)
    base = replay(snap, plan)
    b1, b2 = run_of(plan, base, "B", "VB04"), run_of(plan, base, "B", "VB05")
    with pytest.raises(ScenarioError, match="needs the output"):
        apply_edits(plan, base, (PlaceBefore(b2, b1),))  # b2 depends on b1
    with pytest.raises(ScenarioError, match="before itself"):
        apply_edits(plan, base, (PlaceBefore(b1, b1),))
    with pytest.raises(ScenarioError, match="not a run"):
        apply_edits(plan, base, (PlaceBefore(b1, 99),))


def test_edits_compose_in_order():
    snap = two_op_snapshot()
    plan = build_plan(snap)
    base = replay(snap, plan)
    b2 = run_of(plan, base, "B", "VB05")
    a1 = run_of(plan, base, "A", "VB04")
    scenario = RunEdits((PlaceBefore(b2, a1), MoveRun(a1, "M1")))
    rows, extras = scenario.run(snap, plan, base)
    assert {c["run"] for c in extras["runs_changed"]} >= {b2, a1}
    assert any(c["role"] == "pulled forward" for c in extras["runs_changed"])
    assert "Runs:" in scenario.describe() and len(scenario.describe()) <= 190
    with pytest.raises(ScenarioError, match="at least one"):
        RunEdits(()).run(snap, plan, base)


def test_a_far_cdd_merge_candidate_is_never_placed_twice():
    """If a candidate's own solo run now sits ahead of the run it would merge into, it is dropped from the host."""
    cand = ChargedStep("C", "FX", "LC", "fixture_change", run_index=-1)
    host = WorkItem(0, "VB04", {}, [ChargedStep("H", "FX", "LC", "fixture_change", run_index=0)], None, [cand])
    solo_c = WorkItem(0, "VB04", {}, [cand], None, None)
    notes: list = []
    fixed = _drop_double_placements([solo_c, host], notes)  # solo C now BEFORE its host
    assert fixed[1].soft_merge_candidates is None and "not merged" in " ".join(notes)
    kept = _drop_double_placements([host, solo_c], [])  # normal order: untouched
    assert kept[0].soft_merge_candidates == [cand]
