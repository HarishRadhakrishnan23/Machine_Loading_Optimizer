import { useEffect, useState } from 'react'

import PageHeader from '../components/PageHeader'
import StatTile from '../components/StatTile'
import SearchableSelect from '../components/SearchableSelect'
import { LoadingPanel, ErrorPanel } from '../components/LoadingState'
import { getCompletedMaterials, getArchivedRuns } from '../api/client'

const CURRENT_VALUE = 'current'

function isoDaysAgo(days) {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return d.toISOString().slice(0, 10)
}
function isoDaysAhead(days) {
  const d = new Date()
  d.setDate(d.getDate() + days)
  return d.toISOString().slice(0, 10)
}
function fmt(ts) {
  return new Date(ts).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export default function CompletedMaterials() {
  const [start, setStart] = useState(isoDaysAgo(30))
  const [end, setEnd] = useState(isoDaysAhead(30))
  const [runs, setRuns] = useState(null)
  const [runId, setRunId] = useState(CURRENT_VALUE)
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    getArchivedRuns()
      .then((res) => setRuns(res.runs))
      .catch(() => setRuns([]))
  }, [])

  const run = async (s = start, e = end, r = runId) => {
    setLoading(true)
    setError(null)
    try {
      const res = await getCompletedMaterials(s, e, r)
      setResult(res)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    run()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const runOptions = [
    { value: CURRENT_VALUE, label: 'Current Schedule (live)' },
    ...(runs || []).map((r) => ({ value: r.run_id, label: `${fmt(r.generated_at)} — ${r.row_count} rows` })),
  ]

  return (
    <div>
      <PageHeader
        title="Planned Completed Materials"
        subtitle="How many materials complete in a date window - from any schedule of comparison"
      />
      <div className="p-6 space-y-6 max-w-3xl">
        <div className="card p-4 flex flex-col gap-3">
          <div className="flex items-end gap-3 flex-wrap">
            <div>
              <label className="label">Start date</label>
              <input type="date" className="input" value={start} onChange={(e) => setStart(e.target.value)} />
            </div>
            <div>
              <label className="label">End date</label>
              <input type="date" className="input" value={end} onChange={(e) => setEnd(e.target.value)} />
            </div>
            <button className="btn-primary" onClick={() => run()} disabled={loading}>
              {loading ? 'Loading…' : 'Apply'}
            </button>
          </div>
          <div className="w-72">
            <label className="label">Schedule</label>
            <SearchableSelect
              options={runOptions}
              value={runId}
              onChange={setRunId}
              placeholder="Type to search…"
            />
          </div>
        </div>

        {loading && <LoadingPanel label="Counting completions…" />}
        {!loading && error && <ErrorPanel message={error} onRetry={() => run()} />}

        {!loading && !error && result && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <StatTile
              label="Orders completing"
              value={result.order_count}
              sub={`${result.start} → ${result.end}`}
              tone="brand"
            />
            <StatTile
              label="Total quantity"
              value={result.total_quantity}
              sub="pieces, last operation's balance"
              tone="sky"
            />
          </div>
        )}
      </div>
    </div>
  )
}
