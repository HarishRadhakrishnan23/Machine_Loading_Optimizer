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
- WORKING_MINS → minutes (in both machine_master and machine_daily)
- DOWNTIME → minutes (machine_daily)
- AVAILABLE_MINS → minutes
- start_offset_min, end_offset_min (in schedule_output) → minutes

**start_offset_min / end_offset_min are REAL minutes into the shift (0 … WORKING_MINS of that shift), NOT productive/OEE minutes.** They are a POST-SOLVE display layout only — never a solver constraint. `AVAILABLE_MINS` (the OEE-haircut capacity) governs how much work fits in a slot; `WORKING_MINS` is only the wall-clock canvas the display offsets are drawn on. See **Time-Mapping Strategy (Model B)** below.

Never apply any unit conversion. All values arrive as minutes from Oracle.

---

## Data sources — Oracle DB

### Access summary
- machine_master: read + write (write access reserved — do not write to it in v1)
- machine_daily: read + write — UI must provide a form for planners to update this table directly
- routing_master: read + write (write access reserved — do not write to it in v1)
- wip_orders: read-only — data is owned by ERP, never written by this application
- schedule_output: read + write — Engine 1 writes here
- sim_results: read + write — Engine 2 writes here

---

### machine_master
**Exact Oracle columns:** machine_name, WORKING_MINS, working_shift, oee, AVAILABLE_MINS
- Baseline capacity per machine per shift.
- AVAILABLE_MINS = WORKING_MINS × OEE — use AVAILABLE_MINS directly when column is populated.
- Shifts stored in mixed case in DB; always normalize to lowercase on read: "first", "second", "third"
- All machines are available for all shifts by default — machine_master is the universal baseline.
- OEE = 0.85 uniformly across all machines and all shifts (treat as fixed in v1).
- Used as fallback when machine_daily has no entry for a given machine+shift+date.

### machine_daily
**Exact Oracle columns:** machine_name, date, working_shift, WORKING_MINS, DOWNTIME, oee, AVAILABLE_MINS
- Day-specific overrides for capacity (populated manually by planners via UI).
- AVAILABLE_MINS = (WORKING_MINS − DOWNTIME) × OEE — use AVAILABLE_MINS directly when column is populated.
- If AVAILABLE_MINS = 0 for a machine+shift+date → that machine does not work that slot.
- Planner sets WORKING_MINS = 0 (or AVAILABLE_MINS = 0) when a machine is unavailable due to breakdown or maintenance.
- If no row exists in machine_daily for a machine+shift+date → fall back to machine_master baseline.
- Shift names normalized to lowercase on read.
- UI REQUIREMENT: React frontend provides a form to create or update any machine+shift+date row without touching other tables.

### wip_orders
**Exact Oracle columns:** PRODUCTION_ORDER, PRODUCTION_START_DATE_AND_TIME, CDD, QUANTITY_ORDERED, ITEM_CATEGORY, REFERENCE, OPERATION_NO, OPERATION, WORK_CENTER, CYCLE_TIME, QUANTITY_COMPLETED, QUANTITY_REJECTED

**Column meanings:**
- CDD = Committed Delivery Date = PDD (Promise Delivery Date). Use CDD throughout the codebase.
- ITEM_CATEGORY = concatenation_key (format: Size~Class~Design~MOC, e.g. "30~150~DF~CS")
- PRODUCTION_START_DATE_AND_TIME = order_date, used for ageing score calculation
- REFERENCE = specification_reference — informational only, not used by the engine
- OPERATION = task code (e.g., VB02, VB03, R002 for rework operations)
- WORK_CENTER = the machine or work center assigned to that operation in ERP

**Balance Qty — the ONLY scheduling quantity indicator:**
- Balance Qty = QUANTITY_ORDERED − QUANTITY_COMPLETED − QUANTITY_REJECTED (per operation row)
- If Balance Qty > 0 → schedule this operation with Balance Qty pieces
- If Balance Qty ≤ 0 → operation fully accounted for, skip entirely
- The Balance Qty of Op_n is the maximum input quantity available for Op_n+1
- Rejected pieces: ERP re-entry as a new production order is a manual process outside this system's scope
- Do NOT use OPERATION_STATUS to decide scheduling — Balance Qty is the sole indicator

