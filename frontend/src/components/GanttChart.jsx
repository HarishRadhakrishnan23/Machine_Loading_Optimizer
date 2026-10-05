import { useMemo, useState } from 'react'

// Deterministic color per task code so the same job always gets the same color.
const PALETTE = [
  '#f97316', '#38bdf8', '#a78bfa', '#f472b6', '#fb7185',
  '#fbbf24', '#34d399', '#818cf8', '#2dd4bf', '#f87171',
]
function colorFor(key) {
  let hash = 0
  for (let i = 0; i < key.length; i++) hash = (hash * 31 + key.charCodeAt(i)) % PALETTE.length
  return PALETTE[Math.abs(hash) % PALETTE.length]
}

const PX_PER_HOUR = 10
const ROW_HEIGHT = 34

/**
 * Gantt chart: rows = machines, x-axis = real continuous time (START_TIMESTAMP →
 * END_TIMESTAMP, Model E §E.14) — NOT the retired shift-offset lattice. Bars are
 * positioned/sized by actual elapsed minutes, can span shifts/days. Rows whose
 * REMARK carries the "no fixture/locator match" caveat (still scheduled, §E.11)
 * render normally but flag it in the tooltip; fully excluded rows (no machine,
 * no timestamps) never reach this chart at all.
 */
export default function GanttChart({ assignments }) {
  const [hovered, setHovered] = useState(null)

  const { machines, bars, rangeStart, totalHours, dayMarkers } = useMemo(() => {
    const timed = assignments.filter((a) => a.start_timestamp && a.end_timestamp && a.machine_name)
    if (timed.length === 0) return { machines: [], bars: [], rangeStart: null, totalHours: 0, dayMarkers: [] }

    const machineSet = new Set(timed.map((a) => a.machine_name))
    const starts = timed.map((a) => new Date(a.start_timestamp).getTime())
    const ends = timed.map((a) => new Date(a.end_timestamp).getTime())
    const rangeStartMs = Math.min(...starts)
    const rangeEndMs = Math.max(...ends)
    const totalHrs = (rangeEndMs - rangeStartMs) / 3_600_000

    const bars = timed.map((a) => {
      const s = new Date(a.start_timestamp).getTime()
      const e = new Date(a.end_timestamp).getTime()
      return {
        ...a,
        leftHours: (s - rangeStartMs) / 3_600_000,
        widthHours: Math.max(0.15, (e - s) / 3_600_000),
      }
    })

    // Day gridlines across the visible range.
    const dayMarkers = []
    const cursor = new Date(rangeStartMs)
    cursor.setHours(0, 0, 0, 0)
    while (cursor.getTime() < rangeEndMs) {
      dayMarkers.push({
        leftHours: (cursor.getTime() - rangeStartMs) / 3_600_000,
        label: cursor.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
      })
      cursor.setDate(cursor.getDate() + 1)
    }

    return { machines: Array.from(machineSet).sort(), bars, rangeStart: rangeStartMs, totalHours: totalHrs, dayMarkers }
  }, [assignments])

  if (machines.length === 0) {
    return <p className="text-sm text-text-faint py-8 text-center">No scheduled rows with real timestamps yet.</p>
  }

  const trackWidth = Math.max(600, totalHours * PX_PER_HOUR)

  return (
    <div className="relative">
      <div className="overflow-x-auto thin-scroll border border-ink-600 rounded-xl bg-ink-900">
        <div style={{ width: trackWidth + 140 }}>
          {/* Day header */}
          <div className="flex sticky top-0 z-10 bg-ink-900 border-b border-ink-600">
            <div className="w-[140px] shrink-0" />
            <div className="relative flex-1" style={{ height: 28 }}>
              {dayMarkers.map((d, i) => (
                <div
                  key={i}
                  className="absolute top-0 h-full border-l border-ink-600 text-[10px] text-text-faint font-mono pl-1.5 pt-1.5"
                  style={{ left: d.leftHours * PX_PER_HOUR }}
                >
                  {d.label}
                </div>
              ))}
            </div>
          </div>

          {/* Machine rows */}
          {machines.map((m) => (
            <div key={m} className="flex border-b border-ink-700 group">
              <div className="w-[140px] shrink-0 px-3 flex items-center text-xs font-medium text-text group-hover:bg-ink-800 sticky left-0 bg-ink-900 z-[5] truncate">
                {m}
              </div>
              <div className="relative flex-1" style={{ height: ROW_HEIGHT }}>
                {dayMarkers.map((d, i) => (
                  <div
                    key={i}
                    className="absolute top-0 h-full border-l border-ink-700/60"
                    style={{ left: d.leftHours * PX_PER_HOUR }}
                  />
                ))}
                {bars
                  .filter((b) => b.machine_name === m)
                  .map((b, i) => {
                    const cellKey = `${m}-${i}`
                    const hasCaveat = Boolean(b.remark)
                    const color = colorFor(b.task || b.production_order)
                    return (
                      <div
                        key={cellKey}
                        className="absolute top-1.5 rounded flex items-center text-[10px] font-semibold cursor-pointer transition-opacity"
                        style={{
                          left: b.leftHours * PX_PER_HOUR,
                          width: Math.max(4, b.widthHours * PX_PER_HOUR),
                          height: ROW_HEIGHT - 12,
                          backgroundColor: color,
                          opacity: hovered && hovered !== cellKey ? 0.35 : 0.92,
                          borderBottom: hasCaveat ? '2px dashed #eab308' : 'none',
                        }}
                        onMouseEnter={() => setHovered(cellKey)}
                        onMouseLeave={() => setHovered(null)}
                        title={
                          `${b.production_order} · Op ${b.operation_no} (${b.task || '—'})\n` +
                          `${b.balance_qty} pcs${b.is_safety_stock ? ' · safety stock' : ''}\n` +
                          `${new Date(b.start_timestamp).toLocaleString()} → ${new Date(b.end_timestamp).toLocaleString()}` +
                          (hasCaveat ? `\n⚠ ${b.remark}` : '')
                        }
                      >
                        <span className="truncate px-1.5 text-ink-950 drop-shadow-sm">
                          {b.task || b.production_order.slice(-6)}
                        </span>
                      </div>
                    )
                  })}
              </div>
            </div>
          ))}
        </div>
      </div>
      <p className="mt-2 text-[11px] text-text-faint">
        Bar position/width ∝ real START_TIMESTAMP → END_TIMESTAMP (continuous time) · dashed amber underline =
        no fixture/locator match caveat · hover a bar for detail.
      </p>
    </div>
  )
}
