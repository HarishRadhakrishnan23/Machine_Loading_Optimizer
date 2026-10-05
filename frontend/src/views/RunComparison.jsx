import { useEffect, useState } from 'react'
import clsx from 'clsx'

import PageHeader from '../components/PageHeader'
import StatTile from '../components/StatTile'
import { LoadingPanel, ErrorPanel, EmptyPanel } from '../components/LoadingState'
import { getArchivedRuns, compareRuns, compareRunToActual } from '../api/client'

function fmt(ts) {
  return new Date(ts).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export default function RunComparison() {
  const [runs, setRuns] = useState(null)
  const [mode, setMode] = useState('plan_vs_plan') // 'plan_vs_plan' | 'plan_vs_actual'
  const [runA, setRunA] = useState('')
  const [runB, setRunB] = useState('')
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    getArchivedRuns()
      .then((res) => {
        setRuns(res.runs)
        if (res.runs.length > 0) {
          setRunA(res.runs[res.runs.length - 1].run_id)
          setRunB(res.runs[0].run_id)
        }
      })
      .catch((e) => setError(e.message))
  }, [])

  const run = async () => {
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      if (mode === 'plan_vs_plan') {
        setResult(await compareRuns(runA, runB))
      } else {
        setResult(await compareRunToActual(runA))
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  if (runs && runs.length === 0) {
    return (
      <div>
        <PageHeader title="Run Comparison" subtitle="Compare two frozen schedules, or a frozen schedule against today's reality" />
        <div className="p-6">
          <EmptyPanel
            title="No archived runs yet"
            hint="Go to Overview and click 'Freeze Schedule' at least twice (on different days) to build up comparable history."
          />
        </div>
      </div>
    )
  }

  return (
    <div>
      <PageHeader title="Run Comparison" subtitle="Compare two frozen schedules, or a frozen schedule against today's reality" />
      <div className="p-6 space-y-6">
        <div className="card p-4 space-y-4">
          <div className="flex gap-2">
            {[
              { key: 'plan_vs_plan', label: 'Plan vs. Plan' },
              { key: 'plan_vs_actual', label: 'Plan vs. Actual (today)' },
            ].map((m) => (
              <button
                key={m.key}
                onClick={() => { setMode(m.key); setResult(null) }}
                className={clsx(
                  'px-3 py-1.5 rounded-full text-xs font-medium transition-colors',
                  mode === m.key ? 'bg-accent text-ink-950' : 'bg-ink-700 text-text-muted hover:bg-ink-600',
                )}
              >
                {m.label}
              </button>
            ))}
          </div>

          {!runs && <LoadingPanel label="Loading archived runs…" />}

          {runs && (
            <div className="flex items-end gap-3 flex-wrap">
              <div>
                <label className="label">{mode === 'plan_vs_plan' ? 'Run A (earlier)' : 'Archived run'}</label>
                <select className="input" value={runA} onChange={(e) => setRunA(e.target.value)}>
                  {runs.map((r) => (
                    <option key={r.run_id} value={r.run_id}>
                      {fmt(r.generated_at)} — {r.row_count} rows
                    </option>
                  ))}
                </select>
              </div>
              {mode === 'plan_vs_plan' && (
                <div>
                  <label className="label">Run B (later)</label>
                  <select className="input" value={runB} onChange={(e) => setRunB(e.target.value)}>
                    {runs.map((r) => (
                      <option key={r.run_id} value={r.run_id}>
                        {fmt(r.generated_at)} — {r.row_count} rows
                      </option>
                    ))}
                  </select>
                </div>
              )}
              <button className="btn-primary" onClick={run} disabled={loading}>
                {loading ? 'Comparing…' : 'Compare'}
              </button>
            </div>
          )}
        </div>

        {error && <ErrorPanel message={error} />}

        {result && mode === 'plan_vs_plan' && (
          <>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <StatTile label="Added orders" value={result.added.length} tone="sky" sub="in run B only" />
              <StatTile label="Removed orders" value={result.removed.length} tone="atrisk" sub="in run A only" />
              <StatTile label="Completion date changed" value={result.changed.length} tone="breach" />
            </div>
            <p className="text-xs text-text-faint">Unchanged: {result.unchanged_count} orders</p>
            {result.changed.length > 0 && (
              <div className="card p-4">
                <h3 className="section-title mb-0">Changed Completion Dates</h3>
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-text-muted font-mono uppercase text-left">
                      <th className="py-1.5 pr-3">Order</th>
                      <th className="py-1.5 pr-3">Old Completion</th>
                      <th className="py-1.5 pr-3">New Completion</th>
                      <th className="py-1.5 pr-3">Delta (days)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.changed.map((c) => (
                      <tr key={c.production_order} className="border-t border-ink-700">
                        <td className="py-1.5 pr-3 text-text">{c.production_order}</td>
                        <td className="py-1.5 pr-3">{c.old_completion_date ?? '—'}</td>
                        <td className="py-1.5 pr-3">{c.new_completion_date ?? '—'}</td>
                        <td className={clsx('py-1.5 pr-3 font-mono', c.delta_days > 0 ? 'text-status-risk' : 'text-status-safe')}>
                          {c.delta_days > 0 ? `+${c.delta_days}` : c.delta_days}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}

        {result && mode === 'plan_vs_actual' && (
          <>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <StatTile label="Completed (no longer in WIP)" value={result.completed_count} tone="safe" />
              <StatTile label="Still in progress" value={result.in_progress_count} tone="atrisk" />
            </div>
            <div className="card p-4">
              <h3 className="section-title mb-0">Orders Still In Progress — current state</h3>
              <div className="overflow-x-auto thin-scroll max-h-96">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-text-muted font-mono uppercase text-left">
                      <th className="py-1.5 pr-3">Order</th>
                      <th className="py-1.5 pr-3">Planned Completion</th>
                      <th className="py-1.5 pr-3">Current Op</th>
                      <th className="py-1.5 pr-3">Current Task</th>
                      <th className="py-1.5 pr-3">Balance Qty</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.orders
                      .filter((o) => o.status === 'in_progress')
                      .map((o) => (
                        <tr key={o.production_order} className="border-t border-ink-700">
                          <td className="py-1.5 pr-3 text-text">{o.production_order}</td>
                          <td className="py-1.5 pr-3">{o.planned_completion_date ?? '—'}</td>
                          <td className="py-1.5 pr-3">{o.current_operation_no}</td>
                          <td className="py-1.5 pr-3">{o.current_task}</td>
                          <td className="py-1.5 pr-3">{o.current_balance_qty}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
