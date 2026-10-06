import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'

import { generateSchedule, freezeSchedule, exportScheduleExcel, getCurrentSchedule } from '../api/client'
import { useToast } from '../hooks/useToast'
import ScheduleActionCard from '../components/ScheduleActionCard'

const GENERATE_PHASES = [
  { label: 'Loading WIP, machine, fixture & routing data from Oracle…', atPercent: 0 },
  { label: 'Running dispatch simulation — batching, fixture pool, safety-stock speculation…', atPercent: 30 },
  { label: 'Writing schedule to MCH_SCHEDULE_OUTPUT…', atPercent: 85 },
]
const GENERATE_ESTIMATE_SECONDS = 45

const PROCESS_STEPS = ['Foundry', 'Machining', 'Fixture / Locator', 'QA', 'Delivery']

function fmtTimestamp(iso) {
  if (!iso) return null
  const d = new Date(iso)
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export default function Overview() {
  const toast = useToast()
  const [lastGenerated, setLastGenerated] = useState(null)

  useEffect(() => {
    getCurrentSchedule()
      .then((data) => {
        const ts = data?.assignments?.[0]?.generated_at
        if (ts) setLastGenerated(fmtTimestamp(ts))
      })
      .catch(() => {})
  }, [])

  return (
    <div className="min-h-screen flex flex-col">
      {/* ── Hero ────────────────────────────────────────────── */}
      <div className="px-10 pt-14 pb-10 relative overflow-hidden">
        {/* Quiet background process-flow motif */}
        <svg
          className="absolute inset-0 w-full h-full opacity-[0.07] pointer-events-none"
          viewBox="0 0 1000 200"
          preserveAspectRatio="none"
        >
          <motion.path
            d="M 0 150 Q 150 50 300 150 T 600 150 T 1000 100"
            fill="none"
            stroke="#f97316"
            strokeWidth="2"
            initial={{ pathLength: 0 }}
            animate={{ pathLength: 1 }}
            transition={{ duration: 2.2, ease: 'easeInOut' }}
          />
        </svg>

        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
          className="relative"
        >
          <p className="text-[11px] font-mono uppercase tracking-[0.2em] text-accent mb-3">
            Emerson Process Management · Valve Manufacturing
          </p>
          <div className="relative inline-block">
            {/* Soft blurred glow copy, sitting behind the crisp text */}
            <h1
              aria-hidden="true"
              className="title-gradient absolute inset-0 text-6xl font-bold tracking-tight blur-xl opacity-60 select-none"
            >
              Apeiron
            </h1>
            <h1 className="title-gradient relative text-6xl font-bold tracking-tight">Apeiron</h1>
            <motion.div
              className="h-[3px] rounded-full mt-2"
              style={{ background: 'linear-gradient(90deg, #f97316, #38bdf8)', transformOrigin: 'left' }}
              initial={{ scaleX: 0 }}
              animate={{ scaleX: 1 }}
              transition={{ delay: 0.4, duration: 0.9, ease: 'easeOut' }}
            />
          </div>
          <p className="text-lg text-text-muted mt-2 max-w-2xl">
            The Machine Loading Scheduler - a deterministic, continuous-time dispatch engine that turns
            every pending valve order into a shop-floor-realistic schedule, governed by real fixture and
            locator availability, not guesswork.
          </p>
        </motion.div>

        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.5, duration: 0.5 }}
          className="relative flex items-center gap-2 mt-6 text-xs font-mono text-text-faint"
        >
          {PROCESS_STEPS.map((step, i) => (
            <div key={step} className="flex items-center gap-2">
              <span className="px-2.5 py-1 rounded-full border border-ink-600 bg-ink-800">{step}</span>
              {i < PROCESS_STEPS.length - 1 && <span className="text-ink-600">→</span>}
            </div>
          ))}
        </motion.div>

        {lastGenerated && (
          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.7 }}
            className="relative text-xs text-text-faint font-mono mt-5 flex items-center gap-1.5"
          >
            <span className="w-1.5 h-1.5 rounded-full bg-status-safe inline-block" />
            Schedule last generated: {lastGenerated}
          </motion.p>
        )}
      </div>

      {/* ── Primary actions ────────────────────────────────────────────── */}
      <motion.div
        initial={{ opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.3, duration: 0.5 }}
        className="px-10 pb-14 grid grid-cols-1 md:grid-cols-3 gap-5 max-w-5xl"
      >
        <ScheduleActionCard
          icon="▶"
          title="Generate Schedule"
          description="Runs Engine 1 end-to-end against live Oracle data and writes a fresh Schdule Output."
          actionLabel="Generate Schedule"
          phases={GENERATE_PHASES}
          estimatedSeconds={GENERATE_ESTIMATE_SECONDS}
          onRun={async () => {
            const res = await generateSchedule()
            toast.success(`Schedule generated — ${res.rows_written} rows written`)
            setLastGenerated(fmtTimestamp(new Date().toISOString()))
            return res
          }}
          formatResult={(res) =>
            `${res.rows_written} rows · ${res.scheduled_count} scheduled · ${res.scheduled_no_fixture_count} no-fixture · ${res.excluded_count} excluded`
          }
        />

        <ScheduleActionCard
          icon="⇩"
          title="Export to Excel"
          description="Downloads the current MCH_SCHEDULE_OUTPUT (all rows) as an .xlsx workbook."
          actionLabel="Export to Excel"
          phases={null}
          onRun={async () => {
            const filename = await exportScheduleExcel()
            toast.success(`Downloaded ${filename}`)
            return { filename }
          }}
          formatResult={(res) => `Saved ${res.filename}`}
        />
        
        <ScheduleActionCard
          icon="⧉"
          title="Freeze Schedule"
          description="Archives the current MCH_SCHEDULE_OUTPUT into MCH_SCHEDULE_OUTPUT_ARCHIVE for historical record."
          actionLabel="Freeze Schedule"
          phases={null}
          onRun={async () => {
            const res = await freezeSchedule()
            if (res.rows_archived === 0) {
              toast.info('Nothing to freeze — generate a schedule first')
            } else {
              toast.success(`Archived ${res.rows_archived} rows under run ${res.run_id?.slice(0, 8)}…`)
            }
            return res
          }}
          formatResult={(res) =>
            res.rows_archived === 0 ? 'Nothing to freeze' : `${res.rows_archived} rows archived`
          }
        />
      </motion.div>
    </div>
  )
}
