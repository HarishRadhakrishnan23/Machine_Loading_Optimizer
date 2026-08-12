import { useCallback, useEffect, useMemo, useState } from 'react'
import clsx from 'clsx'

import PageHeader from '../components/PageHeader'
import StatTile from '../components/StatTile'
import { LoadingPanel, ErrorPanel, EmptyPanel } from '../components/LoadingState'
import { useToast } from '../hooks/useToast'
import { getConfig, putConfig, getMachinesCapacity, getMachinesDaily } from '../api/client'

const SHIFTS = ['first', 'second', 'third']

const FIELD_META = [
  {
    key: 'batch_bonus_months',
    label: 'Batch Bonus Window (months)',
    hint: 'Orders due within this many months get a batch_bonus_value boost.',
    type: 'number',
    step: 1,
  },
  {
    key: 'batch_bonus_value',
    label: 'Batch Bonus Value',
    hint: 'Urgency-weight bonus applied inside the batch bonus window.',
    type: 'number',
    step: 0.1,
  },
  {
    key: 'downstream_queue_bonus_value',
    label: 'Downstream Queue Bonus',
    hint: 'Bonus when another order of the same category is already queued downstream.',
    type: 'number',
    step: 0.1,
  },
  {
    key: 'ageing_normalization_days',
    label: 'Ageing Normalization (days)',
    hint: 'Window used to normalize the order-ageing score to 0→1.',
    type: 'number',
    step: 1,
  },
  {
    key: 'machine_priority_epsilon',
    label: 'Machine Priority Epsilon',
    hint: 'Tie-breaker weight for preferred machine — never overrides tardiness.',
    type: 'number',
    step: 0.001,
  },
  {
    key: 'risk_safe_threshold_days',
    label: 'Risk Safe Threshold (days)',
    hint: 'Slack above this many days is classified SAFE in Engine 2.',
    type: 'number',
    step: 1,
  },
  {
    key: 'engine2_time_limit_seconds',
    label: 'Engine 2 Time Limit (sec)',
    hint: 'Max CP-SAT solve time for a priority-elevation simulation.',
    type: 'number',
    step: 1,
  },
  {
    key: 'scheduling_horizon_safety_factor',
    label: 'Horizon Safety Factor',
    hint: 'Multiplier applied to the feasibility-derived horizon length.',
    type: 'number',
    step: 1,
  },
  {
    key: 'scheduling_horizon_buffer_days',
    label: 'Horizon Buffer (days)',
    hint: 'Extra days always added on top of the derived horizon.',
    type: 'number',
    step: 1,
  },
]

