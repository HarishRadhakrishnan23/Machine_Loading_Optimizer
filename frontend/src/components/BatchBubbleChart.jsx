import { useMemo, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { ScatterChart, Scatter, XAxis, YAxis, ZAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts'

// MOC (material of construction) -> COLOUR. Vivid, glow-friendly hues on the
// Apeiron navy: the two dominant MOCs keep the project's orange / sky identity
// (pushed to full saturation), the rest are spread across clearly different
// hue families so no two read as shades of the same colour.
const FIXED_MOC_COLORS = {
  'Carbon Steel': '#ff7a1a', // Emerson orange, vivid
  'Stainless Steel': '#1ec8ff', // electric sky
  Duplex: '#b05cff', // vivid violet
  'Super Duplex': '#2dffa8', // neon mint
}
const PALETTE = ['#ff3d8b', '#ffe14d', '#6b7bff', '#b6ff3d', '#ff5a4d', '#2de2e6']

// DESIGN -> SHAPE. A bubble's colour says MOC, its shape says Design, its
// size says order count. The hexagon is deliberately first: it echoes the
// Apeiron honeycomb identity and goes to the most common Design.
const SHAPES = ['hexagon', 'circle', 'diamond', 'square', 'triangle', 'star', 'pentagon', 'plus']

const R_MIN = 3.5
const R_MAX = 10.5
const SPREAD_DX = 0.5 // inches between side-by-side batches in one Size×Class cell
const SPREAD_DY = 0.24 // class-band fraction between stacked rows

/** Polygon vertices (flat "x,y x,y …") of a regular n-gon, `rot` radians, circumradius r. */
function ngon(cx, cy, r, n, rot = -Math.PI / 2) {
  return Array.from({ length: n }, (_, i) => {
    const a = rot + (i * 2 * Math.PI) / n
    return `${cx + r * Math.cos(a)},${cy + r * Math.sin(a)}`
  }).join(' ')
}

function starPoints(cx, cy, r) {
  return Array.from({ length: 10 }, (_, i) => {
    const rad = i % 2 === 0 ? r * 1.15 : r * 0.55
    const a = -Math.PI / 2 + (i * Math.PI) / 5
    return `${cx + rad * Math.cos(a)},${cy + rad * Math.sin(a)}`
  }).join(' ')
}

function plusPoints(cx, cy, r) {
  const o = r * 1.1
  const t = r * 0.42
  const pts = [
    [-t, -o], [t, -o], [t, -t], [o, -t], [o, t], [t, t],
    [t, o], [-t, o], [-t, t], [-o, t], [-o, -t], [-t, -t],
  ]
  return pts.map(([x, y]) => `${cx + x},${cy + y}`).join(' ')
}

/** Draws one shape centred on (cx, cy). Used by both the chart and the legend. */
function ShapeMark({ shape, cx, cy, r, fill, stroke, strokeWidth = 1.2, opacity = 1 }) {
  const common = { fill, stroke, strokeWidth, opacity, strokeLinejoin: 'round' }
  switch (shape) {
    case 'circle':
      return <circle cx={cx} cy={cy} r={r} {...common} />
    case 'square': {
      const h = r * 0.9
      return <rect x={cx - h} y={cy - h} width={h * 2} height={h * 2} rx={1.5} {...common} />
    }
    case 'diamond':
      return <polygon points={ngon(cx, cy, r * 1.2, 4)} {...common} />
    case 'triangle':
      return <polygon points={ngon(cx, cy + r * 0.12, r * 1.2, 3)} {...common} />
    case 'pentagon':
      return <polygon points={ngon(cx, cy, r * 1.05, 5)} {...common} />
    case 'star':
      return <polygon points={starPoints(cx, cy, r)} {...common} />
    case 'plus':
      return <polygon points={plusPoints(cx, cy, r)} {...common} />
    case 'hexagon':
    default:
      return <polygon points={ngon(cx, cy, r * 1.05, 6, 0)} {...common} />
  }
}

/** Recharts custom shape: MOC colour fill, Design shape, glow, dim-others-on-hover. */
function BatchShape({ cx, cy, payload, hoveredKey, onHover }) {
  if (cx == null || cy == null) return null
  const dimmed = hoveredKey && hoveredKey !== payload.key
  return (
    <g
      onMouseEnter={() => onHover(payload.key)}
      onMouseLeave={() => onHover(null)}
      style={{ filter: dimmed ? 'none' : `drop-shadow(0 0 3px ${payload.color}88)`, cursor: 'pointer' }}
      opacity={dimmed ? 0.22 : 1}
    >
      <ShapeMark shape={payload.shape} cx={cx} cy={cy} r={payload.r} fill={payload.color} stroke="#0d1117" strokeWidth={1.4} />
    </g>
  )
}

/**
 * SIZE × CLASS bubble plot, one mark per distinct SIZE_INCH~CLASS~MOC~DESIGN
 * batch key (CLAUDE.md batch/fixture key):
 *   x = size, y = class, colour = MOC, shape = Design, size = distinct orders.
 * Batches sharing the same Size×Class used to sit exactly on top of each
 * other; they are now spread side by side inside their cell ("Spread" toggle,
 * on by default — the tooltip always reports the true values, never the
 * nudged position).
 */
export default function BatchBubbleChart({ assignments }) {
  const [spread, setSpread] = useState(true)
  const [hoveredKey, setHoveredKey] = useState(null)

  const { points, mocLegend, designLegend, classes } = useMemo(() => {
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
      } else if (a.operation_no === existing.operation_no) {
        // Same operation, another LINE_NO slice (§E.18) — the slices'
        // quantities add up to the operation's full balance.
        existing.qty += a.balance_qty || 0
      }
    }

    const batches = Object.values(byKey)

    // MOC -> colour (index-based, never hashed, so distinct MOCs can't collide).
    const mocOrder = []
    for (const v of batches) if (!mocOrder.includes(v.moc)) mocOrder.push(v.moc)
    let nextPaletteIndex = 0
    const colorForMoc = {}
    for (const moc of mocOrder) {
      colorForMoc[moc] = FIXED_MOC_COLORS[moc] || PALETTE[nextPaletteIndex++ % PALETTE.length]
    }

    // Design -> shape, most-used Design first so the commonest gets the hexagon.
    const designCounts = {}
    for (const v of batches) designCounts[v.design] = (designCounts[v.design] || 0) + v.orders.size
    const designsRanked = Object.keys(designCounts).sort((a, b) => designCounts[b] - designCounts[a] || a.localeCompare(b))
    const shapeForDesign = {}
    designsRanked.forEach((d, i) => {
      shapeForDesign[d] = SHAPES[i % SHAPES.length]
    })

    const classes = [...new Set(batches.map((v) => v.cls))].sort((a, b) => a - b)
    const classIndex = Object.fromEntries(classes.map((c, i) => [c, i]))
    const maxCount = Math.max(1, ...batches.map((v) => v.orders.size))

    // Dodge: batches in the same Size×Class cell are laid out side by side
    // (two or three rows once a cell holds more than 3), grouped by MOC then Design.
    const cells = {}
    for (const v of batches) (cells[`${v.size}|${v.cls}`] ||= []).push(v)
    for (const group of Object.values(cells)) {
      group.sort((a, b) => a.moc.localeCompare(b.moc) || a.design.localeCompare(b.design))
      const n = group.length
      const rows = n <= 2 ? 1 : n <= 6 ? 2 : 3
      const cols = Math.ceil(n / rows)
      group.forEach((v, i) => {
        const r = Math.floor(i / cols)
        const c = i % cols
        v.dx = (c - (cols - 1) / 2) * SPREAD_DX
        v.dy = (r - (rows - 1) / 2) * SPREAD_DY
      })
    }

    const points = batches.map((v) => {
      const qty = [...v.orders.values()].reduce((sum, o) => sum + o.qty, 0)
      const count = v.orders.size
      return {
        key: v.key,
        size: v.size,
        cls: v.cls,
        moc: v.moc,
        design: v.design,
        count,
        qty,
        color: colorForMoc[v.moc],
        shape: shapeForDesign[v.design],
        r: R_MIN + (R_MAX - R_MIN) * Math.sqrt(count / maxCount),
        x0: v.size,
        y0: classIndex[v.cls],
        x1: v.size + v.dx,
        y1: classIndex[v.cls] + v.dy,
      }
    })
    // Largest first so small marks are drawn on top and never hidden.
    points.sort((a, b) => b.count - a.count)

    const mocCounts = {}
    for (const p of points) mocCounts[p.moc] = (mocCounts[p.moc] || 0) + p.count
    const mocLegend = Object.entries(mocCounts)
      .sort((a, b) => b[1] - a[1])
      .map(([moc]) => ({ moc, color: colorForMoc[moc] }))
    const designLegend = designsRanked.map((d) => ({ design: d, shape: shapeForDesign[d] }))

    return { points, mocLegend, designLegend, classes }
  }, [assignments])

  if (points.length === 0) {
    return <p className="text-sm text-text-faint py-8 text-center">No batch data to plot yet.</p>
  }

  const plotted = points.map((p) => ({ ...p, x: spread ? p.x1 : p.x0, y: spread ? p.y1 : p.y0 }))

  return (
    <div>
      <div className="flex justify-end -mb-1">
        <label className="flex items-center gap-1.5 text-[11px] text-text-muted cursor-pointer select-none">
          <input type="checkbox" className="accent-[#f97316]" checked={spread} onChange={(e) => setSpread(e.target.checked)} />
          Spread overlapping batches
        </label>
      </div>

      <ResponsiveContainer width="100%" height={400}>
        <ScatterChart margin={{ top: 10, right: 20, bottom: 10, left: 0 }}>
          <CartesianGrid stroke="#30363d" />
          <XAxis type="number" dataKey="x" name="Size" unit='"' domain={[0, 50]} tick={{ fontSize: 11, fill: '#8b949e' }} />
          {/* Numeric (not category) so batches can be nudged inside their class band;
              ticks are pinned to whole indexes and printed as the class value. */}
          <YAxis
            type="number"
            dataKey="y"
            name="Class"
            domain={[-0.6, classes.length - 0.4]}
            ticks={classes.map((_, i) => i)}
            interval={0}
            tickFormatter={(v) => classes[v] ?? ''}
            tick={{ fontSize: 11, fill: '#8b949e' }}
          />
          <ZAxis type="number" dataKey="count" range={[60, 900]} name="Orders" />
          <Tooltip cursor={{ strokeDasharray: '3 3' }} content={<BubbleTooltip />} />
          <Scatter
            data={plotted}
            shape={(props) => <BatchShape {...props} hoveredKey={hoveredKey} onHover={setHoveredKey} />}
            isAnimationActive={false}
          />
        </ScatterChart>
      </ResponsiveContainer>

      {/* Legend: colour = MOC, shape = Design, size = orders */}
      <div className="mt-3 px-1 space-y-2">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
          <span className="text-[10px] font-mono uppercase tracking-widest text-text-faint w-16">Colour · MOC</span>
          {mocLegend.map(({ moc, color }) => (
            <div key={moc} className="flex items-center gap-1.5 text-[11px] text-text-muted">
              <span
                className="w-2.5 h-2.5 rounded-full shrink-0"
                style={{ backgroundColor: color, boxShadow: `0 0 6px ${color}` }}
              />
              {moc}
            </div>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
          <span className="text-[10px] font-mono uppercase tracking-widest text-text-faint w-16">Shape · Design</span>
          {designLegend.map(({ design, shape }) => (
            <div key={design || '(none)'} className="flex items-center gap-1.5 text-[11px] text-text-muted">
              <svg width="16" height="16" viewBox="0 0 16 16">
                <ShapeMark shape={shape} cx={8} cy={8} r={5.5} fill="#8b949e" stroke="#0d1117" strokeWidth={1} />
              </svg>
              {design || '(no design)'}
            </div>
          ))}
          <span className="text-[11px] text-text-faint ml-auto">Size = number of orders · hover a mark to isolate it</span>
        </div>
      </div>
    </div>
  )
}

/**
 * Fully custom tooltip — Recharts' default content for a multi-axis Scatter
 * (X/Y/Z all carrying a `name`) renders an extra, unlabelled "label" line
 * above the itemized rows (observed as a stray bare number); bypassing it
 * entirely and reading straight from the hovered point's own payload avoids
 * that artifact. Reports the batch's TRUE size/class (never the spread
 * position). Accent stripe + swatch use the point's own MOC colour, and the
 * Design line carries the same shape used on the chart.
 */
function BubbleTooltip({ active, payload }) {
  const p = active && payload && payload.length > 0 ? payload[0].payload : null
  return (
    <AnimatePresence>
      {p && (
        <motion.div
          initial={{ opacity: 0, scale: 0.92, y: 4 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          transition={{ duration: 0.15, ease: 'easeOut' }}
          style={{
            background: '#161b22',
            border: '1px solid #30363d',
            borderLeft: `3px solid ${p.color}`,
            borderRadius: 8,
            boxShadow: '0 10px 25px -5px rgb(0 0 0 / 0.45)',
          }}
          className="px-3.5 py-2.5 text-xs min-w-[170px]"
        >
          <p className="text-accent font-semibold mb-1.5 font-mono">{p.key}</p>
          <div className="space-y-0.5 text-text">
            <p>Size: {p.size}"</p>
            <p>Class: {p.cls}</p>
            <p className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full" style={{ backgroundColor: p.color }} />
              MOC: {p.moc}
            </p>
            <p className="flex items-center gap-1.5">
              <svg width="14" height="14" viewBox="0 0 16 16">
                <ShapeMark shape={p.shape} cx={8} cy={8} r={5} fill={p.color} stroke="#0d1117" strokeWidth={1} />
              </svg>
              Design: {p.design || '(none)'}
            </p>
            <p className="pt-1 mt-1 border-t border-ink-600 font-semibold">{p.count} orders</p>
            <p className="font-semibold">{p.qty} pieces</p>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
