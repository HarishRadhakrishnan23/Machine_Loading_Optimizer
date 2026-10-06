"""
dispatch_scope.py — Engine 1 Model E: scheduling scope filters and the REMARK
taxonomy (CLAUDE.md §E.12), plus the fixture/locator lookup fallback (§E.11).

Every eligible order-operation from MCH_WIP always produces exactly one row in
MCH_SCHEDULE_OUTPUT — scheduled or not. This module is the single source of
truth for classifying one raw WIP operation row into an outcome that decides
both what REMARK it gets and whether it proceeds any further:

  - Every EXCLUDED_* outcome: the row is written with WORK_CENTER/SHIFT/
    SCHEDULED_DATE/FIXTURE_ID/LOCATOR_ID all NULL, REMARK explains why, and it
    never enters batching, machine selection, the timeline, or the pool.
  - SCHEDULED_NO_FIXTURE: proceeds to machine selection via plain routing
    (Σ CYCLE_TIME only — no fixture/locator effect, no fixture-run membership,
    no pool involvement — CLAUDE.md §E.11), but REMARK still records the
    caveat that no fixture/locator match was found. FIXTURE_ID/LOCATOR_ID
    stay NULL even though the row IS scheduled.
  - SCHEDULED: proceeds normally through the full fixture/locator machinery.
    REMARK is NULL.

Precedence bridging: CLAUDE.md §E.12 states an op with no routing entry is
"the only case still bridged transparently in precedence" — connecting an
order's previous schedulable op directly to its next one. In practice this
generalizes to every EXCLUDED_* outcome, not just the routing-gap one: NONE of
them produce a START_TIMESTAMP/END_TIMESTAMP for precedence to chain from, so
an order's downstream operations must always key off the nearest operation
that actually got scheduled, regardless of why the ones in between were
excluded. `is_excluded()` doubles as "is bridged" for exactly this reason —
this is a deliberate generalization of the literal spec text, flagged here
because CLAUDE.md's prose only calls out the routing-gap case by name.

One outcome not named verbatim in CLAUDE.md's REMARK list is also added here:
EXCLUDED_NO_MACHINE_FOR_COMBO — the TASK code IS routable in general (some
routing rows exist for it), but no MCH_MACHINE_PRIORITY row matches this
order's exact SIZE~CLASS~MOC~DESIGN. Model C silently dropped this case with
no REMARK at all (see preprocess.py's old comment: "Routable per the TASK
filter but no machine matches this exact ITEM_CATEGORY — cannot schedule;
skip"); under Model E's "never silently drop a row" mandate that can no
longer be silent, so it gets its own outcome and REMARK, distinct from "no
routing entry for this TASK at all" — flagging this as an addition beyond the
literal CLAUDE.md text, for the same reason.

A second such addition, discovered during E-6 validation against live Oracle
data: EXCLUDED_UNKNOWN_FIXTURE_DEVICE. MCH_ITEMWISE_FIXTURE_LOCATOR can
reference a FIXTURE/LOCATOR DEVICE_NAME that has no matching row in
MCH_FIXTURE_LOCATOR's physical inventory at all (as opposed to the literal
value "NA", which means "no device needed" and is filtered out upstream by
dispatch_pool.parse_devices before this check ever runs). Per explicit
confirmation, this fails loudly for just that operation — REMARK explains
it, never scheduled — rather than guessing a device quantity or silently
dropping the pool constraint for an unregistered physical device.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

QA_WORK_CENTER_TOKEN = "QAINSP"
EXCLUDED_CLASS = "PN10"


class ScopeOutcome(str, Enum):
    EXCLUDED_CT_ZERO = "excluded_ct_zero"
    EXCLUDED_BALANCE_ZERO = "excluded_balance_zero"
    EXCLUDED_QAINSP = "excluded_qainsp"
    EXCLUDED_PN10 = "excluded_pn10"
    EXCLUDED_NO_ROUTING = "excluded_no_routing"
    EXCLUDED_NO_MACHINE_FOR_COMBO = "excluded_no_machine_for_combo"  # see module docstring
    EXCLUDED_UNKNOWN_FIXTURE_DEVICE = "excluded_unknown_fixture_device"  # see module docstring
    SCHEDULED_NO_FIXTURE = "scheduled_no_fixture"
    SCHEDULED = "scheduled"


REMARKS: dict[ScopeOutcome, Optional[str]] = {
    # Plain ASCII hyphens only, never an em dash (U+2014) or other non-Latin-1
    # character — the live Oracle schema's REMARK column sits under a
    # WE8ISO8859P1 (Latin-1) database characterset, which cannot represent
    # U+2014 at all; python-oracledb silently substitutes an unmappable
    # character with a corrupted byte on insert rather than raising, so this
    # was a real, previously undetected data-corruption bug (every REMARK
    # using an em dash came back as "...0x{garbage}..." on read).
    ScopeOutcome.EXCLUDED_CT_ZERO: "CT = 0 - excluded",
    ScopeOutcome.EXCLUDED_BALANCE_ZERO: "Balance Qty <= 0 - fully accounted for",
    ScopeOutcome.EXCLUDED_QAINSP: "QAINSP - manual QA gate, excluded from scheduling",
    ScopeOutcome.EXCLUDED_PN10: "CLASS = PN10 - excluded, no routing/fixture setup yet",
    ScopeOutcome.EXCLUDED_NO_ROUTING: "No routing entry for this TASK",
    ScopeOutcome.EXCLUDED_NO_MACHINE_FOR_COMBO: (
        "TASK has routing entries, but none match this exact SIZE~CLASS~MOC~DESIGN"
    ),
    ScopeOutcome.EXCLUDED_UNKNOWN_FIXTURE_DEVICE: (
        "Fixture/locator device not in inventory - cannot schedule"
    ),
    ScopeOutcome.SCHEDULED_NO_FIXTURE: "No fixture/locator match - scheduled via plain routing",
    ScopeOutcome.SCHEDULED: None,
}

_EXCLUDED_OUTCOMES = frozenset(
    {
        ScopeOutcome.EXCLUDED_CT_ZERO,
        ScopeOutcome.EXCLUDED_BALANCE_ZERO,
        ScopeOutcome.EXCLUDED_QAINSP,
        ScopeOutcome.EXCLUDED_PN10,
        ScopeOutcome.EXCLUDED_NO_ROUTING,
        ScopeOutcome.EXCLUDED_NO_MACHINE_FOR_COMBO,
        ScopeOutcome.EXCLUDED_UNKNOWN_FIXTURE_DEVICE,
    }
)


def remark_for(outcome: ScopeOutcome) -> Optional[str]:
    return REMARKS[outcome]


def is_excluded(outcome: ScopeOutcome) -> bool:
    """
    True for every outcome that never enters batching/machine selection/
    timeline/pool — and (see module docstring) is therefore also the set of
    outcomes an order's own precedence chain must bridge transparently over.
    """
    return outcome in _EXCLUDED_OUTCOMES


@dataclass(frozen=True)
class ScopeCheckInputs:
    """Everything needed to classify one raw MCH_WIP operation row (§E.12)."""

    cycle_time: float
    balance_qty: int  # QUANTITY_ORDERED - QUANTITY_COMPLETED - QUANTITY_REJECTED(nullable->0), precomputed by caller
    work_center: Optional[str]  # MCH_WIP's own WORK_CENTER (informational/ERP-assigned)
    class_val: str
    has_routing_entry: bool  # this order's TASK appears anywhere in MCH_MACHINE_PRIORITY
    has_machine_for_combo: bool  # >=1 MCH_MACHINE_PRIORITY row matches this exact SIZE~CLASS~MOC~DESIGN + TASK
    has_fixture_locator_match: bool  # (SIZE,CLASS,MOC,DESIGN,TASK) present in MCH_ITEMWISE_FIXTURE_LOCATOR for >=1 candidate machine
    fixture_locator_devices_known: bool = True  # only consulted when has_fixture_locator_match is True — see EXCLUDED_UNKNOWN_FIXTURE_DEVICE


def classify_operation(inputs: ScopeCheckInputs) -> ScopeOutcome:
    """
    CLAUDE.md §E.12, evaluated in the exact order its prose lists the gating
    conditions (first match wins — a row can fail more than one, the earliest
    check in this order determines its REMARK). Only once every gating
    condition passes does §E.11's fixture/locator lookup (optional, not
    gating) decide SCHEDULED vs SCHEDULED_NO_FIXTURE — and, when a match
    exists, whether its referenced devices are actually known to the
    physical inventory (EXCLUDED_UNKNOWN_FIXTURE_DEVICE if not).
    """
    if inputs.cycle_time <= 0:
        return ScopeOutcome.EXCLUDED_CT_ZERO
    if inputs.balance_qty <= 0:
        return ScopeOutcome.EXCLUDED_BALANCE_ZERO
    if inputs.work_center and QA_WORK_CENTER_TOKEN in inputs.work_center.upper():
        return ScopeOutcome.EXCLUDED_QAINSP
    if inputs.class_val == EXCLUDED_CLASS:
        return ScopeOutcome.EXCLUDED_PN10
    if not inputs.has_routing_entry:
        return ScopeOutcome.EXCLUDED_NO_ROUTING
    if not inputs.has_machine_for_combo:
        return ScopeOutcome.EXCLUDED_NO_MACHINE_FOR_COMBO
    if not inputs.has_fixture_locator_match:
        return ScopeOutcome.SCHEDULED_NO_FIXTURE
    if not inputs.fixture_locator_devices_known:
        return ScopeOutcome.EXCLUDED_UNKNOWN_FIXTURE_DEVICE
    return ScopeOutcome.SCHEDULED
