"""
dispatch_pool.py — Engine 1 Model E: the fixture/locator pool constraint
(CLAUDE.md §E.9), hard and plant-wide.

`MCH_FIXTURE_LOCATOR.QUANTITY` caps how many jobs may use a given fixture or
locator DEVICE_NAME at the same instant, across the whole plant — regardless
of machine availability. This module tracks that as reserved TimePoint
intervals per device and answers the two questions the dispatcher needs:

  - `has_capacity(device, quantity, start, end)` — would reserving [start, end)
    push concurrent usage of `device` above its QUANTITY at any instant?
  - `reserve(device, start, end)` — commit that reservation once accepted.

Fixtures and locators are both just "devices" here — the distinction in
CLAUDE.md §E.9 (a fixture is held for a whole run's start-to-end span; a
locator is held only for its own sub-batch inside that run, released before
the next locator mounts) is a matter of WHAT interval the caller reserves for
each, not something this module special-cases. Release is implicit: a
reservation simply has an end time and stops counting once the query window
no longer overlaps it — there is no separate "release" call, matching CLAUDE.md
§E.9's own framing ("release and re-mount are instantaneous").

This module intentionally does NOT search for "the next time a slot is free" —
CLAUDE.md's own auditability language for this constraint is a check
("no more than QUANTITY rows' timestamp ranges overlap at any instant"), not a
search. `earliest_release_hint` gives the dispatcher's retry loop a starting
point to re-check from, not a guaranteed answer — the loop must still re-run
`has_capacity` after advancing, since other reservations may still conflict.

Compound devices (real ERP data discovery, confirmed during E-6 validation
against live MCH_ITEMWISE_FIXTURE_LOCATOR data): a LOCATOR value can be TWO
device names joined by "+" (e.g. "AT18/212-F02+AT18/212-LC2") — never seen on
FIXTURE. This means the operation needs BOTH physical devices held
simultaneously, not one device with an unusual name — each component is a
real, separate DEVICE_NAME in MCH_FIXTURE_LOCATOR's own inventory, with its
own QUANTITY cap. `parse_devices` + the `*_multi` methods below handle this
as a thin layer on top of the single-device primitives above: a plain device
name is just a one-element list. No change to the timing/duration formula —
this is purely about which device(s) a reservation checks/holds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from dispatch_timeline import TimePoint

Interval = tuple[TimePoint, TimePoint]  # half-open: [start, end)


@dataclass
class DevicePool:
    """
    quantities: DEVICE_NAME -> QUANTITY, straight from MCH_FIXTURE_LOCATOR.
    Devices not present in `quantities` have no reservations tracked and
    `has_capacity` raises — a device must be known before it can be reserved.
    """

    quantities: dict[str, int]
    _held: dict[str, list[Interval]] = field(default_factory=dict)

    def _require_known(self, device: str) -> int:
        if device not in self.quantities:
            raise KeyError(f"Unknown device (not in MCH_FIXTURE_LOCATOR inventory): {device!r}")
        return self.quantities[device]

    def max_concurrent_overlap(self, device: str, start: TimePoint, end: TimePoint) -> int:
        """
        Peak number of ALREADY-HELD reservations for `device` active at any
        single instant within [start, end) — a sweep-line over just the
        reservations that overlap the query window. Does not count the
        candidate interval itself (see `has_capacity`).
        """
        self._require_known(device)
        overlapping = [iv for iv in self._held.get(device, []) if iv[0] < end and start < iv[1]]
        if not overlapping:
            return 0

        # (time, delta) events; releases (-1) sort before acquires (+1) at an
        # identical instant, so a reservation ending exactly when another
        # begins is correctly treated as non-overlapping (half-open intervals).
        events: list[tuple[TimePoint, int]] = []
        for s, e in overlapping:
            events.append((max(s, start), 1))
            events.append((min(e, end), -1))
        events.sort(key=lambda ev: (ev[0], ev[1]))

        running = peak = 0
        for _, delta in events:
            running += delta
            peak = max(peak, running)
        return peak

    def has_capacity(self, device: str, start: TimePoint, end: TimePoint) -> bool:
        """True iff reserving [start, end) for `device` keeps concurrent usage <= QUANTITY throughout."""
        quantity = self._require_known(device)
        # +1 for the candidate reservation itself, which isn't in _held yet.
        return self.max_concurrent_overlap(device, start, end) + 1 <= quantity

    def reserve(self, device: str, start: TimePoint, end: TimePoint) -> None:
        """
        Commit [start, end) for `device`. Caller must have already confirmed
        `has_capacity` — this method does not re-check (keeps the accept/commit
        steps separately observable for the dispatcher's retry loop).
        """
        self._require_known(device)
        if not (start < end):
            raise ValueError(f"Invalid reservation interval for {device!r}: start {start} must be < end {end}")
        self._held.setdefault(device, []).append((start, end))

    def clone(self) -> "DevicePool":
        """
        Cheap fork of this pool's reservation state — used by the safety-stock
        full speculative check (CLAUDE.md §E.10) to fork simulation state
        before running the "append" and "defer" continuations independently.
        `TimePoint` and the interval tuples are immutable, so this only needs
        to copy the dict-of-lists structure, not deep-copy the values inside
        it; `quantities` is never mutated after construction, so it's shared,
        not copied.
        """
        cloned = DevicePool(quantities=self.quantities)
        cloned._held = {device: list(intervals) for device, intervals in self._held.items()}
        return cloned

    def earliest_release_hint(self, device: str, not_before: TimePoint) -> Optional[TimePoint]:
        """
        Earliest end-time, at or after `not_before`, of any reservation
        currently held for `device` — a hint for where a blocked dispatcher
        should resume its search, NOT a guarantee that capacity is free there
        (other overlapping reservations may still block it; re-check
        `has_capacity`). Returns None if there is nothing held at/after
        `not_before` — the device is free right away.
        """
        self._require_known(device)
        ends = sorted(e for _, e in self._held.get(device, []) if e > not_before)
        return ends[0] if ends else None

    # ── Compound-device layer (see module docstring) ──────────────────────
    def has_capacity_multi(self, device_name: str, start: TimePoint, end: TimePoint) -> bool:
        """`has_capacity`, but `device_name` may be a "+"-joined compound —
        true only if EVERY component device has capacity for [start, end)."""
        return all(self.has_capacity(d, start, end) for d in parse_devices(device_name))

    def reserve_multi(self, device_name: str, start: TimePoint, end: TimePoint) -> None:
        """`reserve`, but commits [start, end) against EVERY component device
        of a "+"-joined compound name (both held together, per CLAUDE.md's
        confirmed real-data convention)."""
        for d in parse_devices(device_name):
            self.reserve(d, start, end)

    def earliest_release_hint_multi(self, device_name: str, not_before: TimePoint) -> Optional[TimePoint]:
        """
        `earliest_release_hint`, but for a possibly-compound `device_name`:
        the whole compound isn't free until ALL of its components are, so
        this is the LATEST of the components' individual hints (a component
        already free contributes nothing — it isn't what's blocking). None
        iff every component is free right now.
        """
        hints = [h for h in (self.earliest_release_hint(d, not_before) for d in parse_devices(device_name)) if h is not None]
        return max(hints) if hints else None


def parse_devices(device_name: str) -> list[str]:
    """
    Split a possibly-compound device name into its individual DEVICE_NAMEs.
    Real ERP conventions (confirmed during E-6 validation against live data):

      - A LOCATOR value can join two physical devices with "+" (never seen
        on FIXTURE), meaning the operation needs BOTH held simultaneously.
      - EITHER field (confirmed on both FIXTURE and LOCATOR — e.g. VB03 Weld
        Overlay rows carry FIXTURE=LOCATOR="NA") can be the literal string
        "NA", meaning no physical device is needed at all for that slot —
        filtered out here, never treated as a device name to look up.

    A plain name returns a single-element list, "NA" (alone or inside a
    compound) contributes nothing, so every caller can treat this uniformly
    — including the "no device needed at all" case, which naturally becomes
    an empty list (has_capacity_multi vacuously True, reserve_multi a no-op,
    earliest_release_hint_multi None).
    """
    return [d.strip() for d in device_name.split("+") if d.strip().upper() != "NA"]
