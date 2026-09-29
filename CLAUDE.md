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

**Mission:** Deliver real-time, shop-floor-realistic shift-level schedules across all machines and orders — minimizing delivery tardiness, maximizing machine utilization, respecting the plant's finite fixture/locator inventory, and giving planners instant impact analysis when priorities change.

---

## What this project is

An ML-based machine loading and scheduling web application for Emerson's Triple Offset Valve (TOV) manufacturing plant. TOV valve bodies arrive semi-machined from the foundry and go through a sequence of machining operations before QA.

Operations are always executed in strict sequential order determined by OPERATION_NO ascending. Operation numbers are normally multiples of 10 (10, 20, 30, 40...). When a new operation is inserted between two existing ones in the ERP, it is assigned the midpoint (e.g., Op35 inserted between Op30 and Op40). Always follow ascending OPERATION_NO — never assume fixed gaps.

The system has two ML engines:
1. **Engine 1 — Scheduling Optimizer**: a deterministic, continuous-time discrete-event dispatch simulator that produces a shop-floor-realistic schedule for all pending orders across all machines, driven by fixture/locator availability rather than machine-minute capacity. This is **Model E** (see below) — the current and final model.
2. **Engine 2 — Recommendation Engine**: simulates what happens when a planner wants to elevate the priority of one or more orders, and produces a risk report showing impact on promise dates of other orders.

---

## Model history (trace — do not re-implement these, context only)

The project went through four scheduling-engine designs before arriving at **Model E**, the current and final design:

- **Model B** (retired, never shipped): CP-SAT, one machine locked per whole operation (`assign[t,m]`) plus a hard contiguity constraint forcing every slot between start and end to be occupied. A single closed slot anywhere in that span made the model INFEASIBLE even at ~5% utilisation. Deleted for this reason.
- **Model C** (shipped, Phases 0–4 complete — see `PHASE2_SUMMARY.md`–`PHASE4_SUMMARY.md`): CP-SAT allocating integer piece-quantities into discrete `(machine, date, shift)` capacity buckets on a global slot index, with `SETUP_TIME`-based setup economics and a guaranteed greedy fallback. This is what is actually running in the codebase today (`engine1_scheduler.py`), pending the Model E rebuild described in this document.
- **Model D** (target spec, superseded before full implementation — only schema/audit groundwork landed): introduced the core idea Model E keeps — a continuous-time dispatch simulator with no machine-minute cap, driven by a finite plant-wide fixture/locator pool (`MCH_ITEMWISE_FIXTURE_LOCATOR`, `MCH_FIXTURE_LOCATOR`) instead of `SETUP_TIME`. Its batch/fixture key was `SIZE~CLASS~DESIGN` (MOC excluded). Superseded by Model E before its dispatch simulator, batching, or pool-constraint phases were built.
- **Model E** (current, final): keeps Model D's continuous-time/fixture-pool architecture, but **includes MOC in the batch/fixture key everywhere** (`SIZE~CLASS~MOC~DESIGN`), replaces Model D's abstract "fixture runs, locator-ordered" batching with an explicit, fully-specified priority-walk batching algorithm, adds a `REMARK` column so every WIP row is auditable (scheduled or not), surfaces each order's overall completion date/shift directly on every one of its rows, and fully retires CP-SAT — the dispatch simulator is the only engine, not an optional post-optimizer.

---

## Time units — ALL times are in minutes

Every time-related value in every table is always in minutes:
- CYCLE_TIME (cycle time per piece) → minutes
- FIXTURE_CHANGE_TIME, LOCATOR_CHANGE_TIME, LOAD_UNLOAD_TIME (MCH_ITEMWISE_FIXTURE_LOCATOR) → minutes
- WORKING_MINS → minutes (in both MCH_MACHINE_AVAILABILITY and MCH_MACHINE_AVAILABILITY_BY_DATE)
- AVAILABLE_MINS → minutes (pre-computed in the ERP views — use directly)

Never apply any unit conversion. All values arrive as minutes from Oracle.

`SETUP_TIME` (still present in `MCH_MACHINE_PRIORITY`, ERP-owned) is **ignored by the application** — Model E replaced setup-time economics with fixture/locator change-time economics (see Model E section).

---

## Shift clock-time convention (fixed)

None of the 6 ERP views carry a shift's real clock-time-of-day — only its *duration* (`WORKING_MINS`). The engine's internal clock (`TimePoint`: day + shift + minutes-into-shift) doesn't need one, but converting a `TimePoint` into a real Oracle `TIMESTAMP` (for `START_TIMESTAMP`/`END_TIMESTAMP`) does. This mapping is fixed, plant-wide, identical every day it applies, and does **not** come from Oracle — it's a hardcoded constant:

| Shift | Starts | Ends |
|---|---|---|
| first | 07:00 | 15:30 |
| second | 15:30 | 23:30 |
| third | 23:30 | 07:00 (next calendar day) |

Applies Monday through Saturday — Monday's first shift starts the week at 07:00, and Saturday's third shift ends Sunday at 07:00. There is no defined shift window for the rest of Sunday (07:00 Sunday → 07:00 Monday): this isn't a rule the engine hardcodes — it falls out naturally from `AVAILABLE_MINS` being 0 for that window in the ERP data (baseline or daily-override), exactly like any other closed shift (CLAUDE.md's existing "no capacity cap, closed shifts are skipped" behavior handles it with no special-casing).

A `TimePoint`'s `minute` offset is assumed to map linearly onto real clock time from the shift's start above — there's no finer-grained break/downtime subdivision in any ERP source to do otherwise.

---

## Data sources — Oracle DB

**Six** upstream data sources are **read-only ERP views** (created and owned in Oracle by the ERP team; this application only SELECTs from them). The application writes to exactly **two** tables, which it creates and owns: MCH_SCHEDULE_OUTPUT (Engine 1) and MCH_SIM_RESULTS (Engine 2).

