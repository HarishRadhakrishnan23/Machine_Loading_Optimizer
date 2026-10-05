import clsx from 'clsx'

const TONES = {
  neutral: 'text-text',
  brand: 'text-accent',
  sky: 'text-sky',
  safe: 'text-status-safe',
  atrisk: 'text-status-warn',
  breach: 'text-status-risk',
}

/** Compact KPI tile — label, big number, optional sub-line. Used for at-a-glance summaries. */
export default function StatTile({ label, value, sub, tone = 'neutral', icon }) {
  return (
    <div className="card p-4 flex flex-col gap-1 min-w-[140px]">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-text-muted uppercase tracking-wide font-mono">{label}</span>
        {icon}
      </div>
      <span className={clsx('text-2xl font-semibold tabular-nums', TONES[tone])}>{value}</span>
      {sub && <span className="text-xs text-text-faint">{sub}</span>}
    </div>
  )
}
