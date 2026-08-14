import { useMemo, useState } from 'react'
import clsx from 'clsx'

const SHIFTS = ['first', 'second', 'third']
const SHIFT_LABEL = { first: '1st', second: '2nd', third: '3rd' }
// Assumed wall-clock shift length for utilization % display only (matches CLAUDE.md WORKING_MINS=480 default).
const SHIFT_WORKING_MINS = 480

// Deterministic color per item/task code so the same job always gets the same color.
const PALETTE = [
  '#2563eb', '#0891b2', '#7c3aed', '#c026d3', '#dc2626',
  '#d97706', '#16a34a', '#4f46e5', '#0d9488', '#be185d',
]
function colorFor(key) {
  let hash = 0
  for (let i = 0; i < key.length; i++) hash = (hash * 31 + key.charCodeAt(i)) % PALETTE.length
  return PALETTE[Math.abs(hash) % PALETTE.length]
}

/**
 * Gantt chart: rows = machines, columns = dates, each date split into 3 shift lanes.
 * Each lane shows stacked task bars sized by consumed minutes, plus a utilization %
 * strip so machine load is visible at a glance without opening anything.
 */
export default function GanttChart({ assignments }) {
  const [hovered, setHovered] = useState(null)

  const { machines, dates, byMachineDateShift } = useMemo(() => {
    const machineSet = new Set()
    const dateSet = new Set()
    const grouped = {}

    for (const a of assignments) {
      machineSet.add(a.machine_name)
      dateSet.add(a.scheduled_date)
      const key = `${a.machine_name}|${a.scheduled_date}|${a.shift}`
      if (!grouped[key]) grouped[key] = []
      grouped[key].push(a)
    }

    // Fill the full contiguous date range (not just dates with activity) — otherwise
    // an idle day silently disappears from the timeline instead of reading as "idle".
    const sortedDates = Array.from(dateSet).sort()
    let filledDates = sortedDates
    if (sortedDates.length > 0) {
      filledDates = []
      const cursor = new Date(sortedDates[0])
      const last = new Date(sortedDates[sortedDates.length - 1])
      while (cursor <= last) {
        filledDates.push(cursor.toISOString().slice(0, 10))
        cursor.setDate(cursor.getDate() + 1)
      }
    }

    return {
      machines: Array.from(machineSet).sort(),
      dates: filledDates,
      byMachineDateShift: grouped,
    }
  }, [assignments])

  if (machines.length === 0) {
    return null
  }

  return (
    <div className="relative">
      <div className="overflow-x-auto thin-scroll border border-slate-200 rounded-xl">
        <table className="border-collapse text-xs w-full">
          <thead>
            <tr>
              <th className="sticky left-0 z-10 bg-slate-50 border-b border-r border-slate-200 px-3 py-2 text-left font-semibold text-slate-600 min-w-[140px]">
                Machine
              </th>
              {dates.map((d) => (
                <th
                  key={d}
                  colSpan={3}
                  className="border-b border-l border-slate-200 bg-slate-50 px-1 py-2 text-center font-semibold text-slate-600 min-w-[150px]"
                >
                  {new Date(d).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                </th>
              ))}
            </tr>
            <tr>
              <th className="sticky left-0 z-10 bg-slate-50 border-b border-r border-slate-200"></th>
              {dates.map((d) =>
                SHIFTS.map((s) => (
                  <th
                    key={d + s}
                    className="border-b border-l border-slate-100 bg-slate-50/70 px-1 py-1 text-center text-[10px] font-medium text-slate-400"
                  >
                    {SHIFT_LABEL[s]}
                  </th>
                )),
              )}
            </tr>
          </thead>
          <tbody>
            {machines.map((m) => (
              <tr key={m} className="group">
                <td className="sticky left-0 z-10 bg-white group-hover:bg-slate-50 border-r border-b border-slate-200 px-3 py-2 font-medium text-slate-700 whitespace-nowrap">
                  {m}
                </td>
                {dates.map((d) =>
                  SHIFTS.map((s) => {
                    const key = `${m}|${d}|${s}`
                    const tasks = byMachineDateShift[key] || []
                    const consumed = tasks.reduce(
                      (sum, t) => sum + (t.end_offset_min - t.start_offset_min),
                      0,
                    )
                    const utilization = Math.min(100, Math.round((consumed / SHIFT_WORKING_MINS) * 100))
                    return (
                      <td
                        key={key}
                        className="border-l border-b border-slate-100 p-1.5 align-top"
                        style={{ minWidth: 90 }}
                      >
                        <div className="relative rounded bg-slate-50 overflow-hidden flex flex-col gap-1">
                          {tasks.length === 0 ? (
                            <div className="h-12 flex items-center justify-center text-slate-300 text-[10px]">
                              idle
                            </div>
                          ) : (
                            <div className="space-y-0.5 p-1">
                              {tasks.map((t, i) => {
                                const widthPct = Math.max(
                                  12,
                                  ((t.end_offset_min - t.start_offset_min) / SHIFT_WORKING_MINS) * 100,
                                )
                                const color = colorFor(t.task || t.production_order)
                                const cellKey = `${key}-${i}`
                                // Abbreviate: show task code if available, else last 6 chars of order
                                const label = t.task ? t.task : t.production_order.slice(-6)
                                return (
                                  <div
                                    key={cellKey}
                                    className="rounded flex items-center justify-center text-slate-700 text-[10px] font-bold cursor-pointer hover:shadow-md transition-all"
                                    style={{
                                      backgroundColor: color,
                                      height: '20px',
                                      minWidth: '100%',
                                      opacity: hovered && hovered !== cellKey ? 0.45 : 0.9,
                                    }}
                                    onMouseEnter={() => setHovered(cellKey)}
                                    onMouseLeave={() => setHovered(null)}
                                    title={`${t.production_order} · Op ${t.operation_no}\n${t.balance_qty} pcs · ${Math.round((t.end_offset_min - t.start_offset_min) / 60)}m`}
                                  >
                                    <span className="truncate px-1 text-white drop-shadow-sm">{label}</span>
                                  </div>
                                )
                              })}
                            </div>
                          )}
                        </div>
                        <div className="mt-0.5 flex items-center gap-1">
                          <div className="flex-1 h-1 rounded-full bg-slate-100 overflow-hidden">
                            <div
                              className={clsx(
                                'h-full rounded-full',
                                utilization >= 85
                                  ? 'bg-risk-breach'
                                  : utilization >= 50
                                    ? 'bg-brand-500'
                                    : 'bg-slate-300',
                              )}
                              style={{ width: `${utilization}%` }}
                            />
                          </div>
                          <span className="text-[9px] text-slate-400 w-7 text-right">{utilization}%</span>
                        </div>
                      </td>
                    )
                  }),
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-[11px] text-slate-400">
        Bar width ∝ minutes consumed · thin strip below each shift = utilization % of shift capacity ·
        hover a bar for order / operation detail.
      </p>
    </div>
  )
}
