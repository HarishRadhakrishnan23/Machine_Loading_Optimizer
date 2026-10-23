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

The project went through five scheduling-engine designs before arriving at **Model F**, the current and final design:

- **Model B** (retired, never shipped): CP-SAT, one machine locked per whole operation (`assign[t,m]`) plus a hard contiguity constraint forcing every slot between start and end to be occupied. A single closed slot anywhere in that span made the model INFEASIBLE even at ~5% utilisation. Deleted for this reason.
- **Model C** (shipped, Phases 0–4 complete — see `PHASE2_SUMMARY.md`–`PHASE4_SUMMARY.md`): CP-SAT allocating integer piece-quantities into discrete `(machine, date, shift)` capacity buckets on a global slot index, with `SETUP_TIME`-based setup economics and a guaranteed greedy fallback. This is what is actually running in the codebase today (`engine1_scheduler.py`), pending the Model E rebuild described in this document.
- **Model D** (target spec, superseded before full implementation — only schema/audit groundwork landed): introduced the core idea Model E keeps — a continuous-time dispatch simulator with no machine-minute cap, driven by a finite plant-wide fixture/locator pool (`MCH_ITEMWISE_FIXTURE_LOCATOR`, `MCH_FIXTURE_LOCATOR`) instead of `SETUP_TIME`. Its batch/fixture key was `SIZE~CLASS~DESIGN` (MOC excluded). Superseded by Model E before its dispatch simulator, batching, or pool-constraint phases were built.
- **Model E** (shipped, superseded by Model F): keeps Model D's continuous-time/fixture-pool architecture, but **includes MOC in the batch/fixture key everywhere** (`SIZE~CLASS~MOC~DESIGN`), replaces Model D's abstract "fixture runs, locator-ordered" batching with an explicit, fully-specified priority-walk batching algorithm, adds a `REMARK` column so every WIP row is auditable (scheduled or not), surfaces each order's overall completion date/shift directly on every one of its rows, and fully retires CP-SAT. Live-data validation (E-6) surfaced two real limits in this design, both of which Model F exists to fix: (1) a fixture run's start time was pinned to whichever single member became ready *last*, leaving the machine idle the whole time even when every other member had been ready for days (fixed mid-Model-E as a point patch, but the underlying cause — placement driven by depth-ordered "rounds" rather than true event time — remained); (2) `MCH_MACHINE_PRIORITY` and `MCH_ITEMWISE_FIXTURE_LOCATOR` are two separately-maintained tables keyed on the same combo+task+machine grain, and were found to disagree on which machine is valid for 258 real combos — a structural hazard a single merged source removes entirely.
- **Model F** (current, final): same continuous-time/fixture-pool/REMARK/CDD-priority foundation as Model E — §E.1-§E.4, §E.7-§E.9, §E.11-§E.14 below are **unchanged** and keep their original `§E.n` numbering for stability (cross-referenced throughout this document). What Model F actually changes: a single unified machine+fixture+locator source of truth (`MCH_MACHINE_FNL_MASTER`, §E.15) replacing the two-table join; a hard-exclusion policy for fixture-required tasks with no master-table row (§E.15); one universal "does this disturb a near-term order's CDD" admission check replacing the two narrower mechanisms Model E had (§E.16); within-run reordering for readiness, not a plant-wide event-driven redesign (§E.17 — see its own note on why the originally-planned full event-driven loop was built, measured, and reverted); partial-quantity (lot-streaming) handoff between operations instead of whole-batch-only handoff (§E.18); and a fully named determinism contract (§E.19) covering every tie-break rule the engine relies on. Machine selection (§E.5) is also revised under Model F — see that section.

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

**Seven** upstream data sources are **read-only** (six MCH_* ERP views owned by the ERP team, plus one cross-schema holiday calendar table — see below; this application only SELECTs from all seven). The application writes to exactly **two** tables, which it creates and owns: MCH_SCHEDULE_OUTPUT (Engine 1) and MCH_SIM_RESULTS (Engine 2).

### Table / view name map (ERP name ⇄ legacy name used in this doc)
| Role in this doc | Oracle object (real name)         | Kind  | Access |
|------------------|-----------------------------------|-------|--------|
| machine_master   | MCH_MACHINE_AVAILABILITY          | view  | read-only |
| machine_daily    | MCH_MACHINE_AVAILABILITY_BY_DATE  | view  | read-only |
| routing_master   | MCH_MACHINE_PRIORITY              | view  | read-only |
| wip_orders       | MCH_WIP                           | view  | read-only |
| fixture_locator_master    | MCH_ITEMWISE_FIXTURE_LOCATOR | view | read-only |
| fixture_locator_inventory | MCH_FIXTURE_LOCATOR          | view | read-only |
| holiday_calendar | L750.TCCCP019 (cross-schema)       | table | read-only |
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
- **Two real conventions in live data, confirmed and handled (`dispatch_pool.parse_devices`):**
  - `LOCATOR` (never `FIXTURE`) can be a `"+"`-joined compound of two physical device names (e.g. `"AT18/212-F02+AT18/212-LC2"`) — meaning the operation needs **both** devices held simultaneously, each checked against its own `QUANTITY` cap.
  - Either `FIXTURE` or `LOCATOR` can be the literal string `"NA"` — meaning **no physical device is needed at all** for that slot (confirmed on ~1200 rows, always FIXTURE and LOCATOR together, e.g. VB03 Weld Overlay). Filtered out before any inventory lookup — never treated as a device name, never charged against any `QUANTITY`.
  - A `FIXTURE`/`LOCATOR` device genuinely absent from `MCH_FIXTURE_LOCATOR`'s inventory (not `"NA"`, just never entered) makes that operation `EXCLUDED_UNKNOWN_FIXTURE_DEVICE` (REMARK explains it) rather than guessing a quantity — confirmed as the right call; a small, bounded gap in live data (7 devices) that the ERP owner is backfilling.

### fixture_locator_inventory → MCH_FIXTURE_LOCATOR (read-only view) — Model E
**Exact Oracle columns:** DEVICE_NAME, DEVICE_TYPE, DESCRIPTION, QUANTITY, USER_ID, USER_DATE
- Physical shop-floor inventory: `DEVICE_NAME` = a Fixture ID or Locator ID, `DEVICE_TYPE` = `'F'` (fixture) or `'L'` (locator), `QUANTITY` = how many physically exist.
- `QUANTITY` is a **hard, plant-wide concurrency cap** — see Model E §9 (pool constraint).

### holiday_calendar → L750.TCCCP019 (read-only table, cross-schema) — Model E
**Exact Oracle columns:** CALENDAR_CODE, DATE_1, DESCRIPTION
- The confirmed company holiday calendar — **not** one of the other six MCH_* ERP views; a separate, pre-existing cross-schema table (`L750.TCCCP019`), read-only to this application exactly like the rest.
- **On every date listed here, `AVAILABLE_MINS` is forced to `0` for all three shifts, for every machine** — a plant-wide closure, taking precedence over both the `MCH_MACHINE_AVAILABILITY_BY_DATE` override and the `MCH_MACHINE_AVAILABILITY` baseline (`model_e_data.ResolvedAvailability` checks holiday first, unconditionally).
- The table also carries old/historical entries (2008, 2013, 2014, …) alongside forward-looking ones — harmless to read in full; a date that never falls inside a schedule's horizon simply never gets looked up. No filtering by `CALENDAR_CODE` is applied (every row observed in live data is `'VEL'`); revisit if a second calendar code is ever introduced for a different purpose.

