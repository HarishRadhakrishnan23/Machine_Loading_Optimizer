import clsx from 'clsx'

const STYLES = {
  SAFE: 'bg-risk-safeBg text-risk-safe ring-1 ring-inset ring-green-200',
  AT_RISK: 'bg-risk-atriskBg text-risk-atrisk ring-1 ring-inset ring-amber-200',
  BREACH: 'bg-risk-breachBg text-risk-breach ring-1 ring-inset ring-red-200',
}

const LABELS = {
  SAFE: 'Safe',
  AT_RISK: 'At Risk',
  BREACH: 'Breach',
}

const DOT = {
  SAFE: 'bg-risk-safe',
  AT_RISK: 'bg-risk-atrisk',
  BREACH: 'bg-risk-breach',
}

/** Small colored pill for SAFE / AT_RISK / BREACH — used across all views for one-glance risk. */
export default function RiskBadge({ flag, size = 'md' }) {
  if (!flag) return <span className="text-slate-400 text-xs">—</span>
  return (
    <span
      className={clsx(
        'badge',
        STYLES[flag] || 'bg-slate-100 text-slate-600',
        size === 'sm' && 'px-2 py-0 text-[11px]',
      )}
    >
      <span className={clsx('h-1.5 w-1.5 rounded-full', DOT[flag])} />
      {LABELS[flag] || flag}
    </span>
  )
}
