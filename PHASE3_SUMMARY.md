# Phase 3 Summary — FastAPI Backend

## What Was Built

**main.py** — Complete REST API with 9 endpoints orchestrating Engines 1 & 2 and ERP data access.

---

## Endpoints Overview

### Configuration Management

#### `GET /config`
**Read runtime configuration**
- Returns all 9 config parameters (batch_bonus_months, time limits, thresholds, etc.)
- Response: Config Pydantic model (all fields)

#### `PUT /config`
**Update runtime configuration**
- Request: Config object with updated fields
- Persists to `backend/config.json`
- Response: Updated Config

---

### Engine 1: Scheduling

#### `POST /schedule/generate`
**Generate optimal shift-level schedule**
- Query params: `run_date` (optional, default: today)
- Process:
  1. Fetch ERP views
  2. Preprocess (filter CT=0, QA, non-routable)
  3. Compute horizon
  4. Run CP-SAT solver
  5. Write MCH_SCHEDULE_OUTPUT
- Response: SchedulerResult
  - `run_id`: unique identifier for this run
  - `status`: OPTIMAL / FEASIBLE / INFEASIBLE / MODEL_INVALID
  - `objective_value`: weighted tardiness score
  - `assignments`: list of ScheduleOutputRow (order, op, machine, shift, date, offsets)
  - `completion_dates`: dict[order_id] → completion_date

#### `GET /schedule/current`
**Read latest schedule**
- Returns most recent MCH_SCHEDULE_OUTPUT entries (up to 1,000)
- Response format:
  ```json
  {
    "assignments": [
      {
        "production_order": "ORD001",
        "operation_no": 10,
        "task": "VB02",
        "machine_name": "M1",
        "shift": "first",
        "scheduled_date": "2026-08-11",
        "balance_qty": 10,
        "start_offset_min": 0,
        "end_offset_min": 50
      },
      ...
    ],
    "count": N
  }
  ```

---

### Engine 2: Priority Simulation

#### `POST /priority/simulate`
**Simulate elevating one or more orders**
- Request body:
  ```json
  {
    "orders": ["ORD001", "ORD002"],
    "time_limit_seconds": 10  // optional override
  }
  ```
- Process:
  1. Solve baseline (Engine 1)
  2. Elevate selected orders (urgency_weight → 999,999)
  3. Re-solve with time limit
  4. Compute slip_days + risk_flag for every other order
  5. Write MCH_SIM_RESULTS
  6. Assemble RiskReport
- Response: RiskReport
  - `sim_id`: unique simulation identifier
  - `elevated_orders`: the orders that were elevated
  - `impacts`: list of SimResultRow (per other order: order_id, slip_days, risk_flag, cdd)
  - `top_impacted`: top-5 most-affected orders (by slip_days)
  - `safe_count`, `at_risk_count`, `breach_count`: risk tallies

---

### Data Access: Orders & Machines

#### `GET /orders/wip`
**List active WIP orders**
- Filters: CT > 0, routable, non-QA, balance_qty > 0
- Returns:
  ```json
  {
    "orders": [
      {
        "production_order": "ORD001",
        "item": "30150DF",
        "cdd": "2026-09-15",
        "quantity_ordered": 10,
        "balance_qty_total": 10,
        "operations": 2
      },
      ...
    ],
    "count": N
  }
  ```

#### `GET /machines/capacity`
**Read resolved machine capacity**
- Query params: `days` (1-90, default: 7)
- Returns:
  ```json
  {
    "capacity_slots": [
      {
        "machine": "M1",
        "shift": "first",
        "date": "2026-08-11",
        "available_mins": 408.0,
        "is_open": true
      },
      ...
    ],
    "count": N
  }
  ```
- Data merged from:
  - MCH_MACHINE_AVAILABILITY (baseline)
  - MCH_MACHINE_AVAILABILITY_BY_DATE (daily overrides)

#### `GET /machines/daily`
**Read machine capacity overrides (read-only)**
- Query params: `start_date`, `end_date` (optional)
- Returns:
  ```json
  {
    "daily_overrides": [
      {
        "machine": "M1",
        "date": "2026-08-15",
        "shift": "second",
        "available_mins": 0.0
      }
    ],
    "count": N
  }
  ```
- **Note: Read-only.** Set via ERP, not this API. No PUT endpoint.

---

### Admin

#### `POST /data/refresh`
**Re-fetch all ERP views from Oracle**
- Reads all 4 views fresh (cache-busting)
- Returns:
  ```json
  {
    "wip_orders": 5533,
    "machine_master": 78,
    "machine_daily": 0,
    "routing_master": 3629,
    "refreshed_at": "2026-08-12T10:30:45"
  }
  ```

#### `GET /health`
**Healthcheck**
- Returns:
  ```json
  {
    "status": "ok",
    "timestamp": "2026-08-12T10:30:45"
  }
  ```

