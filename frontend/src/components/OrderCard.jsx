import { useDrag } from 'react-dnd'
import clsx from 'clsx'
import { differenceInCalendarDays, format, parseISO } from 'date-fns'

export const ORDER_CARD_TYPE = 'ORDER_CARD'

function isValidDate(dateStr) {
  if (!dateStr || dateStr === 'None' || dateStr === 'null') return false
  try {
    const d = parseISO(String(dateStr))
    return !isNaN(d.getTime())
  } catch {
    return false
  }
}

function urgencyTone(cdd) {
  if (!isValidDate(cdd)) return { label: 'Safety stock', tone: 'text-slate-400', ring: 'ring-slate-200' }
  const days = differenceInCalendarDays(parseISO(String(cdd)), new Date())
  if (days < 0) return { label: `${Math.abs(days)}d overdue`, tone: 'text-risk-breach', ring: 'ring-red-200' }
  if (days <= 7) return { label: `${days}d left`, tone: 'text-risk-atrisk', ring: 'ring-amber-200' }
  return { label: `${days}d left`, tone: 'text-risk-safe', ring: 'ring-green-200' }
}

/** Draggable WIP order card. Drop onto the "elevate tray" in OrderBoard to queue for Engine 2 simulation. */
export default function OrderCard({ order, selected, onToggle }) {
  const [{ isDragging }, drag] = useDrag(() => ({
    type: ORDER_CARD_TYPE,
    item: { productionOrder: order.production_order },
    collect: (monitor) => ({ isDragging: monitor.isDragging() }),
  }))

  const urgency = urgencyTone(order.cdd)
  // quantity_ordered_total = quantity_ordered × pending ops — the correct denominator
  // for balance_qty_total, which is itself summed across those same pending ops
  // (balance_qty is a per-operation pipeline quantity, not additive against a single
  // order-level quantity_ordered — see backend main.py /orders/wip for why).
  const denominator = order.quantity_ordered_total ?? order.quantity_ordered * order.operations
  const progressPct =
    denominator > 0
      ? Math.min(100, Math.max(0, Math.round(((denominator - order.balance_qty_total) / denominator) * 100)))
      : 0

  return (
    <div
      ref={drag}
      onClick={() => onToggle(order.production_order)}
      className={clsx(
        'card p-3 cursor-grab active:cursor-grabbing select-none transition-all',
        'hover:shadow-popover hover:-translate-y-0.5',
        selected && 'ring-2 ring-brand-500 border-brand-300',
        isDragging && 'opacity-40',
      )}
      title="Drag to the Elevate tray, or click to select for simulation"
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-slate-800">{order.production_order}</p>
          <p className="text-xs text-slate-500">{order.item}</p>
        </div>
        <span className={clsx('badge ring-1 ring-inset text-[11px]', urgency.tone, urgency.ring)}>
          {urgency.label}
        </span>
      </div>

      <div className="mt-2.5 flex items-center justify-between text-[11px] text-slate-500">
        <span>CDD {isValidDate(order.cdd) ? format(parseISO(String(order.cdd)), 'MMM d, yyyy') : '—'}</span>
        <span>{order.operations} op{order.operations !== 1 ? 's' : ''} left</span>
      </div>

      <div className="mt-2 flex items-center gap-2">
        <div className="flex-1 h-1.5 rounded-full bg-slate-100 overflow-hidden">
          <div className="h-full rounded-full bg-brand-500" style={{ width: `${progressPct}%` }} />
        </div>
        <span className="text-[10px] text-slate-400 tabular-nums w-20 text-right">
          {order.balance_qty_total} pcs left
        </span>
      </div>

      {selected && (
        <div className="mt-2 flex items-center gap-1 text-[11px] font-medium text-brand-600">
          <span className="h-1.5 w-1.5 rounded-full bg-brand-600" /> Queued for elevation
        </div>
      )}
    </div>
  )
}
