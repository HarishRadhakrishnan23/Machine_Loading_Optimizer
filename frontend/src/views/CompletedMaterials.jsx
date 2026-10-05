import { useEffect, useState } from 'react'

import PageHeader from '../components/PageHeader'
import StatTile from '../components/StatTile'
import { LoadingPanel, ErrorPanel } from '../components/LoadingState'
import { getCompletedMaterials } from '../api/client'

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

export default function CompletedMaterials() {
  const [start, setStart] = useState(isoDaysAgo(30))
  const [end, setEnd] = useState(isoDaysAhead(30))
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const run = async (s = start, e = end) => {
    setLoading(true)
    setError(null)
    try {
      const res = await getCompletedMaterials(s, e)
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

  return (
    <div>
      <PageHeader
        title="Completed Materials"
        subtitle="How many materials complete in a date window — from live MCH_SCHEDULE_OUTPUT's ORDER_COMPLETION_DATE"
      />
      <div className="p-6 space-y-6 max-w-3xl">
        <div className="card p-4 flex items-end gap-3 flex-wrap">
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