**Rework operations (R002):**
- R002 rows are treated as normal operations — schedule exactly like any other operation
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
- Only 7 operations currently have routing_master entries and are scheduled by CP-SAT:
  VB02 (Cone Oversize), VB03 (ST-21 Weld Overlay), VB04 (Stem Boring), VB05 (Cone Finishing),
  VB06 (Stem Boring & Cone Finishing), VB07 (Serration), VB09 (Phosphating)
- 15 other operations in WIP have no routing entries — skip them in CP-SAT for v1
- System is routing-extensible: when routing entries are added for additional operations, they are automatically included in scheduling without code changes

### routing_master
**Exact Oracle columns:** size, class, design, moc, ITEM_CATEGORY, OPERATION, machine_name, SETUP_TIME, MACHINE_PRIORITY

- Capability matrix — defines which machines can perform each operation for each ITEM_CATEGORY
- A single machine can appear in multiple rows for different operations and/or different valve types
- SETUP_TIME: time required to set up a machine for the FIRST PIECE of a new ITEM_CATEGORY.
  Same ITEM_CATEGORY back-to-back on the same machine = SETUP_TIME of 0.
- MACHINE_PRIORITY: integer 1 (most preferred) to 4 (least preferred).
  Used as a soft tiebreaker in the CP-SAT objective — does not override queue-depth-driven allocation.

---

## capacity_resolved — NOT a table

The merge of machine_daily (override) and machine_master (baseline) happens entirely in memory (pandas) every time the engines run. It is never written to any table.

```python
def resolve_capacity(machine_master_df, machine_daily_df, target_date):
    resolved = machine_master_df.copy()
    resolved['working_shift'] = resolved['working_shift'].str.lower()
    daily = machine_daily_df[machine_daily_df['date'] == target_date].copy()
    daily['working_shift'] = daily['working_shift'].str.lower()
    for _, row in daily.iterrows():
        mask = (
            (resolved['machine_name'] == row['machine_name']) &
            (resolved['working_shift'] == row['working_shift'])
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
  "scheduling_horizon_buffer_days": 7
}
```

- `scheduling_horizon_safety_factor` / `scheduling_horizon_buffer_days` size the scheduling horizon (see **Horizon derivation** in the Time-Mapping Strategy). They guarantee the horizon is long enough that every task is placeable (an infeasible-by-deadline order still gets scheduled — just tardy — rather than making the whole model infeasible).

FastAPI exposes `GET /config` and `PUT /config` to read and update this file.
The UI's Machine Availability editor provides a settings panel to change `batch_bonus_months` (and other params) without touching any Oracle table.

---

## Engine 1 — CP-SAT Scheduling Optimizer

Library: Google OR-Tools (`pip install ortools`), Apache 2.0 license, fully free.

### Preprocessing pipeline (preprocess.py)
Execute in this order before building the CP-SAT model:
1. Load all 4 Oracle tables into pandas DataFrames
2. **Filter CT=0:** Drop all rows where `CYCLE_TIME = 0`
3. **Filter routable ops:** Keep only rows where `OPERATION` is in routing_master (`OPERATION` column)
4. **Filter QA:** Drop rows where `WORK_CENTER` contains 'QAINSP'
5. **Compute Balance Qty:** `balance_qty = QUANTITY_ORDERED − QUANTITY_COMPLETED − QUANTITY_REJECTED`; drop rows where `balance_qty ≤ 0`
6. **Normalize shifts:** `working_shift = working_shift.str.lower()` in both machine_master and machine_daily
7. **Resolve capacity:** Call `resolve_capacity()` for each scheduling date to produce capacity_resolved

### Time-Mapping Strategy — Model B (global slot index + capacity buckets)

Engine 1 models time as **discrete `(date, shift)` slots**, not a continuous minute clock. This is the foundation every constraint builds on. It replaces the earlier "per-machine absolute-minute axis" idea, which broke precedence and tardiness (those are cross-machine, but a per-machine compressed minute axis drifts apart between machines with different AVAILABLE_MINS).

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
- `cap = 0` forces all `qty` in that (machine, slot) to 0 (Hard Rule 7).
- Batch overflow steps OVER them to the next open slot on the same machine (Hard Rule 4):
```
next_open(m, k) = smallest k' > k with cap[m, k'] > 0     # closed slots skipped
```
A task occupies EXACTLY the OPEN slots of its assigned machine between its `start_slot` and `end_slot` (inclusive); closed slots inside that range are transparently skipped, with no gaps of idle open slots — this is the contiguity guarantee.

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