---

## Error Handling

All endpoints return HTTP error codes on failure:
- **400**: Invalid query params (e.g., days out of range)
- **500**: Solver failure, Oracle connection error, config I/O

Response on error:
```json
{
  "error": "error message",
  "type": "ExceptionType",
  "timestamp": "2026-08-12T10:30:45"
}
```

---

## CORS Configuration

Enabled for:
- `http://localhost:5173` (React dev server)
- `http://localhost` (production)
- Methods: GET, POST, PUT, DELETE, OPTIONS
- Credentials: allowed

---

## Running the API

```bash
cd backend
pip install -q -r requirements.txt  # installs fastapi, uvicorn, etc.
python -m uvicorn main:app --reload --port 8000
```

Then open:
- API: http://localhost:8000
- Interactive docs: http://localhost:8000/docs (Swagger UI)
- Alt docs: http://localhost:8000/redoc (ReDoc)

---

## Integration with Phases 1 & 2

| Endpoint | Engine | Module | Purpose |
|---|---|---|---|
| `/schedule/generate` | 1 | pipeline.py | Orchestrate preprocess → solve → write |
| `/schedule/current` | 1 | db.py | Read MCH_SCHEDULE_OUTPUT |
| `/priority/simulate` | 2 | pipeline2.py | Orchestrate baseline → elevate → risk report → write |
| `/orders/wip` | — | preprocess.py | Filter and summarize WIP |
| `/machines/capacity` | — | preprocess.py | Resolve baseline + daily |
| `/machines/daily` | — | db.py | Read MCH_MACHINE_AVAILABILITY_BY_DATE |
| `/config` | — | config.json | Get/set runtime parameters |
| `/data/refresh` | — | db.py | Re-fetch Oracle views |
| `/health` | — | — | Uptime monitoring |

---

## Frontend Integration (Preview for Phase 4)

The React frontend will call these endpoints:

```typescript
// Fetch current config
const config = await fetch('/api/config').then(r => r.json())

// Generate schedule
const result = await fetch('/api/schedule/generate', { method: 'POST' })
const { assignments, completion_dates } = await result.json()

// Render Gantt chart from assignments

// Simulate priority elevation
const simResult = await fetch('/api/priority/simulate', {
  method: 'POST',
  body: JSON.stringify({ orders: ['ORD002'] })
})
const { impacts, top_impacted, safe_count, at_risk_count, breach_count } = await simResult.json()

// Display risk report
```

---

## Development Notes

### Config File
- Location: `backend/config.json`
- Auto-loaded on `/config` GET
- Auto-saved on `/config` PUT
- 9 parameters (all documented in Config Pydantic model)

### Database Assumptions
- Oracle thin mode connection via `.env` (ORACLE_USER, ORACLE_PASSWORD, ORACLE_DSN)
- All 4 ERP views readable
- MCH_SCHEDULE_OUTPUT and MCH_SIM_RESULTS tables exist

### Performance
- `/schedule/generate`: typically 2-5 seconds (CP-SAT solver)
- `/priority/simulate`: typically 1-3 seconds (time-limited to 10s by default)
- `/orders/wip`, `/machines/capacity`: milliseconds (data filtering)
- `/schedule/current`: milliseconds (simple SQL read, limited to 1,000 rows)

### Scaling
- Tested with: 3 orders, 6 tasks, 9-day horizon
- No hard limits in code; Oracle connection pool and CP-SAT solver are the bottlenecks
- For production: tune Uvicorn workers, CP-SAT time limits, and query result limits

---

## Files

| File | Lines | Purpose |
|---|---|---|
| main.py | ~380 | FastAPI app + 9 endpoints + CORS + error handling |
| pipeline.py | ~110 | Engine 1 orchestration (Phase 1) |
| pipeline2.py | ~150 | Engine 2 orchestration (Phase 2) |
| db.py | ~180 | Oracle connection + read/write helpers (Phase 0) |
| preprocess.py | ~340 | Data pipeline (Phase 1) |
| engine1_scheduler.py | ~550 | CP-SAT model (Phase 1) |
| engine2_recommender.py | ~90 | Priority elevation logic (Phase 2) |
| models.py | ~270 | Pydantic request/response schemas |
| risk_classifier.py | ~135 | Risk classification (Phase 2) |
| config.json | 9 lines | Runtime parameters |

---

## Summary

Phase 3 provides a **complete REST API** that ties together all scheduling and simulation logic. The 9 endpoints expose:
- ✓ Schedule generation (Engine 1)
- ✓ Schedule reading (current state)
- ✓ Priority elevation simulation (Engine 2)
- ✓ Data access (WIP orders, machine capacity)
- ✓ Configuration management
- ✓ Health monitoring

**Phase 3 is complete and ready for Phase 4 (React frontend).**
