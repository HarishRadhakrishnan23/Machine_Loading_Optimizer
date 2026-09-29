"""
dispatch_planned_release.py — Engine 1 Model E: Planned-order release gating
(CLAUDE.md §E.1/§E.8).

A "Planned" order (ORDER_STATUS = 'Planned') cannot begin its first operation
before its foundry material physically arrives. Its earliest-start is exactly
`PRODUCTION_START_DATE_AND_TIME + planned_order_start_buffer_days` (config
default: +1 calendar day), first shift of that day onward — settled explicitly
during design discussion as a hard instant, not a fuzzy "near that day."

The "near that day" language in the original brief turned out to mean
something narrower: if every machine capable of the order's first operation is
fully busy on that exact day, the engine must never interrupt an
already-running batch to force the order in early — it simply waits for a
machine to free up at/after its earliest-start, same as any other order would.
That "never interrupt a running batch" behavior falls out for free from how
`dispatch_timeline.advance_clock` already works (a machine's next batch can
only start once its current one — plus cooling — has finished); this module's
only job is to compute the earliest-start instant and gate on it.

"Active" orders have no such gate — they are eligible immediately.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from dispatch_timeline import TimePoint

ORDER_STATUS_ACTIVE = "Active"
ORDER_STATUS_PLANNED = "Planned"


def compute_planned_earliest_start(production_start_date: date, buffer_days: int) -> TimePoint:
    """
    CLAUDE.md §E.1/§E.8 — the earliest instant a Planned order's first
    operation may begin: material-arrival date + buffer, first shift of that
    day onward. Call only for ORDER_STATUS == 'Planned'; Active orders have
    no earliest-start gate at all.
    """
    return TimePoint(production_start_date + timedelta(days=buffer_days), 0, 0.0)


def is_released(order_status: str, at: TimePoint, earliest_start: Optional[TimePoint] = None) -> bool:
    """
    True iff an order-operation may be considered for batching/placement at
    simulated instant `at`.

    - "Active": always released — `earliest_start` is ignored.
    - "Planned": released only once `at >= earliest_start` (inclusive — first
      shift of the release day onward). Requires `earliest_start` (build it
      with `compute_planned_earliest_start`, once, per Planned order).

    This is a point-in-time eligibility check only — it never causes an
    order to preempt or bump a machine's already-running batch. An order that
    isn't released yet simply isn't considered this round; it becomes
    eligible again on its own priority turn once `at` has advanced far enough.
    """
    if order_status == ORDER_STATUS_ACTIVE:
        return True
    if order_status != ORDER_STATUS_PLANNED:
        raise ValueError(f"Unknown ORDER_STATUS: {order_status!r}")
    if earliest_start is None:
        raise ValueError("Planned orders require earliest_start (see compute_planned_earliest_start)")
    return at >= earliest_start
