import { useMemo } from 'react'
import { ScatterChart, Scatter, XAxis, YAxis, ZAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts'

/**
 * SIZE × CLASS bubble plot, bubble = distinct order count sharing that
 * SIZE_INCH~CLASS~MOC~DESIGN batch key (CLAUDE.md batch/fixture key) —
 * the prototype's best-received visualization (Frontend doc), rebuilt
 * against Model E's real BATCH_KEY instead of the old ML-engine fields.
 */
export default function BatchBubbleChart({ assignments }) {
  const points = useMemo(() => {
    const byKey = {}
    for (const a of assignments) {
      if (!a.batch_key) continue
      const parts = a.batch_key.split('~')
      const size = Number(parts[0])
      const cls = Number(parts[1])
      if (!Number.isFinite(size) || !Number.isFinite(cls)) continue
      const k = a.batch_key
      if (!byKey[k]) byKey[k] = { size, cls, orders: new Set(), key: k }
      byKey[k].orders.add(a.production_order)
    }
    return Object.values(byKey).map((v) => ({
      size: v.size,
      cls: v.cls,
      count: v.orders.size,
      key: v.key,
    }))
  }, [assignments])

  if (points.length === 0) {
    return <p className="text-sm text-text-faint py-8 text-center">No batch data to plot yet.</p>
  }

  return (
    <ResponsiveContainer width="100%" height={320}>
      <ScatterChart margin={{ top: 10, right: 20, bottom: 10, left: 0 }}>
        <CartesianGrid stroke="#30363d" />
        <XAxis type="number" dataKey="size" name="Size" unit='"' tick={{ fontSize: 11, fill: '#8b949e' }} />
        <YAxis type="number" dataKey="cls" name="Class" tick={{ fontSize: 11, fill: '#8b949e' }} />
        <ZAxis type="number" dataKey="count" range={[60, 900]} name="Orders" />
        <Tooltip
          cursor={{ strokeDasharray: '3 3' }}
          contentStyle={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 8, fontSize: 12 }}
          labelStyle={{ color: '#e6edf3' }}
          formatter={(value, name, props) => {
            if (name === 'Orders') return [`${value} orders`, props.payload.key]
            return [value, name]
          }}
        />
        <Scatter data={points} fill="#f97316" fillOpacity={0.75} />
      </ScatterChart>
    </ResponsiveContainer>
  )
}