export default function MachineAvailability() {
  const [tab, setTab] = useState('capacity')

  return (
    <div>
      <PageHeader
        title="Machines & Settings"
        subtitle="Read-only machine capacity (ERP-owned) and Engine runtime configuration"
        actions={
          <div className="flex bg-slate-100 rounded-lg p-1">
            {[
              { key: 'capacity', label: 'Capacity' },
              { key: 'settings', label: 'Settings' },
            ].map((t) => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={clsx(
                  'text-sm px-3 py-1.5 rounded-md font-medium transition-colors',
                  tab === t.key ? 'bg-white shadow-sm text-brand-700' : 'text-slate-500',
                )}
              >
                {t.label}
              </button>
            ))}
          </div>
        }
      />

      <div className="p-6">
        {tab === 'capacity' ? <CapacityPanel /> : <SettingsPanel />}
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
function CapacityPanel() {
  const [days, setDays] = useState(7)
  const [slots, setSlots] = useState(null)
  const [overrides, setOverrides] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const load = useCallback(async (d) => {
    setLoading(true)
    setError(null)
    try {
      const [capData, dailyData] = await Promise.all([getMachinesCapacity(d), getMachinesDaily()])
      setSlots(capData.capacity_slots || [])
      setOverrides(dailyData.daily_overrides || [])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load(days)
  }, [load, days])

  const { machines, dates, byKey } = useMemo(() => {
    if (!slots) return { machines: [], dates: [], byKey: {} }
    const machineSet = new Set()
    const dateSet = new Set()
    const grouped = {}
    for (const s of slots) {
      machineSet.add(s.machine)
      dateSet.add(s.date)
      grouped[`${s.machine}|${s.date}|${s.shift}`] = s
    }
    return {
      machines: Array.from(machineSet).sort(),
      dates: Array.from(dateSet).sort(),
      byKey: grouped,
    }
  }, [slots])

  const closedCount = slots?.filter((s) => !s.is_open).length ?? 0
  const overrideCount = overrides?.length ?? 0

  if (loading) return <LoadingPanel label="Loading machine capacity…" />
  if (error) return <ErrorPanel message={error} onRetry={() => load(days)} />

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="grid grid-cols-3 gap-4 flex-1 max-w-lg">
          <StatTile label="Machines" value={machines.length} tone="brand" />
          <StatTile label="Closed slots" value={closedCount} tone={closedCount > 0 ? 'atrisk' : 'neutral'} />
          <StatTile label="ERP overrides" value={overrideCount} />
        </div>
        <div className="flex items-center gap-2">
          <label className="text-sm text-slate-500">Horizon:</label>
          <select className="input w-32" value={days} onChange={(e) => setDays(Number(e.target.value))}>
            <option value={7}>7 days</option>
            <option value={14}>14 days</option>
            <option value={30}>30 days</option>
          </select>
        </div>
      </div>

      <div className="card p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-slate-700">Capacity Heatmap</h3>
          <div className="flex items-center gap-3 text-[11px] text-slate-400">
            <span className="flex items-center gap-1">
              <span className="h-2.5 w-2.5 rounded bg-risk-safe" /> Open (full)
            </span>
            <span className="flex items-center gap-1">
              <span className="h-2.5 w-2.5 rounded bg-amber-300" /> Partial
            </span>
            <span className="flex items-center gap-1">
              <span className="h-2.5 w-2.5 rounded bg-slate-200" /> Closed
            </span>
          </div>
        </div>

        {machines.length === 0 ? (
          <EmptyPanel title="No machine capacity data" hint="Check that MCH_MACHINE_AVAILABILITY has rows." />
        ) : (
          <div className="overflow-x-auto thin-scroll">
            <table className="border-collapse text-xs w-full">
              <thead>
                <tr>
                  <th className="sticky left-0 bg-white border-b border-r border-slate-200 px-3 py-2 text-left font-semibold text-slate-600">
                    Machine
                  </th>
                  {dates.map((d) => (
                    <th
                      key={d}
                      colSpan={3}
                      className="border-b border-l border-slate-200 px-1 py-2 text-center font-semibold text-slate-600"
                    >
                      {new Date(d).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {machines.map((m) => (
                  <tr key={m}>
                    <td className="sticky left-0 bg-white border-r border-b border-slate-100 px-3 py-1.5 font-medium text-slate-700 whitespace-nowrap">
                      {m}
                    </td>
                    {dates.map((d) =>
                      SHIFTS.map((s) => {
                        const slot = byKey[`${m}|${d}|${s}`]
                        const pct = slot ? Math.min(100, (slot.available_mins / 480) * 100) : 0
                        const color = pct === 0 ? 'bg-slate-200' : pct >= 90 ? 'bg-risk-safe' : 'bg-amber-300'
                        return (
                          <td key={`${m}-${d}-${s}`} className="border-l border-b border-slate-50 p-1 text-center">
                            <div
                              className={clsx('h-5 w-6 mx-auto rounded', color)}
                              title={slot ? `${slot.shift}: ${slot.available_mins} min available` : 'no data'}
                            />
                          </td>
                        )
                      }),
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <p className="text-xs text-slate-400">
        Capacity is prepared entirely in the ERP (MCH_MACHINE_AVAILABILITY /
        MCH_MACHINE_AVAILABILITY_BY_DATE) — this application only reads it. There is no edit form here.
      </p>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
function SettingsPanel() {
  const [config, setConfig] = useState(null)
  const [draft, setDraft] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)
  const toast = useToast()

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getConfig()
      setConfig(data)
      setDraft(data)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const dirty = useMemo(() => {
    if (!config || !draft) return false
    return JSON.stringify(config) !== JSON.stringify(draft)
  }, [config, draft])

  const handleChange = (key, value) => {
    setDraft((d) => ({ ...d, [key]: value === '' ? '' : Number(value) }))
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      const saved = await putConfig(draft)
      setConfig(saved)
      setDraft(saved)
      toast.success('Configuration saved.')
    } catch (e) {
      toast.error(`Save failed: ${e.message}`)
    } finally {
      setSaving(false)
    }
  }

  const handleReset = () => setDraft(config)

  if (loading) return <LoadingPanel label="Loading configuration…" />
  if (error) return <ErrorPanel message={error} onRetry={load} />

  return (
    <div className="max-w-3xl">
      <div className="card p-5">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-slate-700">Runtime Configuration</h3>
          {dirty && <span className="badge bg-amber-50 text-risk-atrisk">Unsaved changes</span>}
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
          {FIELD_META.map((f) => (
            <div key={f.key}>
              <label className="label">{f.label}</label>
              <input
                type="number"
                step={f.step}
                className="input"
                value={draft?.[f.key] ?? ''}
                onChange={(e) => handleChange(f.key, e.target.value)}
              />
              <p className="text-[11px] text-slate-400 mt-1">{f.hint}</p>
            </div>
          ))}
        </div>

        <div className="mt-6 flex items-center gap-2 justify-end border-t border-slate-100 pt-4">
          <button className="btn-ghost" onClick={handleReset} disabled={!dirty}>
            Reset
          </button>
          <button className="btn-primary" onClick={handleSave} disabled={!dirty || saving}>
            {saving ? 'Saving…' : 'Save Changes'}
          </button>
        </div>
      </div>

      <p className="text-xs text-slate-400 mt-3">
        Stored in <code className="bg-slate-100 px-1 rounded">backend/config.json</code> — never in
        Oracle. Changes apply to the next schedule generation or simulation run.
      </p>
    </div>
  )
}
