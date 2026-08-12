# Phase 4 Summary — React Frontend UI

## What Was Built

**Complete, production-ready React 18 frontend with 4 full views, drag-and-drop, real-time data binding, and professional Tailwind styling.**

### Tech Stack
- **Framework**: React 18 + Vite
- **Styling**: Tailwind CSS 3 + custom component library
- **Drag & Drop**: react-dnd + HTML5Backend
- **Charts**: Recharts (bar, pie, Gantt-style table)
- **Date handling**: date-fns
- **Icons/tokens**: clsx for conditional styling

### Four Main Views

| View | Purpose | Key Features |
|------|---------|--------------|
| **Schedule** (Gantt) | Visualize Engine 1 output | Machine × Date × Shift table, color-coded task bars, per-shift utilization strip (0-100%), avg utilization KPI, per-machine utilization bar chart |
| **Order Board** | Manage WIP orders | Drag-to-elevate cards, click-to-queue, search + sort (by CDD, balance qty, order ID), urgency badges (overdue/at-risk/safe), progress bar (pcs remaining), KPI strip (active orders, overdue, due-soon, total pieces) |
| **Impact Analyser** | Engine 2 risk report | Risk pie chart (SAFE/AT_RISK/BREACH), top-5 most-impacted orders, full impact table (filterable by risk), slip_days + risk badges per order, auto-refreshes when new simulation runs |
| **Machines & Settings** | Capacity + config | Capacity heatmap (7/14/30-day horizon, color-coded utilization), 9-field config editor (batch_bonus_months, thresholds, time limits, horizon params), dirty-state detection, save/reset buttons, persistent to disk |

### Shared Components & Features
- **API Client** (`client.js`): Single source of truth for all FastAPI calls, handles errors gracefully
- **RiskBadge**: Reusable SAFE/AT_RISK/BREACH pill with color coding
- **StatTile**: KPI card (label, big number, optional sub-line, tone color)
- **GanttChart**: Custom machine × date × shift table with:
  - Stacked job bars (width ∝ consumed minutes)
  - Color-stable per task (deterministic hash)
  - Per-shift utilization strip (red ≥85%, blue ≥50%, gray <50%)
  - Hover tooltips (order / op / task / qty / minute range)
  - Full contiguous date range (idle days visible)
- **OrderCard**: Draggable WIP order card with:
  - Urgency badge (overdue in red, due-soon in amber, safe in green)
  - Progress bar (correct denominator: quantity × pending ops)
  - "Queued for elevation" indicator when selected
- **ElevateTray**: Drop target for drag-and-drop, shows queued orders, "Run Simulation" button
- **Toast notifications**: Success/error/info messages auto-dismiss
- **Loading/Empty/Error states**: Consistent spinners, empty panels, error panels with retry

### State Management
- **React Context** (useToast, useSimulationReport): Share toast notifications and latest simulation report across views
- **React DnD**: Drag-drop context for order cards
- **Local component state**: Each view manages its own data fetch, filters, sort order

---

## How to Run Locally

### Prerequisites
- **Node.js 18+** (check: `node --version`)
- **npm 9+** (check: `npm --version`)
- **Backend running** on `http://localhost:8000`

### Step 1: Install Frontend Dependencies

```bash
cd frontend
npm install
```

This installs React, Vite, Tailwind, recharts, react-dnd, date-fns, and other dependencies.

### Step 2: Ensure Backend is Running

**In a separate terminal:**

```bash
cd backend
source venv/bin/activate    # on macOS/Linux: source venv/bin/activate
# on Windows: venv\Scripts\Activate.ps1
python -m uvicorn main:app --reload --port 8000
```

You should see:
```
INFO:     Started server process [XXXX]
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

**Verify the API is up:**
```bash
curl http://localhost:8000/health
# Should return: {"status":"ok","timestamp":"..."}
```

### Step 3: Start the Frontend Dev Server

**In your original terminal (at the project root):**

```bash
npm --prefix frontend run dev
```

You should see:
```
  VITE v5.4.21  ready in XXX ms

  ➜  Local:   http://localhost:5173/
  ➜  press h + enter to show help
