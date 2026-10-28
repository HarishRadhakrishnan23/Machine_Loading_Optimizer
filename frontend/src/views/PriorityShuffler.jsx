import { useCallback, useEffect, useMemo, useState } from 'react'
import clsx from 'clsx'

import PageHeader from '../components/PageHeader'
import SearchableSelect from '../components/SearchableSelect'
import { LoadingPanel, ErrorPanel } from '../components/LoadingState'
import { useToast } from '../hooks/useToast'
import WhatIfResult from '../components/WhatIfResult'
import { addFnlMachine, getFnlAudit, getFnlMaster, putFnlPriorities, runWhatIf } from '../api/client'

// Selection order = narrowing order. Blank MOC/DESIGN are real ERP values.
const FIELDS = [
  { key: 'size_inch', label: 'Size (in)' },
  { key: 'class', label: 'Class' },
  { key: 'moc', label: 'MOC' },
  { key: 'design', label: 'Design' },
  { key: 'task', label: 'Task' },
]
const EMPTY_SEL = { size_inch: null, class: null, moc: null, design: null, task: null }
const BLANK_LABEL = '(blank)'

function sortValues(values) {
  return [...values].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))
}

function loadName() {
  try {
    return localStorage.getItem('tov_shuffler_name') || ''
  } catch {
    return ''
  }
}

