import { useCallback, useEffect, useMemo, useState } from 'react'
import clsx from 'clsx'

import PageHeader from '../components/PageHeader'
import WhatIfResult from '../components/WhatIfResult'
import { LoadingPanel, ErrorPanel, Spinner } from '../components/LoadingState'
import { useToast } from '../hooks/useToast'
import { getWhatIfRuns, refreshWhatIf, runWhatIf } from '../api/client'

const PAGE = 60

function fmtTs(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return `${d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} ${d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false })}`
}

/**
 * Engine 2 — move a fixture run to another machine, or run it before another run, and see the impact.
 * A "run" is one of Engine 1's formed fixture runs. Read-only and advisory: nothing here changes the
 * real schedule. Edits are applied in order; pulling a run earlier automatically pulls its feeder runs.
 */
export default function WhatIfRuns({ active }) {
  const toast = useToast()
  const [runs, setRuns] = useState(null)
  const [loading, setLoading] = useState(false)
  const [started, setStarted] = useState(false)
  const [error, setError] = useState(null)

  const [machineFilter, setMachineFilter] = useState('')
  const [taskFilter, setTaskFilter] = useState('')
  const [query, setQuery] = useState('')
  const [shown, setShown] = useState(PAGE)
  const [expanded, setExpanded] = useState(() => new Set())

  const [edits, setEdits] = useState([]) // {type:'move', run, machine, covers, of} | {type:'before', run, before, pin}
  const [pending, setPending] = useState(null) // run id waiting for "place before which run?"
  const [pin, setPin] = useState(true)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState(null)

  const load = useCallback(async (refresh = false) => {
    setLoading(true)
    setError(null)
    try {
      if (refresh) await refreshWhatIf()
      setRuns((await getWhatIfRuns()).runs)
      setEdits([])
      setResult(null)
      setPending(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  // All pages stay mounted and the first load costs ~10-20s (data + two simulations): start it on first open only.
  useEffect(() => {
    if (active && !started) {
      setStarted(true)
      load()
    }
  }, [active, started, load])

  const byId = useMemo(() => Object.fromEntries((runs || []).map((r) => [r.run, r])), [runs])
  const machines = useMemo(() => [...new Set((runs || []).flatMap((r) => r.machines))].sort(), [runs])
  const tasks = useMemo(() => [...new Set((runs || []).map((r) => r.task))].sort(), [runs])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (runs || [])
      .filter((r) => (!machineFilter || r.machines.includes(machineFilter)) && (!taskFilter || r.task === taskFilter))
      .filter((r) => !q || r.orders.some((o) => o.toLowerCase().includes(q)) || String(r.run) === q)
      .sort((a, b) => a.start.localeCompare(b.start) || a.run - b.run)
  }, [runs, machineFilter, taskFilter, query])

  useEffect(() => setShown(PAGE), [machineFilter, taskFilter, query])

  function addMove(run, machine) {
    if (!machine) return
    const cap = byId[run].capable.find((c) => c.machine === machine)
    // the last move of a run wins
    setEdits([...edits.filter((e) => !(e.type === 'move' && e.run === run)), { type: 'move', run, machine, covers: cap.covers, of: cap.of }])
    setResult(null)
  }

  function addBefore(before) {
    setEdits([...edits, { type: 'before', run: pending, before, pin }])
    setPending(null)
    setResult(null)
  }

  async function run() {
    setRunning(true)
    setResult(null)
    try {
      setResult(
        await runWhatIf({
          kind: 'runs',
          edits: edits.map((e) => (e.type === 'move' ? { type: 'move', run: e.run, machine: e.machine } : { type: 'before', run: e.run, before: e.before, pin: e.pin })),
        }),
      )
    } catch (e) {
      toast.error(e.message)
    } finally {
      setRunning(false)
    }
  }

  const label = (id) => {
    const r = byId[id]
    return r ? `#${id} (${r.task}, ${r.n_orders} order${r.n_orders === 1 ? '' : 's'})` : `#${id}`
  }

  return (
    <div>
      <PageHeader
        title="Move / Reorder Runs"
        subtitle="Move a fixture run to another machine, or run it before another run · Engine 2 · read-only, advisory"
        actions={
          <button className="btn-secondary" onClick={() => load(true)} disabled={loading || running} title="Re-read every input from Oracle">
            Reload data
          </button>
        }
      />

      <div className="p-6 space-y-6">
        <div className="card p-4 border-l-4 border-l-sky text-sm text-text-muted">
          A <b className="text-text">run</b> is one fixture run formed by Engine 1 (orders sharing a fixture mount). Moving a run to a machine that
          can only take some of its orders splits the rest off as their own runs. Placing a run <b className="text-text">before</b> another also pulls
          forward the earlier-operation runs that feed it, so it never starts without its inputs. Nothing is written or changed — to act on a result,
          decide on the shop floor or use the ERP / Priority Shuffler.
        </div>

        {loading && !runs && <LoadingPanel label="Loading data and forming the baseline runs (10–20 seconds the first time)…" />}
        {error && <ErrorPanel message={error} onRetry={() => load()} />}

        {runs && (
          <>
            {/* Edits */}
            <div className="card p-4">
              <div className="flex items-center justify-between mb-3">
                <h3 className="section-title mb-0">1 · Your edits ({edits.length})</h3>
                <label className="flex items-center gap-1.5 text-[11px] text-text-muted cursor-pointer select-none" title="Keep the reordered runs on the machines they have today, so the result shows the effect of ORDER only">
                  <input type="checkbox" className="accent-[#f97316]" checked={pin} onChange={(e) => setPin(e.target.checked)} />
                  Pin machines when reordering
                </label>
              </div>

              {pending !== null && (
                <div className="mb-3 rounded-lg border border-accent bg-accent/10 px-3 py-2 text-sm text-text flex items-center gap-3">
                  <span>Placing <b>{label(pending)}</b> — choose the run it should go <b>before</b> in the table below.</span>
                  <button className="btn-ghost ml-auto" onClick={() => setPending(null)}>Cancel</button>
                </div>
              )}

              {edits.length === 0 ? (
                <p className="text-xs text-text-faint">Use <b>Move to…</b> or <b>Run earlier…</b> on a row below. Edits are applied in the order you add them.</p>
              ) : (
                <ol className="space-y-1.5">
                  {edits.map((e, i) => (
                    <li key={i} className="flex items-center gap-3 rounded-lg bg-ink-800 border border-ink-600 px-3 py-2 text-sm">
                      <span className="font-mono text-accent w-6">{i + 1}</span>
                      {e.type === 'move' ? (
                        <span className="text-text">
                          Move {label(e.run)} to <b className="font-mono">{e.machine}</b>
                          <span className={clsx('ml-2 text-xs', e.covers < e.of ? 'text-status-warn' : 'text-text-faint')}>
                            covers {e.covers} of {e.of} orders{e.covers < e.of ? ' — the rest become their own runs' : ''}
                          </span>
                        </span>
                      ) : (
                        <span className="text-text">
                          Run {label(e.run)} <b>before</b> {label(e.before)}
                          <span className="ml-2 text-xs text-text-faint">{e.pin ? 'machines pinned' : 'machines re-picked'} · feeders pulled forward automatically</span>
                        </span>
                      )}
                      <button className="btn-ghost px-2 py-1 ml-auto" onClick={() => { setEdits(edits.filter((_, j) => j !== i)); setResult(null) }} title="Remove">×</button>
                    </li>
                  ))}
                </ol>
              )}

              <div className="mt-4 flex items-center gap-3">
                <button className="btn-primary" disabled={edits.length === 0 || running} onClick={run}>
                  {running ? 'Simulating…' : 'Run what-if'}
                </button>
                {running && (
                  <span className="flex items-center gap-2 text-xs text-text-muted"><Spinner className="h-4 w-4" /> a few seconds</span>
                )}
                {edits.length > 0 && !running && (
                  <button className="btn-ghost" onClick={() => { setEdits([]); setResult(null) }}>Clear edits</button>
                )}
              </div>
            </div>

            {result && (
              <div className="card p-4">
                <h3 className="section-title">2 · Impact — {result.summary}</h3>
                <WhatIfResult result={result} />
              </div>
            )}

            {/* Runs table */}
            <div className="card p-4">
              <div className="flex flex-wrap items-center gap-3 mb-3">
                <h3 className="section-title mb-0">Runs in the current schedule ({filtered.length})</h3>
                <select className="input max-w-[160px]" value={machineFilter} onChange={(e) => setMachineFilter(e.target.value)}>
                  <option value="">All machines</option>
                  {machines.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
                <select className="input max-w-[130px]" value={taskFilter} onChange={(e) => setTaskFilter(e.target.value)}>
                  <option value="">All tasks</option>
                  {tasks.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
                <input className="input max-w-[220px]" placeholder="Order or run #…" value={query} onChange={(e) => setQuery(e.target.value)} />
              </div>

              <div className="table-scroll thin-scroll border border-ink-600 rounded-xl max-h-[68vh]">
                <table className="w-full text-xs border-collapse">
                  <thead>
                    <tr className="bg-ink-900 text-text-muted font-mono uppercase tracking-wide">
                      {['Run', 'Task', 'Orders', 'Machine', 'Starts', 'Ends', 'Move to…', 'Order'].map((h) => (
                        <th key={h} className="px-3 py-2 text-left border-b border-ink-600 whitespace-nowrap">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.slice(0, shown).map((r) => {
                      const isPending = pending === r.run
                      const open = expanded.has(r.run)
                      return (
                        <tr key={r.run} className={clsx('border-b border-ink-700 align-top', isPending && 'bg-accent/10')}>
                          <td className="px-3 py-2 font-mono text-text">#{r.run}</td>
                          <td className="px-3 py-2">{r.task}</td>
                          <td className="px-3 py-2">
                            <button
                              className="text-left hover:text-accent"
                              onClick={() => { const n = new Set(expanded); open ? n.delete(r.run) : n.add(r.run); setExpanded(n) }}
                              title="Show / hide the orders"
                            >
                              <span className="font-mono">{r.n_orders}</span>
                              <span className="text-text-faint"> · {r.pieces} pcs {open ? '▾' : '▸'}</span>
                            </button>
                            {open && <div className="mt-1 font-mono text-[11px] text-text-muted max-w-[260px] break-words">{r.orders.join(', ')}</div>}
                          </td>
                          <td className="px-3 py-2 font-mono whitespace-nowrap">{r.machines.join(', ')}</td>
                          <td className="px-3 py-2 font-mono whitespace-nowrap">{fmtTs(r.start)}</td>
                          <td className="px-3 py-2 font-mono whitespace-nowrap">{fmtTs(r.end)}</td>
                          <td className="px-3 py-2">
                            <select className="input py-1 max-w-[190px]" value="" onChange={(e) => addMove(r.run, e.target.value)}>
                              <option value="">Move to…</option>
                              {r.capable.filter((c) => !(r.machines.length === 1 && c.machine === r.machines[0])).map((c) => (
                                <option key={c.machine} value={c.machine}>{c.machine} — covers {c.covers}/{c.of}</option>
                              ))}
                            </select>
                          </td>
                          <td className="px-3 py-2 whitespace-nowrap">
                            {pending === null && (
                              <button className="btn-ghost px-2 py-1" onClick={() => setPending(r.run)}>Run earlier…</button>
                            )}
                            {pending !== null && !isPending && (
                              <button className="btn-secondary px-2 py-1" onClick={() => addBefore(r.run)} title={`Place ${label(pending)} before this run`}>
                                ← Before this
                              </button>
                            )}
                            {isPending && <span className="text-accent text-[11px]">choose target…</span>}
                          </td>
                        </tr>
                      )
                    })}
                    {filtered.length === 0 && (
                      <tr><td colSpan={8} className="px-3 py-8 text-center text-text-faint">No runs match this filter.</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
              {filtered.length > shown && (
                <div className="mt-3 text-center">
                  <button className="btn-secondary" onClick={() => setShown(shown + PAGE)}>Show {Math.min(PAGE, filtered.length - shown)} more</button>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
