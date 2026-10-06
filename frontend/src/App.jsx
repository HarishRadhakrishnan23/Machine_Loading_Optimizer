import { useState } from 'react'
import { DndProvider } from 'react-dnd'
import { HTML5Backend } from 'react-dnd-html5-backend'
import clsx from 'clsx'

import { ToastProvider } from './hooks/useToast'
import { SimulationProvider } from './hooks/useSimulationReport'
import HoneycombBackground from './components/HoneycombBackground'
import Overview from './views/Overview'
import ScheduleView from './views/ScheduleView'
import MachineUtilisation from './views/MachineUtilisation'
import CompletedMaterials from './views/CompletedMaterials'
import RunComparison from './views/RunComparison'
import DownloadArchive from './views/DownloadArchive'
import OrderBoard from './views/OrderBoard'
import ImpactAnalyser from './views/ImpactAnalyser'
import MachineAvailability from './views/MachineAvailability'

const TABS = [
  { key: 'overview', label: 'Overview', icon: '◈' },
  { key: 'pending', label: 'Pending Load Queue', icon: '☰' },
  { key: 'utilisation', label: 'Machine Utilisation', icon: '▦' },
  { key: 'completed', label: 'Completed Materials', icon: '✓' },
  { key: 'compare', label: 'Run Comparison', icon: '⇄' },
  { key: 'download', label: 'Download Archive', icon: '⇩' },
  { key: 'orders', label: 'Order Board', icon: '▤', group: 'What-If (Engine 2)' },
  { key: 'impact', label: 'Impact Analyser', icon: '△', group: 'What-If (Engine 2)' },
  { key: 'machines', label: 'Machines & Settings', icon: '⚙' },
]

export default function App() {
  const [active, setActive] = useState('overview')

  return (
    <ToastProvider>
      <SimulationProvider>
        <DndProvider backend={HTML5Backend}>
          <div className="min-h-screen flex">
            <HoneycombBackground />

            {/* Sidebar */}
            <aside className="w-64 shrink-0 bg-ink-900 text-text flex flex-col border-r border-ink-600">
              <div className="px-5 py-5 border-b border-ink-600 flex items-center gap-2.5">
                <span className="text-accent text-xl">⚙</span>
                <div>
                  <p className="text-sm font-semibold text-text leading-tight tracking-wide">APEIRON</p>
                  <p className="text-[11px] text-text-muted font-mono">Machine Loading Scheduler</p>
                </div>
              </div>
              <nav className="flex-1 px-2 py-4 flex flex-col gap-1 overflow-y-auto">
                {TABS.map((tab, i) => (
                  <div key={tab.key}>
                    {tab.group && (TABS[i - 1]?.group !== tab.group) && (
                      <p className="px-3 pt-4 pb-1 text-[10px] font-mono uppercase tracking-widest text-text-faint">
                        {tab.group}
                      </p>
                    )}
                    <button
                      onClick={() => setActive(tab.key)}
                      className={clsx(
                        'w-full flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-sm font-medium text-left transition-colors',
                        active === tab.key
                          ? 'bg-accent text-ink-950'
                          : 'text-text-muted hover:bg-ink-700 hover:text-text',
                      )}
                    >
                      <span className="text-base w-4 text-center">{tab.icon}</span>
                      {tab.label}
                    </button>
                  </div>
                ))}
              </nav>
              <div className="px-4 py-4 border-t border-ink-600 text-[11px] text-text-faint font-mono">
                Emerson Process Management
                <br />
                Valve Manufacturing
              </div>
            </aside>

            {/* Main content — all views stay mounted so state/data persists across tab switches */}
            <main className="flex-1 min-w-0 overflow-y-auto h-screen relative">
              <div className={active === 'overview' ? '' : 'hidden'}>
                <Overview />
              </div>
              <div className={active === 'pending' ? '' : 'hidden'}>
                <ScheduleView />
              </div>
              <div className={active === 'utilisation' ? '' : 'hidden'}>
                <MachineUtilisation />
              </div>
              <div className={active === 'completed' ? '' : 'hidden'}>
                <CompletedMaterials />
              </div>
              <div className={active === 'compare' ? '' : 'hidden'}>
                <RunComparison />
              </div>
              <div className={active === 'download' ? '' : 'hidden'}>
                <DownloadArchive />
              </div>
              <div className={active === 'orders' ? '' : 'hidden'}>
                <OrderBoard onNavigateToImpact={() => setActive('impact')} />
              </div>
              <div className={active === 'impact' ? '' : 'hidden'}>
                <ImpactAnalyser />
              </div>
              <div className={active === 'machines' ? '' : 'hidden'}>
                <MachineAvailability />
              </div>

              {/* Trademark — tiny, transparent, bottom corner (Frontend doc) */}
              <div className="pointer-events-none fixed bottom-2 right-3 text-[10px] text-text-muted/70 font-mono select-none">
                Developed by Harish Radhakrishnan
              </div>
            </main>
          </div>
        </DndProvider>
      </SimulationProvider>
    </ToastProvider>
  )
}
