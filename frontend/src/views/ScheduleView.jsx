import { useCallback, useEffect, useMemo, useState } from 'react'
import clsx from 'clsx'

import PageHeader from '../components/PageHeader'
import StatTile from '../components/StatTile'
import GanttChart from '../components/GanttChart'
import BatchBubbleChart from '../components/BatchBubbleChart'
import ScheduleTable from '../components/ScheduleTable'
import { LoadingPanel, EmptyPanel, ErrorPanel } from '../components/LoadingState'
import { getCurrentSchedule } from '../api/client'

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'scheduled', label: 'Scheduled' },
  { key: 'no_fixture', label: 'No-fixture caveat' },
  { key: 'excluded', label: 'Excluded' },
  { key: 'safety_stock', label: 'Safety stock' },
]

function classify(a) {
  if (!a.machine_name) return 'excluded'
  if (a.remark) return 'no_fixture'
  return 'scheduled'
}

export default function ScheduleView() {
  const [assignments, setAssignments] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [filter, setFilter] = useState('all')

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getCurrentSchedule()
      setAssignments(data.assignments || [])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const stats = useMemo(() => {
    if (!assignments) return null
    let scheduled = 0, noFixture = 0, excluded = 0, safetyStock = 0
    for (const a of assignments) {
      const c = classify(a)
      if (c === 'scheduled') scheduled++
      else if (c === 'no_fixture') noFixture++
      else excluded++
      if (a.is_safety_stock) safetyStock++
    }
    return { scheduled, noFixture, excluded, safetyStock, total: assignments.length }
  }, [assignments])

  const filtered = useMemo(() => {
    if (!assignments) return []
    if (filter === 'all') return assignments
    if (filter === 'safety_stock') return assignments.filter((a) => a.is_safety_stock)
    return assignments.filter((a) => classify(a) === filter)
  }, [assignments, filter])

  return (
    <div>
      <PageHeader
        title="Pending Load Queue"
        subtitle="Visual representation of Scheduler's Output · Aperion Scheduler"
      />

      <div className="p-6 space-y-6">
        {loading && <LoadingPanel label="Loading current schedule…" />}
        {!loading && error && <ErrorPanel message={error} onRetry={load} />}

        {!loading && !error && (!assignments || assignments.length === 0) && (
          <EmptyPanel
            title="No schedule generated yet"
            hint="Go to Overview and click 'Generate Schedule' to run Engine 1 against current WIP orders."
          />
        )}

        {!loading && !error && assignments && assignments.length > 0 && stats && (
          <>
            {/* KPI row */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <StatTile label="Scheduled" value={stats.scheduled} sub="operations" tone="brand" />
              <StatTile label="No-fixture caveat" value={stats.noFixture} sub="operations" tone="atrisk" />
              <StatTile label="Excluded" value={stats.excluded} sub="operations" tone="breach" />
              <StatTile label="Safety stock" value={stats.safetyStock} sub="operations" tone="sky" />
            </div>

            {/* Bubble chart */}
            <div className="card p-4">
              <h3 className="section-title mb-0">Orders by Valve Size × Class</h3>
              <BatchBubbleChart assignments={assignments} />
            </div>

            {/* Gantt chart */}
            <div className="card p-4">
              <h3 className="section-title mb-0">Gantt — Machine Timeline</h3>
              <GanttChart assignments={assignments} />
            </div>

            {/* Filter chips + table */}
            <div className="card p-4">
              <div className="flex items-center gap-2 mb-4">
                {FILTERS.map((f) => (
                  <button
                    key={f.key}
                    onClick={() => setFilter(f.key)}
                    className={clsx(
                      'px-3 py-1.5 rounded-full text-xs font-medium transition-colors',
                      filter === f.key
                        ? 'bg-accent text-ink-950'
                        : 'bg-ink-700 text-text-muted hover:bg-ink-600',
                    )}
                  >
                    {f.label}
                  </button>
                ))}
              </div>
              <ScheduleTable rows={filtered} />
            </div>
          </>
        )}
      </div>
    </div>
  )
}
