"""
metrics.py — diff a baseline run against a scenario run and say whether the
scenario helps. Pure functions over FinalRow lists; no Oracle, no simulator.

"Impact" is always measured against each order's REAL CDD, even when a scenario
overrode the CDD used for ranking (elevate).

Verdict rule (shown to the planner with its numbers, never a hidden score):
  1. fewer late committed orders  -> HELPS;  more -> HURTS
  2. equal: less total lateness (days) -> HELPS;  more -> HURTS
  3. equal: no placement changed at all -> NO_EFFECT;  otherwise NEUTRAL
Changeover count/time is not part of the rule in Phase 1 (the engine's rows do
not carry per-run charge types); it is a candidate rule-3 refinement later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from shift_clock import timepoint_to_datetime

TOP_N = 10


def classify_risk(completion: Optional[date], cdd: Optional[date], threshold_days: int) -> str:
    """SAFE / AT_RISK / BREACH from slack = CDD - completion (days). No CDD => SAFE;
    not scheduled => BREACH (same semantics as the old Engine 2 classifier)."""
    if cdd is None:
        return "SAFE"
    if completion is None:
        return "BREACH"
    slack = (cdd - completion).days
    if slack < 0:
        return "BREACH"
    if slack <= threshold_days:
        return "AT_RISK"
    return "SAFE"


def completion_by_order(rows: Iterable) -> dict[str, date]:
    out: dict[str, date] = {}
    for r in rows:
        if r.order_completion_date is not None:
            out[r.production_order] = r.order_completion_date
    return out


def _placements(rows: Iterable) -> dict[tuple, tuple]:
    return {
        (r.production_order, r.operation_no, r.line_no): (r.machine, r.start, r.end)
        for r in rows
        if r.machine is not None
    }


def _machine_stats(rows: Iterable) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    for r in rows:
        if r.machine is None or r.start is None or r.end is None:
            continue
        s = stats.setdefault(r.machine, {"minutes": 0.0, "last_finish": None})
        start, end = timepoint_to_datetime(r.start), timepoint_to_datetime(r.end)
        s["minutes"] += (end - start).total_seconds() / 60.0
        if s["last_finish"] is None or end > s["last_finish"]:
            s["last_finish"] = end
    return stats


@dataclass(frozen=True)
class _Kpis:
    committed: int
    on_time: int
    late: int
    total_lateness_days: int
    worst_lateness_days: int

    def as_dict(self) -> dict:
        return {
            "committed_orders": self.committed,
            "on_time": self.on_time,
            "late": self.late,
            "attainment_pct": round(100.0 * self.on_time / self.committed, 1) if self.committed else None,
            "total_lateness_days": self.total_lateness_days,
            "worst_lateness_days": self.worst_lateness_days,
        }


def _kpis(completion: dict[str, date], real_cdd: dict[str, Optional[date]]) -> _Kpis:
    committed = on_time = late = total = worst = 0
    for order, done in completion.items():
        cdd = real_cdd.get(order)
        if cdd is None:
            continue
        committed += 1
        lateness = (done - cdd).days
        if lateness > 0:
            late += 1
            total += lateness
            worst = max(worst, lateness)
        else:
            on_time += 1
    return _Kpis(committed, on_time, late, total, worst)


def _iso(d: Optional[date]) -> Optional[str]:
    return d.isoformat() if d is not None else None


def compare(
    baseline_rows: list,
    scenario_rows: list,
    real_cdd: dict[str, Optional[date]],
    elevated: frozenset[str],
    threshold_days: int,
) -> dict:
    """Everything the UI needs, JSON-serialisable."""
    before = completion_by_order(baseline_rows)
    after = completion_by_order(scenario_rows)
    k_before, k_after = _kpis(before, real_cdd), _kpis(after, real_cdd)

    base_pl, scen_pl = _placements(baseline_rows), _placements(scenario_rows)
    placements_changed = sum(1 for k in set(base_pl) | set(scen_pl) if base_pl.get(k) != scen_pl.get(k))

    orders: list[dict] = []
    improved = worsened = newly_late = recovered = 0
    for order in sorted(set(before) | set(after)):
        old, new = before.get(order), after.get(order)
        cdd = real_cdd.get(order)
        old_risk = classify_risk(old, cdd, threshold_days)
        new_risk = classify_risk(new, cdd, threshold_days)
        slip = (new - old).days if old is not None and new is not None else None
        is_target = order in elevated
        if not is_target:
            if slip is not None and slip < 0:
                improved += 1
            elif slip is not None and slip > 0:
                worsened += 1
        if old_risk != "BREACH" and new_risk == "BREACH":
            newly_late += 1
        if old_risk == "BREACH" and new_risk != "BREACH":
            recovered += 1
        if is_target or slip != 0 or old_risk != new_risk:
            orders.append(
                {
                    "order": order,
                    "cdd": _iso(cdd),
                    "old_completion": _iso(old),
                    "new_completion": _iso(new),
                    "slip_days": slip,
                    "old_risk": old_risk,
                    "new_risk": new_risk,
                    "target": is_target,
                }
            )

    others = [o for o in orders if not o["target"] and o["slip_days"] is not None]
    top_worsened = sorted((o for o in others if o["slip_days"] > 0), key=lambda o: -o["slip_days"])[:TOP_N]
    top_improved = sorted((o for o in others if o["slip_days"] < 0), key=lambda o: o["slip_days"])[:TOP_N]

    # Machine load shift (scheduled span, same end-start convention as the Machine Utilisation page)
    ms_before, ms_after = _machine_stats(baseline_rows), _machine_stats(scenario_rows)
    machines = []
    for m in sorted(set(ms_before) | set(ms_after)):
        b, a = ms_before.get(m, {"minutes": 0.0, "last_finish": None}), ms_after.get(m, {"minutes": 0.0, "last_finish": None})
        delta = a["minutes"] - b["minutes"]
        if abs(delta) < 0.5 and b["last_finish"] == a["last_finish"]:
            continue
        machines.append(
            {
                "machine": m,
                "before_hours": round(b["minutes"] / 60.0, 1),
                "after_hours": round(a["minutes"] / 60.0, 1),
                "delta_hours": round(delta / 60.0, 1),
                "before_last_finish": b["last_finish"].isoformat() if b["last_finish"] else None,
                "after_last_finish": a["last_finish"].isoformat() if a["last_finish"] else None,
            }
        )
    machines.sort(key=lambda r: -abs(r["delta_hours"]))

    verdict = _verdict(k_before, k_after, placements_changed)
    return {
        "verdict": verdict,
        "kpis": {"before": k_before.as_dict(), "after": k_after.as_dict()},
        "placements_changed": placements_changed,
        "counts": {
            "orders_changed": len(orders),
            "improved": improved,
            "worsened": worsened,
            "newly_late": newly_late,
            "recovered": recovered,
        },
        "orders": orders,
        "top_worsened": top_worsened,
        "top_improved": top_improved,
        "machines": machines,
    }


def _verdict(b: _Kpis, a: _Kpis, placements_changed: int) -> dict:
    if a.late != b.late:
        label = "HELPS" if a.late < b.late else "HURTS"
        reason = f"Late committed orders: {b.late} -> {a.late}."
    elif a.total_lateness_days != b.total_lateness_days:
        label = "HELPS" if a.total_lateness_days < b.total_lateness_days else "HURTS"
        reason = (
            f"Same number of late orders ({b.late}), total lateness {b.total_lateness_days} -> "
            f"{a.total_lateness_days} days."
        )
    elif placements_changed == 0:
        label = "NO_EFFECT"
        reason = "No operation changed machine or time — the engine would produce the same schedule."
    else:
        label = "NEUTRAL"
        reason = (
            f"{placements_changed} placement(s) changed, but late orders ({b.late}) and total lateness "
            f"({b.total_lateness_days} days) are unchanged."
        )
    return {
        "label": label,
        "reason": reason,
        "rule": "1) fewer late committed orders, 2) less total lateness, 3) otherwise neutral / no effect",
    }
