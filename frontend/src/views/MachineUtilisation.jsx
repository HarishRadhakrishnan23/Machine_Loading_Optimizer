import { useEffect, useMemo, useState } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Cell, ReferenceLine,
  PieChart, Pie,
} from 'recharts'

import PageHeader from '../components/PageHeader'
import { LoadingPanel, ErrorPanel, EmptyPanel } from '../components/LoadingState'
import { getCurrentSchedule, getMachinesCapacity } from '../api/client'

const OEE_TARGET = 80
const HORIZON_DAYS = 30
const PIE_COLORS = ['#f97316', '#38bdf8', '#a78bfa', '#f472b6', '#fbbf24', '#34d399', '#818cf8', '#2dd4bf']

export default function MachineUtilisation() {
  const [assignments, setAssignments] = useState(null)
  const [capacity, setCapacity] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    Promise.all([getCurrentSchedule(), getMachinesCapacity(HORIZON_DAYS)])
      .then(([sched, cap]) => {
        setAssignments(sched.assignments || [])
        setCapacity(cap.capacity_slots || [])
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const data = useMemo(() => {
    if (!assignments || !capacity) return null

    const horizonStart = new Date()
    horizonStart.setHours(0, 0, 0, 0)
    const horizonEnd = new Date(horizonStart)
    horizonEnd.setDate(horizonEnd.getDate() + HORIZON_DAYS)

    const busyByMachine = {}
    const ordersByMachine = {}
    const loadByTask = {}
    const ordersByTask = {}

    for (const a of assignments) {
      if (!a.machine_name || !a.start_timestamp || !a.end_timestamp) continue
      const start = new Date(a.start_timestamp)
      if (start < horizonStart || start >= horizonEnd) continue
      const minutes = (new Date(a.end_timestamp) - start) / 60000
      busyByMachine[a.machine_name] = (busyByMachine[a.machine_name] || 0) + minutes
      if (!ordersByMachine[a.machine_name]) ordersByMachine[a.machine_name] = new Set()
      ordersByMachine[a.machine_name].add(a.production_order)
      if (a.task) {
        loadByTask[a.task] = (loadByTask[a.task] || 0) + minutes
        if (!ordersByTask[a.task]) ordersByTask[a.task] = new Set()
        ordersByTask[a.task].add(a.production_order)
      }
    }

    const availByMachine = {}
    for (const slot of capacity) {
      availByMachine[slot.machine] = (availByMachine[slot.machine] || 0) + slot.available_mins
    }

    const machines = Array.from(new Set([...Object.keys(busyByMachine), ...Object.keys(availByMachine)]))

    const ordersQueued = machines
      .map((m) => ({ machine: m, orders: ordersByMachine[m]?.size || 0 }))
      .filter((d) => d.orders > 0)
      .sort((a, b) => b.orders - a.orders)

    const utilisation = machines
      .map((m) => {
        const avail = availByMachine[m] || 0
        const busy = busyByMachine[m] || 0
        return { machine: m, pct: avail > 0 ? Math.min(100, Math.round((busy / avail) * 100)) : 0, busy, avail }
      })
      .filter((d) => d.avail > 0)
      .sort((a, b) => b.pct - a.pct)

    const loadShare = Object.entries(loadByTask)
      .map(([task, mins]) => ({ task, mins: Math.round(mins) }))
      .sort((a, b) => b.mins - a.mins)

    const totalTaskMins = loadShare.reduce((sum, d) => sum + d.mins, 0)
    const operationDetail = loadShare.map((d) => ({
      task: d.task,
      orders: ordersByTask[d.task]?.size || 0,
      mins: d.mins,
      pctShare: totalTaskMins > 0 ? Math.round((d.mins / totalTaskMins) * 100) : 0,
    }))

    const detail = machines
      .map((m) => ({
        machine: m,
        orders: ordersByMachine[m]?.size || 0,
        busyMins: Math.round(busyByMachine[m] || 0),
        availMins: Math.round(availByMachine[m] || 0),
        pct: availByMachine[m] > 0 ? Math.min(100, Math.round(((busyByMachine[m] || 0) / availByMachine[m]) * 100)) : 0,
      }))
      .sort((a, b) => b.busyMins - a.busyMins)

    return { ordersQueued, utilisation, loadShare, operationDetail, detail }
  }, [assignments, capacity])

  return (
    <div>
      <PageHeader
        title="Machine Utilisation"
        subtitle={`Orders queued, utilisation vs. ${OEE_TARGET}% OEE target, and load share by operation — ${HORIZON_DAYS}-day horizon`}
      />
      <div className="p-6 space-y-6">
        {loading && <LoadingPanel label="Loading machine utilisation…" />}
        {!loading && error && <ErrorPanel message={error} />}
        {!loading && !error && data && data.detail.length === 0 && (
          <EmptyPanel title="No scheduled load in this horizon" hint="Generate a schedule from Overview first." />
        )}

        {!loading && !error && data && data.detail.length > 0 && (
          <>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
              <div className="card p-4">
                <h3 className="section-title mb-0">Orders Queued per Machine</h3>
                <ResponsiveContainer width="100%" height={Math.max(180, data.ordersQueued.length * 28)}>
                  <BarChart data={data.ordersQueued} layout="vertical" margin={{ left: 8, right: 24 }}>
                    <CartesianGrid stroke="#30363d" horizontal={false} />
                    <XAxis type="number" tick={{ fontSize: 11, fill: '#8b949e' }} />
                    <YAxis type="category" dataKey="machine" width={110} tick={{ fontSize: 10, fill: '#8b949e' }} />
                    <Tooltip
                      cursor={{ fill: 'rgba(255,255,255,0.04)' }}
                      contentStyle={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 8 }}
                      labelStyle={{ color: '#f97316', fontWeight: 600 }}
                      itemStyle={{ color: '#e6edf3' }}
                    />
                    <Bar dataKey="orders" radius={[0, 4, 4, 0]} barSize={14} fill="#f97316" />
                  </BarChart>
                </ResponsiveContainer>
              </div>

              <div className="card p-4">
                <h3 className="section-title mb-0">Utilisation % (dashed = {OEE_TARGET}% OEE target)</h3>
                <ResponsiveContainer width="100%" height={Math.max(180, data.utilisation.length * 28)}>
                  <BarChart data={data.utilisation} layout="vertical" margin={{ left: 8, right: 24 }}>
                    <CartesianGrid stroke="#30363d" horizontal={false} />
                    <XAxis type="number" domain={[0, 100]} unit="%" tick={{ fontSize: 11, fill: '#8b949e' }} />
                    <YAxis type="category" dataKey="machine" width={110} tick={{ fontSize: 10, fill: '#8b949e' }} />
                    <Tooltip
                      cursor={{ fill: 'rgba(255,255,255,0.04)' }}
                      contentStyle={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 8 }}
                      labelStyle={{ color: '#f97316', fontWeight: 600 }}
                      itemStyle={{ color: '#e6edf3' }}
                      formatter={(v) => [`${v}%`, 'Utilisation']}
                    />
                    <ReferenceLine x={OEE_TARGET} stroke="#f97316" strokeDasharray="4 4" />
                    <Bar dataKey="pct" radius={[0, 4, 4, 0]} barSize={14}>
                      {data.utilisation.map((d, i) => (
                        <Cell key={i} fill={d.pct >= OEE_TARGET ? '#22c55e' : d.pct >= 50 ? '#eab308' : '#ef4444'} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* Machine Detail — full-width, below the two top charts */}
            <div className="card p-4">
              <h3 className="section-title mb-0">Machine Detail</h3>
              <div className="overflow-x-auto thin-scroll max-h-72">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-text-muted font-mono uppercase text-left">
                      <th className="py-1.5 pr-2">Machine</th>
                      <th className="py-1.5 pr-2">Orders</th>
                      <th className="py-1.5 pr-2">Busy (h)</th>
                      <th className="py-1.5 pr-2">Available (h)</th>
                      <th className="py-1.5 pr-2">Util %</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.detail.map((d) => (
                      <tr key={d.machine} className="border-t border-ink-700">
                        <td className="py-1.5 pr-2 text-text">{d.machine}</td>
                        <td className="py-1.5 pr-2">{d.orders}</td>
                        <td className="py-1.5 pr-2">{Math.round(d.busyMins / 60)}</td>
                        <td className="py-1.5 pr-2">{Math.round(d.availMins / 60)}</td>
                        <td className="py-1.5 pr-2">{d.pct}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Load Share by Operation (left) + Operation Detail (right) */}
            <div className="grid grid-cols-1 md:grid-cols-5 gap-5">
              <div className="card p-4 md:col-span-2">
                <h3 className="section-title mb-0">Load Share by Operation</h3>
                <ResponsiveContainer width="100%" height={280}>
                  <PieChart>
                    <Pie data={data.loadShare} dataKey="mins" nameKey="task" innerRadius={55} outerRadius={95} paddingAngle={1}>
                      {data.loadShare.map((_, i) => (
                        <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                      ))}
                    </Pie>
                    <Tooltip
                      contentStyle={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 8 }}
                      labelStyle={{ color: '#f97316', fontWeight: 600 }}
                      itemStyle={{ color: '#e6edf3' }}
                      formatter={(v, n) => [`${Math.round(v / 60)}h`, n]}
                    />
                  </PieChart>
                </ResponsiveContainer>
              </div>

              <div className="card p-4 md:col-span-3">
                <h3 className="section-title mb-0">Operation Detail</h3>
                <div className="overflow-x-auto thin-scroll max-h-72">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-text-muted font-mono uppercase text-left">
                        <th className="py-1.5 pr-2">Operation</th>
                        <th className="py-1.5 pr-2">Orders</th>
                        <th className="py-1.5 pr-2">Load (h)</th>
                        <th className="py-1.5 pr-2">Share %</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.operationDetail.map((d) => (
                        <tr key={d.task} className="border-t border-ink-700">
                          <td className="py-1.5 pr-2 text-text">{d.task}</td>
                          <td className="py-1.5 pr-2">{d.orders}</td>
                          <td className="py-1.5 pr-2">{Math.round(d.mins / 60)}</td>
                          <td className="py-1.5 pr-2">{d.pctShare}%</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
