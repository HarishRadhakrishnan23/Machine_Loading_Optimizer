# TOV Machine Loading Optimizer — Project Brief for Claude Code

## Who you are working with
- Project Owner: Harish Radhakrishnan
- Role: AI Automation Intern, Emerson Process Management (India) Pvt. Ltd.
- Development machine: Windows 11 Enterprise
- Production target: Windows Server (IIS NOT used — Express.js replaces it entirely)
- Constraint: All tools must be free / open-source. No paid cloud storage or hosting.

---

## Project Vision & Mission

**Vision:** A fully automated, ML-driven machine scheduling system that eliminates manual planning bottlenecks in Emerson's TOV valve manufacturing shop floor.

**Mission:** Deliver real-time, optimal shift-level schedules across all machines and orders — minimizing delivery tardiness, maximizing machine utilization, and giving planners instant impact analysis when priorities change.

---

## What this project is

An ML-based machine loading and scheduling web application for Emerson's Triple Offset Valve (TOV) manufacturing plant. TOV valve bodies arrive semi-machined from the foundry and go through a sequence of machining operations before QA.

Operations are always executed in strict sequential order determined by OPERATION_NO ascending. Operation numbers are normally multiples of 10 (10, 20, 30, 40...). When a new operation is inserted between two existing ones in the ERP, it is assigned the midpoint (e.g., Op35 inserted between Op30 and Op40). Always follow ascending OPERATION_NO — never assume fixed gaps.

The system has two ML engines:
1. Engine 1 — Scheduling Optimizer: Uses CP-SAT (Google OR-Tools) to generate an optimal shift-level Gantt schedule for all pending orders across all machines.
2. Engine 2 — Recommendation Engine: Simulates what happens when a planner wants to elevate the priority of one or more orders, and produces a risk report showing impact on promise dates of other orders.

---

## Time units — ALL times are in minutes

Every time-related value in every table is always in minutes:
- CYCLE_TIME (cycle time per piece) → minutes
- SETUP_TIME (setup time per machine per ITEM_CATEGORY change) → minutes
- WORKING_MINS → minutes (in both MCH_MACHINE_AVAILABILITY and MCH_MACHINE_AVAILABILITY_BY_DATE)
- AVAILABLE_MINS → minutes (pre-computed in the ERP views — use directly)
- start_offset_min, end_offset_min (in MCH_SCHEDULE_OUTPUT) → minutes

**start_offset_min / end_offset_min are REAL minutes into the shift (0 … WORKING_MINS of that shift), NOT productive/OEE minutes.** They are a POST-SOLVE display layout only — never a solver constraint. `AVAILABLE_MINS` (the OEE-haircut capacity) governs how much work fits in a slot; `WORKING_MINS` is only the wall-clock canvas the display offsets are drawn on. See **Time-Mapping Strategy (Model C)** below.

Never apply any unit conversion. All values arrive as minutes from Oracle.

---

## Data sources — Oracle DB

All four upstream data sources are **read-only ERP views** (created and owned in Oracle by the
ERP team; this application only SELECTs from them). The application writes to exactly **two**
tables, which it creates and owns: MCH_SCHEDULE_OUTPUT (Engine 1) and MCH_SIM_RESULTS (Engine 2).

### Table / view name map (ERP name ⇄ legacy name used in this doc)
| Role in this doc | Oracle object (real name)         | Kind  | Access |
|------------------|-----------------------------------|-------|--------|
| machine_master   | MCH_MACHINE_AVAILABILITY          | view  | read-only |
| machine_daily    | MCH_MACHINE_AVAILABILITY_BY_DATE  | view  | read-only |
| routing_master   | MCH_MACHINE_PRIORITY              | view  | read-only |
| wip_orders       | MCH_WIP                           | view  | read-only |
| schedule_output  | MCH_SCHEDULE_OUTPUT               | table | read + write (Engine 1 writes) |
| sim_results      | MCH_SIM_RESULTS                   | table | read + write (Engine 2 writes) |

Legacy names (machine_master, wip_orders, schedule_output, …) are kept as readable aliases
throughout this document; the **real Oracle object names are the MCH_* names above**.

### Column-name changes vs. earlier drafts (apply everywhere)
- machine identifier: **WORK_CENTER** (was `machine_name`) — unified across all views.
- shift column: **SHIFT** (was `working_shift`) — still normalized to lowercase on read.
- machine_daily date column: **WORKING_DATE** (was `date`).
- **No DOWNTIME column** anymore — the views publish AVAILABLE_MINS directly (already OEE-adjusted).
- In MCH_WIP: the ascending sequence number is the **OPERATION** column (this doc historically
  calls it `OPERATION_NO` — they are the same thing), and the task/operation code (VB02, R002, …)
  is the **TASK** column (this doc historically called that `OPERATION`).
- In MCH_MACHINE_PRIORITY (routing): the operation code is **TASK**, the capable machine is
  **WORK_CENTER**. Routing ⇄ WIP join is `MCH_MACHINE_PRIORITY.TASK = MCH_WIP.TASK`.
- **COMPANY** appears in every view — a tenant/company filter; informational, not used by the engine.

---

### machine_master → MCH_MACHINE_AVAILABILITY (read-only view)
**Exact Oracle columns:** COMPANY, WORK_CENTER, SHIFT, WORKING_MINS, OEE, AVAILABLE_MINS
- Baseline capacity per machine (WORK_CENTER) per shift (SHIFT).
- WORK_CENTER is the machine identifier throughout the system (was `machine_name`).
- AVAILABLE_MINS = WORKING_MINS × OEE, pre-computed in the view — use AVAILABLE_MINS directly.
- SHIFT stored in mixed case in DB; always normalize to lowercase on read: "first", "second", "third".
- All machines are available for all shifts by default — this view is the universal baseline.
- OEE = 0.85 uniformly across all machines and all shifts (treat as fixed in v1).
- Used as fallback when MCH_MACHINE_AVAILABILITY_BY_DATE has no row for a WORK_CENTER+shift+date.
- COMPANY: tenant/company code — informational, not used by the engine.

### machine_daily → MCH_MACHINE_AVAILABILITY_BY_DATE (read-only view)
**Exact Oracle columns:** COMPANY, WORK_CENTER, WORKING_DATE, SHIFT, WORKING_MINS, OEE, AVAILABLE_MINS
- Day-specific capacity overrides, **prepared entirely in the ERP** — this application only reads it.
- **Read-only.** There is NO UI write layer and NO application write path for this view; the earlier
  "planner edits machine_daily via a form" requirement is removed. Closures/maintenance days are
  set upstream in the ERP.
- **No DOWNTIME column** — the view already publishes AVAILABLE_MINS (OEE-adjusted). Use AVAILABLE_MINS directly.
- WORKING_DATE is the date column (was `date`); WORK_CENTER is the machine; SHIFT is the shift.
- If AVAILABLE_MINS = 0 for a WORK_CENTER+shift+WORKING_DATE → that machine does not work that slot.
- If no row exists for a WORK_CENTER+shift+date → fall back to the MCH_MACHINE_AVAILABILITY baseline.
- SHIFT normalized to lowercase on read.
- COMPANY: tenant/company code — informational, not used by the engine.

