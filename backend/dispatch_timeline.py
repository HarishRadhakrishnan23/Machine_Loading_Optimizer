"""
dispatch_timeline.py — Engine 1 Model E: the continuous machine timeline
(CLAUDE.md §E.7).

Time is continuous wall-clock minutes; there is no discrete shift-slot lattice
and no per-machine capacity cap (CLAUDE.md is explicit: AVAILABLE_MINS is a
rate/window, never a budget). What this module provides is the pause/resume
mechanics: machining (and cooling) only progresses while a shift is open,
pauses the instant that shift's available minutes are exhausted, and resumes
at the machine's next OPEN shift — skipping any closed shift entirely
(AVAILABLE_MINS = 0, e.g. breakdown/maintenance from
MCH_MACHINE_AVAILABILITY_BY_DATE), as if it took no time at all. A missing
override row for a machine+date+shift means fully available at that machine's
MCH_MACHINE_AVAILABILITY baseline (CLAUDE.md: "every machine is always
considered") — that merge is the caller's job (see `AvailabilityFn` below),
not this module's.

Cooling (CLAUDE.md's `cooling_minutes`, default 20) is spent through the exact
same pause/resume mechanics as machining time — it is "the machine is
occupied," and CLAUDE.md is explicit that it may fall in the same shift, the
next shift, or the next working day. The only difference between machining and
cooling is that one produces output; this module has no opinion on that, and
exposes one `advance_clock` primitive for both.

No Oracle/pandas dependency — availability is injected as a plain callable so
this module is unit-testable with hand-built fixtures; the caller wires in a
real resolved-capacity lookup (baseline + daily override) built from the ERP
views.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

# Fixed chronological order of shifts within one working day (unchanged from
# Model C/D — CLAUDE.md: "SHIFT_ORDER = [first, second, third]").
SHIFT_ORDER: tuple[str, ...] = ("first", "second", "third")

# (machine, calendar date, shift name) -> AVAILABLE_MINS for that slot. The
# caller resolves MCH_MACHINE_AVAILABILITY_BY_DATE over MCH_MACHINE_AVAILABILITY
# before handing this in — this module never sees Oracle.
AvailabilityFn = Callable[[str, date, str], float]


@dataclass(frozen=True, order=True)
class TimePoint:
    """
    A continuous-time instant: (calendar date, shift, minutes into that shift).
    Directly orderable — (day, shift_index, minute) sorts exactly like real
    chronological time, because shifts always run in SHIFT_ORDER and never
    overlap.
    """

    day: date
    shift_index: int  # 0=first, 1=second, 2=third — index into SHIFT_ORDER
    minute: float = 0.0  # elapsed minutes since this shift started

    @property
    def shift(self) -> str:
        return SHIFT_ORDER[self.shift_index]


def _next_shift(day: date, shift_index: int) -> tuple[date, int]:
    """The next (day, shift_index) after this one, wrapping third -> next day's first."""
    if shift_index < len(SHIFT_ORDER) - 1:
        return day, shift_index + 1
    return day + timedelta(days=1), 0


# A machine genuinely closed on every shift, forever (e.g. a routing entry
# with zero rows of its own in MCH_MACHINE_AVAILABILITY/_BY_DATE — a real,
# confirmed live-data gap, not hypothetical) has no open instant to find, and
# without a bound the search below runs one day at a time without limit until
# Python's own `date` arithmetic overflows deep inside `_next_shift`, surfacing
# as an opaque "date value out of range" 500 with no indication which machine
# or order caused it. Callers are expected to filter out machines with zero
# availability data before they ever reach here (see dispatch_orders.
# build_order_chains's `known_machines` / EXCLUDED_NO_AVAILABILITY_DATA) — this
# is only the last-resort net for a gap that check didn't anticipate, turning
# the crash into one clear, diagnosable error instead.
_MAX_SHIFT_SEARCH_DAYS = 3650  # ~10 years — far beyond any real schedule horizon


def next_open_instant(point: TimePoint, machine: str, available: AvailabilityFn) -> TimePoint:
    """
    The next instant at/after `point` where `machine`'s shift is actually open
    (AVAILABLE_MINS > 0 and `point` isn't already past its end). If `point` is
    already inside an open shift with room left, returns it unchanged. Closed
    shifts are skipped as if they took zero time — never counted as elapsed
    duration.
    """
    day, shift_index, minute = point.day, point.shift_index, point.minute
    for _ in range(_MAX_SHIFT_SEARCH_DAYS * len(SHIFT_ORDER)):
        cap = available(machine, day, SHIFT_ORDER[shift_index])
        if cap > 0 and minute < cap:
            return TimePoint(day, shift_index, minute)
        day, shift_index = _next_shift(day, shift_index)
        minute = 0.0
    raise RuntimeError(
        f"Machine {machine!r} has no open shift within {_MAX_SHIFT_SEARCH_DAYS} days of {point!r} — "
        "it likely has zero rows in MCH_MACHINE_AVAILABILITY/_BY_DATE and should have been excluded "
        "upstream (see dispatch_orders.build_order_chains's known_machines parameter)."
    )


def advance_clock(point: TimePoint, minutes: float, machine: str, available: AvailabilityFn) -> TimePoint:
    """
    CLAUDE.md §E.7 — spend `minutes` of continuous time on `machine`, starting
    at `point`, pausing at the end of each shift and resuming at the machine's
    next open shift (skipping closed ones). Used identically for machining
    time and cooling time.
    """
    if minutes < 0:
        raise ValueError("minutes must be >= 0")

    cursor = next_open_instant(point, machine, available)
    remaining = minutes

    while remaining > 0:
        cap = available(machine, cursor.day, SHIFT_ORDER[cursor.shift_index])
        left_in_shift = cap - cursor.minute
        if remaining <= left_in_shift:
            return TimePoint(cursor.day, cursor.shift_index, cursor.minute + remaining)
        remaining -= left_in_shift
        day, shift_index = _next_shift(cursor.day, cursor.shift_index)
        cursor = next_open_instant(TimePoint(day, shift_index, 0.0), machine, available)

    return cursor


def start_of_day(day: date) -> TimePoint:
    """First shift, minute 0, of `day` — the earliest instant that day could offer work."""
    return TimePoint(day, 0, 0.0)