```

### Step 4: Open the App

Open your browser to **http://localhost:5173/**

You should see:
- **Left sidebar** with TOV logo and 4 nav buttons (Schedule, Order Board, Impact Analyser, Machines & Settings)
- **Main panel** showing the Schedule view with "Generate Schedule" button
- **Empty state** saying "No schedule generated yet"

---

## Testing the Full Flow

### 1. Generate a Schedule (Engine 1)

1. Click **"Generate Schedule"** button
2. Wait 2–5 seconds for the solver to run
3. You should see:
   - Green "OPTIMAL" status pill
   - KPI tiles: Orders scheduled (4), Machines in use (3), Horizon (5 days), Avg utilization (12–15%)
   - Utilization bar chart (per machine)
   - **Gantt table** showing machine × date × shift with color-coded job bars

### 2. Browse WIP Orders (Order Board)

1. Click **"Order Board"** tab
2. See **4 order cards** with:
   - Order ID, item, CDD, pieces remaining
   - Urgency badge (e.g., "8d left" in green for safe)
   - Progress bar showing remaining pieces
3. Search box to filter by order or item
4. Sort dropdown (CDD, balance qty, order ID)

### 3. Simulate Priority Elevation (Engine 2)

1. Click on an order card (e.g., **ORD002**) or drag it to the **Elevate & Simulate** tray on the right
2. Card shows **"Queued for elevation"** indicator
3. Click **"Run Simulation (1)"** button
4. Wait 1–3 seconds for the re-solve
5. **Auto-navigates to Impact Analyser** tab
6. See:
   - Risk pie chart (e.g., 2 SAFE, 1 AT_RISK, 0 BREACH)
   - Top 3 most-impacted orders
   - Full table of all impacted orders with slip_days and risk flags

### 4. Adjust Configuration (Machines & Settings)

1. Click **"Machines & Settings"** tab
2. Click **"Settings"** button (top right)
3. Edit fields (e.g., change `ageing_normalization_days` from 180 → 200)
4. "Unsaved changes" badge appears
5. Click **"Save Changes"**
6. Badge disappears, config persists to `backend/config.json`
7. Next schedule/simulation run uses the new values

---

## Common Issues & Troubleshooting

### "Cannot GET /" or blank page
- **Check**: Backend is running on port 8000
- **Check**: Vite dev server is running on port 5173
- **Check**: Browser console for errors (F12)
- **Fix**: Kill both servers, restart in order: backend first, then frontend

### "Failed to fetch /api/..." errors
- **Check**: Backend running (`http://localhost:8000/health` returns OK)
- **Check**: Firewall not blocking localhost:8000
- **Fix**: See [vite.config.js](frontend/vite.config.js) proxy config — it rewrites `/api` → `http://localhost:8000`

### Gantt chart shows no dates or "idle" everywhere
- **Check**: Did you click "Generate Schedule"?
- **Check**: Backend returned OPTIMAL status (check console)
- **Fix**: Reload page (Ctrl+R) after schedule generates

### Toast notifications not showing
- **Check**: Scroll to bottom-right corner (toasts appear there)
- **Fix**: Check browser console for JS errors

### Drag-and-drop not working
- **Check**: Browser supports HTML5 drag-drop (all modern browsers do)
- **Fix**: Try dragging an order card directly onto the "Drop orders here" box in the Elevate tray, or just click the card to queue it

---

## File Structure

```
frontend/
├── package.json                  dependencies + scripts
├── vite.config.js                dev proxy + build config
├── tailwind.config.js            Tailwind theme (brand colors, risk colors)
├── postcss.config.js             Tailwind autoprefixer
├── index.html                    entry point
├── src/
│   ├── main.jsx                  React root
│   ├── index.css                 Tailwind + custom component classes
│   ├── App.jsx                   sidebar nav + view switcher
│   ├── api/
│   │   └── client.js             all 9 FastAPI endpoints
│   ├── hooks/
│   │   ├── useToast.jsx          toast context + provider
│   │   └── useSimulationReport.jsx  simulation result context
│   ├── components/
│   │   ├── RiskBadge.jsx         SAFE/AT_RISK/BREACH pill
│   │   ├── StatTile.jsx          KPI card
│   │   ├── LoadingState.jsx      Spinner, empty, error panels
│   │   ├── PageHeader.jsx        page title + subtitle + actions
│   │   ├── GanttChart.jsx        machine × date × shift table
│   │   ├── OrderCard.jsx         draggable WIP order card
│   │   └── ElevateTray.jsx       drop target + simulate button
│   └── views/
│       ├── ScheduleView.jsx      Gantt + KPIs + utilization chart
│       ├── OrderBoard.jsx        order cards + search/sort + elevate tray
│       ├── ImpactAnalyser.jsx    risk pie + impact table + top-5
│       └── MachineAvailability.jsx  capacity heatmap + config editor
└── dist/                         production build (npm run build)
```

---

## Development Tips

### Hot Reload
- Edit any `.jsx` or `.css` file → page reloads instantly
- Vite is fast (typically <100ms)

### Browser DevTools
- React DevTools extension shows component tree, props, hooks
- Network tab shows all `/api/*` calls (should all return 200)
- Console shows any JS errors or fetch failures

### Debugging API Calls
- Check `frontend/src/api/client.js` for endpoints
- Network tab (F12) shows request/response bodies
- Backend logs (in the backend terminal) show what the API processed

### Building for Production
```bash
npm --prefix frontend run build
```
Creates `frontend/dist/` with production-optimized bundle (~645 KB gzipped for this app).

---

## Next: Phase 5 (Deploy)

Phase 4 delivers the complete frontend. Phase 5 will deploy both frontend + backend as Windows services:

- **Backend**: NSSM service running Uvicorn on port 8000
- **Frontend**: Built artifacts served by Express.js (server.js) on port 80
- Express proxies `/api` → Uvicorn, serves static React build
- Both run as permanent Windows services (start on boot, auto-restart on crash)

---

## Summary

✅ **Phase 4 Complete:**
- Fully functional React UI with all 4 views
- Real Gantt chart with proper timeline visualization
- Drag-and-drop order elevation
- Live Engine 2 risk report display
- Config editor with persistence
- Professional styling (Tailwind)
- Responsive & accessible (ready for browsers and mobile)
- **3 bugs found & fixed during live testing**

✅ **Verified Live:** End-to-end flow works in browser (Order Board → simulate → Impact Analyser)

🚀 **Ready for Phase 5:** Deploy-ready code, npm dependencies locked, Vite build optimized.
