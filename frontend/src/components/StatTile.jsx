import clsx from 'clsx'

const TONES = {
  neutral: 'text-slate-900',
  brand: 'text-brand-700',
  safe: 'text-risk-safe',
  atrisk: 'text-risk-atrisk',
  breach: 'text-risk-breach',
}

/** Compact KPI tile — label, big number, optional sub-line. Used for at-a-glance summaries. */
export default function StatTile({ label, value, sub, tone = 'neutral', icon }) {
  return (
    <div className="card p-4 flex flex-col gap-1 min-w-[140px]">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-slate-500 uppercase tracking-wide">{label}</span>
        {icon}
      </div>
      <span className={clsx('text-2xl font-semibold tabular-nums', TONES[tone])}>{value}</span>
      {sub && <span className="text-xs text-slate-400">{sub}</span>}
    </div>
  )
}
