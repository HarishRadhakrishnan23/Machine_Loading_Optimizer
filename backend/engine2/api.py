"""
api.py — Engine 2's FastAPI router (mounted by one `include_router` line in main.py).

All endpoints are read-only except POST /whatif/result/{id}/save (explicit,
planner-triggered write of the per-order rows to MCH_SIM_RESULTS).
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from engine2 import service
from engine2.scenarios import AddedMachine, ElevateOrders, MachinePriority, ScenarioError

router = APIRouter(prefix="/whatif", tags=["Engine 2 (what-if)"])


class AddedMachineIn(BaseModel):
    work_center: str
    fixture_change_time: Optional[float] = Field(default=None, ge=0)
    locator_change_time: Optional[float] = Field(default=None, ge=0)
    load_unload_time: Optional[float] = Field(default=None, ge=0)


class RunRequest(BaseModel):
    kind: Literal["elevate", "machine_priority"]
    refresh: bool = False  # reload the input snapshot from Oracle first

    # kind == "elevate": the planner's orders, highest priority first
    orders: list[str] = Field(default_factory=list)

    # kind == "machine_priority": one combo+task, machines most-preferred first
    size_inch: str = ""
    class_val: str = Field(default="", alias="class")
    moc: str = ""
    design: str = ""
    task: str = ""
    ordering: list[str] = Field(default_factory=list)
    added: list[AddedMachineIn] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


@router.get("/status")
def get_status():
    try:
        return service.status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Engine 2 status failed: {e}")


@router.post("/refresh")
def refresh_snapshot():
    """Reload every input from Oracle (read-only) and drop the cached baseline."""
    try:
        service.get_snapshot(refresh=True)
        return service.status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Refresh failed: {e}")


@router.get("/orders")
def list_orders():
    """Orders the engine schedules, for the order picker."""
    try:
        return {"orders": service.list_orders()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list orders: {e}")


@router.post("/run")
def run(req: RunRequest):
    """Run one scenario against the baseline and return the diff. Nothing is written."""
    try:
        if req.kind == "elevate":
            scenario = ElevateOrders(orders=tuple(req.orders))
        else:
            scenario = MachinePriority(
                size_inch=req.size_inch, class_val=req.class_val, moc=req.moc, design=req.design, task=req.task,
                ordering=tuple(req.ordering),
                added=tuple(
                    AddedMachine(a.work_center, a.fixture_change_time, a.locator_change_time, a.load_unload_time)
                    for a in req.added
                ),
            )
        return service.run_scenario(scenario, refresh=req.refresh)
    except ScenarioError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"What-if run failed: {e}")


@router.get("/result/{result_id}")
def get_result(result_id: str):
    result = service.get_result(result_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Result no longer in memory (server restarted or it aged out).")
    return result


@router.post("/result/{result_id}/save")
def save_result(result_id: str):
    """Optional: persist this result's per-order rows to MCH_SIM_RESULTS."""
    try:
        return service.save_result(result_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Result no longer in memory (server restarted or it aged out).")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Save failed: {e}")
