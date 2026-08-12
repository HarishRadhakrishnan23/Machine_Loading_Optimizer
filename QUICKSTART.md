# Quick Start — Running the TOV Machine Loading Optimizer Locally

## 60-Second Setup

### Terminal 1: Start Backend
```bash
cd backend
source venv/bin/activate              # macOS/Linux
# or on Windows:
venv\Scripts\Activate.ps1

pip install -q -r requirements.txt    # one-time only
python -m uvicorn main:app --reload --port 8000
```

You should see:
```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

### Terminal 2: Start Frontend
```bash
npm --prefix frontend run dev
```

You should see:
```
➜  Local:   http://localhost:5173/
```

### Browser
Open **http://localhost:5173** — you're done!

---

## What You'll See

### Schedule View
- Click **"Generate Schedule"** to run Engine 1 (CP-SAT optimizer)
- See Gantt chart with machines, dates, shifts, color-coded jobs
- Machine utilization percentages below each job
- KPI tiles: orders scheduled, machines in use, avg utilization

### Order Board
- **4 WIP orders** displayed as draggable cards
- Each card shows: order ID, item, CDD, pieces remaining, urgency (overdue/at-risk/safe)
- **Elevate tray** on the right: drag a card there or click to queue it
- Click **"Run Simulation"** to run Engine 2 (priority elevation simulator)

### Impact Analyser
- Auto-shows after Engine 2 simulation completes
- Risk pie chart (SAFE/AT_RISK/BREACH)
- Top-5 most-impacted orders
- Full table of all impacted orders with slip_days and risk

### Machines & Settings
- **Capacity tab**: heatmap of machine utilization by date/shift
- **Settings tab**: edit all 9 config parameters (batch bonus, time limits, etc.)
  - Change a value → "Unsaved changes" badge appears
  - Click "Save Changes" → persists to `backend/config.json`

---

## Testing the Full Flow

1. **Generate Schedule**
   - Click "Generate Schedule" on Schedule tab
   - Wait for OPTIMAL status
   - See Gantt chart populate

2. **Elevate an Order**
   - Go to Order Board tab
   - Click on **ORD002** (or any order card)
   - Card shows "Queued for elevation"

3. **Run Simulation**
   - Click "Run Simulation (1)" in the Elevate tray
   - Wait for solve to complete
   - Auto-navigates to Impact Analyser
   - See risk report with 3 impacted orders

4. **Edit Config**
   - Go to Machines & Settings tab
   - Click "Settings" button
   - Change `ageing_normalization_days` to 200
   - "Unsaved changes" badge appears
   - Click "Save Changes"
   - Value persists to `backend/config.json`

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Cannot GET /" or blank page | Ensure both backend (port 8000) and frontend (5173) are running. Check F12 console for errors. |
| "Failed to fetch /api/..." | Backend not running. Check `http://localhost:8000/health` returns OK. |
| No schedule appears after generate | Did you click "Generate Schedule"? Check backend logs for errors. Reload page (Ctrl+R). |
| Toasts not visible | Check bottom-right corner of screen. Toast auto-hides after 4–6 seconds. |
| Drag-drop not working | Try clicking the card instead of dragging, or drag directly onto "Drop orders here" box. |
| Settings not saving | Check browser console for JS errors. Verify `backend/config.json` has write permissions. |

---

## Stopping the Servers

- **Backend**: Ctrl+C in Terminal 1
- **Frontend**: Ctrl+C in Terminal 2

---

## Next Time

Skip the "60-Second Setup" and just run:

```bash
# Terminal 1
cd backend && venv\Scripts\Activate.ps1 && python -m uvicorn main:app --reload --port 8000

# Terminal 2
npm --prefix frontend run dev
```

---

## For Production Deployment

See **Phase 5** (coming next) for NSSM Windows services + Express.js proxy.

---

## Want to Learn More?

- **Frontend code**: See [PHASE4_SUMMARY.md](PHASE4_SUMMARY.md) for architecture, components, state management
- **Backend code**: See [PHASE3_SUMMARY.md](PHASE3_SUMMARY.md) for 9 REST endpoints
- **Scheduling algorithm**: See [CLAUDE.md](CLAUDE.md) for full CP-SAT model, Hard Rules 1–7, time-mapping strategy
- **Database schema**: See [CLAUDE.md](CLAUDE.md) for MCH_* table structures