### Hard Rules (enforced by CP-SAT model)
1. Operations scheduled in strictly ascending OPERATION_NO order (precedence via slot index)
2. Each scheduling unit is one `(PRODUCTION_ORDER, OPERATION_NO)` pair — quantity = balance_qty
3. A batch stays on ONE assigned machine throughout — no mid-batch machine switching
4. If `balance_qty × CYCLE_TIME` exceeds `AVAILABLE_MINS` in a slot, the remainder schedules on the **same machine in the next immediate OPEN shift** (`next_open(m, k)` — closed slots are skipped), spanning arbitrarily many shifts/days until the batch completes
5. SETUP_TIME is only a factor during initial machine selection, never a reason to switch machines mid-batch; it is charged at **slot granularity** (see Setup logic below)
6. Non-routed operations are transparent to the CP-SAT model: precedence connects the last schedulable Op_n directly to the next schedulable Op_n+k, skipping all unrouted operations in between
7. If AVAILABLE_MINS = 0 for a machine+shift → no task assigned to that machine for that slot

**No-overlap note (Model B):** there is no continuous timeline to overlap on. A machine is never over-committed because the per-slot capacity constraint (`Σ work + setup ≤ AVAILABLE_MINS`) already caps total load in each `(machine, slot)` bucket. The old `AddNoOverlap` is therefore subsumed by the capacity constraint — it is not a separate constraint in Model B.

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
  + Σ_j [ config.machine_priority_epsilon × (MACHINE_PRIORITY_j − 1) × is_assigned_j ]
                                                                          ← secondary: machine priority tiebreaker
```

The machine priority term uses epsilon = 0.001 (or `config.machine_priority_epsilon`).
This weight is small enough that it never overrides the tardiness objective — it only resolves ties
between machine candidates that have equal tardiness impact.

**Day resolution + integer scaling (Model B).** `completion_day` and `pdd_day` are DAY indices, not minutes:
```
completion_day[order] = end_slot[last routable op] // 3      # slot index → day (3 shifts/day)
pdd_day[order]        = (CDD − D[0]).days                    # days from horizon origin
```
Because CP-SAT is integer-only, the objective is evaluated in scaled integers:
```
urgency_s[order] = round(urgency_weight[order] × URGENCY_SCALE)      # URGENCY_SCALE = 1000
eps_s            = round(config.machine_priority_epsilon × URGENCY_SCALE)   # 0.001 × 1000 = 1

Minimize:  Σ_order urgency_s[order] × tardiness_days[order]
         + Σ_{t,m}  eps_s × (MACHINE_PRIORITY[t,m] − 1) × assign[t,m]
```
With `URGENCY_SCALE = 1000`, one tardy day costs at least a few hundred units while the priority
tiebreaker costs 1 per priority level — so priority can only break ties among equal-tardiness solutions,
never override tardiness.

### CP-SAT model structure (engine1_scheduler.py) — Model B (quantity-in-slot)

Model B allocates integer **quantities into `(machine, slot)` buckets** rather than placing
`OptionalIntervalVar`s on a minute clock. Every hard rule maps to a bucket/slot-index constraint.

```
Notation:
  t   = schedulable task = (PRODUCTION_ORDER, OPERATION_NO), quantity q_t = balance_qty
  M_t = capable machines for t   (routing_master rows for t.OPERATION × t.ITEM_CATEGORY)
  K   = all slot indices in the horizon   (|horizon_dates| × 3)
  c_t = t.ITEM_CATEGORY

Decision variables:
  assign[t,m] ∈ {0,1}      one per m ∈ M_t            # machine chosen for the batch
  qty[t,m,k]  ∈ [0, q_t]   integer                    # pieces of t done on m in slot k
  occ[t,m,k]  ∈ {0,1}                                 # 1 iff qty[t,m,k] > 0
  start_slot[t], end_slot[t] ∈ [0, |K|−1]  integer    # first / last occupied slot of t

