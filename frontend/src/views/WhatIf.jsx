import { useCallback, useEffect, useMemo, useState } from 'react'

import PageHeader from '../components/PageHeader'
import SearchableSelect from '../components/SearchableSelect'
import WhatIfResult from '../components/WhatIfResult'
import { LoadingPanel, ErrorPanel, Spinner } from '../components/LoadingState'
import { useToast } from '../hooks/useToast'
import { getWhatIfOrders, getWhatIfStatus, refreshWhatIf, runWhatIf } from '../api/client'

function fmtDate(iso) {
  if (!iso) return 'no CDD'
  return new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: '2-digit' })
}

/**
 * Engine 2 — "what if I prioritise these orders above everything else?"
 * Read-only: re-simulates a copy of the data with Engine 1's own rules and
 * diffs it against a baseline. Machine-priority what-ifs live on the
 * Priority Shuffler page ("Preview impact"), not here.
 */
export default function WhatIf({ active }) {
  const toast = useToast()
  const [orders, setOrders] = useState(null)
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(false)
  const [started, setStarted] = useState(false)
  const [error, setError] = useState(null)
  const [picked, setPicked] = useState([]) // order ids, highest priority first
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState(null)

  const load = useCallback(async (refresh = false) => {
    setLoading(true)
    setError(null)
    try {
      if (refresh) await refreshWhatIf()
      const [o, s] = await Promise.all([getWhatIfOrders(), getWhatIfStatus()])
      setOrders(o.orders)
      setStatus(s)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  // All pages stay mounted, and the first load costs ~10s (Oracle reads + a baseline
  // simulation) — so only start it the first time this tab is actually opened.
  useEffect(() => {
    if (active && !started) {
      setStarted(true)
      load()
    }
  }, [active, started, load])

  const byId = useMemo(() => Object.fromEntries((orders || []).map((o) => [o.order, o])), [orders])
  const options = useMemo(
    () =>
      (orders || [])
        .filter((o) => !picked.includes(o.order))
        .map((o) => ({ value: o.order, label: `${o.order} · CDD ${fmtDate(o.cdd)} · ${o.batch_key}` })),
    [orders, picked],
  )

  function move(i, delta) {
    const j = i + delta
    if (j < 0 || j >= picked.length) return
    const next = [...picked]
    ;[next[i], next[j]] = [next[j], next[i]]
    setPicked(next)
  }

  async function run() {
    setRunning(true)
    setResult(null)
    try {
      setResult(await runWhatIf({ kind: 'elevate', orders: picked }))
      setStatus(await getWhatIfStatus())
    } catch (e) {
      toast.error(e.message)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div>
      <PageHeader
        title="What-If"
        subtitle="Elevate orders above everything else and see who pays · Engine 2 · read-only, advisory"
        actions={
          <button className="btn-secondary" onClick={() => load(true)} disabled={loading || running} title="Re-read every input from Oracle">
            Reload data
          </button>
        }
      />

      <div className="p-6 space-y-6">
        <div className="card p-4 border-l-4 border-l-sky text-sm text-text-muted">
          Re-simulates a <b className="text-text">copy</b> of the current data with Engine 1's own rules (batching, release dates, no
          interrupting a running operation) and compares it with a baseline. Nothing is written and the real schedule is untouched
          — to act on a result, change the CDD in the ERP or use the Priority Shuffler.
          {status && (
            <span className="block mt-1 text-[11px] text-text-faint font-mono">
              Data loaded {new Date(status.snapshot_loaded_at).toLocaleTimeString()} · run date {status.run_date}
            </span>
          )}
        </div>

        {loading && !orders && <LoadingPanel label="Loading data and computing the baseline (about 10 seconds the first time)…" />}
        {error && <ErrorPanel message={error} onRetry={() => load()} />}

        {orders && (
          <>
            <div className="card p-4">
              <h3 className="section-title">1 · Which orders go first?</h3>
              <SearchableSelect
                options={options}
                value={null}
                onChange={(v) => setPicked([...picked, v])}
                placeholder={`Add an order (${orders.length} schedulable)…`}
              />

              {picked.length > 0 ? (
                <ol className="mt-3 space-y-1.5">
                  {picked.map((id, i) => (
                    <li key={id} className="flex items-center gap-3 rounded-lg bg-ink-800 border border-ink-600 px-3 py-2 text-sm">
                      <span className="font-mono text-accent w-6">#{i + 1}</span>
                      <span className="font-mono text-text">{id}</span>
                      <span className="text-text-muted text-xs flex-1">
                        CDD {fmtDate(byId[id]?.cdd)} · {byId[id]?.batch_key} · currently done {fmtDate(byId[id]?.completion)}
                        {byId[id]?.status === 'Planned' && ' · Planned (cannot start before its material arrives)'}
                      </span>
                      <button className="btn-ghost px-2 py-1" disabled={i === 0} onClick={() => move(i, -1)} title="Higher priority">▲</button>
                      <button className="btn-ghost px-2 py-1" disabled={i === picked.length - 1} onClick={() => move(i, 1)} title="Lower priority">▼</button>
                      <button className="btn-ghost px-2 py-1" onClick={() => setPicked(picked.filter((x) => x !== id))} title="Remove">×</button>
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="mt-3 text-xs text-text-faint">Pick one or more orders. They rank above every other order, at every operation, in the order you list them.</p>
              )}

              <div className="mt-4 flex items-center gap-3">
                <button className="btn-primary" disabled={picked.length === 0 || running} onClick={run}>
                  {running ? 'Simulating…' : 'Run what-if'}
                </button>
                {running && (
                  <span className="flex items-center gap-2 text-xs text-text-muted">
                    <Spinner className="h-4 w-4" /> about 10 seconds
                  </span>
                )}
                {picked.length > 0 && !running && (
                  <button className="btn-ghost" onClick={() => { setPicked([]); setResult(null) }}>Clear</button>
                )}
              </div>
            </div>

            {result && (
              <div className="card p-4">
                <h3 className="section-title">2 · Impact — {result.summary}</h3>
                <WhatIfResult result={result} />
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