### Table / view name map (ERP name ⇄ legacy name used in this doc)
| Role in this doc | Oracle object (real name)         | Kind  | Access |
|------------------|-----------------------------------|-------|--------|
| machine_master   | MCH_MACHINE_AVAILABILITY          | view  | read-only |
| machine_daily    | MCH_MACHINE_AVAILABILITY_BY_DATE  | view  | read-only |
| routing_master   | MCH_MACHINE_PRIORITY              | view  | read-only |
| wip_orders       | MCH_WIP                           | view  | read-only |
| fixture_locator_master    | MCH_ITEMWISE_FIXTURE_LOCATOR | view | read-only |
| fixture_locator_inventory | MCH_FIXTURE_LOCATOR          | view | read-only |
| schedule_output  | MCH_SCHEDULE_OUTPUT               | table | read + write (Engine 1 writes) |
| sim_results      | MCH_SIM_RESULTS                   | table | read + write (Engine 2 writes) |

### Batch / fixture key — used everywhere, all tables (Model E)

**`SIZE_INCH ~ CLASS ~ MOC ~ DESIGN`** — this exact field order, computed directly from the raw typed columns on every table that needs it. This **replaces** Model D's MOC-excluded `SIZE~CLASS~DESIGN` key.

> **Critical implementation detail, carried forward from Model D and still true:** this key must **never** be derived by string-splitting `ITEM_CATEGORY`. `ITEM_CATEGORY`'s own concatenation order is `Size~Class~Design~MOC` (**Design before MOC** — a different order than the batch/fixture key's `MOC` before `DESIGN`), and when DESIGN is blank in the ERP data, `ITEM_CATEGORY`'s concatenated string silently drops that segment, shifting MOC into the position DESIGN would have occupied. Always build the key from the four typed columns (`SIZE_INCH`, `CLASS`, `MOC`, `DESIGN`) directly, in that order, independent of `ITEM_CATEGORY`.

### machine_master → MCH_MACHINE_AVAILABILITY (read-only view)
**Exact Oracle columns:** COMPANY, WORK_CENTER, SHIFT, WORKING_MINS, OEE, AVAILABLE_MINS
- Baseline availability window per machine (WORK_CENTER) per shift (SHIFT). Under Model E this is a **rate/window, not a hard cap** — see Model E §7.
- SHIFT stored in mixed case in DB; always normalize to lowercase on read: "first", "second", "third".
- All machines are available for all shifts by default — this view is the universal baseline. No row for a machine ⇒ that machine is fully available for all its baseline AVAILABLE_MINS.
- OEE = 0.85 uniformly across all machines and all shifts (treat as fixed in v1).
- COMPANY: tenant/company code — informational, not used by the engine.

### machine_daily → MCH_MACHINE_AVAILABILITY_BY_DATE (read-only view)
**Exact Oracle columns:** COMPANY, WORK_CENTER, WORKING_DATE, SHIFT, WORKING_MINS, OEE, AVAILABLE_MINS
- Day-specific availability overrides (breakdown/maintenance), prepared entirely in the ERP — this application only reads it. **Read-only, no UI write path.**
- `AVAILABLE_MINS = 0` for a WORK_CENTER+shift+WORKING_DATE ⇒ that machine does not work that window.
- If no row exists for a WORK_CENTER+shift+date → fall back to the MCH_MACHINE_AVAILABILITY baseline.
- SHIFT normalized to lowercase on read. COMPANY informational only.

### wip_orders → MCH_WIP (read-only view)
**Exact Oracle columns:** COMPANY, PRODUCTION_ORDER, PRODUCTION_START_DATE_AND_TIME, ORDER_STATUS, ITEM, ITEM_DESCRIPTION, SIZE_INCH, CLASS, MOC, DESIGN, ITEM_CATEGORY, REFERENCE, QUANTITY_ORDERED, CDD, OPERATION, OPERATION_STATUS, TASK, WORK_CENTER, QUANTITY_COMPLETED, QUANTITY_REJECTED, CYCLE_TIME

**Column meanings** (unchanged from prior models):
- CDD = Committed Delivery Date = PDD. Use CDD throughout the codebase. `CDD = NULL` ⇒ safety-stock order.
- OPERATION (NUMBER) = ascending operation-sequence number (10, 20, 35, …) — this doc's `OPERATION_NO`.
- TASK (VARCHAR2) = task/operation code (VB02, VB03, R002, …) — routing capability is matched on TASK.
- PRODUCTION_START_DATE_AND_TIME = the date foundry material for this order lands on the shop floor. Meaning now depends on **ORDER_STATUS** — see Model E §1.
- ITEM_CATEGORY = ERP's own concatenation (`Size~Class~Design~MOC`) — informational display only; **never** parsed for the batch/fixture key (see warning above).
- WORK_CENTER (in MCH_WIP) = the machine currently assigned in ERP — informational; Engine 1 makes its own assignment.
- ORDER_STATUS / OPERATION_STATUS: ERP status text. OPERATION_STATUS is informational only (Balance Qty is the sole scheduling indicator). **ORDER_STATUS is now load-bearing** — see Model E §1 (Active vs. Planned).
- QUANTITY_REJECTED is nullable — treat NULL as 0.

**Balance Qty — the ONLY scheduling quantity indicator:**
- Balance Qty = QUANTITY_ORDERED − QUANTITY_COMPLETED − QUANTITY_REJECTED (per operation row)
- Balance Qty > 0 → schedule this operation with Balance Qty pieces. Balance Qty ≤ 0 → fully accounted for, excluded (with a REMARK — see Model E §12).
- The Balance Qty of Op_n is the maximum input quantity available for Op_n+1.

