"""
models.py — Pydantic schemas for the TOV Machine Loading Optimizer.

These models are the *contract* between the layers:

    preprocess.py  ──(SchedulerInput)──▶  engine1_scheduler.py  ──(SchedulerResult)──▶  db.py / schedule_output

Everything here is provider- and DB-agnostic: preprocess.py builds these objects
out of raw Oracle DataFrames, and the CP-SAT engine consumes *only* these objects
(never a DataFrame). That keeps the solver testable with hand-built fixtures.

ALL time values are in MINUTES (project-wide rule). No unit conversion anywhere.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────
class Shift(str, Enum):
    """Shift names are ALWAYS normalized to lowercase on read (CLAUDE.md rule)."""

    FIRST = "first"
    SECOND = "second"
    THIRD = "third"


# Canonical chronological order of shifts within a single working day.
# Used by the batch-overflow rule: a batch that does not fit in shift N spills
# into the *same machine* in the next immediate shift; third → first of next day.
SHIFT_ORDER: tuple[Shift, ...] = (Shift.FIRST, Shift.SECOND, Shift.THIRD)


class RiskFlag(str, Enum):
    """Engine 2 classification (kept here so both engines share one vocabulary)."""

    SAFE = "SAFE"
    AT_RISK = "AT_RISK"
    BREACH = "BREACH"


# ─────────────────────────────────────────────────────────────────────────────
# Runtime configuration (mirror of backend/config.json)
# ─────────────────────────────────────────────────────────────────────────────
class Config(BaseModel):
    """Runtime parameters. Loaded from backend/config.json, never from Oracle."""

    batch_bonus_months: int = 2
    batch_bonus_value: float = 0.5
    downstream_queue_bonus_value: float = 0.3
    ageing_normalization_days: int = 180
    machine_priority_epsilon: float = 0.001
    risk_safe_threshold_days: int = 5
    engine2_time_limit_seconds: int = 10
    # Horizon sizing (CLAUDE.md "Horizon derivation"). Present in config.json;
    # without them here Pydantic silently drops the keys and horizon logic that
    # reads them raises AttributeError at runtime.
    scheduling_horizon_safety_factor: int = 2
    scheduling_horizon_buffer_days: int = 7
    # Development mode: limit WIP orders to top-N by urgency (for fast testing).
    # 0 = no limit (full dataset). Set to 100 for rapid iteration, 0 for production.
    dev_max_orders: int = 0
    # CP-SAT solver parallelization and time budget.
    solver_workers: int = 0  # 0 = use ALL CPU cores; set to a specific count to limit
    solver_time_limit_seconds: int = 600  # time budget for Engine 1 (production: 600-3600s OK)
    # Model C — soft-cost weights (see CLAUDE.md "Objective function").
    # setup_penalty_weight: mild objective cost per setup event — nudges the solver to
    # batch same-size work together (maximise utilisation) WITHOUT ever delaying a
    # delivery to save a setup. Kept small so tardiness always dominates. 0 disables it.
    setup_penalty_weight: float = 0.05


# ─────────────────────────────────────────────────────────────────────────────
# Capability matrix — one candidate machine for a task (from routing_master)
# ─────────────────────────────────────────────────────────────────────────────
class MachineCandidate(BaseModel):
    """
    A machine that *can* perform a task's operation for its ITEM_CATEGORY.

    Derived from routing_master rows keyed by (OPERATION, ITEM_CATEGORY).
    The CP-SAT model creates one OptionalIntervalVar per candidate and then
    AddExactlyOne over the candidates' `is_on` booleans.
    """

    machine_name: str
    setup_time: float = Field(ge=0, description="SETUP_TIME in minutes; applied only on ITEM_CATEGORY change")
    machine_priority: int = Field(ge=1, le=4, description="1 = most preferred … 4 = least preferred")


# ─────────────────────────────────────────────────────────────────────────────
# One schedulable unit = one (PRODUCTION_ORDER, OPERATION_NO) pair
# ─────────────────────────────────────────────────────────────────────────────
class SchedulableTask(BaseModel):
    """
    A single scheduling unit. One row per (PRODUCTION_ORDER, OPERATION_NO) that
    survived preprocessing (CT>0, routable, non-QA, balance_qty>0).

    `candidates` is the set of machines from routing_master capable of this
    operation for this ITEM_CATEGORY — the solver picks exactly one.
    """

    production_order: str
    operation_no: float = Field(description="OPERATION_NO ascending; may be a midpoint like 35")
    operation: str = Field(description="Task code, e.g. VB02, VB03, R002")
    item_category: str = Field(description="concatenation_key Size~Class~Design~MOC")
    batch_key: str = Field(
        description="SIZE_INCH~CLASS~DESIGN, computed directly from the raw WIP columns "
        "(NOT parsed from item_category — that string's segment order shifts when DESIGN "
        "is blank in the ERP data, which would silently pull MOC into the batch key). "
        "Orders sharing a batch_key are queued together on one machine per operation."
    )

    balance_qty: int = Field(gt=0, description="ORDERED − COMPLETED − REJECTED; the quantity to schedule")
    cycle_time: float = Field(gt=0, description="CYCLE_TIME per piece, in minutes")

    cdd: Optional[date] = Field(default=None, description="Committed Delivery Date (== PDD). NULL ⇒ safety stock")
    order_date: Optional[datetime] = Field(default=None, description="PRODUCTION_START_DATE_AND_TIME, for ageing")

    urgency_weight: float = Field(ge=0, description="pdd + ageing + batch_bonus + downstream_queue_bonus; 0 for safety stock")

    candidates: list[MachineCandidate] = Field(min_length=1)

    @property
    def total_work_minutes(self) -> float:
        """Raw machining time for the whole batch, excluding setup."""
        return self.balance_qty * self.cycle_time

    @property
    def is_safety_stock(self) -> bool:
        return self.cdd is None


# ─────────────────────────────────────────────────────────────────────────────
# Resolved capacity — one machine × shift × date slot
# ─────────────────────────────────────────────────────────────────────────────
class CapacitySlot(BaseModel):
    """
    A capacity bucket produced by resolve_capacity() (machine_daily override
    on top of machine_master baseline). available_mins == 0 ⇒ machine idle
    that slot (breakdown / maintenance / not staffed).
    """

    machine_name: str
    shift: Shift
    slot_date: date
    available_mins: float = Field(ge=0)

    @property
    def is_open(self) -> bool:
        return self.available_mins > 0


# ─────────────────────────────────────────────────────────────────────────────
# Engine 1 input bundle
# ─────────────────────────────────────────────────────────────────────────────
class SchedulerInput(BaseModel):
    """Everything the CP-SAT model needs. Built entirely by preprocess.py."""

    tasks: list[SchedulableTask]
    capacity: list[CapacitySlot]
    horizon_dates: list[date] = Field(description="Ordered scheduling dates the model may place work on")
    config: Config

    @field_validator("horizon_dates")
    @classmethod
    def _sorted_unique_dates(cls, v: list[date]) -> list[date]:
        return sorted(set(v))


# ─────────────────────────────────────────────────────────────────────────────
# Engine 1 output — mirrors the schedule_output Oracle table
# ─────────────────────────────────────────────────────────────────────────────
class ScheduleOutputRow(BaseModel):
    """
    One placed slice of work. A batch that overflows across shifts produces
    multiple rows (same PRODUCTION_ORDER/OPERATION_NO/machine, different shift/date).

    Columns map 1:1 to the schedule_output table.
    """

    production_order: str
    operation_no: float
    machine_name: str
    shift: Shift
    scheduled_date: date

    balance_qty: int = Field(description="Pieces placed in THIS row's slot")
    start_offset_min: int = Field(ge=0, description="Minutes from shift start")
    end_offset_min: int = Field(ge=0, description="Minutes from shift start")

    batch_key: str = Field(description="SIZE_INCH~CLASS~DESIGN — groups this row's order with "
                                        "others queued through the same operation on this machine")
    is_safety_stock: bool = Field(description="True when the order's CDD is NULL — UI should flag "
                                                "this distinctly so planners can judge whether to "
                                                "include it in a manual run")

    run_id: str
    generated_at: datetime


class SolveStatus(str, Enum):
    OPTIMAL = "OPTIMAL"
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    MODEL_INVALID = "MODEL_INVALID"
    UNKNOWN = "UNKNOWN"


class SchedulerResult(BaseModel):
    """What engine1_scheduler.solve() returns; db.py persists `assignments`."""

    run_id: str
    generated_at: datetime
    status: SolveStatus
    objective_value: Optional[float] = None
    assignments: list[ScheduleOutputRow] = Field(default_factory=list)

    # Per-order completion dates, keyed by PRODUCTION_ORDER, for Engine 2 diffing.
    # completion = max(scheduled_date) across the order's last routable operation.
    completion_dates: dict[str, date] = Field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return self.status in (SolveStatus.OPTIMAL, SolveStatus.FEASIBLE)


# ═════════════════════════════════════════════════════════════════════════════
# Engine 2 — Recommendation / Impact simulation
# ═════════════════════════════════════════════════════════════════════════════

# urgency_weight forced onto an elevated order so CP-SAT floats it to the top of
# the scheduling queue (CLAUDE.md Engine 2, step 2). Far above any organic weight.
ELEVATED_URGENCY_WEIGHT: float = 999_999.0


class SimulationRequest(BaseModel):
    """Payload for POST /priority/simulate — the order(s) the planner wants elevated."""

    orders: list[str] = Field(min_length=1, description="PRODUCTION_ORDERs to elevate")
    time_limit_seconds: Optional[float] = Field(
        default=None,
        description="Overrides config.engine2_time_limit_seconds for this run only",
    )


class SimResultRow(BaseModel):
    """
    One row of the sim_results Oracle table — the impact of the elevation on a
    single OTHER order (elevated orders themselves are excluded from impact rows).

    Columns map 1:1 to sim_results. `sim_id` is shared across every row of one
    simulation run (it identifies the run / groups the batch); `elevated_order`
    records which orders were elevated, comma-joined when more than one.
    """

    sim_id: str
    elevated_order: str
    order: str
    old_completion_date: Optional[date] = None
    new_completion_date: Optional[date] = None
    slip_days: Optional[int] = Field(default=None, description="new_completion − old_completion, in days")
    risk_flag: RiskFlag
    created_at: datetime


class RiskReport(BaseModel):
    """
    API-facing result of one simulation (returned by POST /priority/simulate).
    Wraps the persisted rows plus the top-5 most impacted orders and a summary.
    """

    sim_id: str
    created_at: datetime
    elevated_orders: list[str]
    status: SolveStatus

    impacts: list[SimResultRow] = Field(default_factory=list)
    top_impacted: list[SimResultRow] = Field(default_factory=list, description="Top-5 by slip_days")

    # Convenience counts for the Impact Analyser UI badges.
    safe_count: int = 0
    at_risk_count: int = 0
    breach_count: int = 0
