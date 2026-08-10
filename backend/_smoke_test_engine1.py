"""
Throwaway smoke test for the Model B rewrite of engine1_scheduler.py.
Not part of the project's test suite — verifies the model builds and solves
on a small hand-built SchedulerInput, exercising precedence, batch overflow
across shifts, and slot-granular setup carryover.

Run: python _smoke_test_engine1.py
"""

from datetime import date, datetime

from models import (
    CapacitySlot,
    Config,
    MachineCandidate,
    SchedulableTask,
    SchedulerInput,
    Shift,
)
from engine1_scheduler import run_engine1

D0 = date(2026, 8, 10)
D1 = date(2026, 8, 11)
HORIZON = [D0, D1]

config = Config()

# Machine M1 available first/second/third both days. Small AVAILABLE_MINS on
# purpose to force batch overflow across shifts.
capacity = []
for d in HORIZON:
    for shift, mins in [(Shift.FIRST, 100.0), (Shift.SECOND, 100.0), (Shift.THIRD, 100.0)]:
        capacity.append(CapacitySlot(machine_name="M1", shift=shift, slot_date=d, available_mins=mins))

# Two tasks, same order, ascending operation_no, same ITEM_CATEGORY (so the
# 2nd op's setup on M1 should be waived if it carries over from the 1st op's
# category — different category here would force a fresh setup).
tasks = [
    SchedulableTask(
        production_order="PO1",
        operation_no=10.0,
        operation="VB02",
        item_category="30~150~DF~CS",
        balance_qty=25,          # 25 * 10 = 250 mins > 100 available in one shift -> overflow
        cycle_time=10.0,
        cdd=date(2026, 8, 12),
        order_date=datetime(2026, 7, 1),
        urgency_weight=1.0,
        candidates=[MachineCandidate(machine_name="M1", setup_time=20.0, machine_priority=1)],
    ),
    SchedulableTask(
        production_order="PO1",
        operation_no=20.0,
        operation="VB03",
        item_category="30~150~DF~CS",
        balance_qty=5,
        cycle_time=10.0,
        cdd=date(2026, 8, 12),
        order_date=datetime(2026, 7, 1),
        urgency_weight=1.0,
        candidates=[MachineCandidate(machine_name="M1", setup_time=20.0, machine_priority=1)],
    ),
]

scheduler_input = SchedulerInput(
    tasks=tasks,
    capacity=capacity,
    horizon_dates=HORIZON,
    config=config,
)

result = run_engine1(scheduler_input, max_time_in_seconds=30)

print("status:", result.status)
print("objective:", result.objective_value)
print("completion_dates:", result.completion_dates)
print()
for row in sorted(result.assignments, key=lambda r: (r.production_order, r.operation_no, r.scheduled_date, r.shift.value)):
    print(
        f"  {row.production_order} op{row.operation_no:>5} {row.machine_name} "
        f"{row.scheduled_date} {row.shift.value:<6} qty={row.balance_qty:>3} "
        f"[{row.start_offset_min}-{row.end_offset_min}]"
    )

# Basic assertions
assert result.status.value in ("OPTIMAL", "FEASIBLE"), f"solve failed: {result.status}"
total_op10 = sum(r.balance_qty for r in result.assignments if r.operation_no == 10.0)
total_op20 = sum(r.balance_qty for r in result.assignments if r.operation_no == 20.0)
assert total_op10 == 25, f"op10 qty mismatch: {total_op10}"
assert total_op20 == 5, f"op20 qty mismatch: {total_op20}"

# op20 must start no earlier than op10 ends (precedence).
op10_rows = [r for r in result.assignments if r.operation_no == 10.0]
op20_rows = [r for r in result.assignments if r.operation_no == 20.0]
def slot_key(r):
    return (r.scheduled_date, ["first", "second", "third"].index(r.shift.value))
op10_end = max(slot_key(r) for r in op10_rows)
op20_start = min(slot_key(r) for r in op20_rows)
assert op20_start >= op10_end, f"precedence violated: op10 ends {op10_end}, op20 starts {op20_start}"

print("\nALL SMOKE ASSERTIONS PASSED")