export default function PriorityShuffler() {
  const toast = useToast()
  const [master, setMaster] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [sel, setSel] = useState(EMPTY_SEL)
  const [draft, setDraft] = useState([]) // work centers in the planner's desired order
  const [saving, setSaving] = useState(false)
  const [changedBy, setChangedBy] = useState(loadName)
  const [audit, setAudit] = useState(null)
  const [auditError, setAuditError] = useState(null)

  // Engine 2 "Preview impact" (read-only what-if of the DRAFT order — never writes the master)
  const [preview, setPreview] = useState(null)
  const [previewing, setPreviewing] = useState(false)

  // add-machine form
  const [newMachine, setNewMachine] = useState(null)
  const [newPos, setNewPos] = useState(null)
  const [newTimes, setNewTimes] = useState({ fixture_change_time: '', locator_change_time: '', load_unload_time: '' })

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setMaster(await getFnlMaster())
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  // Cascading options: each field is narrowed by the fields chosen before it.
  const optionsFor = useMemo(() => {
    const rows = master?.rows || []
    return FIELDS.map((f, idx) => {
      const narrowed = rows.filter((r) => FIELDS.slice(0, idx).every((p) => sel[p.key] === null || r[p.key] === sel[p.key]))
      const values = sortValues(new Set(narrowed.map((r) => r[f.key])))
      return values.map((v) => ({ value: v, label: v === '' ? BLANK_LABEL : v }))
    })
  }, [master, sel])

  const complete = FIELDS.every((f) => sel[f.key] !== null)

  const comboRows = useMemo(() => {
    if (!master || !complete) return []
    return master.rows
      .filter((r) => FIELDS.every((f) => r[f.key] === sel[f.key]))
      .sort((a, b) => (a.priority ?? 1e9) - (b.priority ?? 1e9) || a.work_center.localeCompare(b.work_center))
  }, [master, sel, complete])

  // Re-seed the draft whenever the combo (or the underlying data) changes.
  useEffect(() => {
    setDraft(comboRows.map((r) => r.work_center))
    setPreview(null)
    setNewMachine(null)
    setNewPos(null)
    setNewTimes({ fixture_change_time: '', locator_change_time: '', load_unload_time: '' })
  }, [comboRows])

  const byWc = useMemo(() => Object.fromEntries(comboRows.map((r) => [r.work_center, r])), [comboRows])
  const original = comboRows.map((r) => r.work_center)
  const dirty = draft.length > 0 && draft.some((wc, i) => wc !== original[i])
  const sharedFixture = comboRows.length > 0 && comboRows.every((r) => r.fixture_id === comboRows[0].fixture_id)

  const addableMachines = useMemo(() => {
    const have = new Set(original)
    return (master?.machines || []).filter((m) => !have.has(m)).map((m) => ({ value: m, label: m }))
  }, [master, original])

  function setField(key, value) {
    const idx = FIELDS.findIndex((f) => f.key === key)
    const next = { ...sel }
    FIELDS.forEach((f, i) => {
      if (i === idx) next[f.key] = value
      else if (i > idx) next[f.key] = null
    })
    setSel(next)
  }

  function move(i, delta) {
    const j = i + delta
    if (j < 0 || j >= draft.length) return
    const next = [...draft]
    ;[next[i], next[j]] = [next[j], next[i]]
    setDraft(next)
    setPreview(null) // a preview describes one specific order — stale once it changes
  }

  /** Engine 2 what-if of `ordering` (+ any added machines) for the selected combo. Read-only. */
  async function previewImpact(ordering, added = []) {
    setPreviewing(true)
    setPreview(null)
    try {
      setPreview(await runWhatIf({ kind: 'machine_priority', ...comboKey(), ordering, added }))
    } catch (e) {
      toast.error(e.message)
    } finally {
      setPreviewing(false)
    }
  }

  function previewAddMachine() {
    const pos = newPos ?? comboRows.length + 1
    const ordering = [...original]
    ordering.splice(pos - 1, 0, newMachine)
    const num = (v) => (v === '' ? null : Number(v))
    previewImpact(ordering, [{
      work_center: newMachine,
      fixture_change_time: num(newTimes.fixture_change_time),
      locator_change_time: num(newTimes.locator_change_time),
      load_unload_time: num(newTimes.load_unload_time),
    }])
  }

  function rememberName(v) {
    setChangedBy(v)
    try {
      localStorage.setItem('tov_shuffler_name', v)
    } catch {
      /* storage unavailable - name just isn't remembered */
    }
  }

  const comboKey = () => ({ size_inch: sel.size_inch, class: sel.class, moc: sel.moc, design: sel.design, task: sel.task })

  async function saveOrder() {
    const summary = draft.map((wc, i) => `${i + 1}. ${wc}`).join('\n')
    if (!window.confirm(
      `Write this priority order to MCH_MACHINE_FNL_MASTER?\n\n${sel.size_inch}~${sel.class}~${sel.moc || BLANK_LABEL}~${sel.design || BLANK_LABEL} · ${sel.task}\n${summary}\n\nIt takes effect on the next Generate Schedule.`,
    )) return
    setSaving(true)
    try {
      const res = await putFnlPriorities({
        ...comboKey(),
        items: draft.map((wc, i) => ({ work_center: wc, priority: i + 1, expected_priority: byWc[wc].priority })),
        changed_by: changedBy || null,
      })
      toast.success(`Saved - ${res.rows_changed} machine priorit${res.rows_changed === 1 ? 'y' : 'ies'} changed. Regenerate the schedule to apply.`)
      await load()
      if (audit) refreshAudit()
    } catch (e) {
      toast.error(e.message)
      if (e.message.startsWith('409')) load()
    } finally {
      setSaving(false)
    }
  }

  async function submitAddMachine() {
    const pos = newPos ?? comboRows.length + 1
    if (!window.confirm(
      `Add ${newMachine} as priority ${pos} for ${sel.size_inch}~${sel.class}~${sel.moc || BLANK_LABEL}~${sel.design || BLANK_LABEL} · ${sel.task}?\n\nMachines at priority ${pos} or lower preference move down by one. Fixture/locator are copied from the current priority-1 machine row. Takes effect on the next Generate Schedule.`,
    )) return
    const num = (v) => (v === '' ? null : Number(v))
    setSaving(true)
    try {
      await addFnlMachine({
        ...comboKey(),
        work_center: newMachine,
        priority: pos,
        fixture_change_time: num(newTimes.fixture_change_time),
        locator_change_time: num(newTimes.locator_change_time),
        load_unload_time: num(newTimes.load_unload_time),
        changed_by: changedBy || null,
      })
      toast.success(`${newMachine} added at priority ${pos}. Regenerate the schedule to apply.`)
      await load()
      if (audit) refreshAudit()
    } catch (e) {
      toast.error(e.message)
    } finally {
      setSaving(false)
    }
  }

  async function refreshAudit() {
    setAuditError(null)
    try {
      setAudit((await getFnlAudit(200)).entries)
    } catch (e) {
      setAuditError(e.message)
    }
  }

  return (
    <div>
      <PageHeader
        title="Priority Shuffler"
        subtitle="Re-rank machine priority or add a capable machine for one Size~Class~MOC~Design + Task · writes MCH_MACHINE_FNL_MASTER"
        actions={
          <button className="btn-secondary" onClick={load} disabled={loading}>
            Reload
          </button>
        }
      />

      <div className="p-6 space-y-6">
        <div className="card p-4 border-l-4 border-l-sky text-sm text-text-muted">
          Changes are written straight to <span className="font-mono text-text">MCH_MACHINE_FNL_MASTER</span> (audited) and take
          effect on the <b className="text-text">next Generate Schedule</b>. The schedule currently on screen is not recomputed.
          Only <b className="text-text">PRIORITY</b> and <b className="text-text">add-machine</b> are editable here.
        </div>

        {loading && !master && <LoadingPanel label="Loading MCH_MACHINE_FNL_MASTER…" />}
        {error && <ErrorPanel message={error} onRetry={load} />}

        {master && (
          <>
            {/* Combo selector */}
            <div className="card p-4">
              <h3 className="section-title">Choose the combination</h3>
              <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                {FIELDS.map((f, idx) => (
                  <div key={f.key}>
                    <label className="block text-[11px] font-mono uppercase tracking-wide text-text-faint mb-1">{f.label}</label>
                    <SearchableSelect
                      options={optionsFor[idx]}
                      value={sel[f.key]}
                      onChange={(v) => setField(f.key, v)}
                      placeholder="Select…"
                    />
                  </div>
                ))}
              </div>
              <div className="mt-3 flex items-center gap-2">
                <label className="text-[11px] font-mono uppercase tracking-wide text-text-faint">Your name (audit)</label>
                <input
                  className="input max-w-[220px]"
                  value={changedBy}
                  onChange={(e) => rememberName(e.target.value)}
                  placeholder="optional"
                  maxLength={100}
                />
                {FIELDS.some((f) => sel[f.key] !== null) && (
                  <button className="btn-ghost ml-auto" onClick={() => setSel(EMPTY_SEL)}>
                    Clear selection
                  </button>
                )}
              </div>
            </div>

            {complete && comboRows.length === 0 && (
              <p className="text-sm text-text-faint">No rows for this combination.</p>
            )}

            {complete && comboRows.length > 0 && (
              <>
                {/* Priority list */}
                <div className="card p-4">
                  <div className="flex items-center justify-between mb-3">
                    <h3 className="section-title mb-0">Machine priority (1 = most preferred)</h3>
                    <div className="flex gap-2">
                      <button className="btn-ghost" disabled={!dirty || saving} onClick={() => { setDraft(original); setPreview(null) }}>
                        Reset
                      </button>
                      <button
                        className="btn-secondary"
                        disabled={!dirty || saving || previewing}
                        onClick={() => previewImpact(draft)}
                        title="Simulate this order (read-only, nothing is saved) and compare it with today's"
                      >
                        {previewing ? 'Simulating…' : 'Preview impact'}
                      </button>
                      <button className="btn-primary" disabled={!dirty || saving} onClick={saveOrder}>
                        {saving ? 'Saving…' : 'Save order'}
                      </button>
                    </div>
                  </div>

                  <div className="table-scroll thin-scroll border border-ink-600 rounded-xl max-h-[68vh]">
                    <table className="w-full text-xs border-collapse">
                      <thead>
                        <tr className="bg-ink-900 text-text-muted font-mono uppercase tracking-wide">
                          {['New', 'Was', 'Machine', 'Fixture', 'Locator', 'Fix chg', 'Loc chg', 'Load/unload', 'Move'].map((h) => (
                            <th key={h} className="px-3 py-2 text-left border-b border-ink-600 whitespace-nowrap">{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {draft.map((wc, i) => {
                          const r = byWc[wc]
                          const moved = r.priority !== i + 1
                          return (
                            <tr key={wc} className={clsx('border-b border-ink-700', moved && 'bg-accent/10')}>
                              <td className="px-3 py-2 font-mono font-semibold text-text">{i + 1}</td>
                              <td className="px-3 py-2 font-mono text-text-muted">{r.priority ?? '—'}</td>
                              <td className="px-3 py-2 font-medium text-text">{wc}</td>
                              <td className="px-3 py-2 font-mono">{r.fixture_id ?? '—'}</td>
                              <td className="px-3 py-2 font-mono">{r.locator_id ?? '—'}</td>
                              <td className="px-3 py-2 font-mono">{r.fixture_change_time ?? '—'}</td>
                              <td className="px-3 py-2 font-mono">{r.locator_change_time ?? '—'}</td>
                              <td className="px-3 py-2 font-mono">{r.load_unload_time ?? '—'}</td>
                              <td className="px-3 py-2 whitespace-nowrap">
                                <button className="btn-ghost px-2 py-1" disabled={i === 0} onClick={() => move(i, -1)} title="Higher priority">▲</button>
                                <button className="btn-ghost px-2 py-1" disabled={i === draft.length - 1} onClick={() => move(i, 1)} title="Lower priority">▼</button>
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                  <p className="mt-2 text-[11px] text-text-faint">
                    Saving renumbers this combo to 1…{draft.length}.{' '}
                    {sharedFixture && comboRows[0].fixture_id && <>All machines use fixture <span className="font-mono">{comboRows[0].fixture_id}</span>. </>}
                    The engine takes the lowest number among machines that are free (§E.5) - it does not wait for priority 1.
                  </p>
                </div>

                {/* Add machine */}
                <div className="card p-4">
                  <h3 className="section-title">Add a capable machine</h3>
                  <div className="grid grid-cols-2 md:grid-cols-6 gap-3 items-end">
                    <div className="md:col-span-2">
                      <label className="block text-[11px] font-mono uppercase tracking-wide text-text-faint mb-1">Machine</label>
                      <SearchableSelect options={addableMachines} value={newMachine} onChange={setNewMachine} placeholder="Select machine…" />
                    </div>
                    <div>
                      <label className="block text-[11px] font-mono uppercase tracking-wide text-text-faint mb-1">At priority</label>
                      <select className="input" value={newPos ?? comboRows.length + 1} onChange={(e) => setNewPos(Number(e.target.value))}>
                        {Array.from({ length: comboRows.length + 1 }, (_, i) => i + 1).map((p) => (
                          <option key={p} value={p}>{p}{p === comboRows.length + 1 ? ' (last)' : ''}</option>
                        ))}
                      </select>
                    </div>
                    {[
                      ['fixture_change_time', 'Fix chg (min)'],
                      ['locator_change_time', 'Loc chg (min)'],
                      ['load_unload_time', 'Load/unload (min)'],
                    ].map(([k, label]) => (
                      <div key={k}>
                        <label className="block text-[11px] font-mono uppercase tracking-wide text-text-faint mb-1">{label}</label>
                        <input
                          className="input"
                          type="number"
                          min="0"
                          step="any"
                          value={newTimes[k]}
                          placeholder={String(comboRows[0][k] ?? '')}
                          onChange={(e) => setNewTimes({ ...newTimes, [k]: e.target.value })}
                        />
                      </div>
                    ))}
                  </div>
                  <div className="mt-3 flex items-center justify-between">
                    <p className="text-[11px] text-text-faint">
                      Fixture, locator and any blank timing are copied from the current priority-1 machine row. Only machines with
                      availability data in the ERP are listed.
                    </p>
                    <div className="flex gap-2">
                      <button className="btn-secondary" disabled={!newMachine || saving || previewing} onClick={previewAddMachine}>
                        {previewing ? 'Simulating…' : 'Preview impact'}
                      </button>
                      <button className="btn-primary" disabled={!newMachine || saving} onClick={submitAddMachine}>
                        Add machine
                      </button>
                    </div>
                  </div>
                </div>
              </>
            )}

            {/* Engine 2 preview of the draft (read-only) */}
            {(previewing || preview) && (
              <div className="card p-4">
                <h3 className="section-title">Preview impact — {preview ? preview.summary : 'simulating…'}</h3>
                {previewing && <LoadingPanel label="Simulating the draft against today's schedule (about 10 seconds)…" />}
                {preview && (
                  <>
                    <p className="text-xs text-text-muted mb-4">
                      A simulation only — nothing has been saved. Click <b className="text-text">Save order</b> / <b className="text-text">Add machine</b> to
                      make it real, then Generate Schedule to apply it.
                    </p>
                    <WhatIfResult result={preview} />
                  </>
                )}
              </div>
            )}

            {/* Audit log */}
            <div className="card p-4">
              <div className="flex items-center justify-between mb-3">
                <h3 className="section-title mb-0">Change log</h3>
                <button className="btn-secondary" onClick={refreshAudit}>
                  {audit ? 'Refresh' : 'Show change log'}
                </button>
              </div>
              {auditError && <p className="text-xs text-status-risk">{auditError}</p>}
              {audit && audit.length === 0 && <p className="text-xs text-text-faint">No changes recorded yet.</p>}
              {audit && audit.length > 0 && (
                <div className="table-scroll thin-scroll border border-ink-600 rounded-xl max-h-80">
                  <table className="w-full text-xs border-collapse">
                    <thead>
                      <tr className="bg-ink-900 text-text-muted font-mono uppercase tracking-wide sticky top-0">
                        {['When', 'By', 'Action', 'Combination', 'Task', 'Machine', 'From', 'To', 'Note'].map((h) => (
                          <th key={h} className="px-3 py-2 text-left border-b border-ink-600 whitespace-nowrap">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {audit.map((a, i) => (
                        <tr key={i} className="border-b border-ink-700">
                          <td className="px-3 py-2 whitespace-nowrap font-mono">{a.changed_at ? new Date(a.changed_at).toLocaleString() : '—'}</td>
                          <td className="px-3 py-2">{a.changed_by ?? '—'}</td>
                          <td className="px-3 py-2">{a.action}</td>
                          <td className="px-3 py-2 font-mono whitespace-nowrap">{[a.size_inch, a.class, a.moc, a.design].join('~')}</td>
                          <td className="px-3 py-2">{a.task}</td>
                          <td className="px-3 py-2">{a.work_center}</td>
                          <td className="px-3 py-2 font-mono">{a.old_priority ?? '—'}</td>
                          <td className="px-3 py-2 font-mono">{a.new_priority ?? '—'}</td>
                          <td className="px-3 py-2 text-text-muted">{a.note ?? ''}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
