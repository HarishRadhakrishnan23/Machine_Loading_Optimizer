"""
Phase 2 gate: an UNEDITED replay of the formed runs must reproduce Engine 1's simulator exactly.
(Live-data version of the same check: engine2_live_parity.py, which also exercises admission merges.)

Run: backend/venv/Scripts/python.exe -m pytest backend/testing/test_engine2_plan.py -q
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from engine2.plan import build_plan, replay
from engine2.runner import simulate
from test_engine2 import make_snapshot


def test_unedited_replay_equals_engine1_simple():
    snap = make_snapshot({"A": date(2026, 10, 5), "B": date(2026, 10, 9)})
    assert replay(snap, build_plan(snap)).rows == simulate(snap)


def test_unedited_replay_equals_engine1_two_machines_and_ties():
    snap = make_snapshot(
        {"A": date(2026, 10, 5), "B": date(2026, 10, 5)},
        machines_for={"A": [("M1", 1), ("M2", 2)], "B": [("M1", 1), ("M2", 2)]},
    )
    assert replay(snap, build_plan(snap)).rows == simulate(snap)


def test_replay_never_mutates_the_snapshot_or_the_plan():
    snap = make_snapshot({"A": date(2026, 10, 5), "B": date(2026, 10, 9)})
    plan = build_plan(snap)
    items_before = list(plan.work_items)
    first = replay(snap, plan)
    second = replay(snap, plan)
    assert first.rows == second.rows  # deterministic
    assert plan.work_items == items_before and snap.pool._held == {}


def test_every_placement_is_tagged_with_its_run():
    snap = make_snapshot({"A": date(2026, 10, 5), "B": date(2026, 10, 9)})
    plan = build_plan(snap)
    result = replay(snap, plan)
    tagged = sum(len(v) for v in result.placed_by_item.values())
    assert tagged == sum(1 for r in result.rows if r.machine is not None)
    assert all(0 <= i < len(plan.work_items) for i in result.placed_by_item)