**Rework (TASK = R002):** scheduled exactly like any other operation, no special logic.
**QA Inspection** (WORK_CENTER contains 'QAINSP'): excluded from scheduling entirely, shown in UI, REMARK explains the exclusion.
**CT = 0 rows:** skipped entirely (external vendor ops, missing data, non-machine work), REMARK explains the exclusion.
**v1 scheduling scope:** only TASK codes with a `MCH_MACHINE_PRIORITY` routing entry are scheduled (currently VB02, VB03, VB04, VB05, VB06, VB07, VB09). Routing-extensible — new routing entries are automatically included without code changes. `CLASS = 'PN10'` is deliberately excluded (no established fixture/routing setup yet).

### routing_master → MCH_MACHINE_PRIORITY (read-only view)
**Exact Oracle columns:** COMPANY, SIZE_INCH, CLASS, MOC, DESIGN, ITEM_CATEGORY, TASK, MACHINE_PRIORITY, WORK_CENTER, SETUP_TIME
- Capability matrix — which machines (WORK_CENTER) can perform each TASK for each SIZE~CLASS~MOC~DESIGN.
- `MACHINE_PRIORITY`: integer 1 (most preferred) … 4 (least preferred) — a **soft tiebreaker only** among machines free at the same earliest time (Model E §7). Never overrides earliest-availability.
- `SETUP_TIME`: **present in the view, ignored by the application** (Model E replaced this with fixture/locator change-time economics).
- COMPANY informational only.

