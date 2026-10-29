import { useState } from 'react'
import clsx from 'clsx'

import StatTile from './StatTile'
import RiskBadge from './RiskBadge'
import { useColumnFilters, FilterHeader, NoMatchRow } from './ColumnFilter'
import { useToast } from '../hooks/useToast'
import { saveWhatIfResult } from '../api/client'

const VERDICT = {
  HELPS: { label: 'Helps', tone: 'border-status-safe text-status-safe bg-status-safeBg', icon: '▲' },
  HURTS: { label: 'Hurts', tone: 'border-status-risk text-status-risk bg-status-riskBg', icon: '▼' },
  NEUTRAL: { label: 'Neutral', tone: 'border-status-warn text-status-warn bg-status-warnBg', icon: '●' },
  NO_EFFECT: { label: 'No effect', tone: 'border-ink-500 text-text-muted bg-ink-700', icon: '○' },
}

function fmtDate(iso) {
  if (!iso) return '—'
  return new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function fmtTs(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return `${d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} ${d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false })}`
}

function Slip({ days }) {
  if (days == null) return <span className="text-text-faint">—</span>
  if (days === 0) return <span className="text-text-muted font-mono">0</span>
  return (
    <span className={clsx('font-mono font-semibold', days > 0 ? 'text-status-risk' : 'text-status-safe')}>
      {days > 0 ? `+${days}` : days}d
    </span>
  )
}

const ORDER_COLS = [
  { label: 'Order', get: (o) => o.order },
  { label: 'CDD', get: (o) => fmtDate(o.cdd) },
  { label: 'Was', get: (o) => fmtDate(o.old_completion) },
  { label: 'Now', get: (o) => fmtDate(o.new_completion) },
  { label: 'Slip', get: (o) => (o.slip_days == null ? '—' : o.slip_days > 0 ? `+${o.slip_days}d` : `${o.slip_days}d`) },
  { label: 'Risk before', get: (o) => o.old_risk },
  { label: 'Risk now', get: (o) => o.new_risk },
]

const RUN_COLS = [
  { label: 'Run', get: (c) => `#${c.run}` },
  { label: 'Task', get: (c) => c.task },
  { label: 'Orders', get: (c) => c.orders },
  { label: 'Role', get: (c) => c.role },
  { label: 'Machine', get: (c) => `${c.before.machines.join(', ') || '—'} → ${c.after.machines.join(', ') || '—'}` },
  { label: 'Starts', get: (c) => `${fmtTs(c.before.start)} → ${fmtTs(c.after.start)}` },
  { label: 'Ends', get: (c) => `${fmtTs(c.before.end)} → ${fmtTs(c.after.end)}` },
]

const fmtLocal = (v) => (v ? new Date(v).toLocaleString() : '—')
const MACHINE_COLS = [
  { label: 'Machine', get: (m) => m.machine },
  { label: 'Before', get: (m) => m.before_hours },
  { label: 'After', get: (m) => m.after_hours },
  { label: 'Change', get: (m) => `${m.delta_hours > 0 ? '+' : ''}${m.delta_hours}` },
  { label: 'Last finish before', get: (m) => fmtLocal(m.before_last_finish) },
  { label: 'Last finish after', get: (m) => fmtLocal(m.after_last_finish) },
]

const FILTER_TH = 'px-3 py-2 text-left border-b border-ink-600 whitespace-nowrap align-top'

function OrderTable({ rows, empty }) {
  const cf = useColumnFilters(rows, ORDER_COLS)
  if (!rows.length) return <p className="text-xs text-text-faint py-3">{empty}</p>
  return (
    <div className="table-scroll thin-scroll border border-ink-600 rounded-xl max-h-[68vh]">
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr className="bg-ink-900 text-text-muted font-mono uppercase tracking-wide">
            {ORDER_COLS.map((c, i) => (
              <th key={c.label} className={FILTER_TH}><FilterHeader cf={cf} i={i} /></th>
            ))}
          </tr>
        </thead>
        <tbody>
          {cf.rows.length === 0 && <NoMatchRow cf={cf} colSpan={ORDER_COLS.length} />}
          {cf.rows.map((o) => (
            <tr key={o.order} className="border-b border-ink-700">
              <td className="px-3 py-2 font-mono text-text">{o.order}</td>
              <td className="px-3 py-2 font-mono">{fmtDate(o.cdd)}</td>
              <td className="px-3 py-2 font-mono">{fmtDate(o.old_completion)}</td>
              <td className="px-3 py-2 font-mono">{fmtDate(o.new_completion)}</td>
              <td className="px-3 py-2"><Slip days={o.slip_days} /></td>
              <td className="px-3 py-2"><RiskBadge flag={o.old_risk} size="sm" /></td>
              <td className="px-3 py-2"><RiskBadge flag={o.new_risk} size="sm" /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function RunsChangedTable({ rows }) {
  const cf = useColumnFilters(rows, RUN_COLS)
  return (
    <div className="table-scroll thin-scroll border border-ink-600 rounded-xl max-h-72">
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr className="bg-ink-900 text-text-muted font-mono uppercase tracking-wide">
            {RUN_COLS.map((c, i) => (
              <th key={c.label} className={FILTER_TH}><FilterHeader cf={cf} i={i} /></th>
            ))}
          </tr>
        </thead>
        <tbody>
          {cf.rows.length === 0 && <NoMatchRow cf={cf} colSpan={RUN_COLS.length} />}
          {cf.rows.map((c) => (
            <tr key={c.run} className="border-b border-ink-700">
              <td className="px-3 py-2 font-mono text-text">#{c.run}</td>
              <td className="px-3 py-2">{c.task}</td>
              <td className="px-3 py-2 font-mono">{c.orders}</td>
              <td className="px-3 py-2 text-text-muted">{c.role}</td>
              <td className="px-3 py-2 font-mono whitespace-nowrap">{c.before.machines.join(', ') || '—'} → {c.after.machines.join(', ') || '—'}</td>
              <td className="px-3 py-2 font-mono whitespace-nowrap">{fmtTs(c.before.start)} → {fmtTs(c.after.start)}</td>
              <td className="px-3 py-2 font-mono whitespace-nowrap">{fmtTs(c.before.end)} → {fmtTs(c.after.end)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function MachineLoadTable({ rows }) {
  const cf = useColumnFilters(rows, MACHINE_COLS)
  return (
    <div className="table-scroll thin-scroll border border-ink-600 rounded-xl max-h-72">
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr className="bg-ink-900 text-text-muted font-mono uppercase tracking-wide">
            {MACHINE_COLS.map((c, i) => (
              <th key={c.label} className={FILTER_TH}><FilterHeader cf={cf} i={i} /></th>
            ))}
          </tr>
        </thead>
        <tbody>
          {cf.rows.length === 0 && <NoMatchRow cf={cf} colSpan={MACHINE_COLS.length} />}
          {cf.rows.map((m) => (
            <tr key={m.machine} className="border-b border-ink-700">
              <td className="px-3 py-2 font-medium text-text">{m.machine}</td>
              <td className="px-3 py-2 font-mono">{m.before_hours}</td>
              <td className="px-3 py-2 font-mono">{m.after_hours}</td>
              <td className={clsx('px-3 py-2 font-mono font-semibold', m.delta_hours > 0 ? 'text-sky' : m.delta_hours < 0 ? 'text-accent' : 'text-text-muted')}>
                {m.delta_hours > 0 ? '+' : ''}{m.delta_hours}
              </td>
              <td className="px-3 py-2 font-mono">{fmtLocal(m.before_last_finish)}</td>
              <td className="px-3 py-2 font-mono">{fmtLocal(m.after_last_finish)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function delta(before, after, lowerIsBetter = true) {
  const d = Math.round((after - before) * 10) / 10 // KPI percentages carry float noise (e.g. -1.1999999999999957)
  if (d === 0) return { text: 'no change', tone: 'neutral' }
  const good = lowerIsBetter ? d < 0 : d > 0
  return { text: `${d > 0 ? '+' : ''}${d}`, tone: good ? 'safe' : 'breach' }
}

/**
 * One what-if result: verdict + the rule that produced it, before/after KPIs,
 * the orders that got better/worse, the targets, machine-load shift, and the
 * optional "Save this result" (the only write Engine 2 ever makes).
 */
export default function WhatIfResult({ result }) {
  const toast = useToast()
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  if (!result) return null

  const v = VERDICT[result.verdict.label] || VERDICT.NEUTRAL
  const { before, after } = result.kpis
  const late = delta(before.late, after.late)
  const lateness = delta(before.total_lateness_days, after.total_lateness_days)
  const attainment =
    before.attainment_pct != null && after.attainment_pct != null
      ? delta(before.attainment_pct, after.attainment_pct, false)
      : null
  const targets = result.orders.filter((o) => o.target)

  async function save() {
    setSaving(true)
    try {
      const res = await saveWhatIfResult(result.id)
      setSaved(true)
      toast.success(`Saved ${res.rows_written} rows to MCH_SIM_RESULTS.`)
    } catch (e) {
      toast.error(e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-5">
      {/* Verdict */}
      <div className={clsx('card p-4 border-l-4 flex items-start gap-4', v.tone)}>
        <span className="text-2xl leading-none mt-0.5">{v.icon}</span>
        <div className="flex-1">
          <p className="text-lg font-semibold">{v.label}</p>
          <p className="text-sm text-text mt-0.5">{result.verdict.reason}</p>
          <p className="text-[11px] text-text-faint mt-1">Rule: {result.verdict.rule}</p>
        </div>
        <button className="btn-secondary shrink-0" disabled={saving || saved} onClick={save} title="Writes this result's per-order rows to MCH_SIM_RESULTS">
          {saved ? 'Saved' : saving ? 'Saving…' : 'Save this result'}
        </button>
      </div>

      {result.notes?.map((n) => (
        <p key={n} className="card p-3 text-xs text-status-warn border-l-4 border-l-status-warn">{n}</p>
      ))}

      {/* KPIs */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatTile label="Late orders" value={`${before.late} → ${after.late}`} sub={late.text} tone={late.tone} />
        <StatTile label="Total lateness" value={`${before.total_lateness_days} → ${after.total_lateness_days}`} sub={`days · ${lateness.text}`} tone={lateness.tone} />
        <StatTile
          label="On-time"
          value={before.attainment_pct == null ? '—' : `${before.attainment_pct}% → ${after.attainment_pct}%`}
          sub={attainment ? (attainment.text === 'no change' ? 'no change' : `${attainment.text} pts`) : ''}
          tone={attainment?.tone ?? 'neutral'}
        />
        <StatTile label="Placements changed" value={result.placements_changed} sub={`${result.counts.orders_changed} orders affected`} tone="sky" />
      </div>

      {/* Phase 2: the runs that were edited / pulled forward */}
      {result.runs_changed?.length > 0 && (
        <div>
          <h4 className="section-title">Runs affected <span className="text-text-faint font-normal">(edited or pulled forward)</span></h4>
          <RunsChangedTable rows={result.runs_changed} />
        </div>
      )}

      {/* Targets */}
      {targets.length > 0 && (
        <div>
          <h4 className="section-title">Elevated orders</h4>
          <OrderTable rows={targets} empty="" />
        </div>
      )}

      {/* Winners / losers */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
        <div>
          <h4 className="section-title">Most delayed <span className="text-text-faint font-normal">({result.counts.worsened} orders slip)</span></h4>
          <OrderTable rows={result.top_worsened} empty="No other order finishes later." />
        </div>
        <div>
          <h4 className="section-title">Most improved <span className="text-text-faint font-normal">({result.counts.improved} orders gain)</span></h4>
          <OrderTable rows={result.top_improved} empty="No other order finishes earlier." />
        </div>
      </div>

      {/* Machine load */}
      {result.machines.length > 0 && (
        <div>
          <h4 className="section-title">Machine load shift <span className="text-text-faint font-normal">(scheduled span, hours)</span></h4>
          <MachineLoadTable rows={result.machines} />
        </div>
      )}

      <p className="text-[11px] text-text-faint">
        Simulation of the data loaded {new Date(result.snapshot_loaded_at).toLocaleString()} (run date {result.run_date}). Advisory only —
        nothing here changes the real schedule; act on it via the ERP CDD, the Priority Shuffler, or on the shop floor.
      </p>
    </div>
  )
}
