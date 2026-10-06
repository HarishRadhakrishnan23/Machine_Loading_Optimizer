import { useEffect, useState } from 'react'
import clsx from 'clsx'

import PageHeader from '../components/PageHeader'
import StatTile from '../components/StatTile'
import SearchableSelect from '../components/SearchableSelect'
import { LoadingPanel, ErrorPanel, EmptyPanel } from '../components/LoadingState'
import { getArchivedRuns, compareRuns, compareRunToCurrent } from '../api/client'

const CURRENT_VALUE = '__current__'

function fmt(ts) {
  return new Date(ts).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export default function RunComparison() {
  const [runs, setRuns] = useState(null)
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
          setRunB(CURRENT_VALUE)
        }
      })
      .catch((e) => setError(e.message))
  }, [])

  const runOptions = (runs || []).map((r) => ({ value: r.run_id, label: `${fmt(r.generated_at)} — ${r.row_count} rows` }))
  const runBOptions = [{ value: CURRENT_VALUE, label: 'Current Schedule (live)' }, ...runOptions]

  const run = async () => {
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      setResult(runB === CURRENT_VALUE ? await compareRunToCurrent(runA) : await compareRuns(runA, runB))
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  if (runs && runs.length === 0) {
    return (
      <div>
        <PageHeader title="Run Comparison" subtitle="Compare two frozen schedules, or a frozen schedule against the current one" />
        <div className="p-6">
          <EmptyPanel
            title="No archived runs yet"
            hint="Go to Overview and click 'Freeze Schedule' at least once to create a run you can compare."
          />
        </div>
      </div>
    )
  }

  return (
    <div>
      <PageHeader title="Run Comparison" subtitle="Compare two frozen schedules, or a frozen schedule against the current one" />
      <div className="p-6 space-y-6">
        <div className="card p-4 space-y-4">
          {!runs && <LoadingPanel label="Loading archived runs…" />}

          {runs && (
            <div className="flex items-end gap-3 flex-wrap">
              <div className="w-64">
                <label className="label">Run A (earlier)</label>
                <SearchableSelect options={runOptions} value={runA} onChange={setRunA} placeholder="Type to search…" />
              </div>
              <div className="w-64">
                <label className="label">Run B (later)</label>
                <SearchableSelect options={runBOptions} value={runB} onChange={setRunB} placeholder="Type to search…" />
              </div>
              <button className="btn-primary" onClick={run} disabled={loading}>
                {loading ? 'Comparing…' : 'Compare'}
              </button>
            </div>
          )}
        </div>

        {error && <ErrorPanel message={error} />}

        {result && (
          <>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <StatTile label="Added orders" value={result.added.length} tone="sky" sub="in run B only" />
              <StatTile label="Completed orders" value={result.removed.length} tone="atrisk" sub="in run A only" />
              <StatTile label="Completion date changed" value={result.changed.length} tone="breach" />
            </div>
            <p className="text-xs text-text-faint">Unchanged: {result.unchanged_count} orders</p>

            {result.added.length > 0 && (
              <div className="card p-4">
                <h3 className="section-title mb-0">Added Orders — new in Run B</h3>
                <div className="overflow-x-auto thin-scroll max-h-72">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-text-muted font-mono uppercase text-left">
                        <th className="py-1.5 pr-3">Order</th>
                        <th className="py-1.5 pr-3">Completion Date</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.added.map((o) => (
                        <tr key={o.production_order} className="border-t border-ink-700">
                          <td className="py-1.5 pr-3 text-text">{o.production_order}</td>
                          <td className="py-1.5 pr-3">{o.completion_date ?? '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {result.removed.length > 0 && (
              <div className="card p-4">
                <h3 className="section-title mb-0">Completed Orders — only in Run A</h3>
                <div className="overflow-x-auto thin-scroll max-h-72">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-text-muted font-mono uppercase text-left">
                        <th className="py-1.5 pr-3">Order</th>
                        <th className="py-1.5 pr-3">Completion Date</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.removed.map((o) => (
                        <tr key={o.production_order} className="border-t border-ink-700">
                          <td className="py-1.5 pr-3 text-text">{o.production_order}</td>
                          <td className="py-1.5 pr-3">{o.completion_date ?? '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {result.changed.length > 0 && (
              <div className="card p-4">
                <h3 className="section-title mb-0">Changed Completion Dates</h3>
                <div className="overflow-x-auto thin-scroll max-h-72">
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
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