── Machine selection (Hard Rules 2, 3) ──
  AddExactlyOne(assign[t,m] for m ∈ M_t)              # exactly one machine per batch
  Σ_{m,k} qty[t,m,k] = q_t                            # every piece scheduled
  qty[t,m,k] ≤ q_t × assign[t,m]                      # work only on the chosen machine
  qty[t,m,k] ≤ q_t × occ[t,m,k]                       # occ = 0 ⇒ qty = 0
  occ[t,m,k] ≤ qty[t,m,k]                             # qty = 0 ⇒ occ = 0

── Capacity + setup per (machine, slot)  (Hard Rules 4, 7; setup at slot granularity) ──
  Σ_t qty[t,m,k] × ct_s[t]  +  setup_s_total[m,k]  ≤  cap_s[m,k]
      cat_present[c,m,k]   = OR of occ[t,m,k] over tasks t with c_t = c
      carried[c,m,k]       = cat_present[c,m,k] AND cat_present[c,m, prev_open(m,k)]
      setup_charged[c,m,k] = cat_present[c,m,k] AND NOT carried[c,m,k]
      setup_s_total[m,k]   = Σ_c setup_s[c,m] × setup_charged[c,m,k]
  # cap_s = 0 (closed slot) ⇒ every qty in the bucket is forced to 0.
  # One SETUP_TIME per distinct ITEM_CATEGORY a machine touches in a slot, WAIVED when the
  # same category carried over from that machine's previous OPEN slot (prev_open).

── Occupancy window + contiguity (Hard Rules 3, 4 — one machine, skip closed slots) ──
  occ_any[t,k] = Σ_m occ[t,m,k]                       # 0/1 (single machine)
  end_slot[t]   = MaxEquality( k · occ_any[t,k] )                      over k ∈ K
  start_slot[t] = MinEquality( k · occ_any[t,k] + |K| · (1 − occ_any[t,k]) )
  For the chosen machine m:
      occ[t,m,k] = 1  ⟺  cap[m,k] > 0  AND  start_slot[t] ≤ k ≤ end_slot[t]
  # ⇒ occupied slots are EXACTLY m's OPEN slots in [start_slot, end_slot]; closed slots skipped,
  #   no idle-open gaps → contiguity + "next immediate open shift" overflow for free.

── Precedence, skipping non-routed ops (Hard Rules 1, 6) ──
  Within each order, sort schedulable ops ascending OPERATION_NO: o_1 < o_2 < ...
  For each consecutive pair:  start_slot[o_{i+1}] ≥ end_slot[o_i]
  # Non-routed ops were dropped in preprocessing, so consecutive survivors chain directly.

── Objective (see Objective function section) ──
  completion_day[order] = end_slot[last routable op] // 3
  Minimize Σ urgency_s × tardiness_days + Σ eps_s × (MACHINE_PRIORITY − 1) × assign