---

## Configuration — backend/config.json (Model F shape)

Runtime parameters editable via UI, stored as JSON at `backend/config.json`, never in Oracle.

```json
{
  "batch_consolidation_window_days": 60,
  "cooling_minutes": 20,
  "heavy_operations": ["VB03", "VB04", "VB05", "VB06"],
  "fixture_required_tasks": ["VB02", "VB03", "VB04", "VB05", "VB06", "VB07"],
  "protect_committed_over_safety": true,
  "planned_order_start_buffer_days": 1,
  "risk_safe_threshold_days": 5,
  "dev_max_orders": 0,
  "admission_check_max_horizon_days": 365
}
```

- `batch_consolidation_window_days` (§E.6): an order is eligible to be pulled into a fixture-run consolidation by ordinary (non-admission-check) means only if its CDD is within this many days of **today** (run date). Default 60 (~2 months). Also the boundary §E.16's universal admission check protects — any order with CDD inside this window is in the protected "must not newly breach" set. **Hard rule, no override for in-window orders.**
- `cooling_minutes` (§E.7): fixed machine dead-time after every batch, before that machine's next batch. Default 20.
- `heavy_operations` (§E.10): TASK codes where mixed committed/safety-stock batches get the committed-first policy.
- `fixture_required_tasks` (§E.15, new under Model F): TASK codes for which a `MCH_MACHINE_FNL_MASTER` row is mandatory — a missing row for one of these is a hard exclusion (`EXCLUDED_NO_FNL_MASTER_ENTRY`), not the permissive plain-routing fallback every other task still gets. Default `["VB02","VB03","VB04","VB05","VB06","VB07"]`.
- `protect_committed_over_safety`: must always be `true` — no order within `batch_consolidation_window_days` may ever slip past its CDD to accommodate safety stock or a §E.16-admitted beyond-window candidate. Does not formally bound slip for committed orders beyond the window — see §E.16's explicit scope note.
- `planned_order_start_buffer_days` (§E.1/§E.8): days added to a Planned order's `PRODUCTION_START_DATE_AND_TIME` to get its earliest-start date. Default 1.
- `risk_safe_threshold_days`: Engine 2 SAFE/AT_RISK/BREACH threshold (unchanged from prior models).
- `dev_max_orders`: dev knob, limit scheduling to top-N most urgent orders for fast iteration. 0 = full dataset (production).
- `admission_check_max_horizon_days` (§E.16, new under Model F): a sane upper bound on how far beyond `batch_consolidation_window_days` a candidate's own CDD may be and still be offered to the admission check at all — purely a compute-scope guard (keeps the candidate search space bounded), never a slip allowance; the admission check itself always requires zero new breach regardless of where in this range the candidate's CDD falls. Default 365 (effectively "consider any committed order, however far out"); lower it if admission-check volume needs trimming.

**Retired under Model F, replaced by §E.16's single mechanism:** `allow_soft_consolidation_beyond_window`, `soft_consolidation_max_extra_days` (Model E's soft-launch extension — see Model history).

**Retired with Model C/CP-SAT (no longer meaningful under a deterministic dispatch simulator):** `batch_bonus_months`, `batch_bonus_value`, `downstream_queue_bonus_value`, `ageing_normalization_days`, `machine_priority_epsilon`, `engine2_time_limit_seconds`, `scheduling_horizon_safety_factor`, `scheduling_horizon_buffer_days`, `solver_workers`, `solver_time_limit_seconds`, `setup_penalty_weight` — these existed to shape a CP-SAT objective function or size a CP-SAT horizon; Model F has neither.

FastAPI exposes `GET /config` and `PUT /config` to read and update this file (endpoints unchanged; the `Config` schema they serve needs the migration above).

---

## Engine 1 — Model F (current, final design)

