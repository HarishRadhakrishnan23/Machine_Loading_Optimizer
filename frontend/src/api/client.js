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