```

**Post-solve extraction → schedule_output** (see Output): for each `(t, m, k)` with `qty > 0`,
emit one row `(order, op, m, shift = S[k%3], date = D[k//3], balance_qty = qty)`, then lay the slot's
tasks back-to-back from offset 0 to fill `start_offset_min / end_offset_min` within `[0, WORKING_MINS]`.

### Output
Written to `schedule_output`. One row per `(PRODUCTION_ORDER, OPERATION_NO, machine_name, shift, scheduled_date)` — a batch that overflows across slots produces multiple rows (same order/op/machine, different shift/date).

- `scheduled_date = D[k // 3]` and `shift = S[k % 3]` for each occupied slot `k`.
- `start_offset_min / end_offset_min`: POST-SOLVE display layout only. Within each `(machine, slot)`, lay the assigned tasks back-to-back from offset 0 using consumed minutes (`qty × CYCLE_TIME` + any charged setup). Since consumed ≤ `AVAILABLE_MINS ≤ WORKING_MINS`, all offsets fall inside `[0, WORKING_MINS]`. These offsets are NOT solver constraints.
- `new_completion_date` for an order = `D[end_slot[last routable op] // 3]` = max(scheduled_date) across the rows of that order's last routable operation. Consumed by Engine 2 for slip_days.

---

## Engine 2 — Recommendation Engine (engine2_recommender.py)

### Trigger
Planner selects one or more orders to elevate via the Order Board UI.

### Process
1. Read current `schedule_output` → old_completion_date per order (baseline snapshot)
2. Set elevated order(s): `urgency_weight = 999999` (forces to top of scheduling queue)
3. Re-run Engine 1 CP-SAT with `max_time_in_seconds = config.engine2_time_limit_seconds` (default: 10s)
4. For every other order in the new schedule:
   - `slip_days = new_completion_date − old_completion_date`
   - `slack = CDD − new_completion_date`
5. Classify risk:
   - **SAFE:**    `slack > config.risk_safe_threshold_days` (default: 5 days)
   - **AT_RISK:** `0 ≤ slack ≤ config.risk_safe_threshold_days`
   - **BREACH:**  `slack < 0`
6. Write all results to `sim_results` table
7. Return top-5 most impacted orders (highest slip_days) in API response

---

## Write tables (Python engines write here)

**schedule_output**
- PRODUCTION_ORDER, OPERATION_NO, machine_name, shift, scheduled_date
- balance_qty, start_offset_min, end_offset_min, run_id, generated_at

**sim_results**
- sim_id, elevated_order, order, old_completion_date, new_completion_date
- slip_days, risk_flag (SAFE / AT_RISK / BREACH), created_at

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

### Endpoints (10 total)
- POST /schedule/generate — triggers Engine 1, writes to schedule_output
- GET  /schedule/current — reads latest schedule_output, returns Gantt data
- POST /priority/simulate — triggers Engine 2 with payload `{orders: [...]}`, writes to sim_results
- GET  /orders/wip — returns active wip_orders (CT > 0, routable, balance_qty > 0)
- GET  /machines/capacity — returns capacity_resolved for next N days
- POST /data/refresh — re-fetches all 4 read-only tables from Oracle
- GET  /machines/daily — returns machine_daily records for a given date range
- PUT  /machines/daily — planner creates or updates a machine+shift+date row
- GET  /config — returns current config.json contents
- PUT  /config — updates config.json (batch_bonus_months, etc.)

CORS enabled for React frontend origin.
Schedule regeneration: manual trigger only in v1 (no background timer).

---

## React frontend

Framework: React 18 + Vite | Styling: TailwindCSS | Charts: Recharts | Drag-and-drop: React DnD

### Four main views
1. **Schedule view** — Gantt chart per machine, showing shift utilisation %
2. **Order board** — WIP order cards, drag-to-reprioritise, trigger Engine 2 simulation
3. **Impact analyser** — sim_results: risk scores, slip days, SAFE/AT_RISK/BREACH badges per order
4. **Machine availability editor** — form to create/update machine_daily records + settings panel for config.json (batch_bonus_months and other parameters editable here)

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
        │   └── MachineAvailability.jsx  ← machine_daily editor + config settings panel
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
| 0 — Foundation | Oracle XE running, CREATE TABLE DDL (6 tables), import script, test_connection.py, .env | Ready to start |
| 1 — Engine 1 | preprocess.py, engine1_scheduler.py (full CP-SAT with batch overflow + setup + priority), write schedule_output | After Phase 0 |
| 2 — Engine 2 | engine2_recommender.py, risk classifier, write sim_results | After Phase 1 |
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
- Batch stays on one assigned machine throughout — no mid-batch machine switching.
- Overflow: if batch doesn't fit in a slot, remainder goes to the same machine in the next immediate OPEN shift (closed slots skipped), spanning arbitrarily many days until complete.
- SETUP_TIME: applies only when ITEM_CATEGORY changes on a machine. Same ITEM_CATEGORY = zero setup. Charged at slot granularity (waived when the category carried over from the machine's previous open slot).
- **Time model = Model B**: discrete `(date, shift)` slots with a global, machine-independent slot index (`3 × day_pos + shift_pos`); capacity is a per-`(machine, slot)` scalar bucket = AVAILABLE_MINS. No continuous minute axis, no OptionalIntervalVar, no AddNoOverlap (capacity bucket subsumes it).
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
- machine_daily: the only Oracle table written by this application in v1.
- machine_master, routing_master, wip_orders: strictly read-only in v1.
- config.json: all runtime settings stored here. Never stored in Oracle tables.
- IIS not used. Express.js on Node.js handles all production serving.
- Both Uvicorn (port 8000) and Express (port 80) run as permanent NSSM Windows Services.
