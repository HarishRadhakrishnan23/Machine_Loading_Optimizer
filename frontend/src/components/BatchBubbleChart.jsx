import { useMemo } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { ScatterChart, Scatter, XAxis, YAxis, ZAxis, Tooltip, ResponsiveContainer, CartesianGrid, Cell } from 'recharts'

// Distinct, readable-on-dark categorical palette — one color per MOC (material
// of construction), so e.g. a Carbon Steel batch and a Stainless Steel batch
// at the same Size×Class are visually distinguishable, not just two orange dots.
const PALETTE = [
  '#f97316', '#38bdf8', '#a78bfa', '#f472b6', '#34d399',
  '#fbbf24', '#818cf8', '#2dd4bf', '#f87171', '#facc15',
]
function colorFor(key) {
  let hash = 0
  for (let i = 0; i < key.length; i++) hash = (hash * 31 + key.charCodeAt(i)) % PALETTE.length
  return PALETTE[Math.abs(hash) % PALETTE.length]
}

/**
 * SIZE × CLASS bubble plot, bubble = distinct order count sharing that
 * SIZE_INCH~CLASS~MOC~DESIGN batch key (CLAUDE.md batch/fixture key) —
 * the prototype's best-received visualization (Frontend doc), rebuilt
 * against Model E's real BATCH_KEY instead of the old ML-engine fields.
 * Colored by MOC (not a flat orange) so compositions at the same Size×Class
 * stay visually distinguishable.
 */
export default function BatchBubbleChart({ assignments }) {
  const { points, mocLegend } = useMemo(() => {
    const byKey = {}
    for (const a of assignments) {
      if (!a.batch_key) continue
      const parts = a.batch_key.split('~')
      const size = Number(parts[0])
      const cls = Number(parts[1])
      const moc = parts[2] || 'Unknown'
      const design = parts[3] || ''
      if (!Number.isFinite(size) || !Number.isFinite(cls)) continue
      const k = a.batch_key
      if (!byKey[k]) byKey[k] = { size, cls, moc, design, orders: new Set(), key: k }
      byKey[k].orders.add(a.production_order)
    }
    const points = Object.values(byKey).map((v) => ({
      size: v.size,
      cls: v.cls,
      count: v.orders.size,
      key: v.key,
      moc: v.moc,
      design: v.design,
      color: colorFor(v.moc),
    }))

    // Legend: every distinct MOC present, ranked by total order count.
    const mocCounts = {}
    for (const p of points) mocCounts[p.moc] = (mocCounts[p.moc] || 0) + p.count
    const mocLegend = Object.entries(mocCounts)
      .sort((a, b) => b[1] - a[1])
      .map(([moc]) => ({ moc, color: colorFor(moc) }))

    return { points, mocLegend }
  }, [assignments])

  if (points.length === 0) {
    return <p className="text-sm text-text-faint py-8 text-center">No batch data to plot yet.</p>
  }

  return (
    <div>
      <ResponsiveContainer width="100%" height={320}>
        <ScatterChart margin={{ top: 10, right: 20, bottom: 10, left: 0 }}>
          <CartesianGrid stroke="#30363d" />
          <XAxis type="number" dataKey="size" name="Size" unit='"' tick={{ fontSize: 11, fill: '#8b949e' }} />
          <YAxis type="number" dataKey="cls" name="Class" tick={{ fontSize: 11, fill: '#8b949e' }} />
          <ZAxis type="number" dataKey="count" range={[60, 900]} name="Orders" />
          <Tooltip cursor={{ strokeDasharray: '3 3' }} content={<BubbleTooltip />} />
          <Scatter data={points} fillOpacity={0.8}>
            {points.map((p, i) => (
              <Cell key={i} fill={p.color} />
            ))}
          </Scatter>
        </ScatterChart>
      </ResponsiveContainer>

      {/* MOC legend */}
      <div className="flex flex-wrap gap-x-4 gap-y-1.5 mt-3 px-1">
        {mocLegend.map(({ moc, color }) => (
          <div key={moc} className="flex items-center gap-1.5 text-[11px] text-text-muted">
            <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: color }} />
            {moc}
          </div>
        ))}
      </div>
    </div>
  )
}

/**
 * Fully custom tooltip — Recharts' default content for a multi-axis Scatter
 * (X/Y/Z all carrying a `name`) renders an extra, unlabelled "label" line
 * above the itemized rows (observed as a stray bare number); bypassing it
 * entirely and reading straight from the hovered point's own payload avoids
 * that artifact. Animated entrance + an accent stripe colored by the point's
 * own MOC for a more polished feel than a flat static box.
 */
function BubbleTooltip({ active, payload }) {
  return (
    <AnimatePresence>
      {active && payload && payload.length > 0 && (
        <motion.div
          initial={{ opacity: 0, scale: 0.92, y: 4 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          transition={{ duration: 0.15, ease: 'easeOut' }}
          style={{
            background: '#161b22',
            border: '1px solid #30363d',
            borderLeft: `3px solid ${payload[0].payload.color}`,
            borderRadius: 8,
            boxShadow: '0 10px 25px -5px rgb(0 0 0 / 0.45)',
          }}
          className="px-3.5 py-2.5 text-xs min-w-[160px]"
        >
          <p className="text-accent font-semibold mb-1.5 font-mono">{payload[0].payload.key}</p>
          <div className="space-y-0.5 text-text">
            <p>Size: {payload[0].payload.size}"</p>
            <p>Class: {payload[0].payload.cls}</p>
            <p>MOC: {payload[0].payload.moc}</p>
            <p className="pt-1 mt-1 border-t border-ink-600 font-semibold">{payload[0].payload.count} orders</p>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