### wip_orders → MCH_WIP (read-only view)
**Exact Oracle columns:** COMPANY, PRODUCTION_ORDER, PRODUCTION_START_DATE_AND_TIME, ORDER_STATUS,
ITEM, ITEM_DESCRIPTION, SIZE_INCH, CLASS, MOC, DESIGN, ITEM_CATEGORY, REFERENCE, QUANTITY_ORDERED,
CDD, OPERATION, OPERATION_STATUS, TASK, WORK_CENTER, QUANTITY_COMPLETED, QUANTITY_REJECTED, CYCLE_TIME

**Column meanings:**
- CDD = Committed Delivery Date = PDD (Promise Delivery Date). Use CDD throughout the codebase.
- **OPERATION** (NUMBER) = the ascending operation-sequence number (10, 20, 30, 35, …). This is the
  value this doc historically calls `OPERATION_NO`; the two names refer to the same column.
- **TASK** (VARCHAR2) = the task/operation code (e.g., VB02, VB03, R002 for rework). This is the value
  this doc historically called `OPERATION`. Routing capability is matched on TASK.
- ITEM_CATEGORY = concatenation_key (format: Size~Class~Design~MOC, e.g. "30~150~DF~CS").
  Its components are also available directly as SIZE_INCH, CLASS, DESIGN, MOC.
- PRODUCTION_START_DATE_AND_TIME = order_date, used for ageing score calculation.
- REFERENCE = specification_reference — informational only, not used by the engine.
- WORK_CENTER = the machine / work center assigned to that operation in ERP.
- ITEM / ITEM_DESCRIPTION = part number and its description — informational (UI display).
- ORDER_STATUS / OPERATION_STATUS = ERP status text — informational only. Do NOT use for scheduling
  decisions (Balance Qty is the sole indicator; see below).
- QUANTITY_REJECTED is nullable — treat NULL as 0 when computing Balance Qty.
- COMPANY: tenant/company code — informational, not used by the engine.

**Balance Qty — the ONLY scheduling quantity indicator:**
- Balance Qty = QUANTITY_ORDERED − QUANTITY_COMPLETED − QUANTITY_REJECTED (per operation row)
- If Balance Qty > 0 → schedule this operation with Balance Qty pieces
- If Balance Qty ≤ 0 → operation fully accounted for, skip entirely
- The Balance Qty of Op_n is the maximum input quantity available for Op_n+1
- Rejected pieces: ERP re-entry as a new production order is a manual process outside this system's scope
- Do NOT use OPERATION_STATUS to decide scheduling — Balance Qty is the sole indicator

**Rework operations (R002 task):**
- Rows whose TASK = R002 are treated as normal operations — schedule exactly like any other operation
- No special exclusion or detection logic for rework; if it has a valid CYCLE_TIME and positive Balance Qty, it is scheduled

**QA Inspection operations:**
- Operations where WORK_CENTER contains 'QAINSP' are manual quality gates performed by quality engineers
- Exclude from CP-SAT scheduling entirely (no machine assignment, no capacity consumption)
- Include in UI display — they are part of the order flow visible to planners

**Rows to skip entirely (CT = 0 rule):**
- Skip ALL rows where CYCLE_TIME = 0
- This covers: external vendor operations, operations with missing cycle time data, and any non-machine work
- These rows are excluded from both CP-SAT scheduling and UI display

**v1 scheduling scope:**
- Only 7 TASK codes currently have MCH_MACHINE_PRIORITY (routing) entries and are scheduled by CP-SAT:
  VB02 (Cone Oversize), VB03 (ST-21 Weld Overlay), VB04 (Stem Boring), VB05 (Cone Finishing),
  VB06 (Stem Boring & Cone Finishing), VB07 (Serration), VB09 (Phosphating)
- 15 other operations in WIP have no routing entries — skip them in CP-SAT for v1
- System is routing-extensible: when routing entries are added for additional operations, they are automatically included in scheduling without code changes

### routing_master → MCH_MACHINE_PRIORITY (read-only view)
**Exact Oracle columns:** COMPANY, SIZE_INCH, CLASS, MOC, DESIGN, ITEM_CATEGORY, TASK, MACHINE_PRIORITY, WORK_CENTER, SETUP_TIME

- Capability matrix — defines which machines (WORK_CENTER) can perform each TASK for each ITEM_CATEGORY.
- TASK is the operation code (VB02, …); it joins to MCH_WIP.TASK. WORK_CENTER is the capable machine
  (was `machine_name`). SIZE_INCH/CLASS/MOC/DESIGN are the ITEM_CATEGORY components (was size/class/moc/design).
- A single machine can appear in multiple rows for different tasks and/or different valve types.
- SETUP_TIME: time required to set up a machine for the FIRST PIECE of a new ITEM_CATEGORY.
  Same ITEM_CATEGORY back-to-back on the same machine = SETUP_TIME of 0.
- MACHINE_PRIORITY: integer 1 (most preferred) to 4 (least preferred).
  Used as a soft tiebreaker in the CP-SAT objective — does not override queue-depth-driven allocation.
- COMPANY: tenant/company code — informational, not used by the engine.

---

## capacity_resolved — NOT a table

The merge of MCH_MACHINE_AVAILABILITY_BY_DATE (override) onto MCH_MACHINE_AVAILABILITY (baseline)
happens entirely in memory (pandas) every time the engines run. It is never written to any table.

```python
def resolve_capacity(machine_master_df, machine_daily_df, target_date):
    resolved = machine_master_df.copy()
    resolved['SHIFT'] = resolved['SHIFT'].str.lower()
    daily = machine_daily_df[machine_daily_df['WORKING_DATE'] == target_date].copy()
    daily['SHIFT'] = daily['SHIFT'].str.lower()
    for _, row in daily.iterrows():
        mask = (
            (resolved['WORK_CENTER'] == row['WORK_CENTER']) &
            (resolved['SHIFT'] == row['SHIFT'])
        )
        resolved.loc[mask, 'AVAILABLE_MINS'] = row['AVAILABLE_MINS']
    return resolved
```

---

## Configuration — backend/config.json

Runtime parameters editable via UI. Stored as a JSON file at `backend/config.json`. Never stored in Oracle tables.

```json
{
  "batch_bonus_months": 2,
  "batch_bonus_value": 0.5,
  "downstream_queue_bonus_value": 0.3,
  "ageing_normalization_days": 180,
  "machine_priority_epsilon": 0.001,
  "risk_safe_threshold_days": 5,
  "engine2_time_limit_seconds": 10,
  "scheduling_horizon_safety_factor": 2,
  "scheduling_horizon_buffer_days": 7,
  "dev_max_orders": 0,
  "solver_workers": 0,
  "solver_time_limit_seconds": 300,
  "setup_penalty_weight": 0.05
}
```

- `scheduling_horizon_safety_factor` / `scheduling_horizon_buffer_days` size the scheduling horizon (see **Horizon derivation** in the Time-Mapping Strategy). They guarantee the horizon is long enough that every task is placeable (an infeasible-by-deadline order still gets scheduled — just tardy — rather than making the whole model infeasible).
- `dev_max_orders` — dev knob: limit scheduling to the top-N most urgent orders for fast iteration. **0 = full dataset (production).**
- `solver_workers` — CP-SAT parallel search workers. **0 = use ALL CPU cores** (the engine sets `num_workers = os.cpu_count()`); set a positive number only to cap it.
- `solver_time_limit_seconds` — Engine 1 time budget. Because a greedy fallback guarantees a schedule, this is a *quality* budget (more time → closer to optimal), not a correctness requirement. The run never fails if it expires.
- `setup_penalty_weight` — mild objective cost per setup event; encourages batching same valve sizes together (utilisation) without ever delaying a delivery. 0 disables it.

