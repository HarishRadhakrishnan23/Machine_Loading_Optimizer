"""
dispatch_consolidation_window.py — Engine 1 Model E: the batching consolidation
window (CLAUDE.md §E.6) — HARD RULE, no override.

An order is eligible to be pulled into an already-started fixture run only if
its CDD is within `batch_consolidation_window_days` of today (run date). This
is now a hard cutoff, not a soft preference with an escape hatch: an order
beyond the window is simply never eligible for consolidation, full stop —
there is no "unless it wouldn't delay anything" exception any more. Safety-
stock orders (CDD = NULL) are always outside the window and can never pull
others forward.

`window_days` is read fresh from config on every call (the caller passes it
through), so changing `batch_consolidation_window_days` in config.json (e.g.
60 -> 90) takes effect immediately with no code change — the hard cutoff
lives entirely in that one number.
"""

from __future__ import annotations

from datetime import date
from typing import Optional


def is_within_window(cdd: Optional[date], today: date, window_days: int) -> bool:
    """
    True iff `cdd` is not null and falls within `window_days` of `today`.
    Safety stock (cdd is None) is always outside the window.
    """
    if cdd is None:
        return False
    return (cdd - today).days <= window_days


def is_consolidation_eligible(cdd: Optional[date], today: date, window_days: int) -> bool:
    """
    CLAUDE.md §E.6 — the hard eligibility gate for pulling an order into an
    already-started fixture run. Exactly `is_within_window`; kept as its own
    named function so callers read "consolidation eligibility" at the call
    site rather than reasoning about window math directly, and so a future
    rule change here has one place to land.
    """
    return is_within_window(cdd, today, window_days)
