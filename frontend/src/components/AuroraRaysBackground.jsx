import { useMemo } from 'react'

/**
 * Light-mode ambient background — a northern-lights curtain: many thin vertical
 * rays of green, violet and lavender hanging from a sweeping S-shaped line, each
 * brightest at its lower end and fading upward. Every ray sways, brightens and
 * dims on its own staggered timing, so a shimmer travels along the curtain the
 * way real aurora does. Soft edges, masked so it dissolves into the page.
 * Dark mode keeps the honeycomb. Fixed, behind content, pointer-events-none.
 */
const W = 1000
const H = 800
const RAYS = 110

const PALETTE = [
  { id: 'g', core: '#00bf71', glow: '#6fe6b0' },
  { id: 'v', core: '#6331ff', glow: '#a58bff' },
  { id: 'l', core: '#b59bf3', glow: '#dccdfb' },
]

// Deterministic 0..1 "random" so the picture is identical on every load.
const rnd = (n) => {
  const x = Math.sin(n * 127.1 + 311.7) * 43758.5453
  return x - Math.floor(x)
}

// The line the curtain hangs from: a slow S-curve with a tighter ripple on top.
const edge = (x) => 470 + 95 * Math.sin(x / 135) + 38 * Math.sin(x / 58 + 1.3) - (x - 300) * 0.12

export default function AuroraRaysBackground() {
  const rays = useMemo(
    () =>
      Array.from({ length: RAYS }, (_, i) => {
        const t = i / (RAYS - 1)
        const x = 300 + t * 760
        const w = 6 + rnd(i + 1) * 10
        const h = 140 + rnd(i + 7) * 190 + 80 * Math.sin(t * Math.PI) // taller mid-curtain
        const c = rnd(i + 13)
        const colour = c < 0.55 ? PALETTE[0] : c < 0.82 ? PALETTE[1] : PALETTE[2]
        return {
          x: x - w / 2, w, h, y: edge(x) - h, colour,
          op: 0.45 + rnd(i + 19) * 0.45,
          dur: 5 + rnd(i + 23) * 5,
          delay: -i * 0.33, // staggered along x -> shimmer travels across the curtain
        }
      }),
    [],
  )

  return (
    <svg
      aria-hidden="true"
      className="fixed inset-0 w-full h-full pointer-events-none select-none -z-10"
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="xMaxYMin slice"
    >
      <style>{`
        @keyframes aurora-ray {
          0%, 100% { transform: translate(0px, 6px) scaleY(0.88); opacity: var(--op-lo); }
          50%      { transform: translate(7px, -12px) scaleY(1.1); opacity: var(--op-hi); }
        }
        .aurora-ray {
          transform-box: fill-box; transform-origin: 50% 100%;
          animation-name: aurora-ray; animation-timing-function: ease-in-out; animation-iteration-count: infinite;
        }
        @media (prefers-reduced-motion: reduce) { .aurora-ray { animation: none; } }
      `}</style>
      <defs>
        {PALETTE.map((p) => (
          // Light gathers at the bottom of each ray and thins out upward.
          <linearGradient key={p.id} id={`aurora-ray-${p.id}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={p.core} stopOpacity="0" />
            <stop offset="55%" stopColor={p.core} stopOpacity="0.28" />
            <stop offset="90%" stopColor={p.glow} stopOpacity="1" />
            <stop offset="100%" stopColor={p.glow} stopOpacity="0" />
          </linearGradient>
        ))}
        <filter id="aurora-soft" x="-10%" y="-10%" width="120%" height="120%">
          <feGaussianBlur stdDeviation="2.2" />
        </filter>
        <filter id="aurora-halo" x="-30%" y="-30%" width="160%" height="160%">
          <feGaussianBlur stdDeviation="30" />
        </filter>
        <linearGradient id="aurora-halo-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#6331ff" stopOpacity="0" />
          <stop offset="70%" stopColor="#8f6bff" stopOpacity="0.22" />
          <stop offset="100%" stopColor="#00bf71" stopOpacity="0.2" />
        </linearGradient>
        <radialGradient id="aurora-fade" cx="72%" cy="48%" r="64%">
          <stop offset="0%" stopColor="#fff" stopOpacity="1" />
          <stop offset="65%" stopColor="#fff" stopOpacity="0.8" />
          <stop offset="100%" stopColor="#fff" stopOpacity="0" />
        </radialGradient>
        <mask id="aurora-mask" maskUnits="userSpaceOnUse" x="0" y="0" width={W} height={H}>
          <rect width={W} height={H} fill="url(#aurora-fade)" />
        </mask>
      </defs>

      <g mask="url(#aurora-mask)">
        {/* faint haze behind the rays, following the same curve */}
        <path
          d={`M300,${edge(300) - 230} ` +
            Array.from({ length: 20 }, (_, i) => {
              const x = 300 + (i / 19) * 760
              return `L${x.toFixed(0)},${(edge(x) - 230).toFixed(0)}`
            }).join(' ') +
            ` L1060,${edge(1060) + 30} ` +
            Array.from({ length: 20 }, (_, i) => {
              const x = 1060 - (i / 19) * 760
              return `L${x.toFixed(0)},${(edge(x) + 30).toFixed(0)}`
            }).join(' ') + ' Z'}
          fill="url(#aurora-halo-grad)"
          filter="url(#aurora-halo)"
        />
        <g filter="url(#aurora-soft)">
          {rays.map((r, i) => (
            <rect
              key={i}
              className="aurora-ray"
              x={r.x} y={r.y} width={r.w} height={r.h + 14}
              rx={r.w / 2}
              fill={`url(#aurora-ray-${r.colour.id})`}
              style={{
                '--op-lo': r.op * 0.5,
                '--op-hi': r.op,
                animationDuration: `${r.dur}s`,
                animationDelay: `${r.delay}s`,
              }}
            />
          ))}
        </g>
      </g>
    </svg>
  )
}