### fixture_locator_master → MCH_ITEMWISE_FIXTURE_LOCATOR (read-only view) — Model E
**Exact Oracle columns:** SIZE_INCH, CLASS, MOC, DESIGN, TASK, WORK_CENTER, FIXTURE, LOCATOR, FIXTURE_CHANGE_TIME, LOCATOR_CHANGE_TIME, LOAD_UNLOAD_TIME, USER_ID, USER_DATE
- Per `(SIZE_INCH, CLASS, MOC, DESIGN, TASK, WORK_CENTER)`: which FIXTURE and LOCATOR are used, and the minutes to change each (charged once, to the first piece of a batch) plus LOAD_UNLOAD_TIME (charged **per piece**).
- For a given SIZE~CLASS~MOC~DESIGN~TASK, FIXTURE and LOCATOR are constant across every WORK_CENTER row (only the change-times/load-unload may vary by machine).
- **Lookup is optional, not gating** — see Model E §11 for the no-match fallback.
- Background pattern (not a hardcoded rule — the table above is authoritative): fixtures tend to be shared within SIZE bands (3–6", 8–14", 16–24", 26–36", 40–48"), with exceptions when DESIGN = "DFL" or CLASS = "600". The engine never hardcodes these bands; it always looks up FIXTURE/LOCATOR directly from this table.

### fixture_locator_inventory → MCH_FIXTURE_LOCATOR (read-only view) — Model E
**Exact Oracle columns:** DEVICE_NAME, DEVICE_TYPE, DESCRIPTION, QUANTITY, USER_ID, USER_DATE
- Physical shop-floor inventory: `DEVICE_NAME` = a Fixture ID or Locator ID, `DEVICE_TYPE` = `'F'` (fixture) or `'L'` (locator), `QUANTITY` = how many physically exist.
- `QUANTITY` is a **hard, plant-wide concurrency cap** — see Model E §9 (pool constraint).

---

## Configuration — backend/config.json (Model E shape)

Runtime parameters editable via UI, stored as JSON at `backend/config.json`, never in Oracle.

```json
{
  "batch_consolidation_window_days": 60,
  "cooling_minutes": 20,
  "heavy_operations": ["VB03", "VB04", "VB05", "VB06"],
  "protect_committed_over_safety": true,
  "planned_order_start_buffer_days": 1,
  "risk_safe_threshold_days": 5,
  "dev_max_orders": 0
}
```

- `batch_consolidation_window_days` (Model E §6): an order is eligible to be pulled into a fixture-run consolidation only if its CDD is within this many days of **today** (run date). Default 60 (~2 months). **Hard rule, no override** — see §6.
- `cooling_minutes` (Model E §7): fixed machine dead-time after every batch, before that machine's next batch. Default 20.
- `heavy_operations` (Model E §10): TASK codes where mixed committed/safety-stock batches get the committed-first policy.
- `protect_committed_over_safety`: must always be `true` — no committed order may ever slip past its CDD to accommodate safety stock, or to accommodate the §6 consolidation override.
- `planned_order_start_buffer_days` (Model E §1/§8): days added to a Planned order's `PRODUCTION_START_DATE_AND_TIME` to get its earliest-start date. Default 1.
- `risk_safe_threshold_days`: Engine 2 SAFE/AT_RISK/BREACH threshold (unchanged from prior models).
- `dev_max_orders`: dev knob, limit scheduling to top-N most urgent orders for fast iteration. 0 = full dataset (production).

**Retired with Model C/CP-SAT (no longer meaningful under a deterministic dispatch simulator):** `batch_bonus_months`, `batch_bonus_value`, `downstream_queue_bonus_value`, `ageing_normalization_days`, `machine_priority_epsilon`, `engine2_time_limit_seconds`, `scheduling_horizon_safety_factor`, `scheduling_horizon_buffer_days`, `solver_workers`, `solver_time_limit_seconds`, `setup_penalty_weight` — these existed to shape a CP-SAT objective function or size a CP-SAT horizon; Model E has neither. `backend/config.json` and `models.py::Config` still need to be migrated to this shape (tracked as implementation work, not yet done).

FastAPI exposes `GET /config` and `PUT /config` to read and update this file (endpoints unchanged; the `Config` schema they serve needs the migration above).

---

## Engine 1 — Model E (current, final design)

Engine 1 is a **deterministic, continuous-time discrete-event dispatch simulator** — not a CP-SAT capacity model, and not an interval/slot solver of any kind. CP-SAT is fully retired; there is no fallback to it and no post-optimization pass. Same inputs always produce the same schedule (essential for a $50M line's auditability).

### E.1 — Order types (ORDER_STATUS)

- **"Active"** — foundry material is already on the shop floor. Plan normally: check CDD, batch per §5, assign to machines per §7. No extra constraint.
- **"Planned"** — material for this order arrives at `PRODUCTION_START_DATE_AND_TIME`. The order's **earliest-start** is `PRODUCTION_START_DATE_AND_TIME + planned_order_start_buffer_days` (default: +1 calendar day), first shift of that day onward. A Planned order is **excluded from batching entirely** until its earliest-start passes — it cannot join a fixture run that starts before that date. If every machine capable of its first operation is fully busy on that exact day, the engine **never interrupts an already-running operation** to force the order in — it simply queues normally and starts as soon as a capable machine frees up at/after its earliest-start.
- Every order — Active or Planned — is targeted to complete **on or before its CDD**. Downstream operations follow ordinary precedence once the first operation starts.

### E.2 — Priority ordering

**Lower CDD ⇒ higher priority.** Ties broken by ageing (older `PRODUCTION_START_DATE_AND_TIME` first). **Safety-stock orders (CDD = NULL) are always lowest priority**, scheduled last / as backfill (subject to §10). This is a simple sort key for the dispatch simulator — there is no CP-SAT-style weighted urgency score (that formula was specific to Model C's objective function and does not apply here).

### E.3 — Fixture & Locator model (replaces SETUP_TIME)

Every `SIZE_INCH~CLASS~MOC~DESIGN` has one FIXTURE and one LOCATOR per operation (from `MCH_ITEMWISE_FIXTURE_LOCATOR`), constant across machines. `SETUP_TIME` (MCH_MACHINE_PRIORITY) is ignored entirely.

- When the **FIXTURE** on a machine changes: charge `FIXTURE_CHANGE_TIME + LOCATOR_CHANGE_TIME` once, before the first piece.
- When only the **LOCATOR** changes (fixture unchanged): charge `LOCATOR_CHANGE_TIME` once, before the first piece.
- **LOAD_UNLOAD_TIME** is charged **per piece**, for every piece in the batch, regardless of fixture/locator change.

```
Fixture change:  FIXTURE_CHANGE_TIME + LOCATOR_CHANGE_TIME + (LOAD_UNLOAD_TIME × QTY)
Locator change (fixture unchanged):  LOCATOR_CHANGE_TIME + (LOAD_UNLOAD_TIME × QTY)
No change (same fixture + same locator carried over):  LOAD_UNLOAD_TIME × QTY
```
`CYCLE_TIME` (per piece, from MCH_WIP) is **added on top** of the above — it is the actual cut time, unaffected by fixture/locator logic.

### E.4 — Batching algorithm (the priority-walk)

Goal: minimize fixture/locator changeovers while respecting CDD priority. Batching happens **first, across the whole plant**, before any machine is chosen (§7 picks the machine afterward, per the intersection of capable machines for the whole batch).

Given the pool of eligible order-operations (same TASK) sorted by priority (§2), walk it as follows:

1. Take the highest-priority order not yet batched. Start a new **fixture run**: charge `FIXTURE_CHANGE_TIME + LOCATOR_CHANGE_TIME + LOAD_UNLOAD_TIME × qty`.
2. Search the remaining pool, in this preference order, for the next order to append to the **current** atomic sub-batch / fixture run:
   a. **Exact same SIZE~CLASS~MOC~DESIGN, same fixture AND same locator already active** → append, charge only `LOAD_UNLOAD_TIME × qty`.
   b. Else, **same fixture + same locator combo** (any SCMD) → append, charge only `LOAD_UNLOAD_TIME × qty`.
   c. Else, **same fixture, different locator**, highest priority among candidates → append, charge `LOCATOR_CHANGE_TIME + LOAD_UNLOAD_TIME × qty`; this starts a new locator sub-batch within the same fixture run — repeat (a)/(b) inside it before falling back to (c) again.
   d. When no order sharing this fixture remains, the fixture run is closed.
3. Move to the next-highest-priority remaining order and repeat from step 1 (new fixture run, machine takes it after `cooling_minutes` — §7).

This is worked in full, line-by-line, in the two examples preserved in `Chat Reports/Engine1_Finallogic(BOSS)` — implementers should validate against both exactly before considering the batching engine correct.

**Routing-divergence split** (inherited from Model D): a batch normally stays intact across operations (same SCMD ⇒ same fixture/locator downstream), but when members' routing diverges at a later operation (e.g. one has an extra rework op), the batch splits only at the operation where routing diverges and rejoins where it reconverges.

### E.5 — Machine selection

For a formed fixture run: intersect the capable machines (per `MCH_MACHINE_PRIORITY`, evaluated for every member) across all members of the run. Pick the machine **free earliest**; break ties by lowest `MACHINE_PRIORITY`. Priority is soft — never wait on a preferred machine while another capable machine is idle and could take the work. If no single machine is capable of every member, keep the run intact on one common machine and drop the incapable member(s) — they schedule in their own run.

### E.6 — Consolidation window (60 days) — HARD RULE, no override

An order is eligible to be pulled into a fixture-run consolidation (§4 step 2) **iff its CDD is within `batch_consolidation_window_days` (default 60) of today** (run date).

**This window governs committed-vs-committed consolidation only — it does not gate safety-stock orders.** Safety stock (CDD = NULL) always sorts last in priority (§2) and can therefore never *anchor* a run ahead of a committed order — it has no CDD to "pull forward" and no way to make the window's concern (needlessly consolidating a near-term committed order with something due far out) happen. So a safety-stock order riding along *after* an already-forming, committed-anchored run is exempt from this window check entirely; whether it actually gets appended is decided solely by §E.10's safety-stock policy below, never by §E.6. (An order's own committed CDD, if it has one, is still fully subject to the window regardless of what else is in the run.)

**This is a hard cutoff, not a soft preference — there is no escape hatch.** An order beyond the window is simply never eligible for consolidation, regardless of machine idleness, downstream impact, or anything else. (An earlier draft of this rule had a "delays no committed order" override; that override was deliberately removed — §6 is now evaluated purely on the window number, nothing else.) `batch_consolidation_window_days` remains a plain config value — changing it (e.g. 60 → 90) takes effect immediately with no code change, since the cutoff is read from config on every call, never hardcoded.

### E.7 — Continuous timeline, no capacity cap

- Time is continuous wall-clock minutes. There is **no discrete shift-slot lattice** and **no per-machine capacity cap** — every machine can take infinite work; what governs timing is fixture/locator/machine availability, not a minute budget.
- Machining only progresses inside a shift's `AVAILABLE_MINS` window (from `MCH_MACHINE_AVAILABILITY`, overridden by `MCH_MACHINE_AVAILABILITY_BY_DATE` when present; no row ⇒ fully available). If a batch's remaining work exceeds the time left in the current shift, it pauses and resumes in that machine's next working shift/day.
- **`cooling_minutes` (default 20)** fixed dead-time after every batch, on every machine, before that machine's next batch — regardless of whether the next available instant falls in the same shift, the next shift, or the next working day. A machine is never left idle for a placeable task beyond this cooling window.
- **Every machine is always considered.** A machine with no row in `MCH_MACHINE_AVAILABILITY_BY_DATE` is fully available for its `MCH_MACHINE_AVAILABILITY` baseline. Breakdowns/maintenance come only from `AVAILABLE_MINS = 0` rows in the by-date view.
- No inter-operation WIP transit time: an order's next operation may start the instant its previous operation ends (subject to machine/fixture/locator availability and cooling).

### E.8 — Planned-order release gating

See §1. Earliest-start = `PRODUCTION_START_DATE_AND_TIME + planned_order_start_buffer_days`, first shift onward. The order is invisible to the batching walk (§4) until that instant passes; once it passes, it competes for machines/fixtures normally, by priority (§2). Never preempt or interrupt a machine's in-progress batch to slot a Planned order in early.

### E.9 — Fixture/Locator pool constraint (hard, plant-wide)

`MCH_FIXTURE_LOCATOR.QUANTITY` caps **concurrent** use of each device (fixture or locator) across the entire plant. If only 1 unit of a fixture exists, two batches needing it cannot run on two machines at the same time — one waits, regardless of machine availability.

- A fixture run holds **1 unit of its FIXTURE for the run's entire start-to-end span**.
- It holds **1 unit of a LOCATOR only during that locator's own sub-batch** inside the run, releasing it before the next locator sub-batch mounts.
- No transfer/movement time when a device moves between machines — release and re-mount are instantaneous; `QUANTITY` is purely a concurrency cap, not a location tracker.
- **Same SIZE~CLASS~MOC~DESIGN, same operation, running on two machines at once** is an **additional soft preference to avoid** — not a hard rule enforced only by the pool cap. Even when enough fixture/locator units physically exist to allow a split, the batching walk (§4) should still prefer keeping same-SCMD same-operation work together on one machine wherever priority and fixture/locator lookup allow it.

### E.10 — Safety-stock policy on heavy operations

`heavy_operations = ["VB03", "VB04", "VB05", "VB06"]` (Weld Overlay, Stem Boring, Cone Finishing, Stem Boring + Cone Finishing).

Split a fixture+locator sub-batch into its **committed** (CDD not null) and **safety-stock** (CDD null) pieces.

- **Heavy operations:** schedule the committed pieces first. Append the safety-stock pieces immediately after (free — same fixture/locator already mounted) **only if** doing so delays no committed order, anywhere, past its CDD. If it would, release the fixture/locator and send the safety-stock pieces to the **back of the global priority queue** — picked up later whenever their fixture/locator naturally comes free again.
- **Light operations** (everything not in `heavy_operations`): committed and safety-stock pieces batch together normally, no special ordering — the marginal cost of including safety-stock is only `LOAD_UNLOAD_TIME × qty`.
- **`protect_committed_over_safety = true` is absolute** — no committed order is ever delayed to build safety stock, under any circumstance.

**Implementation requirement — full speculative check, not a proxy.** "Delays no committed order, anywhere" must be answered by actually forking the simulation state and running two independent continuations of the rest of the schedule — **append** and **defer** — then comparing whether the append branch causes any committed order to breach its own CDD. A local proxy (checking only orders sharing the same machine/fixture/locator) was explicitly rejected in favor of this full check, given the project's zero-tolerance for a wrongly-approved committed delay.

Because both continuations are pure CPU-bound Python (no I/O), running them one after another would double the cost of every single heavy-op decision across a run with many such decisions. **This must be parallelized across separate OS processes, not threads** — CPython's GIL means threads buy no real speedup on CPU-bound work. On Windows (this project's dev machine and production target), `multiprocessing`'s spawn start method pickles every branch's target function and arguments to hand to a worker process, so each branch's continuation must be a plain module-level function operating on plain, picklable state (a forked `machine_free_at` dict, a forked `DevicePool` via `DevicePool.clone()`, the remaining order queue) — never a closure or bound method capturing outer state. See `backend/dispatch_parallel.py` (the branch-evaluation harness) and `DevicePool.clone()` in `backend/dispatch_pool.py` (the forkable pool state) — both already built; the orchestrator still needs to supply the actual "run the rest of the schedule from this forked state" continuation.

### E.11 — Fixture/locator lookup: optional, not gating

If `(SIZE_INCH, CLASS, MOC, DESIGN, TASK)` has **no** row in `MCH_ITEMWISE_FIXTURE_LOCATOR` for any candidate machine, the operation is **still scheduled** — via plain routing + `Σ CYCLE_TIME` only (no fixture/locator effect, no fixture-run membership, no pool involvement). `FIXTURE_ID`/`LOCATOR_ID` are left NULL on its output row, and `REMARK` records `"No fixture/locator match — scheduled via plain routing"`.

### E.12 — Scheduling scope & the REMARK column

An operation is **scheduled** (consumes real machine/fixture/locator time) iff **all** hold: `CYCLE_TIME > 0`, `balance_qty > 0`, `WORK_CENTER` does not contain `'QAINSP'`, `CLASS != 'PN10'`, and it has a routing entry in `MCH_MACHINE_PRIORITY`.

**Every eligible order-operation from MCH_WIP now always produces a row in `MCH_SCHEDULE_OUTPUT`, scheduled or not.** When it is not scheduled, `WORK_CENTER`/`SHIFT`/`SCHEDULED_DATE`/fixture fields are left NULL and `REMARK` explains why, in plain language, e.g.:
- `"CT = 0 — excluded"`
- `"Balance Qty <= 0 — fully accounted for"`
- `"QAINSP — manual QA gate, excluded from scheduling"`
- `"CLASS = PN10 — excluded, no routing/fixture setup yet"`
- `"No routing entry for this TASK"`
- `"No fixture/locator match — scheduled via plain routing"` (this one **is** scheduled — see §11 — REMARK is a caveat, not an exclusion, in this case)

An op with genuinely no routing entry is the only case still bridged transparently in precedence (the next schedulable op connects directly to the last schedulable one), matching prior models.

### E.13 — Solver architecture

The **only** engine is the deterministic dispatch simulator described above. CP-SAT is fully retired — not a fallback, not a post-optimizer. Determinism (same inputs ⇒ same schedule) is required for plant-floor auditability.

### E.14 — Output (MCH_SCHEDULE_OUTPUT)

See DDL in **Write tables** below, and `backend/testing/model_e_schedule_output_schema.sql`. Key points:
- **One row per `(PRODUCTION_ORDER, OPERATION_NO, LINE_NO)`.** `LINE_NO` starts at 1 and increments **only** when one order-operation's own balance is genuinely split across more than one machine (e.g. a mid-batch machine breakdown forcing a handoff) — this is rare, not the normal case.
- `START_TIMESTAMP`/`END_TIMESTAMP` are the row's real, continuous-time bounds (can span shifts/days). `SCHEDULED_DATE`/`SHIFT` are derived display fields = the calendar date / shift window containing `END_TIMESTAMP` (the Gantt shows completion, not start — unchanged convention from Model D).
- `BATCH_KEY` stores the literal `SIZE_INCH~CLASS~MOC~DESIGN` string. `FIXTURE_ID`/`LOCATOR_ID` are separate columns holding the `DEVICE_NAME` actually assigned (NULL when §11's no-match fallback applies, or when the row is unscheduled).
- `ORDER_COMPLETION_DATE`/`ORDER_COMPLETION_SHIFT`: taken from the order's own last-operation row (highest `OPERATION_NO` among its scheduled rows) and **stamped identically across every row of that `PRODUCTION_ORDER`**, so any row for that order shows its eventual completion without a join.
- `REMARK`: see §12.
- **No historical retention** — each `/schedule/generate` run deletes all rows and writes fresh (unchanged from prior models).

---

## Engine 2 — Recommendation Engine (engine2_recommender.py)

Unchanged in concept from prior models — no Model E changes planned as of this writing.

### Trigger
Planner selects one or more orders to elevate via the Order Board UI.

### Process
1. Read current `MCH_SCHEDULE_OUTPUT` → old_completion_date per order (baseline snapshot; use `ORDER_COMPLETION_DATE`).
2. Force the elevated order(s) to the top of the priority queue (§2) — highest priority, ahead of all CDD-based ordering.
3. Re-run Engine 1 (the Model E dispatch simulator).
4. For every other order in the new schedule: `slip_days = new_completion_date − old_completion_date`, `slack = CDD − new_completion_date`.
5. Classify risk: **SAFE** (`slack > risk_safe_threshold_days`), **AT_RISK** (`0 ≤ slack ≤ risk_safe_threshold_days`), **BREACH** (`slack < 0`).
6. Write all results to `MCH_SIM_RESULTS`.
7. Return top-5 most impacted orders (highest slip_days).

Since the dispatch simulator is deterministic and fast (not an iterative solver with a time budget), `engine2_time_limit_seconds` is retired along with the rest of the CP-SAT-era config (see Configuration section).

---

## Write tables (created + owned by this application — the ONLY two objects it writes)

### schedule_output → MCH_SCHEDULE_OUTPUT (Engine 1 writes) — Model E shape
```sql
CREATE TABLE MCH_SCHEDULE_OUTPUT (
    RUN_ID                  VARCHAR2(36)   NOT NULL,   -- audit only; NOT part of the PK (table holds one run at a time)
    PRODUCTION_ORDER        VARCHAR2(9)    NOT NULL,
    OPERATION_NO            NUMBER         NOT NULL,   -- = MCH_WIP.OPERATION
    LINE_NO                 NUMBER         DEFAULT 1 NOT NULL,  -- increments only on a machine-handoff split
    TASK                    VARCHAR2(51),
    WORK_CENTER             VARCHAR2(49),               -- NULL on unscheduled/REMARK rows
    SHIFT                   VARCHAR2(10),               -- NULL on unscheduled/REMARK rows
    SCHEDULED_DATE          DATE,                       -- NULL on unscheduled/REMARK rows
    BALANCE_QTY             NUMBER         NOT NULL,
    GENERATED_AT            TIMESTAMP(6)   NOT NULL,
    BATCH_KEY               VARCHAR2(100),              -- SIZE_INCH~CLASS~MOC~DESIGN
    IS_SAFETY_STOCK         CHAR(1)        DEFAULT 'N',
    FIXTURE_ID              VARCHAR2(40),
    LOCATOR_ID              VARCHAR2(40),
    START_TIMESTAMP         TIMESTAMP(6),
    END_TIMESTAMP           TIMESTAMP(6),
    REMARK                  VARCHAR2(200),              -- human-readable miss/caveat reason
    ORDER_COMPLETION_DATE   DATE,
    ORDER_COMPLETION_SHIFT  VARCHAR2(10),
    CONSTRAINT PK_MCH_SCHEDULE_OUTPUT
        PRIMARY KEY (PRODUCTION_ORDER, OPERATION_NO, LINE_NO)
);
```
Full DDL with indexes: `backend/testing/model_e_schedule_output_schema.sql`. **This has already been applied manually in Oracle SQL Developer** — `models.py::ScheduleOutputRow`, `db.py`, `pipeline.py`, and the FastAPI response shapes in `main.py` still need to be migrated to match (tracked as implementation work, not yet done — the old shape had `START_OFFSET_MIN`/`END_OFFSET_MIN` and a different PK, both retired).

**No historical retention.** Each `/schedule/generate` run DELETEs all existing rows before writing the new schedule.

### sim_results → MCH_SIM_RESULTS (Engine 2 writes) — unchanged
```sql
CREATE TABLE MCH_SIM_RESULTS (
    SIM_ID               VARCHAR2(36)  NOT NULL,
    ELEVATED_ORDER       VARCHAR2(200) NOT NULL,
    PRODUCTION_ORDER     VARCHAR2(9)   NOT NULL,
    OLD_COMPLETION_DATE  DATE,
    NEW_COMPLETION_DATE  DATE,
    SLIP_DAYS            NUMBER,
    RISK_FLAG            VARCHAR2(10)  NOT NULL,
    CREATED_AT           TIMESTAMP     NOT NULL,
    CONSTRAINT PK_MCH_SIM_RESULTS PRIMARY KEY (SIM_ID, PRODUCTION_ORDER)
);
```

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

Credentials stored in `.env` (never committed to git). Local dev DSN: `localhost:1521/XEPDB1` (Oracle XE via Docker).

---

## FastAPI backend

Framework: FastAPI | ASGI server: Uvicorn | Port: 8000

### Endpoints (9 total — unchanged surface, response shapes need Model E migration)
- POST /schedule/generate — triggers Engine 1, writes to MCH_SCHEDULE_OUTPUT
- GET  /schedule/current — reads latest MCH_SCHEDULE_OUTPUT, returns Gantt data (needs to drop offset fields, add REMARK/ORDER_COMPLETION_DATE/ORDER_COMPLETION_SHIFT/LINE_NO)
- POST /priority/simulate — triggers Engine 2 with payload `{orders: [...]}`, writes to MCH_SIM_RESULTS
- GET  /orders/wip — returns active MCH_WIP rows (CT > 0, routable, balance_qty > 0)
- GET  /machines/capacity — returns resolved machine availability for next N days
- POST /data/refresh — re-fetches all 6 read-only ERP views from Oracle
- GET  /machines/daily — returns MCH_MACHINE_AVAILABILITY_BY_DATE rows for a date range (read-only display)
- GET  /config — returns current config.json contents
- PUT  /config — updates config.json

There is **no** `PUT /machines/daily` — read-only ERP view, never written by this application.

CORS enabled for React frontend origin. Schedule regeneration: manual trigger only in v1 (no background timer).

---

## React frontend

Framework: React 18 + Vite | Styling: TailwindCSS | Charts: Recharts | Drag-and-drop: React DnD

### Four main views
1. **Schedule view** — Gantt chart per machine. Needs migration to render against `START_TIMESTAMP`/`END_TIMESTAMP` directly (continuous time) instead of the retired shift-offset fields; should surface `REMARK` and `ORDER_COMPLETION_DATE`/`SHIFT`.
2. **Order board** — WIP order cards, drag-to-reprioritise, trigger Engine 2 simulation.
3. **Impact analyser** — MCH_SIM_RESULTS: risk scores, slip days, SAFE/AT_RISK/BREACH badges.
4. **Machine availability & settings** — read-only view of MCH_MACHINE_AVAILABILITY_BY_DATE + settings panel for the Model E config.json shape.

### API proxy
Development: `vite.config.js` proxies `/api` → `http://localhost:8000`. Production: Express.js handles the `/api` → Uvicorn proxy.

---

## Full stack — development on Windows 11 Enterprise

```
Terminal 1:  cd backend && uvicorn main:app --reload --port 8000
Terminal 2:  cd frontend && npm run dev   (Vite dev server on port 5173)
```

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
│   ├── main.py                   ← FastAPI app + all 9 endpoints
│   ├── db.py                     ← Oracle connection pool + read/write helpers
│   ├── preprocess.py             ← preprocessing pipeline (Model C-era; needs Model E migration)
│   ├── engine1_scheduler.py      ← scheduling engine (currently Model C CP-SAT; needs Model E dispatch-simulator rewrite)
│   ├── batch_grouping.py         ← Model C batch-key grouping (needs Model E SIZE~CLASS~MOC~DESIGN migration)
│   ├── engine2_recommender.py    ← priority simulation + risk scoring
│   ├── pipeline.py / pipeline2.py← orchestration for Engine 1 / Engine 2 runs
│   ├── models.py                 ← Pydantic request/response schemas (needs Model E migration)
│   ├── config.json               ← runtime config (needs Model E migration — see Configuration section)
│   ├── testing/
│   │   └── model_e_schedule_output_schema.sql   ← Model E DDL (applied)
│   └── requirements.txt
└── frontend/
    ├── package.json
    ├── vite.config.js
    ├── tailwind.config.js
    └── src/
        ├── App.jsx
        ├── views/            (ScheduleView, OrderBoard, ImpactAnalyser, MachineAvailability)
        ├── components/       (GanttChart, OrderCard, RiskBadge, ...)
        └── api/client.js
```

---

## Phase execution status

| Phase | Deliverable | Status |
|-------|-------------|--------|
| 0 — Foundation | Oracle connection, original write-table DDL, test_connection.py, .env | ✅ Done |
| 1 — Engine 1 (Model C) | preprocess.py, engine1_scheduler.py (CP-SAT), write MCH_SCHEDULE_OUTPUT | ✅ Done |
| 2 — Engine 2 | engine2_recommender.py, risk classifier, write MCH_SIM_RESULTS | ✅ Done |
| 3 — FastAPI | All 9 endpoints, config GET/PUT, Pydantic schemas, CORS, error handling | ✅ Done |
| 4 — React UI | All 4 views, config settings panel, drag-drop Order Board | ✅ Done, verified end-to-end (see `PHASE4_SUMMARY.md`) |
| E-1 — Model E schema | Rebuild `MCH_SCHEDULE_OUTPUT` (new PK, REMARK, ORDER_COMPLETION_*, drop offset columns) | ✅ Done (`backend/testing/model_e_schedule_output_schema.sql`, applied manually) |
| E-2 — Model E data layer | Loaders/validators for MCH_ITEMWISE_FIXTURE_LOCATOR / MCH_FIXTURE_LOCATOR; batch/fixture key migrated to SIZE~CLASS~MOC~DESIGN | ⏳ Partial (loaders exist from Model D prep in `db.py`; key still needs MOC added, needs revalidation) |
| E-3 — Dispatch simulator core | Priority-walk batching (§4), continuous timeline + cooling (§7), machine selection (§5), pool constraint (§9), safety-stock policy (§10) | ❌ Not started |
| E-4 — REMARK & completion surfacing | §12 REMARK taxonomy, ORDER_COMPLETION_DATE/SHIFT population | ❌ Not started |
| E-5 — API/UI migration | `models.py`, `main.py` response shapes, `GanttChart.jsx` continuous-time rendering, config.json migration | ❌ Not started |
| E-6 — Validation | Full-run audit: precedence intact, pool never exceeded, per-order CDD attainment, replicate the two worked batching examples exactly | ❌ Not started |
| 5 — Deploy | NSSM Windows services (Uvicorn + Express), end-to-end integration test | ❌ Not started |

---

## Key constraints and rules — always follow these

- All tools free and open-source. No paid services. Oracle thin mode only.
- ALL time values are in minutes throughout the entire stack. Never convert units.
- Balance Qty = QUANTITY_ORDERED − QUANTITY_COMPLETED − QUANTITY_REJECTED. Skip if ≤ 0 (with REMARK).
- CYCLE_TIME = 0: skip entirely (with REMARK).
- QA Inspection (WORK_CENTER contains 'QAINSP'): exclude from scheduling, show in UI (with REMARK).
- CLASS = 'PN10': excluded (with REMARK) — no established fixture/routing setup yet.
- Rework (R002): no special logic — scheduled exactly like any other operation.
- v1 scheduling scope: only TASK codes with a routing entry in `MCH_MACHINE_PRIORITY` (currently VB02–VB09). Routing-extensible.
- Shift names always normalized to lowercase ("first", "second", "third") on read.
- **Batch/fixture key = `SIZE_INCH~CLASS~MOC~DESIGN`, everywhere, built from typed columns — never parsed from `ITEM_CATEGORY`** (see Data sources warning).
- **`SETUP_TIME` is ignored everywhere** — replaced by fixture/locator change-time economics (Model E §3).
- **No machine capacity cap, anywhere.** Machines have infinite capacity; `AVAILABLE_MINS` is a working-window/rate, not a budget. Timing is governed by fixture/locator/machine availability, not minutes-per-slot.
- **20-minute cooling** (`config.cooling_minutes`) is mandatory after every batch on every machine, before its next batch — regardless of shift/day boundaries.
- **`MACHINE_PRIORITY` is a soft tiebreaker only** — earliest-free machine wins; priority breaks ties among machines free at the same instant.
- **Fixture/locator pool (`MCH_FIXTURE_LOCATOR.QUANTITY`) is a hard, plant-wide concurrency cap** — never exceeded, ever, for any device.
- **Committed orders (CDD not null) are never delayed** — not for safety stock (§10, enforced by a full speculative simulation, not a proxy check). This is absolute. (The 60-day consolidation window, §6, is now a hard rule with no override at all — it never risks a committed delay because there's no path for it to fire in the first place.)
- **Every eligible WIP order-operation always produces a row in `MCH_SCHEDULE_OUTPUT`**, scheduled or not, with `REMARK` explaining any miss.
- **CP-SAT is fully retired.** Engine 1 is a deterministic dispatch simulator — no fallback to CP-SAT, no post-optimization pass.
- `capacity_resolved` (baseline + daily-override merge): still computed in memory only, never stored — now used purely as an availability window/rate, not a bucket capacity.
- Schedule regeneration: manual trigger only in v1. No background timer.
- **No historical retention:** each `/schedule/generate` run DELETEs all `MCH_SCHEDULE_OUTPUT` rows before writing the fresh schedule.
- Writes: only MCH_SCHEDULE_OUTPUT (Engine 1) and MCH_SIM_RESULTS (Engine 2) are written by this application. All six ERP sources are read-only views.
- config.json: all runtime settings stored here (Model E shape — see Configuration section). Never stored in Oracle tables.
- IIS not used. Express.js on Node.js handles all production serving. Both Uvicorn (port 8000) and Express (port 80) run as permanent NSSM Windows Services.