Engine 1 is a **deterministic, continuous-time discrete-event dispatch simulator** — not a CP-SAT capacity model, and not an interval/slot solver of any kind. CP-SAT is fully retired; there is no fallback to it and no post-optimization pass. Same inputs always produce the same schedule (essential for a $50M line's auditability).

Section numbers below are inherited from Model E and kept stable (`§E.n`, not renumbered to `§F.n`) because they're cross-referenced throughout this document. §E.1-§E.4, §E.7-§E.9 and §E.11-§E.14 are Model E's original text, unchanged under Model F. §E.5 is revised below. §E.15-§E.19 are new, Model-F-only sections.

### E.1 — Order types (ORDER_STATUS)

- **"Active"** — foundry material is already on the shop floor. Plan normally: check CDD, batch per §5, assign to machines per §7. No extra constraint.
- **"Planned"** — material for this order arrives at `PRODUCTION_START_DATE_AND_TIME`. The order's **earliest-start** is `PRODUCTION_START_DATE_AND_TIME + planned_order_start_buffer_days` (default: +1 calendar day), first shift of that day onward. A Planned order is **excluded from batching entirely** until its earliest-start passes — it cannot join a fixture run that starts before that date. If every machine capable of its first operation is fully busy on that exact day, the engine **never interrupts an already-running operation** to force the order in — it simply queues normally and starts as soon as a capable machine frees up at/after its earliest-start.
- Every order — Active or Planned — is targeted to complete **on or before its CDD**. Downstream operations follow ordinary precedence once the first operation starts.

### E.2 — Priority ordering

**Lower CDD ⇒ higher priority.** Ties broken by ageing (older `PRODUCTION_START_DATE_AND_TIME` first). **Safety-stock orders (CDD = NULL) are always lowest priority**, scheduled last / as backfill (subject to §10). This is a simple sort key for the dispatch simulator — there is no CP-SAT-style weighted urgency score (that formula was specific to Model C's objective function and does not apply here).

### E.3 — Fixture & Locator model (replaces SETUP_TIME)

Every `SIZE_INCH~CLASS~MOC~DESIGN` has one FIXTURE and one LOCATOR per operation, constant across machines (sourced from `MCH_MACHINE_FNL_MASTER` under Model F — see §E.15 — `MCH_ITEMWISE_FIXTURE_LOCATOR` under Model E). `SETUP_TIME` is ignored entirely either way.

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

### E.5 — Machine selection (Model F: priority-first, not earliest-free-first)

For a formed fixture run: intersect the capable machines (per `MCH_MACHINE_FNL_MASTER`, §E.15, evaluated for every member) across all members of the run. **Model F reverses Model E's rule here.** Model E picked whichever capable machine was free earliest, using `PRIORITY` only as a tiebreak for exact simultaneous free times. Model F instead walks capable machines in ascending `PRIORITY` order (1 = most preferred) and takes the **first one that is currently available** (`machine_free_at` at or before the run's ready time) — a lower-priority-number machine always wins over a higher-priority-number one as long as it's actually free, even if the higher-priority machine became idle earlier. If no candidate is currently available, the run waits for the earliest one to free up (plain availability, no priority comparison possible when nothing is free yet); the moment one or more become free, the same priority-first scan runs again among whoever is free at that instant. This is also §E.19's Rule 2 (machine selection) in the determinism contract — see there for the exact decision procedure.

If no single machine is capable of every member, keep the run intact on one common machine and drop the incapable member(s) — they schedule in their own run (unchanged from Model E).

### E.6 — Consolidation window (60 days) — HARD RULE, no override

An order is eligible to be pulled into a fixture-run consolidation (§4 step 2) **iff its CDD is within `batch_consolidation_window_days` (default 60) of today** (run date).

**This window governs committed-vs-committed consolidation only — it does not gate safety-stock orders.** Safety stock (CDD = NULL) always sorts last in priority (§2) and can therefore never *anchor* a run ahead of a committed order — it has no CDD to "pull forward" and no way to make the window's concern (needlessly consolidating a near-term committed order with something due far out) happen. So a safety-stock order riding along *after* an already-forming, committed-anchored run is exempt from this window check entirely; whether it actually gets appended is decided solely by §E.10's safety-stock policy below, never by §E.6. (An order's own committed CDD, if it has one, is still fully subject to the window regardless of what else is in the run.)

**This is a hard cutoff, not a soft preference — there is no escape hatch.** An order beyond the window is simply never eligible for consolidation, regardless of machine idleness, downstream impact, or anything else. (An earlier draft of this rule had a "delays no committed order" override; that override was deliberately removed — §6 is now evaluated purely on the window number, nothing else.) `batch_consolidation_window_days` remains a plain config value — changing it (e.g. 60 → 90) takes effect immediately with no code change, since the cutoff is read from config on every call, never hardcoded.

**Model E soft-launch extension, superseded by Model F's §E.16 universal admission check.** Model E had an opt-in `allow_soft_consolidation_beyond_window`/`soft_consolidation_max_extra_days` layer (a beyond-window committed order riding an in-window run for free, via a dedicated fork/compare mechanism, `dispatch_orchestrator._decide_soft_merge`) that existed alongside §E.10's separate, differently-scoped safety-stock mechanism. Model F deletes both as distinct mechanisms and replaces them with **one** general admission check — see §E.16 — used for every beyond-window or safety-stock candidate, everywhere, not just these two narrow historical cases. §E.6's hard window rule itself (an order's own CDD must be within the window to anchor or be batched by ordinary means) is unchanged; §E.16 is what now governs the exception path.

### E.7 — Continuous timeline, no capacity cap

- Time is continuous wall-clock minutes. There is **no discrete shift-slot lattice** and **no per-machine capacity cap** — every machine can take infinite work; what governs timing is fixture/locator/machine availability, not a minute budget.
- Machining only progresses inside a shift's `AVAILABLE_MINS` window (from `MCH_MACHINE_AVAILABILITY`, overridden by `MCH_MACHINE_AVAILABILITY_BY_DATE` when present; no row ⇒ fully available). If a batch's remaining work exceeds the time left in the current shift, it pauses and resumes in that machine's next working shift/day.
- **`cooling_minutes` (default 20)** fixed dead-time after every batch, on every machine, before that machine's next batch — regardless of whether the next available instant falls in the same shift, the next shift, or the next working day. A machine is never left idle for a placeable task beyond this cooling window.
- **Every machine is always considered.** A machine with no row in `MCH_MACHINE_AVAILABILITY_BY_DATE` is fully available for its `MCH_MACHINE_AVAILABILITY` baseline. Breakdowns/maintenance come only from `AVAILABLE_MINS = 0` rows in the by-date view — plus, independently, any date listed in the holiday_calendar table closes every machine's every shift outright (see Data sources).
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

- **Heavy operations:** schedule the committed pieces first. Append the safety-stock pieces immediately after (free — same fixture/locator already mounted) **only if** doing so passes §E.16's universal admission check. If it would disturb a near-term order, release the fixture/locator and send the safety-stock pieces to the **back of the global priority queue** — picked up later whenever their fixture/locator naturally comes free again.
- **Light operations** (everything not in `heavy_operations`): committed and safety-stock pieces batch together normally, no special ordering — the marginal cost of including safety-stock is only `LOAD_UNLOAD_TIME × qty`.
- **`protect_committed_over_safety = true` is absolute for orders inside the window** — no order within `batch_consolidation_window_days` is ever delayed to build safety stock, under any circumstance. (§E.16 defines exactly which orders this protection covers under Model F.)

**Model E's bespoke fork/compare implementation for this decision is superseded by §E.16** — it's the same mechanism now, generalized and shared with every other beyond-window/safety-stock admission decision, not a separate one maintained here. See §E.16 for the actual check, and §E.16's own section for the parallelism requirement (separate OS processes, never threads, same reasoning as Model E had here).

### E.11 — Fixture/locator lookup: optional, not gating (Model E) / conditional under Model F, see §E.15

Model E: if `(SIZE_INCH, CLASS, MOC, DESIGN, TASK)` has **no** row in `MCH_ITEMWISE_FIXTURE_LOCATOR` for any candidate machine, the operation is **still scheduled** — via plain routing + `Σ CYCLE_TIME` only (no fixture/locator effect, no fixture-run membership, no pool involvement). `FIXTURE_ID`/`LOCATOR_ID` are left NULL on its output row, and `REMARK` records `"No fixture/locator match - scheduled via plain routing"`.

**Model F changes this for a specific task list** — §E.15 makes the no-match case a hard exclusion (not a permissive fallback) for `fixture_required_tasks`. This section's plain-routing fallback still applies unchanged for every other task.

### E.12 — Scheduling scope & the REMARK column

An operation is **scheduled** (consumes real machine/fixture/locator time) iff **all** hold: `CYCLE_TIME > 0`, `balance_qty > 0`, `WORK_CENTER` does not contain `'QAINSP'`, `CLASS != 'PN10'`, and it has a routing entry in `MCH_MACHINE_PRIORITY`.

**Every eligible order-operation from MCH_WIP now always produces a row in `MCH_SCHEDULE_OUTPUT`, scheduled or not.** When it is not scheduled, `WORK_CENTER`/`SHIFT`/`SCHEDULED_DATE`/fixture fields are left NULL and `REMARK` explains why, in plain language, e.g.:
- `"CT = 0 - excluded"`
- `"Balance Qty <= 0 - fully accounted for"`
- `"QAINSP - manual QA gate, excluded from scheduling"`
- `"CLASS = PN10 - excluded, no routing/fixture setup yet"`
- `"No routing entry for this TASK"`
- `"No fixture/locator match - scheduled via plain routing"` (this one **is** scheduled — see §11 — REMARK is a caveat, not an exclusion, in this case)

(REMARK strings are always plain ASCII, never an em dash or other non-Latin-1 character — the live Oracle schema's REMARK column sits under a `WE8ISO8859P1` characterset, which silently corrupts anything it can't represent on insert rather than raising. This bit a real em-dash once; see `dispatch_scope.py`'s own note.)

An op with genuinely no routing entry is the only case still bridged transparently in precedence (the next schedulable op connects directly to the last schedulable one), matching prior models.

**REMARK also explains every SCHEDULED row's placement, not just an excluded or no-fixture-caveat one** — which machine it landed on, why (a fresh fixture run vs. riding an existing mount for free, a new locator sub-batch, a machine-selection dropout, a deferred-then-requeued safety-stock member, or a §E.6 soft-consolidation merge), and whether other capable machines existed. This is the mechanism for answering "why is machine X idle for a shift/day/month" — read what every OTHER order's REMARK says instead of guessing; a machine listed as a capable-but-not-chosen candidate on other orders explains its own idle time. Kept short (no machine-name lists, just counts) since REMARK is VARCHAR2(200) and device/machine names can be long; `dispatch_writer.py` also defensively truncates as a last resort. See `dispatch_engine._placement_remark` and the soft-merge/dropout/deferred tagging in `_place_fixture_run`/`dispatch_orchestrator.py`.

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

### E.15 — Unified machine/fixture/locator master (`MCH_MACHINE_FNL_MASTER`)

Model F replaces the two separately-maintained `MCH_MACHINE_PRIORITY` (routing) and `MCH_ITEMWISE_FIXTURE_LOCATOR` (fixture/locator timing) views with **one** table: `MCH_MACHINE_FNL_MASTER`, grain `(SIZE_INCH, CLASS, MOC, DESIGN, TASK, WORK_CENTER)` — one row per machine capable of one combo+task, carrying both its routing `PRIORITY` and its fixture/locator identity + timing on the same row. This is a correctness fix, not just a convenience merge: under the two-table design, routing and fixture data could (and did — 258 confirmed live combos) disagree on which machine is valid for a combo+task, because nothing enforced the two tables staying in sync. One row per machine makes that disagreement structurally impossible.

Columns: `COMPANY, SIZE_INCH, CLASS, MOC, DESIGN, COMBINATION, TASK, PRIORITY, WORK_CENTER, FIXTURE_ID, LOCATOR_ID, FIXTURE_CHANGE_TIME, LOCATOR_CHANGE_TIME, LOAD_UNLOAD_TIME`. `FIXTURE_ID`/`LOCATOR_ID`/the three timing columns are nullable — a row with them NULL is a routing-only entry (no fixture/locator needed or matched for this combo+task+machine). **`COMBINATION` is ERP-sourced display text, never parsed for the batch key** — same rule as `ITEM_CATEGORY` always had; the key is always built from the four typed columns directly.

Some operations need no physical fixture/locator but still have a real, machine-specific setup time to account for (e.g. a PTA-welding or serration setting change) — these get a **dummy device row** in `MCH_FIXTURE_LOCATOR`'s inventory (`QUANTITY = 999`, effectively unlimited, e.g. `FD_WELD`/`LD_WELD`/`FD_SERRATION`/`LD_SERRATION`/`LD_DS900`) purely so the existing pool/timing machinery can carry their change-time data without ever actually constraining concurrency. These are real, intentional entries — never treat a 999-quantity device as a data error.

**Fixture-required task policy (hard exclusion, new under Model F):** `fixture_required_tasks` (config, default `["VB02","VB03","VB04","VB05","VB06","VB07"]`) names the TASK codes for which a fixture/locator assignment is mandatory — every live combo for these tasks is expected to have a `MCH_MACHINE_FNL_MASTER` row (even if it's a dummy-device row per above). If a schedulable operation's TASK is in this set and **no row exists** in `MCH_MACHINE_FNL_MASTER` for `(SIZE_INCH, CLASS, MOC, DESIGN, TASK)` on *any* candidate machine, the operation is **excluded** (`EXCLUDED_NO_FNL_MASTER_ENTRY`, REMARK: `"No line matching this SIZE~CLASS~MOC~DESIGN~TASK in MCH_MACHINE_FNL_MASTER"`) rather than falling back to plain routing — a missing row for one of these tasks means the master table itself is incomplete, which is a data problem to surface and fix, not a case to silently route around. For every task **not** in `fixture_required_tasks`, a missing row keeps Model E's original §E.11 behavior: scheduled via plain routing, REMARK is a caveat not an exclusion.

### E.16 — Universal admission check (replaces Model E's two separate mechanisms)

One function answers one question, used everywhere a beyond-window or safety-stock candidate wants to join a cluster or append to a machine's queue: **"does admitting this candidate push any order whose CDD falls within `batch_consolidation_window_days` (the near-term, protected set) past its own CDD, anywhere in the rest of the schedule?"** Admit iff the answer is no. This replaces both of Model E's narrower, separately-implemented mechanisms (§E.10's heavy-op safety-stock resolver, and the retired soft-consolidation-beyond-window extension) with one general-purpose check used at both decision points below:

1. **Cluster admission** — a beyond-window committed order or a safety-stock order wants to join an in-window fixture run (§E.4's cluster) instead of starting its own solo run.
2. **Machine-queue admission** — a cluster (or a partial-quantity slice, §E.18) wants to append to a specific machine's already-live queue rather than open a new one elsewhere.

**Explicit scope limit, by design, not oversight:** this check protects orders within the 60-day window only. It does **not** guarantee zero impact on a committed order whose own CDD is beyond the window — that order's only protection is the normal CDD-priority ordering (§E.2: it's still served ahead of safety stock and ahead of anyone further out), not an explicit no-breach gate. This is a deliberate trade-off: it allows meaningfully larger, more efficient clusters and higher aggregate throughput, at the cost of not formally bounding how far a distant committed order could theoretically slip. `protect_committed_over_safety = true` remains absolute **for the ≤60-day set**; it was never extended to cover every committed order regardless of distance.

**Mechanism — identical to Model E's fork/compare, now used generally:** fork simulation state (`machine_free_at` dict, `DevicePool.clone()`, the remaining order queue), run two continuations — "admit" vs. "don't admit" — to completion, compare which near-term (≤60-day) committed orders breach in each, admit iff admitting introduces no breach beyond what not-admitting already has. No slip tolerance, no partial credit — strict.

**Parallelism:** both continuations are pure CPU-bound Python; this runs across separate OS processes, never threads (CPython's GIL gives threads no real speedup here — same reasoning as Model E's §E.10). `dispatch_parallel.evaluate_branches_in_parallel` opens a fresh `ProcessPoolExecutor` per call today (not yet a persistent pool reused across every admission check within one run) — a real, known cost worth revisiting if admission-check volume on a larger live backlog makes per-call pool startup material; not yet measured as a bottleneck on the backlogs validated so far, so not prioritized ahead of correctness work. Workers only compute answers faster in parallel; results are applied back by the (unchanged, Model-E-style) depth-round loop (§E.17) in fixed order, so parallelism changes nothing about *what* gets decided.

### E.17 — Within-run reordering for readiness (not a plant-wide event-driven redesign)

**A full event-driven rewrite was built, measured on live data, and deliberately reverted — this section documents what actually shipped instead, and why.** The original theory: Model E's orchestrator processed every order's 1st schedulable operation (across the whole plant) before processing *any* order's 2nd — a "depth round" structure — and this artificially delayed machines whose own ready work sat at a later depth. A full priority-queue-of-pools redesign was implemented to fix this (forking the queue state through the admission check, etc.). On live data it produced **identical results** to the simpler depth-round loop, and closer analysis showed why: any two pools genuinely competing for the same machine reduce to the identical greedy choice under both designs (a depth-0 pool always gets first crack — that's precedence, not an artifact), and pools that don't share a machine never affected each other's computed timestamps in either design, since each machine's timeline is independent. The "depth rounds cause artificial idle time" premise didn't hold once the *real* bug (below) was found and fixed. The event-driven code was removed rather than carried as unproven complexity; Model F's orchestrator is Model E's original depth-round flat-list loop, unchanged.

**The real bug, and its fix:** a fixture run's members are placed in a fixed priority order (§E.4's formation order). The original Model E bug pinned the WHOLE run's start to whichever single member became ready *last* (fixed mid-Model-E as a point patch). The *remaining* bug, found via live-data re-investigation of machine `2PMH19` (idle 2026-10-23 → 2026-11-05, 13 days, on a single already-running fixture run): a late-ready member **partway through** the formation-order sequence still blocked every member sequenced *after* it, even ones that had been ready for days, because placement walked the sequence strictly in formation order with no lookahead.

**Fix:** `dispatch_engine._simulate_run_timing` now looks ahead within the SAME run: if the next-in-formation-order member isn't ready yet and the machine would otherwise idle, it runs whichever OTHER not-yet-placed member in the same run IS ready now, instead — the skipped member keeps its place and resumes it the instant it's actually ready, so a later-priority member can temporarily fill a gap but never permanently cut the line. `charge_type` (`fixture_change`/`locator_change`/`none`) is recomputed live from the ACTUAL execution order (same logic `_recompute_charges` already used for dropped/deferred members), never trusted from the original formation-order value, since reordering changes who's actually mounted immediately before whom. Confirmed on live data: this specific 13-day gap closed, live CDD attainment improved (72.8% → 73.4%), and all regression tests pass with zero change elsewhere.

**What's genuinely NOT fixed, and isn't a bug:** a machine can still sit idle when it simply has no OTHER ready, capable work queued — e.g. its next-priority combo's preferred machine is absorbing all current demand for that fixture (§E.9's same-SCMD-one-machine preference), so the backup machine goes unused even while idle. That's a `MCH_MACHINE_FNL_MASTER.PRIORITY` / business-rule outcome, not an engine defect — see live investigation findings on `2PMT16` (confirmed: `3PMC22` is `PRIORITY=1` and continuously busy on the same fixture; `2PMT16` is `PRIORITY=2` and never needed as an overflow target). Addressing it is a planner decision (re-rank `PRIORITY` for specific combos to share load), not a code change.

### E.18 — Partial-quantity (lot-streaming) handoff between operations — ✅ DONE (Part 1 + Part 2)

Model E treated one order's one operation as a single atomic block: the *entire* balance quantity had to finish before that order's next operation could start. Model F allows **partial quantities to flow downstream as they complete**, instead of waiting for the whole batch. Shipped in two parts, both complete and live-validated:

- **Part 1 — shift-boundary quantity slicing.** If a run produces some pieces before others (e.g. 6 of 10 finish in shift 1, the remaining 4 in shift 2), that placement itself splits into multiple `MCH_SCHEDULE_OUTPUT` rows — one per quantity-slice, each its own `LINE_NO`, timestamps and (normally) machine — matching real per-piece completion timing instead of reporting one row that silently spans the gap. Implemented as `_slice_by_shift_raw`/`_slice_by_shift` in `dispatch_engine.py`, wired through `_place_fixture_run` and `_place_no_fixture_op`.
- **Part 2 — downstream consumption of partial completions.** The completed sub-quantity becomes ready for the NEXT operation the instant it finishes — it does not wait for the rest of the batch. **Never interrupt a machine that's currently running.** If a compatible queue for the next operation is already in progress on some capable machine, the completed sub-quantity appends to the *tail* of that queue. If no such queue exists yet, a new run starts with the available sub-quantity, on whichever machine §E.5's priority-first selection picks; the remaining sub-quantity (finishing later) joins that same run's queue when it's ready, exactly like any other queue append. Implemented via a `piece_ready_at: dict[str, list[TimePoint]]` map threaded through the existing call graph (`dispatch_engine.py`, `dispatch_orchestrator.py`) — per order, the most-recently-placed operation's own per-piece completion times, read by the next operation's placement to gate each of its own pieces individually instead of only its overall start. Unconditional, no config flag — degrades to pre-Part-2 single-instant `ready_at` behavior for any op whose upstream never spans a shift boundary (the common case).
  - **Real bug found and fixed during implementation:** the first cut of Part 2 computed a step's/op's overall `start` as `max(machine_free_at, ready_at[order])` — the upstream's scalar, LAST-piece completion — *before* handing control to the per-piece loop. Since that floor already sat at or past every individual piece's own ready time, the loop's `max(cursor, piece_ready_at[k])` comparisons never changed anything, silently defeating early consumption end-to-end. Fixed by flooring on the upstream's *first* piece completion (`piece_ready_at[order][0]`) instead, in both `_place_no_fixture_op` and `_simulate_run_timing` (the latter via a shared `_effective_ready()` helper also used for the within-run readiness scan, §E.17). Caught by a dedicated unit test (`test_lot_streaming_next_op_starts_on_early_pieces_not_whole_batch`, `test_dispatch_orchestrator.py`) before it ever reached live validation.
- **Schema consequence:** `MCH_SCHEDULE_OUTPUT`'s `LINE_NO` (already defined for "this operation's balance was split") becomes the *normal* case for most multi-piece orders under Model F, not the rare machine-breakdown-handoff case it was under Model E. One `(PRODUCTION_ORDER, OPERATION_NO)` can now legitimately produce several rows, one per quantity-slice, each with its own `LINE_NO`, timestamps, and machine. Every downstream reader of this table (Gantt, exports, planners) must expect this as routine, not exceptional — **this is also why `GanttChart.jsx` migration (E-5/step 3 below) must explicitly handle multiple `LINE_NO` rows per operation, not just the continuous-timestamp rendering it already needed.**
- **Validation consequence:** `e6_validate_real_data.py`'s precedence check had to be loosened to match — a downstream op legitimately starting before its upstream op's overall `end` is now expected (that's the whole point), so the check now only flags a downstream op starting before its upstream op even *begins* (true time-travel, still a real bug if it ever happens). On live data (5403 WIP rows, 640 orders, run date 2026-10-01): 5785 output rows (382 from §E.18 slicing), all invariants pass (PK uniqueness, REMARK coverage, pool concurrency, precedence), and CDD attainment improved from 73.5% (pre-Part-2) to **75.9%** — a real, measurable gain from letting downstream ops start on partial completions instead of stalling on whole-batch waits.

### E.19 — Determinism contract

Same inputs must always produce the same schedule (§E.13). Every decision point has exactly one named, fixed rule — no "whichever finishes first" or "whichever looks better" branching anywhere:

1. **Round/placement ordering** (§E.17): Model E's original flat list, strictly `(depth, task)` — every order's 1st schedulable operation across the whole plant, then every 2nd, and so on; within one depth, tasks in alphabetical order. (The plant-wide event-driven alternative described in an earlier draft of this section was built, measured, and reverted — see §E.17's own note.)
2. **Within-run execution order** (§E.17): once a fixture run's formation order is fixed (§E.4), a not-yet-ready member is skipped in favor of any other not-yet-placed member that's ready now, falling back to the formation-order head the instant it becomes ready; if nothing in the run is ready yet, wait for whichever member becomes ready soonest. Ties always resolve to the formation-order position (stable scan, first match wins).
3. **Machine selection** (§E.5): among capable candidates, walk ascending `PRIORITY` (from `MCH_MACHINE_FNL_MASTER`) and take the first one currently available (`machine_free_at` ≤ this decision's ready time). None available → wait for the earliest one to free up, then re-apply the same priority-first scan among whoever is free at that new instant.
4. **Candidate selection** (what a freed machine's cluster/order pick is, among several ready options): lowest CDD first (safety stock/CDD = NULL last); tie → older `order_date` first; still tied → `batch_key` string, alphabetically (last-resort fallback, shouldn't normally trigger).
5. **Admission** (§E.16): beyond-window/safety-stock candidates only — accept iff the fork/compare check shows zero new CDD breach for any order within the 60-day window. In-window committed orders use §E.6's plain window-eligibility rule, no fork check at all.
6. **Cluster formation** (§E.4, unchanged): FIXTURE + TASK rigid, LOCATOR varying, in the (a)/(b)/(c) preference order §E.4 already specifies.
7. **Parallelism** (§E.16): separate OS processes compute admission-check answers faster; it never changes which answer is reached or the order decisions are committed in — that's governed entirely by rules 1-6 above, single-threaded.

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

### schedule_output_archive → MCH_SCHEDULE_OUTPUT_ARCHIVE (Engine 1 writes, append-only) — Model E
```sql
CREATE TABLE MCH_SCHEDULE_OUTPUT_ARCHIVE (
    RUN_ID                  VARCHAR2(36)   NOT NULL,
    PRODUCTION_ORDER        VARCHAR2(9)    NOT NULL,
    OPERATION_NO            NUMBER         NOT NULL,
    LINE_NO                 NUMBER         DEFAULT 1 NOT NULL,
    TASK                    VARCHAR2(51),
    WORK_CENTER             VARCHAR2(49),
    SHIFT                   VARCHAR2(10),
    SCHEDULED_DATE          DATE,
    BALANCE_QTY             NUMBER         NOT NULL,
    GENERATED_AT            TIMESTAMP(6)   NOT NULL,
    BATCH_KEY               VARCHAR2(100),
    IS_SAFETY_STOCK         CHAR(1)        DEFAULT 'N',
    FIXTURE_ID              VARCHAR2(40),
    LOCATOR_ID              VARCHAR2(40),
    START_TIMESTAMP         TIMESTAMP(6),
    END_TIMESTAMP           TIMESTAMP(6),
    REMARK                  VARCHAR2(200),
    ORDER_COMPLETION_DATE   DATE,
    ORDER_COMPLETION_SHIFT  VARCHAR2(10),
    CONSTRAINT PK_MCH_SCHEDULE_OUTPUT_ARCHIVE
        PRIMARY KEY (PRODUCTION_ORDER, OPERATION_NO, LINE_NO)
);
-- plus non-unique indexes on RUN_ID, FIXTURE_ID, LOCATOR_ID
```
- Identical column shape to `MCH_SCHEDULE_OUTPUT`, but **append-only** — never DELETEd wholesale by this application, unlike the live table.
- Purpose: when a planner clicks the frontend's **"Freeze schedule"** button, the schedule currently sitting in `MCH_SCHEDULE_OUTPUT` at that instant is copied, as-is, into this table for historical backup/representation. This gives planners a point-in-time snapshot they can look back on even after the next `/schedule/generate` run deletes-and-replaces the live table.
- `POST /schedule/freeze` (new endpoint — see FastAPI backend section) is the only thing that writes here. It does **not** re-run Engine 1 and does **not** touch `MCH_SCHEDULE_OUTPUT` itself — a pure read-then-insert copy.
- **Idempotent per RUN_ID**: since `MCH_SCHEDULE_OUTPUT` holds exactly one run at a time (its own RUN_ID is audit-only, not part of its PK), freezing deletes that same RUN_ID's own rows from the archive first, then re-inserts — so clicking "Freeze schedule" twice on the same unchanged live schedule does not duplicate rows or error on the archive's PK. Rows from a *different*, earlier RUN_ID already in the archive are never touched.
- `db.archive_schedule_output()` implements this; `backend/testing/model_e_schedule_output_schema.sql`'s sibling DDL (applied manually, same as the live table) is this table's authoritative schema reference.

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

### Endpoints (10 total — response shapes need Model E migration, see E-5 status)
- POST /schedule/generate — triggers Engine 1, writes to MCH_SCHEDULE_OUTPUT
- GET  /schedule/current — reads latest MCH_SCHEDULE_OUTPUT, returns Gantt data (needs to drop offset fields, add REMARK/ORDER_COMPLETION_DATE/ORDER_COMPLETION_SHIFT/LINE_NO)
- POST /schedule/freeze — copies the current MCH_SCHEDULE_OUTPUT into MCH_SCHEDULE_OUTPUT_ARCHIVE (append-only historical snapshot). Triggered by the frontend's "Freeze schedule" button. Does not re-run Engine 1, does not touch MCH_SCHEDULE_OUTPUT. Idempotent per RUN_ID.
- POST /priority/simulate — triggers Engine 2 with payload `{orders: [...]}`, writes to MCH_SIM_RESULTS
- GET  /orders/wip — returns active MCH_WIP rows (CT > 0, routable, balance_qty > 0)
- GET  /machines/capacity — returns resolved machine availability for next N days
- POST /data/refresh — re-fetches all 7 read-only sources from Oracle (6 MCH_* ERP views + holiday_calendar)
- GET  /machines/daily — returns MCH_MACHINE_AVAILABILITY_BY_DATE rows for a date range (read-only display)
- GET  /config — returns current config.json contents
- PUT  /config — updates config.json

There is **no** `PUT /machines/daily` — read-only ERP view, never written by this application.

CORS enabled for React frontend origin. Schedule regeneration: manual trigger only in v1 (no background timer).

---

## React frontend

Framework: React 18 + Vite | Styling: TailwindCSS | Charts: Recharts | Drag-and-drop: React DnD

### Four main views
1. **Schedule view** — Gantt chart per machine. Needs migration to render against `START_TIMESTAMP`/`END_TIMESTAMP` directly (continuous time) instead of the retired shift-offset fields; should surface `REMARK` and `ORDER_COMPLETION_DATE`/`SHIFT`. Needs a **"Freeze schedule"** button that calls `POST /schedule/freeze` (archives the current schedule into `MCH_SCHEDULE_OUTPUT_ARCHIVE` for historical backup — see Write tables).
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
│   ├── main.py                   ← FastAPI app + all 9 endpoints. /schedule/generate, /schedule/current,
│   │                                /data/refresh now call the Model E pipeline. /orders/wip and
│   │                                /priority/simulate (Engine 2) still call Model C-era code — not yet migrated.
│   ├── db.py                     ← Oracle connection pool + read/write helpers. write_schedule_output /
│   │                                delete_schedule_output rewritten for the Model E schema.
│   │                                archive_schedule_output copies MCH_SCHEDULE_OUTPUT into the
│   │                                append-only MCH_SCHEDULE_OUTPUT_ARCHIVE ("Freeze schedule").
│   ├── model_e_data.py           ← adapts live Oracle DataFrames into the dispatch engine's plain input types
│   │                                (RawWipRow/RoutingIndex/FixtureIndex/DevicePool/ResolvedAvailability).
│   ├── model_e_pipeline.py       ← the single entry point: data load → dispatch_orders → dispatch_orchestrator
│   │                                → dispatch_writer → db.py write. What /schedule/generate actually calls.
│   ├── dispatch_orders.py        ← layer 1: raw WIP rows → classified, precedence-bridged OrderChains.
│   ├── dispatch_batching.py / dispatch_consolidation_window.py / dispatch_machine_selection.py /
│   │   dispatch_timeline.py / dispatch_planned_release.py / dispatch_pool.py / dispatch_safety_stock.py /
│   │   dispatch_parallel.py / dispatch_scope.py
│   │                            ← the Model E dispatch simulator's individually-tested building blocks
│   │                                (§E.2–§E.12). See each module's own docstring for which CLAUDE.md
│   │                                section it implements.
│   ├── dispatch_engine.py        ← layer 2 core: places one TASK's ready pool onto machines (batching +
│   │                                machine selection + timeline + pool + window + safety-stock hook, wired).
│   ├── dispatch_orchestrator.py  ← layer 2 outer loop: drives placement across every order's full chain,
│   │                                Planned-order gating, REMARK/ORDER_COMPLETION_* assembly, and the real
│   │                                §E.10 full speculative check (forks state, runs parallel continuations).
│   ├── shift_clock.py            ← the fixed shift clock-time convention; the only place TimePoint becomes
│   │                                a real datetime.
│   ├── dispatch_writer.py        ← FinalRow → MCH_SCHEDULE_OUTPUT row dict (RUN_ID/LINE_NO/GENERATED_AT +
│   │                                shift_clock conversion).
│   ├── preprocess.py             ← Model C-era preprocessing pipeline (historical — no longer called by main.py)
│   ├── engine1_scheduler.py      ← Model C CP-SAT engine (historical — superseded by the dispatch_* modules above)
│   ├── batch_grouping.py         ← now Model E's SIZE~CLASS~MOC~DESIGN batch key (compute_batch_key), used by
│   │                                both the historical Model C path and dispatch_orders.py
│   ├── engine2_recommender.py    ← priority simulation + risk scoring — still Model C-era, not yet migrated
│   │                                to call the Model E pipeline (CLAUDE.md's Engine 2 section says it should)
│   ├── pipeline.py / pipeline2.py← Model C-era orchestration for Engine 1 / Engine 2 runs (pipeline.py no
│   │                                longer called by main.py; pipeline2.py still is, via engine2_recommender.py)
│   ├── models.py                 ← Pydantic schemas. Config migrated to the Model E shape;
│   │                                ScheduleGenerateResponse added for /schedule/generate's new response.
│   ├── config.json               ← Model E shape (see Configuration section) — migrated
│   ├── testing/                   ← cleaned up (F-6): every Model B/C/D-era one-off diagnostic and
│   │   │                             throwaway test script has been deleted now that Model F is the
│   │   │                             only live engine. What's left is all current:
│   │   ├── model_e_schedule_output_schema.sql   ← Model E DDL (applied)
│   │   ├── test_dispatch_*.py / test_shift_clock.py / test_model_e_data_holidays.py /
│   │   │   test_soft_consolidation.py           ← the 17-file regression suite, one per dispatch_* module
│   │   ├── e6_validate_real_data.py             ← E-6 audit script: runs the full pipeline against live
│   │   │                                            Oracle WIP data and checks precedence/pool/REMARK/row-count
│   │   │                                            invariants (read-only, never writes)
│   │   ├── e6_validate_soft_consolidation.py    ← same style of live audit, scoped to the soft-consolidation-
│   │   │                                            beyond-window feature specifically
│   │   ├── benchmark_soft_window.py             ← live read-only sizing diagnostic for that same feature
│   │   ├── test_connection.py / test_api_startup.py ← basic Oracle connectivity / FastAPI startup checks
│   │   └── test_engine2_e2e.py                  ← still Model C-era (Engine 2 hasn't been migrated yet —
│   │                                                see "What's left" below) but kept because it's live,
│   │                                                not deprecated — it's the only regression coverage
│   │                                                Engine 2 currently has
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
| E-2 — Model E data layer | Loaders for MCH_ITEMWISE_FIXTURE_LOCATOR / MCH_FIXTURE_LOCATOR; batch/fixture key migrated to SIZE~CLASS~MOC~DESIGN | ✅ Done (`model_e_data.py`, validated against live Oracle data) |
| E-3 — Dispatch simulator core | Priority-walk batching (§4), continuous timeline + cooling (§7), machine selection (§5), pool constraint (§9), safety-stock policy (§10, full speculative check) | ✅ Done — all `dispatch_*` modules built, unit-tested, and wired together via `dispatch_orchestrator.py` |
| E-4 — REMARK & completion surfacing | §12 REMARK taxonomy, ORDER_COMPLETION_DATE/SHIFT population | ✅ Done (`dispatch_scope.py`, `dispatch_orchestrator.py`), verified against live data — every WIP row produces exactly one output row |
| E-5 — API/UI migration | `models.py`, `main.py` response shapes, `GanttChart.jsx` continuous-time rendering, config.json migration | ⏳ Partial — backend done (`config.json`/`models.Config` migrated, `/schedule/generate`+`/schedule/current`+`/data/refresh` call the Model E pipeline, verified live). **Not done:** `/orders/wip` and `/priority/simulate` (Engine 2) still call Model C-era code; `GanttChart.jsx` and the rest of the frontend still expect the old offset-based shape |
| E-6 — Validation | Full-run audit: precedence intact, pool never exceeded, per-order CDD attainment, replicate the two worked batching examples exactly | ✅ Done against live Oracle WIP data (5005 rows) — see `backend/testing/e6_validate_real_data.py`. Found and fixed 3 real bugs along the way: a `select_machine` int/TimePoint default-type crash, missing compound-device (`"+"`-joined LOCATOR) and `"NA"`-sentinel handling in the fixture/locator pool (both confirmed real ERP conventions), and a precedence bug where `_place_no_fixture_op` never advanced `ready_at`. All invariants pass; CDD attainment on the current live backlog is 28.5% — a real finding about the backlog, not an engine defect |
| F-1 — Unified FNL master (§E.15) | `MCH_MACHINE_FNL_MASTER` replaces the two-table routing+fixture join; `fixture_required_tasks` hard-exclusion policy | ✅ Done — `model_e_data.py` loaders, `dispatch_scope.py` exclusion outcome, unit-tested (`test_dispatch_scope.py`) and live-validated |
| F-2 — Priority-first machine selection (§E.5) | Reverses Model E's earliest-free-first rule: ascending `PRIORITY`, first currently-available candidate wins | ✅ Done — `dispatch_machine_selection.py`, unit-tested, live-validated |
| F-3 — Universal admission check (§E.16) | One fork/compare mechanism replacing Model E's two separate beyond-window/safety-stock resolvers | ✅ Done — `dispatch_orchestrator.py` (`_decide_admission`, `_make_committed_safe_resolver`), `dispatch_parallel.py` (separate-process fork/compare), unit-tested (`test_soft_consolidation.py`, `test_dispatch_safety_stock_speculation.py`) and live-validated |
| F-4 — Within-run reordering for readiness (§E.17) | Fixes a fixture run's late-ready member from blocking every later-sequenced member, without a full event-driven plant-wide redesign | ✅ Done — a full event-driven rewrite was built, measured on live data, found to produce identical results to the simpler depth-round loop, and deliberately reverted (see §E.17's own note for why); the real, kept fix is the lookahead in `dispatch_engine._simulate_run_timing`. Closed a confirmed 13-day idle gap on live data (machine `2PMH19`); live CDD attainment improved 72.8% → 73.4% |
| F-5 — Partial-quantity lot-streaming (§E.18) | Part 1: shift-boundary quantity slicing into multiple `LINE_NO` rows. Part 2: downstream ops consume upstream per-piece completions early instead of waiting for the whole batch | ✅ Done, both parts — see §E.18 above for the full writeup, including the real `_effective_ready()` bug found and fixed during Part 2. Live CDD attainment improved 73.5% → 75.9% |
| F-6 — Engine 1 codebase cleanup | Remove one-off Model B/C/D-era diagnostic and throwaway test scripts left over from superseded designs, now that Model F is the only live engine | ✅ Done — 22 files deleted (`deep_diagnostic.py`, `diagnose_duplication.py`, `regenerate_schedule.py`, and 19 `testing/` scripts: Model B's `_smoke_test_engine1.py`, Model C's `clean_and_test.py`/`diagnose_candidate_divergence.py`/`diagnose_vb05_routing.py`/`test_batch_fix.py`/`test_engine1_e2e.py`/`test_phase1_phase2.py`/`test_phase3_batch_continuity.py`/`verify_fix.py`, Model D's `phase_d1_fixture_locator_validation.py`/`phase_d1_vocabulary_audit.py`, and ad-hoc scratch scripts `check_overscheduled.py`/`diagnostic.py`/`diagnostic2.py`/`check_db.py`/`check_db_state.py`/`validate_excel_export.py`/`validate_excel_export2.py`/`validate_schedule.py`). Confirmed none were imported by any live code path before deletion; the full 17-file dispatch regression suite still passes. **Deliberately NOT deleted:** `preprocess.py`, `engine1_scheduler.py`, `pipeline.py`, `batch_grouping.py` — still load-bearing for Engine 2 (`engine2_recommender.py`/`pipeline2.py`) and two un-migrated `main.py` endpoints (`/orders/wip`, `/machines/capacity`); removing them now would break the running app, not clean it up. They become deletable once step 2 (Engine 2 migration) and the rest of E-5 land |
| **Engine 1 (Model F): feature-complete.** | Every dispatch-simulator behavior in CLAUDE.md's Model F spec (§E.1–§E.19) is implemented, unit-tested, and live-validated. | ✅ |
| 5 — Deploy | NSSM Windows services (Uvicorn + Express), end-to-end integration test | ❌ Not started |

---

## What's left (next chat — do not start these here)

Engine 1 (the Model F dispatch simulator) is feature-complete and live-validated end to end. What remains is everything CLAUDE.md's E-5 row and Phase 5 already flagged as outstanding, now enumerated as a concrete punch list:

1. ~~Investigate remaining machine idle gaps~~ — done this session: root-caused to `MCH_MACHINE_FNL_MASTER.PRIORITY` ranking (a business-rule/data outcome, not an engine defect — planner to re-rank priorities for specific combos if load-sharing is desired).
2. **Engine 2 migration** — `/orders/wip` and `/priority/simulate` (`engine2_recommender.py`/`pipeline2.py`) still call Model C-era code (`preprocess.py`/`engine1_scheduler.py`/`pipeline.py`), not the Model F pipeline. CLAUDE.md's Engine 2 section says this should happen; it hasn't yet.
3. **Frontend migration** — `GanttChart.jsx` needs continuous-`START_TIMESTAMP`/`END_TIMESTAMP` rendering AND must handle multiple `LINE_NO` rows per operation (a direct, routine consequence of §E.18 — not optional once this ships to planners). Rest of the frontend still expects the old offset-based shape.
4. **Admission-check parallelism** — `dispatch_parallel.evaluate_branches_in_parallel` opens a fresh `ProcessPoolExecutor` per call, not a persistent pool reused across one run's admission checks. A known, flagged cost — worth fixing only if a larger live backlog makes per-call pool startup measurably slow (not yet observed as a bottleneck on backlogs validated so far).
5. **Deploy (Phase 5)** — NSSM Windows services (Uvicorn + Express), end-to-end integration test. Not started at all.
6. Once steps 2–5 land, the Engine 1 Model C fallback code (`preprocess.py`, `engine1_scheduler.py`, `pipeline.py`, `batch_grouping.py`'s Model C path) becomes genuinely dead and can be deleted in a follow-up cleanup pass (F-6's deferred half).

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
- Writes: only MCH_SCHEDULE_OUTPUT (Engine 1) and MCH_SIM_RESULTS (Engine 2) are written by this application. All seven upstream sources (six MCH_* ERP views + the holiday_calendar table) are read-only.
- config.json: all runtime settings stored here (Model E shape — see Configuration section). Never stored in Oracle tables.
- IIS not used. Express.js on Node.js handles all production serving. Both Uvicorn (port 8000) and Express (port 80) run as permanent NSSM Windows Services.
