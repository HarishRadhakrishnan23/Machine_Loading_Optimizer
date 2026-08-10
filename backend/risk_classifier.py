"""
risk_classifier.py — Engine 2 impact scoring.

Compares the BASELINE schedule against the SIMULATED schedule (after one or more
orders were elevated) and produces the risk report:

    slip_days = new_completion_date − old_completion_date   (per order, in days)
    slack     = CDD − new_completion_date                   (per order, in days)

    SAFE:    slack >  risk_safe_threshold_days
    AT_RISK: 0 ≤ slack ≤ risk_safe_threshold_days
    BREACH:  slack <  0

Elevated orders are excluded from the impact rows (they are the *cause*, not an
impacted party — CLAUDE.md Engine 2 step 4: "for every OTHER order").

Pure functions only — no DB, no solver. engine2_recommender.py feeds it two
completion-date maps and it returns validated SimResultRow / RiskReport objects.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from models import (
    Config,
    RiskFlag,
    RiskReport,
    SimResultRow,
    SolveStatus,
)

# How many orders the report surfaces as "most impacted".
TOP_IMPACTED_N = 5


def classify_risk(
    new_completion_date: Optional[date],
    cdd: Optional[date],
    threshold_days: int,
) -> RiskFlag:
    """
    Classify one order's risk from its slack against the CDD.

    slack = CDD − new_completion_date (in days).
      • CDD is NULL (safety stock)  → SAFE   (no deadline to breach)
      • not scheduled (new is None) → BREACH (order fell out of the schedule)
    """
    if cdd is None:
        return RiskFlag.SAFE
    if new_completion_date is None:
        return RiskFlag.BREACH

    slack = (cdd - new_completion_date).days
    if slack < 0:
        return RiskFlag.BREACH
    if slack <= threshold_days:
        return RiskFlag.AT_RISK
    return RiskFlag.SAFE


def _slip_days(old: Optional[date], new: Optional[date]) -> Optional[int]:
    """new − old in days; None if either side is missing (cannot diff)."""
    if old is None or new is None:
        return None
    return (new - old).days


def build_risk_report(
    baseline_completion: dict[str, date],
    new_completion: dict[str, date],
    cdd_map: dict[str, Optional[date]],
    elevated_orders: list[str],
    status: SolveStatus,
    config: Config,
    now: Optional[datetime] = None,
) -> RiskReport:
    """
    Assemble the full RiskReport from baseline vs simulated completion dates.

    Parameters
    ----------
    baseline_completion : PRODUCTION_ORDER → old_completion_date (pre-elevation).
    new_completion      : PRODUCTION_ORDER → new_completion_date (post-elevation).
    cdd_map             : PRODUCTION_ORDER → CDD (None ⇒ safety stock).
    elevated_orders     : orders that were elevated (excluded from impact rows).
    status              : the simulated solve's status (surfaced to the UI).
    config              : provides risk_safe_threshold_days.
    now                 : timestamp injected for deterministic tests.
    """
    now = now or datetime.now()
    sim_id = uuid.uuid4().hex
    elevated_set = set(elevated_orders)
    elevated_label = ",".join(sorted(elevated_set))

    # Every order that appears in either schedule and was not itself elevated.
    all_orders = (set(baseline_completion) | set(new_completion)) - elevated_set

    rows: list[SimResultRow] = []
    for order in sorted(all_orders):
        old = baseline_completion.get(order)
        new = new_completion.get(order)
        rows.append(
            SimResultRow(
                sim_id=sim_id,
                elevated_order=elevated_label,
                order=order,
                old_completion_date=old,
                new_completion_date=new,
                slip_days=_slip_days(old, new),
                risk_flag=classify_risk(new, cdd_map.get(order), config.risk_safe_threshold_days),
                created_at=now,
            )
        )

    # Top-5 most impacted = largest positive slip. Rows with unknown slip sort last.
    def _slip_key(r: SimResultRow) -> float:
        return r.slip_days if r.slip_days is not None else float("-inf")

    top = sorted(rows, key=_slip_key, reverse=True)[:TOP_IMPACTED_N]

    return RiskReport(
        sim_id=sim_id,
        created_at=now,
        elevated_orders=sorted(elevated_set),
        status=status,
        impacts=rows,
        top_impacted=top,
        safe_count=sum(1 for r in rows if r.risk_flag is RiskFlag.SAFE),
        at_risk_count=sum(1 for r in rows if r.risk_flag is RiskFlag.AT_RISK),
        breach_count=sum(1 for r in rows if r.risk_flag is RiskFlag.BREACH),
    )