FastAPI exposes `GET /config` and `PUT /config` to read and update this file.
The UI's Machine Availability & Settings view provides a settings panel to change `batch_bonus_months` (and other params) without touching any Oracle table.

---

## Engine 1 — CP-SAT Scheduling Optimizer

Library: Google OR-Tools (`pip install ortools`), Apache 2.0 license, fully free.

### Preprocessing pipeline (preprocess.py)
Execute in this order before building the CP-SAT model:
1. Load all 4 Oracle views (MCH_WIP, MCH_MACHINE_AVAILABILITY, MCH_MACHINE_AVAILABILITY_BY_DATE, MCH_MACHINE_PRIORITY) into pandas DataFrames
2. **Filter CT=0:** Drop all rows where `CYCLE_TIME = 0`
3. **Filter routable ops:** Keep only rows where `TASK` is present in MCH_MACHINE_PRIORITY (`TASK` column)
4. **Filter QA:** Drop rows where `WORK_CENTER` contains 'QAINSP'
5. **Compute Balance Qty:** `balance_qty = QUANTITY_ORDERED − QUANTITY_COMPLETED − QUANTITY_REJECTED`; drop rows where `balance_qty ≤ 0`
6. **Normalize shifts:** `SHIFT = SHIFT.str.lower()` in both machine views
7. **Resolve capacity:** Call `resolve_capacity()` for each scheduling date to produce capacity_resolved

### Time-Mapping Strategy — Model C (global slot index + capacity buckets, flexible routing)

Engine 1 models time as **discrete `(date, shift)` slots**, not a continuous minute clock. This is the foundation every constraint builds on. It replaces the earlier "per-machine absolute-minute axis" idea, which broke precedence and tardiness (those are cross-machine, but a per-machine compressed minute axis drifts apart between machines with different AVAILABLE_MINS).

> **Model C vs. the retired Model B.** Model B assigned each batch to ONE machine
> (`AddExactlyOne(assign[t,m])`) and forced every open slot between the batch's start and end to
> be occupied (a circular contiguity reification). When that one machine hit a closed slot the
> batch was trapped → INFEASIBLE even at ~5 % utilisation. **Model C deletes both `assign` and the
> contiguity constraint.** Pieces are allocated as quantities into `(machine, slot)` buckets on
> ANY capable machine; setup economics (not a hard lock) keep a batch together, and a greedy
> fallback guarantees a schedule. This is simpler, correct, and never gets stuck.

**Slot = one `(date, shift)` pair.** Shifts are ordered `SHIFT_ORDER = [first, second, third]`. Every horizon date carries all three shifts. A shift that is closed (holiday, festival, breakdown, maintenance) is represented by `AVAILABLE_MINS = 0` for that machine+shift+date in machine_daily — the slot still exists in the lattice, it just holds no work. The slot lattice is never edited to remove dates or shifts.

**1. Global slot index — the shared calendar (answers "consecutive shifts contiguous?")**
```
horizon_dates = D = [d0, d1, d2, ...]          # consecutive calendar days, sorted ascending
SHIFT_ORDER   = S = [first, second, third]     # fixed, 3 shifts per day

slot_index(date, shift) = 3 × day_pos(date) + shift_pos(shift)
    day_pos(date)   = index of date in D        (d0→0, d1→1, ...)
    shift_pos: first→0, second→1, third→2
```
The slot index is **machine-independent**: a larger index is always later in real time for EVERY machine. Slots `k` and `k+1` are adjacent in real time — that is the entire meaning of "consecutive/contiguous." This shared calendar is what makes precedence (Op_n → Op_n+1 across different machines) and tardiness (completion vs CDD) correct.

**2. Inverse mapping — slot_index → (date, shift)**
```
day_pos   = slot_index // 3     →  date  = D[day_pos]
shift_pos = slot_index %  3     →  shift = S[shift_pos]
```

**3. (date, shift, offset_min) ↔ absolute_minute (DISPLAY / reference only — NOT a solver variable)**
Shift wall-clock windows are fixed and identical every day and every machine, so absolute-minutes can be derived purely from WORKING_MINS when needed for a Gantt canvas:
```
W_first, W_second, W_third = WORKING_MINS of each shift (fixed wall-clock lengths)
shift_start_in_day(first)  = 0
shift_start_in_day(second) = W_first
shift_start_in_day(third)  = W_first + W_second
day_length = W_first + W_second + W_third

absolute_minute(date, shift, offset_min)
    = day_pos(date) × day_length + shift_start_in_day(shift) + offset_min
      where 0 ≤ offset_min ≤ WORKING_MINS(shift)

inverse: divmod(absolute_minute, day_length) → (day_pos, minute_in_day);
         locate the shift window minute_in_day falls in → shift + offset_min
```
`start_offset_min / end_offset_min` are **real minutes into the shift (0 … WORKING_MINS)**, produced POST-SOLVE (see Output). Absolute-minutes are only a display/reference convenience.

**4. Machine-slot capacity (replaces "absolute-minute bounds for (machine, shift, date)")**
Each `(machine m, slot k)` has ONE scalar capacity — this is the only "bound" the solver needs; there is no per-machine minute axis:
```
cap[m, k] = AVAILABLE_MINS for machine m in the shift & date of slot k
            (from resolve_capacity;  cap = 0  ⇒  closed slot)
```

**5. Closed slots (AVAILABLE_MINS = 0)**
- Stay in the lattice so the date/shift mapping remains uniform.
- A closed `(machine, slot)` has NO variables at all — nothing can be placed there (Hard Rule 7).
- Overflow auto-routes around closed slots to the next open `(machine, slot)` (Hard Rule 4):
```
next_open(m, k) = smallest k' > k with cap[m, k'] > 0     # closed slots skipped (same machine)
```
Pieces prefer the same machine's next open slot (setup carries over = free) but move to another
capable machine when this one is closed/full. There is NO contiguity requirement in Model C —
a task's occupied slots need not be adjacent; setup economics, not a hard constraint, keep a
batch together.

**6. Integer scaling (CP-SAT is integer-only; AVAILABLE_MINS is fractional, e.g. 365.5)**
All minute quantities are scaled to integers by fixed factors before entering the model; piece quantities stay unscaled integers:
```
TIME_SCALE    = 100     # 2-decimal precision on minutes
URGENCY_SCALE = 1000    # 3-decimal precision on objective weights (captures ε = 0.001)

cap_s[m,k]   = round(AVAILABLE_MINS × TIME_SCALE)     # e.g. 365.5 → 36550
ct_s[t]      = round(CYCLE_TIME     × TIME_SCALE)
setup_s[c,m] = round(SETUP_TIME     × TIME_SCALE)
```

