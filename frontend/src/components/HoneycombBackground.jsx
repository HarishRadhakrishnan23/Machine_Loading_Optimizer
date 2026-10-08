import { useMemo } from 'react'

/**
 * A bottom-right ambient glow — hexagons styled after recessed ceiling
 * lighting (warm glow bleeding from within each cell, dark frame edge),
 * anchored to the corner but faded out with a smooth radial mask so it
 * dissolves into the page background rather than ending at a visible box
 * edge. Deliberately modest in opacity — a corner flourish the eye should
 * barely register, never something competing with page text.
 * Fixed + bottom/right-anchored so it never extends under the sidebar.
 * pointer-events-none, no app state.
 */
const HEX_R = 50
const COLS = 7
const ROWS = 8
const GLOW_COLORS = ['#f97316', '#38bdf8', '#fb923c']

function hexPoints(cx, cy, r) {
  const pts = []
  for (let i = 0; i < 6; i++) {
    const angle = (Math.PI / 180) * (60 * i)
    pts.push([cx + r * Math.cos(angle), cy + r * Math.sin(angle)])
  }
  return pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
}

export default function HoneycombBackground() {
  const { hexes, pulses, boxW, boxH } = useMemo(() => {
    const colSpacing = HEX_R * 1.5
    const rowSpacing = Math.sqrt(3) * HEX_R
    const boxW = COLS * colSpacing + HEX_R
    const boxH = ROWS * rowSpacing + HEX_R

    const hexes = []
    let colorIdx = 0
    for (let col = 0; col < COLS; col++) {
      for (let row = 0; row < ROWS; row++) {
        const cx = col * colSpacing + HEX_R
        const cy = row * rowSpacing + (col % 2 ? rowSpacing / 2 : 0) + HEX_R
        hexes.push({
          cx, cy,
          points: hexPoints(cx, cy, HEX_R),
          color: GLOW_COLORS[colorIdx % GLOW_COLORS.length],
          gradId: `honeycomb-glow-${col}-${row}`,
        })
        colorIdx++
      }
    }

    // A handful of slow light trails along edges between neighboring hexes,
    // concentrated nearer the corner (where the mask keeps things visible).
    const nearCorner = hexes.filter((h) => h.cx > boxW * 0.45 && h.cy > boxH * 0.45)
    const pulses = [0, 1, 2, 3].map((i) => {
      const a = nearCorner[(i * 5) % nearCorner.length]
      const b = nearCorner[(i * 5 + 3) % nearCorner.length]
      return {
        x1: a.cx, y1: a.cy - HEX_R * 0.5, x2: b.cx, y2: b.cy + HEX_R * 0.5,
        color: GLOW_COLORS[i % GLOW_COLORS.length],
        duration: 5 + i * 1.1,
        delay: i * 1.4,
      }
    })

    return { hexes, pulses, boxW, boxH }
  }, [])

  const maskId = 'honeycomb-fade-mask'

  return (
    <svg
      aria-hidden="true"
      className="fixed bottom-0 right-0 pointer-events-none select-none -z-10"
      style={{ width: boxW, height: boxH }}
      viewBox={`0 0 ${boxW} ${boxH}`}
    >
      <style>{`
        @keyframes honeycomb-pulse-travel {
          0%   { opacity: 0; stroke-dashoffset: 0; }
          15%  { opacity: 0.8; }
          80%  { opacity: 0.8; }
          100% { opacity: 0; stroke-dashoffset: var(--travel); }
        }
        .honeycomb-pulse {
          stroke-dasharray: 16 400;
          animation-name: honeycomb-pulse-travel;
          animation-timing-function: linear;
          animation-iteration-count: infinite;
        }
      `}</style>
      <defs>
        {hexes.map((h) => (
          <radialGradient key={h.gradId} id={h.gradId} cx="50%" cy="45%" r="65%">
            <stop offset="0%" stopColor={h.color} stopOpacity="0.48" />
            <stop offset="60%" stopColor={h.color} stopOpacity="0.28" />
            <stop offset="100%" stopColor={h.color} stopOpacity="0" />
          </radialGradient>
        ))}
        <filter id="honeycomb-soft-glow" x="-80%" y="-80%" width="260%" height="260%">
          <feGaussianBlur stdDeviation="3.5" />
        </filter>
        {/* Smooth radial falloff anchored at the corner — this is what makes
            the cluster dissolve into the background instead of hard-cutting
            at a box edge. White = visible, black = hidden (luminance mask). */}
        <radialGradient id="honeycomb-fade-grad" cx="100%" cy="100%" r="105%">
          <stop offset="0%" stopColor="#ffffff" stopOpacity="1" />
          <stop offset="35%" stopColor="#ffffff" stopOpacity="0.85" />
          <stop offset="70%" stopColor="#ffffff" stopOpacity="0.35" />
          <stop offset="100%" stopColor="#ffffff" stopOpacity="0" />
        </radialGradient>
        <mask id={maskId} maskContentUnits="objectBoundingBox" maskUnits="objectBoundingBox">
          <rect x="0" y="0" width="1" height="1" fill="url(#honeycomb-fade-grad)" />
        </mask>
      </defs>

      <g mask={`url(#${maskId})`}>
        {/* Warm glow bleeding from within each cell */}
        <g filter="url(#honeycomb-soft-glow)">
          {hexes.map((h, i) => (
            <polygon key={i} points={h.points} fill={`url(#${h.gradId})`} />
          ))}
        </g>

        {/* Faint frame edge, like a recessed light fixture's housing */}
        <g fill="none" stroke="#e6edf3" strokeOpacity="0.1" strokeWidth="1">
          {hexes.map((h, i) => (
            <polygon key={i} points={h.points} />
          ))}
        </g>

        {/* A few slow light trails for a hint of motion */}
        <g fill="none" strokeWidth="1.5" strokeLinecap="round" filter="url(#honeycomb-soft-glow)">
          {pulses.map((p, i) => {
            const len = Math.hypot(p.x2 - p.x1, p.y2 - p.y1)
            return (
              <line
                key={i}
                x1={p.x1} y1={p.y1} x2={p.x2} y2={p.y2}
                stroke={p.color}
                className="honeycomb-pulse"
                style={{
                  '--travel': `${-(len + 16)}px`,
                  animationDuration: `${p.duration}s`,
                  animationDelay: `${p.delay}s`,
                }}
              />
            )
          })}
        </g>
      </g>
    </svg>
  )
}
