import { useDrop } from 'react-dnd'
import clsx from 'clsx'
import { ORDER_CARD_TYPE } from './OrderCard'

/** Drop target for OrderCard drag — collects orders queued for Engine 2 priority simulation. */
export default function ElevateTray({ queued, onAdd, onRemove, onSimulate, simulating }) {
  const [{ isOver, canDrop }, drop] = useDrop(() => ({
    accept: ORDER_CARD_TYPE,
    drop: (item) => onAdd(item.productionOrder),
    collect: (monitor) => ({ isOver: monitor.isOver(), canDrop: monitor.canDrop() }),
  }))

  return (
    <div
      ref={drop}
      className={clsx(
        'card p-4 border-2 border-dashed transition-colors sticky top-4',
        isOver ? 'border-brand-500 bg-brand-50' : 'border-slate-200',
      )}
    >
      <h3 className="text-sm font-semibold text-slate-800 mb-1">Elevate & Simulate</h3>
      <p className="text-xs text-slate-500 mb-3">
        Drag order cards here (or click them) to queue a priority-elevation simulation with Engine 2.
      </p>

      {queued.length === 0 ? (
        <div className="rounded-lg bg-slate-50 border border-slate-100 py-6 text-center text-xs text-slate-400">
          Drop orders here
        </div>
      ) : (
        <ul className="flex flex-col gap-1.5 mb-3">
          {queued.map((id) => (
            <li
              key={id}
              className="flex items-center justify-between rounded-lg bg-brand-50 px-2.5 py-1.5 text-sm text-brand-800"
            >
              <span className="font-medium">{id}</span>
              <button
                className="text-brand-400 hover:text-brand-700"
                onClick={() => onRemove(id)}
                aria-label={`Remove ${id}`}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}

      <button
        className="btn-primary w-full"
        disabled={queued.length === 0 || simulating}
        onClick={onSimulate}
      >
        {simulating ? 'Simulating…' : `Run Simulation (${queued.length})`}
      </button>
    </div>
  )
}
