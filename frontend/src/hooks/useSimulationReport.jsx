import { createContext, useContext, useState } from 'react'

const SimulationContext = createContext(null)

/** Shares the latest Engine 2 RiskReport between OrderBoard (triggers it) and ImpactAnalyser (displays it). */
export function SimulationProvider({ children }) {
  const [report, setReport] = useState(null)
  return (
    <SimulationContext.Provider value={{ report, setReport }}>{children}</SimulationContext.Provider>
  )
}

export function useSimulationReport() {
  const ctx = useContext(SimulationContext)
  if (!ctx) throw new Error('useSimulationReport must be used within SimulationProvider')
  return ctx
}
