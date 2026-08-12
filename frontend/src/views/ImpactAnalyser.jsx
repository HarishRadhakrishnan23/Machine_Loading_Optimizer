import { useMemo, useState } from 'react'
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend } from 'recharts'
import clsx from 'clsx'

import PageHeader from '../components/PageHeader'
import StatTile from '../components/StatTile'
import RiskBadge from '../components/RiskBadge'
import { EmptyPanel } from '../components/LoadingState'
import { useSimulationReport } from '../hooks/useSimulationReport'

const RISK_COLORS = { SAFE: '#16a34a', AT_RISK: '#d97706', BREACH: '#dc2626' }
const FILTERS = ['ALL', 'BREACH', 'AT_RISK', 'SAFE']

export default function ImpactAnalyser() {
  const { report } = useSimulationReport()
  const [filter, setFilter] = useState('ALL')
  const [sortDesc, setSortDesc] = useState(true)

  const pieData = useMemo(() => {
    if (!report) return []
    return [
      { name: 'Safe', key: 'SAFE', value: report.safe_count },
      { name: 'At Risk', key: 'AT_RISK', value: report.at_risk_count },
      { name: 'Breach', key: 'BREACH', value: report.breach_count },
    ].filter((d) => d.value > 0)
  }, [report])

  const rows = useMemo(() => {
    if (!report) return []
    let list = report.impacts
    if (filter !== 'ALL') list = list.filter((r) => r.risk_flag === filter)
    return [...list].sort((a, b) => {
      const av = a.slip_days ?? 0
      const bv = b.slip_days ?? 0
      return sortDesc ? bv - av : av - bv
    })
  }, [report, filter, sortDesc])

  if (!report) {
    return (
      <div>
        <PageHeader
          title="Impact Analyser"
          subtitle="Risk report from the most recent Engine 2 priority-elevation simulation"
        />
        <div className="p-6">
          <EmptyPanel
            title="No simulation run yet"
            hint="Go to the Order Board, queue one or more orders in the Elevate tray, and run a simulation to see its impact here."
          />
        </div>
      </div>
    )
  }

  return (
    <div>
      <PageHeader
        title="Impact Analyser"
        subtitle={`Elevated: ${report.elevated_orders.join(', ')} · Simulation ${report.sim_id.slice(0, 8)} · ${report.status}`}
      />

      <div className="p-6 space-y-6">
        {/* KPI + pie row */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 grid grid-cols-2 md:grid-cols-4 gap-4 content-start">
            <StatTile label="Orders impacted" value={report.impacts.length} tone="brand" />
            <StatTile label="Safe" value={report.safe_count} tone="safe" />
            <StatTile label="At risk" value={report.at_risk_count} tone="atrisk" />
            <StatTile label="Breach" value={report.breach_count} tone="breach" />
          </div>
          <div className="card p-4">
            <h3 className="text-sm font-semibold text-slate-700 mb-1">Risk Distribution</h3>
            <ResponsiveContainer width="100%" height={160}>
              <PieChart>
                <Pie
                  data={pieData}
                  dataKey="value"
                  nameKey="name"
                  innerRadius={40}
                  outerRadius={65}
                  paddingAngle={2}
                >
                  {pieData.map((d) => (
                    <Cell key={d.key} fill={RISK_COLORS[d.key]} />
                  ))}
                </Pie>
                <Tooltip />
                <Legend verticalAlign="bottom" height={24} iconSize={8} wrapperStyle={{ fontSize: 11 }} />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Top impacted highlight strip */}
        {report.top_impacted.length > 0 && (
          <div className="card p-4">
            <h3 className="text-sm font-semibold text-slate-700 mb-3">Top {report.top_impacted.length} Most Impacted</h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
              {report.top_impacted.map((r) => (
                <div key={r.order} className="rounded-lg border border-slate-200 p-3">
                  <div className="flex items-center justify-between mb-1">
                    <span className="font-semibold text-sm text-slate-800">{r.order}</span>
                    <RiskBadge flag={r.risk_flag} size="sm" />
                  </div>
                  <p
                    className={clsx(
                      'text-lg font-bold tabular-nums',
                      (r.slip_days ?? 0) > 0 ? 'text-risk-breach' : 'text-risk-safe',
                    )}
                  >
                    {r.slip_days != null ? `${r.slip_days > 0 ? '+' : ''}${r.slip_days}d` : 'N/A'}
                  </p>
                  <p className="text-[11px] text-slate-400">slip vs. baseline</p>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Full detail table */}
        <div className="card p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-slate-700">All Impacted Orders</h3>
            <div className="flex items-center gap-2">
              {FILTERS.map((f) => (
                <button
                  key={f}
                  onClick={() => setFilter(f)}
                  className={clsx(
                    'text-xs px-2.5 py-1 rounded-full font-medium transition-colors',
                    filter === f ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-500 hover:bg-slate-200',
                  )}
                >
                  {f === 'ALL' ? 'All' : f.replace('_', ' ')}
                </button>
              ))}
            </div>
          </div>

          {rows.length === 0 ? (
            <EmptyPanel title="No orders match this filter" />
          ) : (
            <div className="overflow-x-auto thin-scroll">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-slate-400 uppercase tracking-wide border-b border-slate-200">
                    <th className="py-2 pr-4">Order</th>
                    <th className="py-2 pr-4">Old Completion</th>
                    <th className="py-2 pr-4">New Completion</th>
                    <th
                      className="py-2 pr-4 cursor-pointer select-none"
                      onClick={() => setSortDesc((d) => !d)}
                    >
                      Slip (days) {sortDesc ? '↓' : '↑'}
                    </th>
                    <th className="py-2 pr-4">Risk</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {rows.map((r) => (
                    <tr key={r.order} className="hover:bg-slate-50">
                      <td className="py-2 pr-4 font-medium text-slate-800">{r.order}</td>
                      <td className="py-2 pr-4 text-slate-500">{r.old_completion_date || '—'}</td>
                      <td className="py-2 pr-4 text-slate-500">{r.new_completion_date || '—'}</td>
                      <td
                        className={clsx(
                          'py-2 pr-4 font-semibold tabular-nums',
                          (r.slip_days ?? 0) > 0
                            ? 'text-risk-breach'
                            : (r.slip_days ?? 0) < 0
                              ? 'text-risk-safe'
                              : 'text-slate-500',
                        )}
                      >
                        {r.slip_days != null ? `${r.slip_days > 0 ? '+' : ''}${r.slip_days}` : 'N/A'}
                      </td>
                      <td className="py-2 pr-4">
                        <RiskBadge flag={r.risk_flag} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
