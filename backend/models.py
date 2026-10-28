"""
models.py — Pydantic schemas for the TOV Machine Loading Optimizer.

API-facing schemas only: the runtime Config (backend/config.json), the Engine 1 /schedule/* responses,
and the Priority Shuffler requests. (The Model C CP-SAT schemas — SchedulerInput, SchedulerResult, ... —
were removed in Phase 3 together with the CP-SAT engine.)

ALL time values are in MINUTES (project-wide rule). No unit conversion anywhere.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ─────────────────────────────────────────────────────────────────────────────
# Runtime configuration (mirror of backend/config.json) — Model E shape
# (CLAUDE.md "Configuration — backend/config.json (Model E shape)")
# ─────────────────────────────────────────────────────────────────────────────
class Config(BaseModel):
    """Runtime parameters. Loaded from backend/config.json, never from Oracle.

    `extra="allow"`: a key present in config.json but not declared below is KEPT
    (round-trips through GET/PUT /config) instead of being silently dropped —
    pydantic's default ignores unknown keys, which once made `PUT /config`
    erase `fixture_required_tasks` from the file and turned the §E.15 policy
    off on the API path.
    """

    model_config = ConfigDict(extra="allow")

    batch_consolidation_window_days: int = 60  # §E.6 — hard rule, no override
    cooling_minutes: int = 20  # §E.7
    heavy_operations: list[str] = ["VB03", "VB04", "VB05", "VB06"]  # §E.10
    # §E.15: TASK codes whose FNL-master row is mandatory — a missing row is a
    # hard exclusion (EXCLUDED_NO_FNL_MASTER_ENTRY), not a plain-routing fallback.
    fixture_required_tasks: list[str] = ["VB02", "VB03", "VB04", "VB05", "VB06", "VB07"]
    protect_committed_over_safety: bool = True  # must always be True
    planned_order_start_buffer_days: int = 1  # §E.1/§E.8
    risk_safe_threshold_days: int = 5  # Engine 2 SAFE/AT_RISK/BREACH threshold
    dev_max_orders: int = 0  # dev knob; 0 = full dataset (production)
    # Soft-launch extension on top of §E.6's hard window (default off — zero
    # behavior change unless explicitly enabled): a committed order just past
    # the hard window may ride an existing in-window run's already-mounted
    # fixture/locator for free, but ONLY when the full speculative check
    # (same mechanism as §E.10) confirms it delays no committed order
    # anywhere. See dispatch_consolidation_window.is_soft_eligible /
    # dispatch_orchestrator._decide_soft_merge.
    allow_soft_consolidation_beyond_window: bool = False
    soft_consolidation_max_extra_days: int = 90  # bounds how far past the hard window a candidate may still be considered


# ─────────────────────────────────────────────────────────────────────────────
# Engine 1 (Model E) — /schedule/generate response
# ─────────────────────────────────────────────────────────────────────────────
class ScheduleGenerateResponse(BaseModel):
    """What POST /schedule/generate returns — model_f_pipeline.ScheduleGenerateResult,
    serialized. Replaces the old CP-SAT-era SchedulerResult (status/objective_value
    don't apply to a deterministic dispatch simulator — see CLAUDE.md §E.13)."""

    run_id: str
    rows_written: int
    scheduled_count: int
    scheduled_no_fixture_count: int
    excluded_count: int


class FreezeScheduleResponse(BaseModel):
    """What POST /schedule/freeze returns — a copy-forward of the current
    MCH_SCHEDULE_OUTPUT into MCH_SCHEDULE_OUTPUT_ARCHIVE (append-only history,
    never touched by /schedule/generate's delete-then-insert)."""

    run_id: Optional[str]
    rows_archived: int


class CompletedMaterialsResponse(BaseModel):
    """GET /materials/completed?start=&end= — your manager's ask: how many
    materials complete in a date window, counted from live MCH_SCHEDULE_OUTPUT's
    ORDER_COMPLETION_DATE (never the archive)."""

    start: date
    end: date
    order_count: int
    total_quantity: int


class ArchivedRunSummary(BaseModel):
    run_id: str
    generated_at: datetime
    row_count: int


class ArchivedRunsResponse(BaseModel):
    runs: list[ArchivedRunSummary]


class RunCompareOrderDiff(BaseModel):
    """One changed order's completion-date delta between two archived runs."""

    production_order: str
    old_completion_date: Optional[date]
    new_completion_date: Optional[date]
    delta_days: Optional[int]


class RunCompareOrder(BaseModel):
    """One order that's only present on one side of a run comparison (added/removed)."""

    production_order: str
    completion_date: Optional[date]


class RunCompareResponse(BaseModel):
    """GET /schedule/compare/runs?run_a=&run_b= — per-order diff between two
    frozen MCH_SCHEDULE_OUTPUT_ARCHIVE snapshots (run_b="current" means the
    live MCH_SCHEDULE_OUTPUT instead of a second archived run)."""

    run_a: str
    run_b: str
    added: list[RunCompareOrder]  # present in run_b only, with run_b's completion date
    removed: list[RunCompareOrder]  # present in run_a only, with run_a's completion date
    changed: list[RunCompareOrderDiff]  # present in both, completion date differs
    unchanged_count: int


class ActualOrderStatus(BaseModel):
    """One archived-run order's real-world outcome as of today's live MCH_WIP."""

    production_order: str
    planned_completion_date: Optional[date]
    status: str  # "completed" | "in_progress"
    current_operation_no: Optional[float] = None
    current_task: Optional[str] = None
    current_balance_qty: Optional[int] = None


class ActualCompareResponse(BaseModel):
    """GET /schedule/compare/actual?run_id= — an archived plan vs. what live
    MCH_WIP shows today. An order from the archived run absent from live WIP
    is treated as completed (CLAUDE.md Run Comparison note); one still present
    reports its current operation/balance as real-world progress."""

    run_id: str
    as_of: date
    completed_count: int
    in_progress_count: int
    orders: list[ActualOrderStatus]


# ─────────────────────────────────────────────────────────────────────────────
# Priority Shuffler — MCH_MACHINE_FNL_MASTER edits (fnl_master_admin.py)
# ─────────────────────────────────────────────────────────────────────────────
class FnlComboKey(BaseModel):
    """One SIZE~CLASS~MOC~DESIGN + TASK combo. MOC/DESIGN may legitimately be blank."""

    size_inch: str
    class_val: str = Field(alias="class")
    moc: str = ""
    design: str = ""
    task: str

    model_config = {"populate_by_name": True}


class FnlPriorityItem(BaseModel):
    work_center: str
    priority: int = Field(ge=1, description="New priority (1 = most preferred)")
    expected_priority: Optional[int] = Field(
        default=None, description="Priority the page loaded with - rejects the edit if Oracle changed since"
    )


class FnlPriorityUpdateRequest(FnlComboKey):
    items: list[FnlPriorityItem] = Field(min_length=1, description="EVERY machine of the combo, once each")
    changed_by: Optional[str] = None


class FnlAddMachineRequest(FnlComboKey):
    work_center: str
    priority: int = Field(ge=1)
    fixture_change_time: Optional[float] = Field(default=None, ge=0)
    locator_change_time: Optional[float] = Field(default=None, ge=0)
    load_unload_time: Optional[float] = Field(default=None, ge=0)
    changed_by: Optional[str] = None
