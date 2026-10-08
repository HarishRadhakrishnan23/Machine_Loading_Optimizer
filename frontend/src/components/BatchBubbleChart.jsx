import { useMemo } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { ScatterChart, Scatter, XAxis, YAxis, ZAxis, Tooltip, ResponsiveContainer, CartesianGrid, Cell } from 'recharts'

// Distinct, readable-on-dark categorical palette — one color per MOC (material
// of construction), so e.g. a Carbon Steel batch and a Stainless Steel batch
// at the same Size×Class are visually distinguishable, not just two orange dots.
// Deliberately ordered so adjacent entries read as different hue families
// (not just different shades of red/orange) when assigned in sequence.
const PALETTE = [
  '#a78bfa', '#34d399', '#f472b6',
  '#facc15', '#818cf8', '#2dd4bf', '#fb7185', '#a3e635',
]
// The two dominant MOCs get fixed, familiar colors rather than whatever the
// first-seen palette slot happens to land on — Carbon Steel orange, Stainless
// Steel sky blue, matching the project's established color language elsewhere.
const FIXED_MOC_COLORS = { 'Carbon Steel': '#f97316', 'Stainless Steel': '#38bdf8' }

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
      if (!byKey[k]) byKey[k] = { size, cls, moc, design, orders: new Map(), key: k }
      // Per order, keep only its highest OPERATION_NO row's qty — avoids
      // double-counting the same physical pieces across that order's
      // multiple operation rows (same convention as the backend's
      // "last operation's balance" completed-materials count).
      const existing = byKey[k].orders.get(a.production_order)
      if (!existing || a.operation_no > existing.operation_no) {
        byKey[k].orders.set(a.production_order, { operation_no: a.operation_no, qty: a.balance_qty || 0 })
      }
    }

    // Stable per-MOC color assignment (by first-seen order), ranked by total
    // order count afterward for the legend — assignment is index-based, not
    // hashed, so distinct MOCs never collide onto the same palette slot
    // (hashing two differently-named MOCs into the same bucket was exactly
    // why most bubbles all read as "reddish" before).
    const mocOrder = []
    for (const v of Object.values(byKey)) {
      if (!mocOrder.includes(v.moc)) mocOrder.push(v.moc)
    }
    let nextPaletteIndex = 0
    const colorForMoc = {}
    for (const moc of mocOrder) {
      if (FIXED_MOC_COLORS[moc]) {
        colorForMoc[moc] = FIXED_MOC_COLORS[moc]
      } else {
        colorForMoc[moc] = PALETTE[nextPaletteIndex % PALETTE.length]
        nextPaletteIndex++
      }
    }

    const points = Object.values(byKey).map((v) => {
      const qty = [...v.orders.values()].reduce((sum, o) => sum + o.qty, 0)
      return {
        size: v.size,
        cls: v.cls,
        count: v.orders.size,
        qty,
        key: v.key,
        moc: v.moc,
        design: v.design,
        color: colorForMoc[v.moc],
      }
    })

    const mocCounts = {}
    for (const p of points) mocCounts[p.moc] = (mocCounts[p.moc] || 0) + p.count
    const mocLegend = Object.entries(mocCounts)
      .sort((a, b) => b[1] - a[1])
      .map(([moc]) => ({ moc, color: colorForMoc[moc] }))

    // Sorted ascending so a categorical Y axis (even row spacing regardless
    // of the numeric gap between class values, e.g. 300->600 no wider a band
    // than 100->300) lists them bottom-to-top in the right order — a
    // category axis takes its row order from first appearance in the data.
    points.sort((a, b) => a.cls - b.cls)

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
          <XAxis type="number" dataKey="size" name="Size" unit='"' domain={[0, 50]} tick={{ fontSize: 11, fill: '#8b949e' }} />
          <YAxis type="category" dataKey="cls" name="Class" allowDuplicatedCategory={false} tick={{ fontSize: 11, fill: '#8b949e' }} />
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
            <p className="font-semibold">{payload[0].payload.qty} pieces</p>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
