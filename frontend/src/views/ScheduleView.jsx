import { useCallback, useEffect, useMemo, useState } from 'react'
import clsx from 'clsx'

import PageHeader from '../components/PageHeader'
import GanttChart from '../components/GanttChart'
import BatchBubbleChart from '../components/BatchBubbleChart'
import ScheduleTable from '../components/ScheduleTable'
import { LoadingPanel, EmptyPanel, ErrorPanel } from '../components/LoadingState'
import { getCurrentSchedule, getScheduleSummary } from '../api/client'

const fmt = (n) => (n ?? 0).toLocaleString()

/** One summary figure: label, big order count, quantity. `big` = the large centred style used in the square boxes. */
function Pair({ label, orders, qty, tone = 'text-text', big = false }) {
  return (
    <div className="flex flex-col items-center justify-center text-center px-2">
      <p className={clsx('font-medium text-text-muted uppercase tracking-wide font-mono leading-tight',
        big ? 'text-sm mb-3' : 'text-[11px] mb-2 min-h-[2.2em] flex items-center')}>{label}</p>
      <p className={clsx('font-semibold tabular-nums leading-none', big ? 'text-6xl' : 'text-3xl', tone)}>{fmt(orders)}</p>
      <p className={clsx('text-text-faint uppercase tracking-wider font-mono', big ? 'text-xs mt-2' : 'text-[10px] mt-1')}>orders</p>
      <p className={clsx('tabular-nums text-text', big ? 'text-2xl font-medium mt-4' : 'text-base font-medium mt-3')}>
        {fmt(qty)} <span className={clsx('text-text-faint font-normal', big ? 'text-sm' : 'text-xs')}>qty</span>
      </p>
    </div>
  )
}

/** Two square boxes (Total in WIP, Scheduled) and one wide box (Total Unscheduled and its split). */
function SummaryBoxes({ summary }) {
  if (!summary) return null
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      <div className="card p-5 flex items-center justify-center">
        <Pair big label="Total in WIP" orders={summary.wip.orders} qty={summary.wip.qty} />
      </div>
      <div className="card p-5 flex items-center justify-center">
        <Pair big label="Scheduled" orders={summary.scheduled.orders} qty={summary.scheduled.qty} tone="text-accent" />
      </div>
      <div className="card p-5 col-span-2 flex flex-col justify-between">
        <div className="grid grid-cols-2 md:grid-cols-4 md:divide-x divide-ink-600 gap-y-5">
          <Pair label="Total Unscheduled" orders={summary.unscheduled.orders} qty={summary.unscheduled.qty} tone="text-status-risk" />
          <Pair label="QAINSP Completed" orders={summary.qa_completed.orders} qty={summary.qa_completed.qty} tone="text-status-safe" />
          <Pair label="QAINSP Not Completed" orders={summary.qa_not_completed.orders} qty={summary.qa_not_completed.qty} tone="text-status-warn" />
          <Pair label="Rest orders" orders={summary.rest.orders} qty={summary.rest.qty} />
        </div>
        {/* Block centred in the box; its lines are left-aligned so every line starts at the same edge. */}
        <div className="mt-4 pt-3 border-t border-ink-700 flex justify-center">
          <p className="text-[11px] text-text-faint text-left">
            The last three add up to Total Unscheduled.
            <br />QAINSP Completed = pieces already finished.
            <br />QAINSP Not Completed = pieces waiting for QAINSP.
            <br />Rest = rejected, rework and other omitted pieces.
          </p>
        </div>
      </div>
    </div>
  )
}

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'scheduled', label: 'Scheduled' },
  { key: 'no_fixture', label: 'No-fixture caveat' },
  { key: 'excluded', label: 'Excluded' },
  { key: 'safety_stock', label: 'Safety stock' },
]

function classify(a) {
  if (!a.machine_name) return 'excluded'
  // Every SCHEDULED row carries a REMARK now (placement reasoning — see
  // dispatch_engine._placement_remark), not just the §E.11 no-fixture
  // caveat, so `remark` truthy is no longer the right signal here (it used
  // to make every single scheduled row misclassify as "no_fixture", which
  // is why Scheduled showed 0 on an otherwise fully-working schedule).
  // `fixture_id` is null only for that specific caveat — a real fixture run
  // always has one, even the literal "NA" sentinel (no device needed, still
  // a genuine match).
  if (!a.fixture_id) return 'no_fixture'
  return 'scheduled'
}

export default function ScheduleView() {
  const [assignments, setAssignments] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [filter, setFilter] = useState('all')
  const [summary, setSummary] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getCurrentSchedule()
      setAssignments(data.assignments || [])
      getScheduleSummary().then(setSummary).catch(() => setSummary(null)) // extra, never blocks the page
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

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

        {!loading && !error && assignments && assignments.length > 0 && (
          <>
            {/* Order / quantity summary: current WIP vs current schedule */}
            <SummaryBoxes summary={summary} />

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
                        ? 'bg-accent text-onAccent'
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
