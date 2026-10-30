import { useMemo } from 'react'

/**
 * Light-mode ambient background — long ribbons of northern-lights glow that
 * flow down the right side of the page, top to bottom, like the swooping
 * bands in an aurora photograph. Each ribbon snakes in an S-curve, tapers to
 * nothing at both ends, has a bright pale core inside a wide soft halo, and
 * slowly sways sideways (SMIL path morphing). Fades out toward the left.
 * Dark mode keeps the honeycomb. The earlier vertical-rays look is kept in
 * AuroraRaysBackground.jsx. Fixed, behind content, pointer-events-none.
 */
const W = 1000
const H = 800
const STEPS = 16

// Closed polygon for one ribbon: a centre line x(t) with a width that tapers to 0 at both ends.
function ribbonPath({ x0, amp, waves, width, phase, lean }, scale = 1) {
  const left = []
  const right = []
  for (let i = 0; i <= STEPS; i++) {
    const t = i / STEPS
    const y = -30 + t * (H + 60)
    const cx =
      x0 + lean * (t - 0.5) * 200 +
      amp * Math.sin(t * Math.PI * waves + phase) +
      amp * 0.35 * Math.sin(t * Math.PI * waves * 2.1 + phase * 1.6)
    const w = (width * scale * Math.pow(Math.sin(Math.PI * t), 0.7)) / 2
    left.push([cx - w, y])
    right.push([cx + w, y])
  }
  const pts = [...left, ...right.reverse()]
  return 'M' + pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' L') + ' Z'
}

const RIBBONS = [
  { id: 'a', x0: 800, amp: 62, waves: 1.6, width: 150, lean: 0.2, color: '#00bf71', core: '#c9f7e1', op: 0.6, dur: 21 },
  { id: 'b', x0: 700, amp: 54, waves: 1.3, width: 120, lean: -0.3, color: '#7b30dc', core: '#e3d6fc', op: 0.45, dur: 27 },
  { id: 'c', x0: 900, amp: 48, waves: 2.0, width: 110, lean: 0.1, color: '#b59bf3', core: '#f3eefe', op: 0.8, dur: 24 },
  { id: 'd', x0: 610, amp: 44, waves: 1.7, width: 90, lean: 0.35, color: '#00bf71', core: '#d9f9ea', op: 0.40, dur: 32 },
  { id: 'e', x0: 840, amp: 38, waves: 2.4, width: 70, lean: -0.15, color: '#6331ff', core: '#dcd2ff', op: 0.38, dur: 36 },
]

export default function AuroraBackground() {
  const reduceMotion = useMemo(
    () => typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches,
    [],
  )
  const shapes = useMemo(
    () =>
      RIBBONS.map((r, i) => {
        const ph = [0, 1.5, 3.0, 4.5].map((p) => p + i * 1.1)
        const halo = ph.map((p) => ribbonPath({ ...r, phase: p }))
        const core = ph.map((p) => ribbonPath({ ...r, phase: p }, 0.3))
        return { ...r, haloFrames: [...halo, halo[0]], coreFrames: [...core, core[0]] }
      }),
    [],
  )

  const anim = (frames, dur) =>
    !reduceMotion && <animate attributeName="d" dur={`${dur}s`} repeatCount="indefinite" values={frames.join(';')} />

  return (
    <svg
      aria-hidden="true"
      className="fixed inset-0 w-full h-full pointer-events-none select-none -z-10"
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="xMaxYMin slice"
    >
      <defs>
        {shapes.map((s) => (
          // Light builds along the ribbon and thins out at both ends (top and bottom).
          <linearGradient key={s.id} id={`aurora-r-${s.id}`} gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="0" y2={H}>
            <stop offset="0%" stopColor={s.color} stopOpacity="0" />
            <stop offset="25%" stopColor={s.color} stopOpacity={s.op * 0.7} />
            <stop offset="55%" stopColor={s.color} stopOpacity={s.op} />
            <stop offset="85%" stopColor={s.color} stopOpacity={s.op * 0.6} />
            <stop offset="100%" stopColor={s.color} stopOpacity="0" />
          </linearGradient>
        ))}
        {shapes.map((s) => (
          <linearGradient key={`c${s.id}`} id={`aurora-c-${s.id}`} gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="0" y2={H}>
            <stop offset="0%" stopColor={s.core} stopOpacity="0" />
            <stop offset="30%" stopColor={s.core} stopOpacity="0.55" />
            <stop offset="55%" stopColor="#ffffff" stopOpacity="0.85" />
            <stop offset="80%" stopColor={s.core} stopOpacity="0.5" />
            <stop offset="100%" stopColor={s.core} stopOpacity="0" />
          </linearGradient>
        ))}
        <filter id="aurora-halo" x="-40%" y="-10%" width="180%" height="120%">
          <feGaussianBlur stdDeviation="26" />
        </filter>
        <filter id="aurora-core" x="-40%" y="-10%" width="180%" height="120%">
          <feGaussianBlur stdDeviation="9" />
        </filter>
        <linearGradient id="aurora-fade-x" gradientUnits="userSpaceOnUse" x1="430" y1="0" x2="760" y2="0">
          <stop offset="0%" stopColor="#fff" stopOpacity="0" />
          <stop offset="100%" stopColor="#fff" stopOpacity="1" />
        </linearGradient>
        <mask id="aurora-mask" maskUnits="userSpaceOnUse" x="0" y="0" width={W} height={H}>
          <rect width={W} height={H} fill="url(#aurora-fade-x)" />
        </mask>
      </defs>

      <g mask="url(#aurora-mask)">
        <g filter="url(#aurora-halo)">
          {shapes.map((s) => (
            <path key={s.id} d={s.haloFrames[0]} fill={`url(#aurora-r-${s.id})`}>{anim(s.haloFrames, s.dur)}</path>
          ))}
        </g>
        <g filter="url(#aurora-core)">
          {shapes.map((s) => (
            <path key={s.id} d={s.coreFrames[0]} fill={`url(#aurora-c-${s.id})`}>{anim(s.coreFrames, s.dur)}</path>
          ))}
        </g>
      </g>
    </svg>
  )
}