**7. Horizon derivation — where `horizon_dates` comes from at runtime**
The horizon must be long enough that every task is placeable, or the model is infeasible. Derived feasibility-first at run time:
```
total_work   = Σ_tasks (balance_qty × CYCLE_TIME) + setup allowance
daily_cap    = Σ_machines Σ_shifts AVAILABLE_MINS   (machine_master baseline)
horizon_days = ceil(total_work / daily_cap) × config.scheduling_horizon_safety_factor
horizon_days = max(horizon_days, days_until latest CDD) + config.scheduling_horizon_buffer_days
horizon_dates = [run_date + i days  for i in range(horizon_days)]
```
Consecutive calendar days from the run date. Closures within the horizon come from machine_daily as `cap = 0` slots — never by removing dates.

### Guiding principle — PRODUCTION SHOULD NEVER STOP
The single overriding design rule (from the plant owner): **work must never get stuck
waiting on a specific machine.** Maximum machine AND manpower utilisation is a must.
Every constraint below is subordinate to this: if the "preferred" choice would idle a
batch, the batch flows to whatever capable machine is open next. Setup time is always a
real cost when a *new valve size* (ITEM_CATEGORY) joins a machine — it shapes the choice,
it never blocks production.

### Hard Rules (enforced by CP-SAT model — Model C)
1. Operations scheduled in strictly ascending OPERATION_NO order (precedence via slot index)
2. Each scheduling unit is one `(PRODUCTION_ORDER, OPERATION_NO)` pair — quantity = balance_qty
3. **Flexible per-slot routing (no single-machine lock):** a task's pieces may be placed on
   ANY capable machine (from routing_master) in ANY open slot. There is NO `assign[t,m]`
   "one machine for the whole operation" variable — that lock was the cause of spurious
   INFEASIBILITY and has been removed. Splitting across machines is still possible when
   capacity/deadline genuinely requires it, but see **Batch-Aware Scheduling** below — orders
   sharing the same valve SIZE~CLASS~DESIGN are additionally constrained to consolidate onto
   ONE common machine per operation, so splitting is now the exception, not the default.
4. **Auto-route / overflow:** if a batch does not fit in a slot, the remainder flows to the
   next OPEN `(machine, slot)` — preferring the SAME machine (setup carries over, so it is
   free) but moving to another capable machine when the current one is closed/full. A closed
   slot (`AVAILABLE_MINS = 0`) simply has no variable, so pieces route around it automatically.
   Production never waits for a down machine.
5. **Setup is always a cost on a new size:** SETUP_TIME is charged whenever an ITEM_CATEGORY
   (valve size) newly appears on a machine in a slot — this covers both "a new order/size
   joins the machine" and "the batch switched machines". It is WAIVED only when the same size
   carried over from that machine's previous OPEN slot (so continuing across a shift boundary,
   e.g. shift 3 → shift 1 next day, on the same machine and same size is free). Charged at slot
   granularity inside the capacity bucket, and additionally penalised (mildly) in the objective
   so the solver batches same-size work together to maximise utilisation.
6. Non-routed operations are transparent to the CP-SAT model: precedence connects the last
   schedulable Op_n directly to the next schedulable Op_n+k, skipping unrouted ops in between.
7. If AVAILABLE_MINS = 0 for a machine+shift → that `(machine, slot)` has no variables; no work
   is placed there and pieces route to the next open slot.

**Preference vs. feasibility (Scenario 3 rule):** MACHINE_PRIORITY (1 = most preferred) is only
a *tiny objective tiebreaker*. The solver keeps a batch on the priority-1 machine only while
doing so still meets the deadline; the moment waiting for it would cause tardiness, the batch
goes to an earlier-available capable machine instead — even a lower-priority one — because
production must not stop or slip for a mere preference.

**No-overlap note:** there is no continuous timeline to overlap on. A machine is never
over-committed because the per-slot capacity constraint (`Σ work + setup ≤ AVAILABLE_MINS`)
already caps total load in each `(machine, slot)` bucket. `AddNoOverlap` is subsumed by it.

**Guaranteed feasibility (greedy fallback):** an earliest-slot, carryover-aware greedy pass
always produces a complete, valid schedule. It warm-starts CP-SAT (instant feasible point) and
is returned verbatim if CP-SAT cannot improve on it within the time budget. `/schedule/generate`
therefore ALWAYS returns a runnable plan — it never fails with INFEASIBLE/UNKNOWN on real data.

### Batch-Aware Scheduling — orders queued together to save setups

**Why this exists:** a production planning engineer clubs orders of similar valves before their
first operation and keeps that batch together through every operation that follows, so the
machine pays ONE setup for the whole group instead of one setup per order. Model C's original
per-piece flexible routing (Hard Rule 3) had no concept of this — it could freely fragment a
single order's pieces, let alone a batch's, across every capable machine, which is not how the
shop floor actually runs.

**Batch key — `SIZE_INCH~CLASS~DESIGN` (excludes MOC).** Two production orders belong to the same
batch at a given operation when their valve SIZE, CLASS, and DESIGN match — MOC does NOT need to
match (a Carbon Steel and a Stainless Steel order of the same size/class/design still batch
together). Grouping is evaluated independently **per operation** (per TASK code, e.g. VB02): a
batch formed at Op10 is free to split apart at Op20 if the orders' downstream routing diverges —
there is no requirement that a batch stays intact for an order's whole routing, only that *at any
given operation*, same-batch orders consolidate onto one machine for that operation.

> **Critical implementation detail — where `batch_key` must come from.** `batch_key` is computed
> directly from the raw, typed WIP columns (`SIZE_INCH`, `CLASS`, `DESIGN`) — see
> `preprocess.py::build_scheduler_input` and `batch_grouping.py::compute_batch_key`. It must
> **never** be derived by string-splitting `ITEM_CATEGORY` (`SIZE~CLASS~DESIGN~MOC`). When DESIGN
> is blank for a row, the ERP's concatenated `ITEM_CATEGORY` string silently drops that segment,
> shifting MOC into the position DESIGN would have occupied — a real bug that once merged orders
> with genuinely different DESIGN values into the same nominal batch, causing spurious machine
> splits with a completely different (and misleading) explanation each time it was investigated.
> `SchedulableTask.batch_key` (models.py) exists specifically so callers never re-derive this from
> `item_category`.

**Safety stock rides along, flagged separately.** Orders with `CDD = NULL` participate in
batching exactly like any other order — excluding them would defeat the setup-sharing purpose.
Each output row carries `IS_SAFETY_STOCK` (`Y`/`N`) so the UI can render them in a distinct
colour; a planner can then manually decide whether to include a flagged batch member for a quick
operation (e.g. Cone Oversize) or exclude it for a slower one (e.g. Stem Boring / Cone Finishing).

**Enforcement — CP-SAT (hard constraint) + greedy (sticky machine), both paths, because greedy is
often the one actually used:**
- CP-SAT (`Engine1Scheduler._add_batch_continuity`): for every batch group with more than one
  order, compute the **intersection** of all members' candidate machines. If non-empty, add one
  boolean `chosen[m]` per machine in that intersection with `Σ chosen == 1`, and constrain every
  member's `occ[pid, m, k] ≤ chosen[m]` for `m` in the intersection. If the intersection is empty
  (routing genuinely diverges — see escape hatch below), no constraint is added for that group.
