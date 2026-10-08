/**
 * client.js — all fetch calls to the FastAPI backend.
 *
 * Dev: Vite proxies /api → http://localhost:8000 (vite.config.js)
 * Prod: Express server.js proxies /api → Uvicorn (CLAUDE.md)
 */

const BASE = '/api'

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail || body.error || detail
    } catch {
      /* non-JSON error body */
    }
    throw new Error(`${res.status} ${detail}`)
  }
  return res.json()
}

// ── Config ──────────────────────────────────────────────────────────────
export const getConfig = () => request('/config')
export const putConfig = (config) =>
  request('/config', { method: 'PUT', body: JSON.stringify(config) })

// ── Engine 1: Scheduling ────────────────────────────────────────────────
export const generateSchedule = (runDate) =>
  request(`/schedule/generate${runDate ? `?run_date=${runDate}` : ''}`, { method: 'POST' })
export const getCurrentSchedule = () => request('/schedule/current')
export const freezeSchedule = () => request('/schedule/freeze', { method: 'POST' })

/** Fetches a file-download endpoint and triggers a browser save. Shared by every .xlsx export button. */
async function downloadFile(path, fallbackFilename) {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail || detail
    } catch {
      /* non-JSON error body */
    }
    throw new Error(`${res.status} ${detail}`)
  }
  const blob = await res.blob()
  const disposition = res.headers.get('Content-Disposition') || ''
  const match = disposition.match(/filename="?([^"]+)"?/)
  const filename = match ? match[1] : fallbackFilename

  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
  return filename
}

/** Downloads the current MCH_SCHEDULE_OUTPUT as an .xlsx file (triggers a browser save). */
export const exportScheduleExcel = () => downloadFile('/schedule/export', 'schedule_export.xlsx')

/** Downloads every row of one archived RUN_ID from MCH_SCHEDULE_OUTPUT_ARCHIVE as an .xlsx file. */
export const exportArchivedRunExcel = (runId) =>
  downloadFile(`/schedule/archive/export?run_id=${encodeURIComponent(runId)}`, 'archive_export.xlsx')

// ── Completed Materials ────────────────────────────────────────────────
export const getCompletedMaterials = (start, end, runId) =>
  request(
    `/materials/completed?start=${start}&end=${end}` +
      (runId && runId !== 'current' ? `&run_id=${encodeURIComponent(runId)}` : ''),
  )

// ── Run Comparison ──────────────────────────────────────────────────────
export const getArchivedRuns = () => request('/schedule/archive/runs')
export const compareRuns = (runA, runB) =>
  request(`/schedule/compare/runs?run_a=${encodeURIComponent(runA)}&run_b=${encodeURIComponent(runB)}`)
export const compareRunToCurrent = (runA) =>
  request(`/schedule/compare/runs/to-current?run_a=${encodeURIComponent(runA)}`)

// ── Engine 2: Priority Simulation ───────────────────────────────────────
export const simulatePriority = (orders, timeLimitSeconds) =>
  request('/priority/simulate', {
    method: 'POST',
    body: JSON.stringify({ orders, time_limit_seconds: timeLimitSeconds ?? null }),
  })

// ── Data access ──────────────────────────────────────────────────────────
export const getWipOrders = () => request('/orders/wip')
export const getMachinesCapacity = (days = 7) => request(`/machines/capacity?days=${days}`)
export const getMachinesDaily = (startDate, endDate) => {
  const params = new URLSearchParams()
  if (startDate) params.set('start_date', startDate)
  if (endDate) params.set('end_date', endDate)
  const qs = params.toString()
  return request(`/machines/daily${qs ? `?${qs}` : ''}`)
}
export const refreshData = () => request('/data/refresh', { method: 'POST' })
export const getHealth = () => request('/health')
