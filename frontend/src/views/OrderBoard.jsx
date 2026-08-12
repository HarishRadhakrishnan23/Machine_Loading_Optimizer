import { useCallback, useEffect, useMemo, useState } from 'react'

import PageHeader from '../components/PageHeader'
import StatTile from '../components/StatTile'
import OrderCard from '../components/OrderCard'
import ElevateTray from '../components/ElevateTray'
import { LoadingPanel, EmptyPanel, ErrorPanel } from '../components/LoadingState'
import { useToast } from '../hooks/useToast'
import { useSimulationReport } from '../hooks/useSimulationReport'
import { getWipOrders, simulatePriority } from '../api/client'

const SORTS = {
  cdd: (a, b) => new Date(a.cdd || '9999-12-31') - new Date(b.cdd || '9999-12-31'),
  balance: (a, b) => b.balance_qty_total - a.balance_qty_total,
  order: (a, b) => a.production_order.localeCompare(b.production_order),
}

export default function OrderBoard({ onNavigateToImpact }) {
  const [orders, setOrders] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [search, setSearch] = useState('')
  const [sortKey, setSortKey] = useState('cdd')
  const [queued, setQueued] = useState([])
  const [simulating, setSimulating] = useState(false)
  const toast = useToast()
  const { setReport } = useSimulationReport()

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getWipOrders()
      setOrders(data.orders || [])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const filtered = useMemo(() => {
    if (!orders) return []
    let list = orders
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      list = list.filter(
        (o) => o.production_order.toLowerCase().includes(q) || o.item.toLowerCase().includes(q),
      )
    }
    return [...list].sort(SORTS[sortKey])
  }, [orders, search, sortKey])

  const toggleQueued = (id) => {
    setQueued((q) => (q.includes(id) ? q.filter((x) => x !== id) : [...q, id]))
  }

  const addQueued = (id) => {
    setQueued((q) => (q.includes(id) ? q : [...q, id]))
  }

  const removeQueued = (id) => setQueued((q) => q.filter((x) => x !== id))

  const runSimulation = async () => {
    setSimulating(true)
    try {
      const report = await simulatePriority(queued)
      setReport(report)
      toast.success(
        `Simulation complete: ${report.impacts.length} orders impacted · ` +
          `${report.safe_count} safe, ${report.at_risk_count} at risk, ${report.breach_count} breach. ` +
          `See Impact Analyser.`,
      )
      setQueued([])
      onNavigateToImpact?.()
    } catch (e) {
      toast.error(`Simulation failed: ${e.message}`)
    } finally {
      setSimulating(false)
    }
  }

  const stats = useMemo(() => {
    if (!orders) return null
    const overdue = orders.filter((o) => o.cdd && new Date(o.cdd) < new Date()).length
    const dueSoon = orders.filter((o) => {
      if (!o.cdd) return false
      const days = (new Date(o.cdd) - new Date()) / 86400000
      return days >= 0 && days <= 7
    }).length
    const totalPieces = orders.reduce((s, o) => s + o.balance_qty_total, 0)
    return { total: orders.length, overdue, dueSoon, totalPieces }
  }, [orders])

  return (
    <div>
      <PageHeader
        title="Order Board"
        subtitle="Active WIP orders · drag a card into the Elevate tray to simulate priority changes"
        actions={
          <>
            <input
              className="input w-56"
              placeholder="Search order or item…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <select className="input w-40" value={sortKey} onChange={(e) => setSortKey(e.target.value)}>
              <option value="cdd">Sort: Due date</option>
              <option value="balance">Sort: Balance qty</option>
              <option value="order">Sort: Order ID</option>
            </select>
          </>
        }
      />

      <div className="p-6">
        {loading && <LoadingPanel label="Loading WIP orders…" />}
        {!loading && error && <ErrorPanel message={error} onRetry={load} />}

        {!loading && !error && stats && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            <StatTile label="Active orders" value={stats.total} tone="brand" />
            <StatTile label="Overdue" value={stats.overdue} tone={stats.overdue > 0 ? 'breach' : 'neutral'} />
            <StatTile
              label="Due within 7 days"
              value={stats.dueSoon}
              tone={stats.dueSoon > 0 ? 'atrisk' : 'neutral'}
            />
            <StatTile label="Pieces remaining" value={stats.totalPieces.toLocaleString()} />
          </div>
        )}

        {!loading && !error && filtered.length === 0 && (
          <EmptyPanel title="No matching orders" hint="Try clearing your search filter." />
        )}

        {!loading && !error && filtered.length > 0 && (
          <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
            <div className="lg:col-span-3 grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
              {filtered.map((order) => (
                <OrderCard
                  key={order.production_order}
                  order={order}
                  selected={queued.includes(order.production_order)}
                  onToggle={toggleQueued}
                />
              ))}
            </div>
            <div className="lg:col-span-1">
              <ElevateTray
                queued={queued}
                onAdd={addQueued}
                onRemove={removeQueued}
                onSimulate={runSimulation}
                simulating={simulating}
              />
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
