"""
service.py — Engine 2 orchestration: the cached snapshot + baseline, running a
scenario, the in-memory result store, and the one optional write
("Save this result" -> MCH_SIM_RESULTS).

Everything lives in server memory (single Uvicorn process): the snapshot, its
baseline, and the most recent MAX_RESULTS results. A restart clears them — by
design (CLAUDE.md: results are kept only while you view them).
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from datetime import date, datetime, timedelta
from typing import Optional

from engine2 import metrics
from engine2.runner import simulate
from engine2.scenarios import ElevateOrders, MachinePriority, ScenarioError
from engine2.snapshot import Snapshot, load_snapshot

MAX_RESULTS = 20
STALE_AFTER = timedelta(minutes=30)

_snapshot: Optional[Snapshot] = None
_snapshot_lock = threading.Lock()
_baseline_lock = threading.Lock()
_results: "OrderedDict[str, dict]" = OrderedDict()
_results_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────────────────────
# Snapshot / baseline
# ─────────────────────────────────────────────────────────────────────────────
def get_snapshot(refresh: bool = False) -> Snapshot:
    """The cached snapshot; reloaded when `refresh` is set or it is older than STALE_AFTER."""
    global _snapshot
    with _snapshot_lock:
        stale = _snapshot is None or datetime.now() - _snapshot.loaded_at > STALE_AFTER
        if refresh or stale:
            _snapshot = load_snapshot()
        return _snapshot


def ensure_baseline(snap: Snapshot) -> list:
    """Baseline = a fresh simulation of the CURRENT data, computed once per snapshot."""
    with _baseline_lock:
        if snap.baseline_rows is None:
            snap.baseline_rows = simulate(snap)
            snap.baseline_note = _baseline_vs_saved_note(snap)
        return snap.baseline_rows


def _baseline_vs_saved_note(snap: Snapshot) -> Optional[str]:
    """Tell the planner when the baseline differs from the schedule currently saved in
    MCH_SCHEDULE_OUTPUT (WIP/priorities changed since the last Generate). Also the live
    tripwire for Engine 2 drifting from Engine 1."""
    try:
        from db import read_live_schedule_completions

        saved = read_live_schedule_completions()
    except Exception:
        return None
    if saved.empty:
        return "No schedule is saved in MCH_SCHEDULE_OUTPUT yet; the baseline is a simulation of current data."

    saved_by_order = {}
    for _, r in saved.iterrows():
        d = r["ORDER_COMPLETION_DATE"]
        if d is not None and d == d:  # not NaT
            saved_by_order[r["PRODUCTION_ORDER"]] = d.date() if hasattr(d, "date") else d
    base = metrics.completion_by_order(snap.baseline_rows)
    differing = sum(1 for o, d in base.items() if o in saved_by_order and saved_by_order[o] != d)
    missing = len(set(base) ^ set(saved_by_order))
    if differing == 0 and missing == 0:
        return None
    return (
        f"Heads-up: the schedule currently saved in MCH_SCHEDULE_OUTPUT is out of date — it differs from a fresh "
        f"simulation of today's data for {differing} order(s) and covers {missing} different order(s) (WIP, machine data, "
        "priorities or the run date changed since the last Generate Schedule). What-if results compare against the fresh "
        "simulation, which is exactly what Generate Schedule would produce now (verified: testing/engine2_live_parity.py)."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Running scenarios
# ─────────────────────────────────────────────────────────────────────────────
def run_scenario(scenario, refresh: bool = False) -> dict:
    snap = get_snapshot(refresh=refresh)
    baseline = ensure_baseline(snap)

    raw_rows, fnl_df = scenario.apply(snap)
    scenario_rows = simulate(snap, raw_rows, fnl_df)

    result = metrics.compare(
        baseline,
        scenario_rows,
        snap.real_cdd,
        scenario.elevated_orders(),
        snap.config.get("risk_safe_threshold_days", 5),
    )
    result.update(
        {
            "id": uuid.uuid4().hex,
            "kind": scenario.kind,
            "summary": scenario.describe(),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "snapshot_loaded_at": snap.loaded_at.isoformat(timespec="seconds"),
            "run_date": snap.run_date.isoformat(),
            "elevated_orders": sorted(scenario.elevated_orders()),
            "notes": [snap.baseline_note] if snap.baseline_note else [],
        }
    )
    with _results_lock:
        _results[result["id"]] = result
        while len(_results) > MAX_RESULTS:
            _results.popitem(last=False)
    return result


def get_result(result_id: str) -> Optional[dict]:
    with _results_lock:
        return _results.get(result_id)


# ─────────────────────────────────────────────────────────────────────────────
# Catalogue endpoints
# ─────────────────────────────────────────────────────────────────────────────
def list_orders() -> list[dict]:
    """Orders the engine schedules (those with a baseline placement), for the order picker."""
    snap = get_snapshot()
    baseline = ensure_baseline(snap)
    status_by_order = {}
    for r in snap.raw_rows:
        status_by_order.setdefault(r.production_order, r.order_status)
    seen: dict[str, dict] = {}
    for r in baseline:
        if r.machine is None or r.production_order in seen:
            continue
        seen[r.production_order] = {
            "order": r.production_order,
            "cdd": snap.real_cdd.get(r.production_order).isoformat() if snap.real_cdd.get(r.production_order) else None,
            "batch_key": r.batch_key,
            "status": status_by_order.get(r.production_order),
            "completion": r.order_completion_date.isoformat() if r.order_completion_date else None,
        }
    return sorted(seen.values(), key=lambda o: (o["cdd"] is None, o["cdd"] or "", o["order"]))


def status() -> dict:
    """Never loads or waits: reports only what is already cached (so the status poll cannot block behind a load)."""
    snap = _snapshot
    if snap is None:
        return {"loaded": False, "snapshot_loaded_at": None, "run_date": None, "baseline_ready": False,
                "baseline_note": None, "results_in_memory": len(_results)}
    return {
        "loaded": True,
        "snapshot_loaded_at": snap.loaded_at.isoformat(timespec="seconds"),
        "run_date": snap.run_date.isoformat(),
        "baseline_ready": snap.baseline_rows is not None,
        "baseline_note": snap.baseline_note,
        "results_in_memory": len(_results),
    }


def combos() -> dict:
    """Machine lists per combo+task from the snapshot (for the Shuffler preview's added-machine picker)."""
    snap = get_snapshot()
    return {"machines": sorted(snap.known_machines)}


# ─────────────────────────────────────────────────────────────────────────────
# The one write: optional "Save this result"
# ─────────────────────────────────────────────────────────────────────────────
def save_result(result_id: str) -> dict:
    """Write a result's per-order rows to MCH_SIM_RESULTS (the only write Engine 2 ever makes,
    and only when the planner asks). ELEVATED_ORDER carries the scenario summary (<=200 chars)."""
    result = get_result(result_id)
    if result is None:
        raise KeyError(result_id)
    from db import write_sim_results

    now = datetime.now()
    rows = []
    for o in result["orders"]:
        if o["target"]:
            continue  # the elevated order is the cause, not an impacted party
        rows.append(
            {
                "ELEVATED_ORDER": result["summary"][:200],
                "PRODUCTION_ORDER": o["order"],
                "OLD_COMPLETION_DATE": date.fromisoformat(o["old_completion"]) if o["old_completion"] else None,
                "NEW_COMPLETION_DATE": date.fromisoformat(o["new_completion"]) if o["new_completion"] else None,
                "SLIP_DAYS": o["slip_days"],
                "RISK_FLAG": o["new_risk"],
                "created_at": now,
            }
        )
    written = write_sim_results(rows, result["id"])
    return {"sim_id": result["id"], "rows_written": written}
