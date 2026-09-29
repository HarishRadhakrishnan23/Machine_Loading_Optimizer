"""
shift_clock.py — the fixed, plant-wide shift clock-time convention (CLAUDE.md
"Shift clock-time convention"). No ERP view carries a shift's real time-of-day
— only its duration (WORKING_MINS) — so this mapping is a hardcoded constant,
not derived from Oracle.

    first   07:00 -> 15:30
    second  15:30 -> 23:30
    third   23:30 -> 07:00 (next calendar day)

Applies Monday-Saturday; Sunday's empty window (07:00 Sun -> 07:00 Mon) needs
no special-casing here — it falls out of AVAILABLE_MINS being 0 for that
window in the ERP data, exactly like any other closed shift.

This is the ONLY place `TimePoint` (the engine's internal day+shift+minute
clock) is converted to a real `datetime` — for writing START_TIMESTAMP/
END_TIMESTAMP to MCH_SCHEDULE_OUTPUT. Nothing upstream of this module needs
or should assume a real clock time exists.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from dispatch_timeline import TimePoint

SHIFT_START_TIME: dict[str, time] = {
    "first": time(7, 0),
    "second": time(15, 30),
    "third": time(23, 30),
}


def timepoint_to_datetime(tp: TimePoint) -> datetime:
    """
    Real wall-clock instant for `tp`, per the fixed shift convention above.
    A `TimePoint`'s `minute` is assumed to map linearly onto clock time from
    the shift's start — there's no finer-grained break/downtime data in any
    ERP source to do otherwise.
    """
    start = datetime.combine(tp.day, SHIFT_START_TIME[tp.shift])
    return start + timedelta(minutes=tp.minute)
