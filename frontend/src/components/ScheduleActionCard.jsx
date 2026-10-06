import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import clsx from 'clsx'

/**
 * ScheduleActionCard — the Generate/Freeze Schedule primary actions on
 * Overview. /schedule/generate is a single blocking Oracle call (~43s on
 * live data, no backend progress events — model_e_pipeline.py runs to
 * completion before responding), so this gives an HONEST elapsed-time
 * counter plus a phase-estimate progress bar paced against that known
 * typical duration, rather than faking a precise percentage.
 */
export default function ScheduleActionCard({
  icon,
  title,
  description,
  actionLabel,
  onRun,
  phases, // [{ label, atPercent }], or null for a simple indeterminate run (freeze)
  estimatedSeconds,
  formatResult,
}) {
  const [running, setRunning] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const timerRef = useRef(null)

  useEffect(() => () => clearInterval(timerRef.current), [])

  const percent = phases && estimatedSeconds ? Math.min(99, (elapsed / estimatedSeconds) * 100) : null
  const currentPhase = phases
    ? [...phases].reverse().find((p) => percent >= p.atPercent) ?? phases[0]
    : null

  const handleRun = async () => {
    setRunning(true)
    setResult(null)
    setError(null)
    setElapsed(0)
    const startedAt = Date.now()
    timerRef.current = setInterval(() => setElapsed((Date.now() - startedAt) / 1000), 200)
    try {
      const res = await onRun()
      setResult(res)
    } catch (e) {
      setError(e.message || 'Failed')
    } finally {
      clearInterval(timerRef.current)
      setRunning(false)
    }
  }

  return (
    <div className="card p-6 flex flex-col gap-4 relative overflow-hidden h-full">
      <div className="flex items-start gap-3">
        <span className="text-2xl text-accent shrink-0">{icon}</span>
        <div className="flex-1 min-w-0">
          <p className="text-base font-semibold text-text">{title}</p>
          <p className="text-sm text-text-muted mt-0.5 break-words">{description}</p>
        </div>
      </div>

      <button
        className="btn-primary w-full mt-auto"
        onClick={handleRun}
        disabled={running}
      >
        {running ? 'Running…' : actionLabel}
      </button>

      <AnimatePresence>
        {running && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden"
          >
            <div className="flex items-center justify-between text-xs font-mono text-text-muted mb-1.5">
              <span>{currentPhase ? currentPhase.label : 'Working…'}</span>
              <span>{elapsed.toFixed(1)}s elapsed</span>
            </div>
            <div className="h-1.5 rounded-full bg-ink-700 overflow-hidden">
              <motion.div
                className={clsx('h-full rounded-full', percent !== null ? 'bg-accent' : 'bg-sky')}
                initial={{ width: '0%' }}
                animate={{ width: percent !== null ? `${percent}%` : ['10%', '90%', '10%'] }}
                transition={percent !== null ? { ease: 'easeOut' } : { duration: 1.4, repeat: Infinity }}
              />
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {result && !running && (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            className="text-xs font-mono text-status-safe bg-status-safeBg rounded-lg px-3 py-2"
          >
            {formatResult ? formatResult(result) : 'Done'}
          </motion.div>
        )}
        {error && !running && (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            className="text-xs font-mono text-status-risk bg-status-riskBg rounded-lg px-3 py-2"
          >
            {error}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