- Greedy (`Engine1Scheduler.greedy_schedule`): the first order processed within a batch group
  picks a machine normally; every other member is then forced onto that same machine (with
  overflow onto further open slots on it, per Hard Rule 4). If that machine genuinely has no
  room left for a later member (a real capacity/timing conflict), that member falls back to a
  free search for its own remainder — logged as a "batch override" — rather than blocking
  production to preserve the grouping. Orders are also walked in **batch-clustered order**
  (same-batch orders adjacent in the urgency-sorted queue, not scattered) to minimise the chance
  of an unrelated order consuming the group's chosen machine in between placements.

**Escape hatch — divergent routing.** MOC is excluded from the batch key, but `MCH_MACHINE_PRIORITY`
does key on MOC, so two orders with identical SIZE/CLASS/DESIGN but different MOC can legitimately
have non-overlapping candidate machine sets. When that happens, batching is simply not enforced for
that specific group (CP-SAT skips the constraint; greedy's override path fires immediately) — this
is a real routing difference, not a bug, and production is never blocked over a batching preference.

**No historical retention / no archiving.** A prior design archived `MCH_SCHEDULE_OUTPUT` and
`MCH_SIM_RESULTS` rows older than 90 days into `_ARCHIVE` tables. This was removed: each
`/schedule/generate` run now deletes all rows and writes fresh (see Write tables section below).

### urgency_weight formula
```
pdd_score    = 1 / max(1, days_until_pdd)
                  ↑ exponentially urgent near deadline

ageing_score = (today − PRODUCTION_START_DATE_AND_TIME).days / config.ageing_normalization_days
                  ↑ normalized 0→1 over ~6 months; config.ageing_normalization_days = 180

batch_bonus  = config.batch_bonus_value  if  days_until_pdd < config.batch_bonus_months × 30
             = 0                          otherwise
                  ↑ bonus for orders due within the configured window (default: 2 months)
                  ↑ config.batch_bonus_months is editable via UI

downstream_queue_bonus = config.downstream_queue_bonus_value
                       if (wip_orders contains at least one OTHER production order with:
                           - same ITEM_CATEGORY as this order
                           - balance_qty > 0 at the next downstream schedulable operation
                           - meaning: that order already passed the current operation and is
                             queued downstream — scheduling this order now avoids a new setup
                             at the downstream machine)
                       = 0 otherwise
                  ↑ rewards scheduling orders that can "piggyback" on an active downstream setup
                  ↑ example: 8-class valve at Cone Oversize gets bonus when 8-class valves are
                    already pending at PTA — completing this order now sends pieces to a
                    machine already set up for 8-class (SETUP_TIME = 0 saved)
                  ↑ CP-SAT still weighs this against all other orders' tardiness — never
                    overrides PDD pressure; only promotes the order when capacity is available
                  ↑ config.downstream_queue_bonus_value = 0.3 (less than batch_bonus 0.5)

urgency_weight = pdd_score + ageing_score + batch_bonus + downstream_queue_bonus

Safety stock orders (CDD = NULL): urgency_weight = 0 (always scheduled last, never urgent)
```

### Objective function
```
Minimize:
    Σ_i [ urgency_weight_i × max(0, completion_day_i − pdd_day_i) ]     ← primary: weighted tardiness
  + Σ [ config.setup_penalty_weight × setup_charged[c,m,k] ]           ← secondary: batch same sizes
  + Σ_j [ config.machine_priority_epsilon × (MACHINE_PRIORITY_j − 1) × used[t,m]_j ]
                                                                          ← tertiary: machine priority tiebreaker
```

Weight ordering (all scaled by URGENCY_SCALE = 1000):
- **Tardiness** dominates — one tardy day costs `urgency_weight × 1000` (hundreds to thousands).
- **setup_penalty_weight = 0.05** → ~50 units per avoidable setup: enough to make the solver batch
  same-size work together (maximising machine utilisation) but far too small to ever delay a
  delivery to save a setup. Set to 0 to disable. Setup is *also* charged as real minutes inside the
  capacity bucket — this objective term only adds a mild "prefer fewer setups" nudge on top.
- **machine_priority_epsilon = 0.001** → 1 unit per priority level on `used[t,m]` (a per-task,
  per-machine "this machine was used" bool). Pure tiebreaker: prefer the priority-1 machine only
  when it costs no tardiness. `used[t,m]` replaces Model B's `assign[t,m]`, which no longer exists.

**Day resolution + integer scaling (Model C).** `completion_day` and `pdd_day` are DAY indices, not minutes:
```
completion_day[order] = end_slot[last routable op] // 3      # slot index → day (3 shifts/day)
pdd_day[order]        = (CDD − D[0]).days                    # days from horizon origin
```
Because CP-SAT is integer-only, the objective is evaluated in scaled integers:
```
urgency_s[order] = round(urgency_weight[order] × URGENCY_SCALE)      # URGENCY_SCALE = 1000
setup_pen_s      = round(config.setup_penalty_weight × URGENCY_SCALE)      # 0.05 × 1000 = 50
eps_s            = round(config.machine_priority_epsilon × URGENCY_SCALE)   # 0.001 × 1000 = 1

Minimize:  Σ_order urgency_s[order] × tardiness_days[order]
         + Σ_{c,m,k} setup_pen_s × setup_charged[c,m,k]
         + Σ_{t,m}  eps_s × (MACHINE_PRIORITY[t,m] − 1) × used[t,m]
```
With `URGENCY_SCALE = 1000`, one tardy day costs at least a few hundred units while the priority
tiebreaker costs 1 per priority level — so priority can only break ties among equal-tardiness solutions,
never override tardiness.

### CP-SAT model structure (engine1_scheduler.py) — Model C (flexible quantity-in-slot)

Model C allocates integer **quantities into `(machine, slot)` buckets**, on any capable machine.
There is no `assign` lock and no contiguity constraint — the two most complex, buggy parts of
Model B are simply gone. What remains is small and provably correct.

```
Notation:
  t   = schedulable task = (PRODUCTION_ORDER, OPERATION_NO), quantity q_t = balance_qty
  M_t = capable machines for t   (routing_master rows for t.TASK × t.ITEM_CATEGORY)
  K   = all slot indices in the horizon   (|horizon_dates| × 3)
  c_t = t.ITEM_CATEGORY (valve size)
  open_slots[m] = slots where cap_s[m,k] > 0  (closed slots get NO variables at all)

Decision variables (created only for capable m and OPEN slots k):
  qty[t,m,k]  ∈ [0, q_t]   integer     # pieces of t done on m in slot k — ANY capable machine
  occ[t,m,k]  ∈ {0,1}                   # 1 iff qty[t,m,k] > 0
  occ_any[t,k]∈ {0,1}                   # OR over machines: t does any work in slot k
  start_slot[t], end_slot[t] ∈ integer  # first / last occupied slot of t (for precedence)

── Quantity accounting (Hard Rules 2, 3 — flexible routing) ──
  Σ_{m,k} qty[t,m,k] = q_t             # every piece scheduled somewhere (never stops)
  qty[t,m,k] ≤ q_t × occ[t,m,k]        # occ = 0 ⇒ qty = 0
  occ[t,m,k] ≤ qty[t,m,k]              # qty = 0 ⇒ occ = 0
  # NO assign[t,m], NO AddExactlyOne — pieces may split across any capable machines.

── Capacity + setup per (machine, slot)  (Hard Rules 4, 5, 7; setup at slot granularity) ──
  Σ_t qty[t,m,k] × ct_s[t]  +  Σ_c setup_s[c,m] × setup_charged[c,m,k]  ≤  cap_s[m,k]
      cat_present[c,m,k]   = OR of occ[t,m,k] over tasks t with c_t = c
      carried[c,m,k]       = cat_present[c,m,k] AND cat_present[c,m, prev_open(m,k)]
      setup_charged[c,m,k] = cat_present[c,m,k] − carried[c,m,k]        (∈ {0,1})
  # Closed slot (cap_s = 0) has no variables ⇒ nothing placed there; pieces route elsewhere.
  # One SETUP_TIME per distinct valve size a machine newly touches in a slot, WAIVED when the
  # same size carried over from that machine's previous OPEN slot (prev_open) — so continuing on
  # one machine across a shift boundary is free, switching machines / sizes costs a setup.

── Occupancy window (for precedence only — NO contiguity forcing) ──
  occ_any[t,k]  = MaxEquality( occ[t,m,k] over m )
  end_slot[t]   = MaxEquality( k · occ_any[t,k] )                       over achievable k
  start_slot[t] = MinEquality( k · occ_any[t,k] + BIG · (1 − occ_any[t,k]) )
  # One-directional (derived FROM occ). No constraint forces occ back from start/end, so there
  # is no circular reification — this is the key difference from Model B.

── Precedence, skipping non-routed ops (Hard Rules 1, 6) ──
  Within each order, sort schedulable ops ascending OPERATION_NO: o_1 < o_2 < ...
  For each consecutive pair:  start_slot[o_{i+1}] ≥ end_slot[o_i]

── Objective (see Objective function section) ──
  completion_day[order] = end_slot[last routable op] // 3
  Minimize   Σ urgency_s × tardiness_days                         (primary)
           + Σ setup_pen_s × setup_charged[c,m,k]                 (mild — batch same sizes)
           + Σ eps_s × (MACHINE_PRIORITY − 1) × used[t,m]         (tiny — prefer priority-1)
```

**Greedy warm-start + fallback.** Before solving, an earliest-slot, carryover-aware greedy pass
places every piece (preferring earliest slot, then a slot already set up for the size, then lowest
priority). It hints CP-SAT (instant feasible point) and is returned as-is if CP-SAT does not beat
it in time — so a valid schedule is guaranteed.

**Post-solve extraction → MCH_SCHEDULE_OUTPUT** (see Output): for each `(t, m, k)` with `qty > 0`,
emit one row `(order, op, m, shift = S[k%3], date = D[k//3], balance_qty = qty)`, then lay the slot's
tasks back-to-back from offset 0 to fill `start_offset_min / end_offset_min` within `[0, WORKING_MINS]`.

### Output
Written to `MCH_SCHEDULE_OUTPUT`. One row per `(PRODUCTION_ORDER, OPERATION_NO, WORK_CENTER, shift, scheduled_date)` — a batch that overflows across slots produces multiple rows (same order/op/machine, different shift/date).

- `scheduled_date = D[k // 3]` and `shift = S[k % 3]` for each occupied slot `k`.
- `start_offset_min / end_offset_min`: POST-SOLVE display layout only. Within each `(machine, slot)`, lay the assigned tasks back-to-back from offset 0 using consumed minutes (`qty × CYCLE_TIME` + any charged setup). Since consumed ≤ `AVAILABLE_MINS ≤ WORKING_MINS`, all offsets fall inside `[0, WORKING_MINS]`. These offsets are NOT solver constraints.
- `new_completion_date` for an order = `D[end_slot[last routable op] // 3]` = max(scheduled_date) across the rows of that order's last routable operation. Consumed by Engine 2 for slip_days.

---

## Engine 2 — Recommendation Engine (engine2_recommender.py)

### Trigger
Planner selects one or more orders to elevate via the Order Board UI.

### Process
1. Read current `MCH_SCHEDULE_OUTPUT` → old_completion_date per order (baseline snapshot)
2. Set elevated order(s): `urgency_weight = 999999` (forces to top of scheduling queue)
3. Re-run Engine 1 CP-SAT with `max_time_in_seconds = config.engine2_time_limit_seconds` (default: 10s)
4. For every other order in the new schedule:
   - `slip_days = new_completion_date − old_completion_date`
   - `slack = CDD − new_completion_date`
5. Classify risk:
   - **SAFE:**    `slack > config.risk_safe_threshold_days` (default: 5 days)
   - **AT_RISK:** `0 ≤ slack ≤ config.risk_safe_threshold_days`
   - **BREACH:**  `slack < 0`
6. Write all results to `MCH_SIM_RESULTS` table
7. Return top-5 most impacted orders (highest slip_days) in API response

---

## Write tables (created + owned by this application — the ONLY two objects it writes)

Both are real Oracle tables (not views). They are created once in Oracle SQL Developer using the
DDL below. Machine identity uses **WORK_CENTER** to match the ERP views; the operation-sequence
number is stored as **OPERATION_NO** (= MCH_WIP.OPERATION).

### schedule_output → MCH_SCHEDULE_OUTPUT (Engine 1 writes)
```sql
CREATE TABLE MCH_SCHEDULE_OUTPUT (
    RUN_ID            VARCHAR2(36)  NOT NULL,   -- one id per /schedule/generate run
    PRODUCTION_ORDER  VARCHAR2(9)   NOT NULL,
    OPERATION_NO      NUMBER        NOT NULL,   -- = MCH_WIP.OPERATION (ascending seq number)
    TASK              VARCHAR2(51),             -- task code (VB02, …); for display, nullable
    WORK_CENTER       VARCHAR2(49)  NOT NULL,   -- assigned machine
    SHIFT             VARCHAR2(10)  NOT NULL,   -- first | second | third
    SCHEDULED_DATE    DATE          NOT NULL,
    BALANCE_QTY       NUMBER        NOT NULL,   -- pieces placed in THIS slot
    START_OFFSET_MIN  NUMBER        NOT NULL,   -- display: minutes into shift (0 … WORKING_MINS)
    END_OFFSET_MIN    NUMBER        NOT NULL,   -- display: minutes into shift
    BATCH_KEY         VARCHAR2(100),            -- SIZE_INCH~CLASS~DESIGN (excl. MOC) — see Batch-Aware Scheduling
    IS_SAFETY_STOCK   CHAR(1)       DEFAULT 'N',-- Y when the order's CDD is NULL — UI flags these distinctly
    GENERATED_AT      TIMESTAMP     NOT NULL,
    CONSTRAINT PK_MCH_SCHEDULE_OUTPUT
        PRIMARY KEY (RUN_ID, PRODUCTION_ORDER, OPERATION_NO, WORK_CENTER, SHIFT, SCHEDULED_DATE)
);
```
Model mapping (models.py `ScheduleOutputRow`): production_order→PRODUCTION_ORDER, operation_no→OPERATION_NO,
machine_name→WORK_CENTER, shift→SHIFT, scheduled_date→SCHEDULED_DATE, balance_qty→BALANCE_QTY,
start_offset_min→START_OFFSET_MIN, end_offset_min→END_OFFSET_MIN, batch_key→BATCH_KEY,
is_safety_stock→IS_SAFETY_STOCK ('Y'/'N' in Oracle, bool in Pydantic), run_id→RUN_ID, generated_at→GENERATED_AT.
(TASK is an extra display-only column not currently in the Pydantic model — populate it or leave NULL.)

**No historical retention.** Each `/schedule/generate` run DELETEs all existing rows from
`MCH_SCHEDULE_OUTPUT` before writing the new schedule (see `pipeline.py::_persist_schedule_output`).
There is no archive table for this data — a prior design that moved rows older than 90 days to
`MCH_SCHEDULE_OUTPUT_ARCHIVE` was removed; the table always holds exactly one run's worth of rows.

### sim_results → MCH_SIM_RESULTS (Engine 2 writes)
```sql
CREATE TABLE MCH_SIM_RESULTS (
    SIM_ID               VARCHAR2(36)  NOT NULL,  -- one id per /priority/simulate run
    ELEVATED_ORDER       VARCHAR2(200) NOT NULL,  -- elevated PRODUCTION_ORDER(s), comma-joined
    PRODUCTION_ORDER     VARCHAR2(9)   NOT NULL,  -- the impacted order (models.py `order` field)
    OLD_COMPLETION_DATE  DATE,
    NEW_COMPLETION_DATE  DATE,
    SLIP_DAYS            NUMBER,                   -- new_completion − old_completion, in days
    RISK_FLAG            VARCHAR2(10)  NOT NULL,   -- SAFE | AT_RISK | BREACH
    CREATED_AT           TIMESTAMP     NOT NULL,
    CONSTRAINT PK_MCH_SIM_RESULTS PRIMARY KEY (SIM_ID, PRODUCTION_ORDER)
);
```
Model mapping (models.py `SimResultRow`): sim_id→SIM_ID, elevated_order→ELEVATED_ORDER,
order→PRODUCTION_ORDER (Oracle reserves the word ORDER, so the impacted order is stored as the
column PRODUCTION_ORDER), old_completion_date→OLD_COMPLETION_DATE, new_completion_date→NEW_COMPLETION_DATE,
slip_days→SLIP_DAYS, risk_flag→RISK_FLAG, created_at→CREATED_AT.

---

## Oracle DB connection

python-oracledb in THIN MODE — no Oracle Client installation required. Works on Windows 11 and Windows Server.

```python
import oracledb, os
connection = oracledb.connect(
    user=os.getenv("ORACLE_USER"),
    password=os.getenv("ORACLE_PASSWORD"),
    dsn=os.getenv("ORACLE_DSN")
)
```

Credentials stored in `.env` (never committed to git).
Local dev DSN: `localhost:1521/XEPDB1` (Oracle XE via Docker).

---

## FastAPI backend

Framework: FastAPI | ASGI server: Uvicorn | Port: 8000

### Endpoints (9 total)
- POST /schedule/generate — triggers Engine 1, writes to MCH_SCHEDULE_OUTPUT
- GET  /schedule/current — reads latest MCH_SCHEDULE_OUTPUT, returns Gantt data
- POST /priority/simulate — triggers Engine 2 with payload `{orders: [...]}`, writes to MCH_SIM_RESULTS
- GET  /orders/wip — returns active MCH_WIP rows (CT > 0, routable, balance_qty > 0)
- GET  /machines/capacity — returns capacity_resolved for next N days
- POST /data/refresh — re-fetches all 4 read-only ERP views from Oracle
- GET  /machines/daily — returns MCH_MACHINE_AVAILABILITY_BY_DATE rows for a date range (read-only display)
- GET  /config — returns current config.json contents
- PUT  /config — updates config.json (batch_bonus_months, etc.)

There is **no** `PUT /machines/daily` — MCH_MACHINE_AVAILABILITY_BY_DATE is an ERP-owned read-only
view; the application never writes machine availability.

CORS enabled for React frontend origin.
Schedule regeneration: manual trigger only in v1 (no background timer).

---

## React frontend

Framework: React 18 + Vite | Styling: TailwindCSS | Charts: Recharts | Drag-and-drop: React DnD

### Four main views
1. **Schedule view** — Gantt chart per machine, showing shift utilisation %
2. **Order board** — WIP order cards, drag-to-reprioritise, trigger Engine 2 simulation
3. **Impact analyser** — MCH_SIM_RESULTS: risk scores, slip days, SAFE/AT_RISK/BREACH badges per order
4. **Machine availability & settings** — read-only view of MCH_MACHINE_AVAILABILITY_BY_DATE (capacity is
   ERP-owned, not editable here) + settings panel for config.json (batch_bonus_months and other
   parameters editable here)

### API proxy
Development: `vite.config.js` proxies `/api` → `http://localhost:8000`
Production: Express.js handles the `/api` → Uvicorn proxy

---

## Full stack — development on Windows 11 Enterprise

```
Terminal 1:  cd backend && uvicorn main:app --reload --port 8000
Terminal 2:  cd frontend && npm run dev   (Vite dev server on port 5173)
```

Vite proxies `/api` calls to FastAPI on port 8000. server.js is NOT used in development.

---

## Full stack — production on Windows Server (IIS NOT USED)

IIS is completely removed from the stack. Express.js handles both static file serving and API proxying.

### Backend
```
nssm install TovMLO-API "C:\tov-mlo\venv\Scripts\uvicorn.exe" "main:app --host 0.0.0.0 --port 8000"
nssm set TovMLO-API AppDirectory "C:\tov-mlo\backend"
nssm start TovMLO-API
```

### Frontend
```
cd frontend && npm run build
nssm install TovMLO-Frontend "C:\Program Files\nodejs\node.exe" "server.js"
nssm set TovMLO-Frontend AppDirectory "C:\tov-mlo"
nssm start TovMLO-Frontend
```

### server.js (Express production server)
```javascript
const express = require('express')
const { createProxyMiddleware } = require('http-proxy-middleware')
const path = require('path')
const app = express()

app.use('/api', createProxyMiddleware({ target: 'http://localhost:8000', changeOrigin: true }))
app.use(express.static(path.join(__dirname, 'frontend/dist')))
app.get('*', (req, res) => res.sendFile(path.join(__dirname, 'frontend/dist/index.html')))

app.listen(80)
```

---

## Project folder structure

```
tov-mlo/
├── CLAUDE.md
├── .env                          ← Oracle credentials (gitignored)
├── .gitignore
├── server.js                     ← Express.js production server
├── package.json                  ← express + http-proxy-middleware
├── backend/
│   ├── main.py                   ← FastAPI app + all 10 endpoints
│   ├── db.py                     ← Oracle connection pool + read/write helpers
│   ├── preprocess.py             ← resolve_capacity() + full preprocessing pipeline
│   ├── engine1_scheduler.py      ← CP-SAT scheduling model
│   ├── engine2_recommender.py    ← priority simulation + risk scoring
│   ├── models.py                 ← Pydantic request/response schemas
│   ├── config.json               ← runtime config (batch_bonus_months, epsilon, etc.)
│   └── requirements.txt
└── frontend/
    ├── package.json
    ├── vite.config.js
    ├── tailwind.config.js
    └── src/
        ├── App.jsx
        ├── views/
        │   ├── ScheduleView.jsx
        │   ├── OrderBoard.jsx
        │   ├── ImpactAnalyser.jsx
        │   └── MachineAvailability.jsx  ← read-only machine_daily viewer + config settings panel
        ├── components/
        │   ├── GanttChart.jsx
        │   ├── OrderCard.jsx
        │   └── RiskBadge.jsx
        └── api/
            └── client.js               ← all fetch calls to FastAPI
```

---

## Phase execution plan

| Phase | Deliverable | Status |
|-------|-------------|--------|
| 0 — Foundation | Oracle connection to the 4 ERP views + CREATE TABLE DDL for the 2 write tables (MCH_SCHEDULE_OUTPUT, MCH_SIM_RESULTS), test_connection.py, .env | Ready to start |
| 1 — Engine 1 | preprocess.py, engine1_scheduler.py (full CP-SAT with batch overflow + setup + priority), write MCH_SCHEDULE_OUTPUT | After Phase 0 |
| 2 — Engine 2 | engine2_recommender.py, risk classifier, write MCH_SIM_RESULTS | After Phase 1 |
| 3 — FastAPI | All 10 endpoints, config GET/PUT, Pydantic schemas, CORS, error handling | After Phase 2 |
| 4 — React UI | All 4 views, config settings panel in MachineAvailability, drag-drop Order Board | After Phase 3 |
| 5 — Deploy | NSSM Windows services (Uvicorn + Express), end-to-end integration test | After Phase 4 |

---

## Key constraints and rules — always follow these

- All tools free and open-source. No paid services.
- Oracle thin mode only. No Oracle Instant Client.
- ALL time values are in minutes throughout the entire stack. Never convert units.
- Balance Qty = QUANTITY_ORDERED − QUANTITY_COMPLETED − QUANTITY_REJECTED. Skip if ≤ 0.
- CYCLE_TIME = 0: skip those rows entirely (external vendors, missing data, non-machine ops).
- QA Inspection (WORK_CENTER contains 'QAINSP'): exclude from CP-SAT, show in UI.
- Rework (R002 operation): no special logic — schedule exactly like any other operation.
- v1 scheduling scope: only the 7 operations present in routing_master.
- Routing-extensible: new routing entries automatically extend CP-SAT scope without code changes.
- Shift names: always normalize to lowercase ("first", "second", "third") on read.
- **PRODUCTION SHOULD NEVER STOP** — the overriding rule. A task is never locked to one machine
  by the base model; batch continuity (below) adds a preference for ONE machine per batch, but
  always with an escape hatch back to free routing rather than ever blocking production.
- **Flexible per-slot routing (base capability):** a task's pieces may run on ANY capable machine
  in ANY open slot. If a machine is down/full, pieces auto-route to the next open `(machine, slot)`.
- **Batch-Aware Scheduling (layered on top):** orders sharing `SIZE_INCH~CLASS~DESIGN` (excludes
  MOC) consolidate onto ONE common machine per operation — enforced as a hard CP-SAT constraint
  AND in the greedy fallback (the path actually used when CP-SAT times out on real data). Batches
  may diverge after any single operation; safety stock orders (CDD=NULL) batch normally but are
  flagged via `IS_SAFETY_STOCK` for the UI. See **Batch-Aware Scheduling** section above.
  `batch_key` MUST come from the typed SIZE_INCH/CLASS/DESIGN columns, never parsed from the
  concatenated `ITEM_CATEGORY` string (blank DESIGN silently shifts MOC into that position).
- **Overflow / auto-route:** remainder prefers the SAME machine's next open shift (setup carries over = free), but moves to another capable machine when the current one is closed/full. Closed slots have no variables, so routing around a breakdown is automatic.
- **SETUP_TIME is always a cost on a NEW valve size:** charged whenever an ITEM_CATEGORY newly appears on a machine in a slot (new order joining, or a machine switch). Same size back-to-back on the same machine (carried over from its previous OPEN slot) = zero setup. Charged at slot granularity inside the capacity bucket, and mildly penalised in the objective to encourage batching.
- **MACHINE_PRIORITY is a soft tiebreaker only:** prefer priority-1 when it still meets the deadline; abandon it for an earlier-available capable machine the moment waiting would cause tardiness.
- **Guaranteed schedule:** a greedy fallback always returns a complete valid plan; `/schedule/generate` never fails with INFEASIBLE/UNKNOWN on real data.
- **Time model = Model C**: discrete `(date, shift)` slots with a global, machine-independent slot index (`3 × day_pos + shift_pos`); capacity is a per-`(machine, slot)` scalar bucket = AVAILABLE_MINS. No continuous minute axis, no OptionalIntervalVar, no `assign` lock, no contiguity constraint, no AddNoOverlap (capacity bucket subsumes it).
- Scheduling is at DAY/SHIFT resolution; `start_offset_min/end_offset_min` are post-solve display only, in real minutes into the shift (0 … WORKING_MINS).
- CP-SAT is integer-only: scale minutes by `TIME_SCALE = 100` and objective weights by `URGENCY_SCALE = 1000` (AVAILABLE_MINS can be fractional, e.g. 365.5).
- Horizon: `horizon_dates` = consecutive days from run date, sized feasibility-first (see Horizon derivation); closures come from machine_daily (cap = 0), never by removing dates.
- urgency_weight = pdd_score + ageing_score + batch_bonus + downstream_queue_bonus.
- batch_bonus: only for orders with CDD within batch_bonus_months × 30 days from today.
- downstream_queue_bonus: applied when another order of the same ITEM_CATEGORY has balance_qty > 0 at the next downstream schedulable operation — rewards batching to save downstream setup time. Value = config.downstream_queue_bonus_value (default 0.3). CP-SAT naturally weighs this against all other orders' tardiness; never overrides PDD pressure.
- Safety stock (CDD = NULL): urgency_weight = 0, always scheduled last.
- MACHINE_PRIORITY: soft tiebreaker via epsilon penalty (config.machine_priority_epsilon = 0.001).
- Maximum utilization: guiding principle, not a hard constraint.
- capacity_resolved: computed in memory only, never stored to any table.
- Schedule regeneration: manual trigger only in v1. No background timer.
- **No historical retention:** each `/schedule/generate` run DELETEs all `MCH_SCHEDULE_OUTPUT` rows
  before writing the fresh schedule. There is no archive table — a prior 90-day archiving design
  (`MCH_SCHEDULE_OUTPUT_ARCHIVE` / `MCH_SIM_RESULTS_ARCHIVE`) was removed entirely.
- Writes: only MCH_SCHEDULE_OUTPUT (Engine 1) and MCH_SIM_RESULTS (Engine 2) are written by this application.
- All four ERP sources — MCH_WIP, MCH_MACHINE_AVAILABILITY, MCH_MACHINE_AVAILABILITY_BY_DATE, MCH_MACHINE_PRIORITY — are read-only views (machine_daily is NOT written; there is no UI edit path).
- ERP column names: machine identifier = WORK_CENTER (not machine_name); shift = SHIFT; machine_daily date = WORKING_DATE; MCH_WIP op-sequence number = OPERATION (this doc's "OPERATION_NO"); op/task code = TASK (this doc's old "OPERATION"). No DOWNTIME column — AVAILABLE_MINS comes straight from the views.
- config.json: all runtime settings stored here. Never stored in Oracle tables.
- IIS not used. Express.js on Node.js handles all production serving.
- Both Uvicorn (port 8000) and Express (port 80) run as permanent NSSM Windows Services.
