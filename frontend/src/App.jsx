import { useState } from 'react'
import { DndProvider } from 'react-dnd'
import { HTML5Backend } from 'react-dnd-html5-backend'
import clsx from 'clsx'

import { ToastProvider } from './hooks/useToast'
import { SimulationProvider } from './hooks/useSimulationReport'
import ScheduleView from './views/ScheduleView'
import OrderBoard from './views/OrderBoard'
import ImpactAnalyser from './views/ImpactAnalyser'
import MachineAvailability from './views/MachineAvailability'

const TABS = [
  { key: 'schedule', label: 'Schedule', icon: '📅' },
  { key: 'orders', label: 'Order Board', icon: '📋' },
  { key: 'impact', label: 'Impact Analyser', icon: '⚠️' },
  { key: 'machines', label: 'Machines & Settings', icon: '⚙️' },
]

export default function App() {
  const [active, setActive] = useState('schedule')

  return (
    <ToastProvider>
      <SimulationProvider>
        <DndProvider backend={HTML5Backend}>
          <div className="min-h-screen flex">
            {/* Sidebar */}
            <aside className="w-60 shrink-0 bg-slate-900 text-slate-200 flex flex-col">
              <div className="px-5 py-5 border-b border-slate-800">
                <p className="text-sm font-semibold text-white leading-tight">TOV Machine Loading</p>
                <p className="text-xs text-slate-400">Optimizer</p>
              </div>
              <nav className="flex-1 px-2 py-4 flex flex-col gap-1">
                {TABS.map((tab) => (
                  <button
                    key={tab.key}
                    onClick={() => setActive(tab.key)}
                    className={clsx(
                      'flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-sm font-medium text-left transition-colors',
                      active === tab.key
                        ? 'bg-brand-600 text-white'
                        : 'text-slate-300 hover:bg-slate-800 hover:text-white',
                    )}
                  >
                    <span className="text-base">{tab.icon}</span>
                    {tab.label}
                  </button>
                ))}
              </nav>
              <div className="px-4 py-4 border-t border-slate-800 text-[11px] text-slate-500">
                Emerson Process Management
                <br />
                TOV Valve Manufacturing
              </div>
            </aside>

            {/* Main content — all 4 views stay mounted so state/data persists across tab switches */}
            <main className="flex-1 min-w-0 bg-slate-50 overflow-y-auto h-screen">
              <div className={active === 'schedule' ? '' : 'hidden'}>
                <ScheduleView />
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
            </main>
          </div>
        </DndProvider>
      </SimulationProvider>
    </ToastProvider>
  )
}
