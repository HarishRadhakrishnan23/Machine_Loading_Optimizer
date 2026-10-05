import { useMemo, useState } from 'react'
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getPaginationRowModel,
  getFilteredRowModel,
  flexRender,
} from '@tanstack/react-table'

const COLUMNS = [
  { accessorKey: 'production_order', header: 'Order' },
  { accessorKey: 'operation_no', header: 'Op' },
  { accessorKey: 'task', header: 'Task' },
  { accessorKey: 'machine_name', header: 'Machine', cell: (i) => i.getValue() ?? '—' },
  { accessorKey: 'shift', header: 'Shift', cell: (i) => i.getValue() ?? '—' },
  { accessorKey: 'scheduled_date', header: 'Date', cell: (i) => i.getValue() ?? '—' },
  { accessorKey: 'balance_qty', header: 'Qty' },
  { accessorKey: 'batch_key', header: 'Batch Key', cell: (i) => <span className="font-mono text-xs">{i.getValue()}</span> },
  {
    accessorKey: 'is_safety_stock',
    header: 'Safety Stock',
    cell: (i) => (i.getValue() ? <span className="badge bg-sky-soft text-sky">Y</span> : <span className="text-text-faint">—</span>),
  },
  {
    accessorKey: 'remark',
    header: 'Remark',
    cell: (i) =>
      i.getValue() ? <span className="text-status-warn text-xs">{i.getValue()}</span> : <span className="text-text-faint">—</span>,
  },
]

/** Paginated, sortable, filterable table over the (potentially 5000+-row) MCH_SCHEDULE_OUTPUT. */
export default function ScheduleTable({ rows }) {
  const [sorting, setSorting] = useState([])
  const [globalFilter, setGlobalFilter] = useState('')

  const columns = useMemo(() => COLUMNS, [])

  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting, globalFilter },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize: 20 } },
  })

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <input
          className="input max-w-xs"
          placeholder="Filter by order, task, machine…"
          value={globalFilter}
          onChange={(e) => setGlobalFilter(e.target.value)}
        />
        <span className="text-xs text-text-faint font-mono">
          {table.getFilteredRowModel().rows.length} rows
        </span>
      </div>

      <div className="overflow-x-auto thin-scroll border border-ink-600 rounded-xl">
        <table className="w-full text-xs border-collapse">
          <thead>
            <tr>
              {table.getHeaderGroups()[0].headers.map((h) => (
                <th
                  key={h.id}
                  onClick={h.column.getToggleSortingHandler()}
                  className="bg-ink-900 border-b border-ink-600 px-3 py-2 text-left font-mono uppercase tracking-wide text-text-muted cursor-pointer select-none whitespace-nowrap"
                >
                  {flexRender(h.column.columnDef.header, h.getContext())}
                  {{ asc: ' ▲', desc: ' ▼' }[h.column.getIsSorted()] ?? ''}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => (
              <tr key={row.id} className="border-b border-ink-700 hover:bg-ink-700/40">
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="px-3 py-2 text-text whitespace-nowrap">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
            {table.getRowModel().rows.length === 0 && (
              <tr>
                <td colSpan={columns.length} className="px-3 py-8 text-center text-text-faint">
                  No rows match this filter.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between mt-3 text-xs text-text-muted">
        <div className="flex items-center gap-1.5">
          <button className="btn-ghost px-2 py-1" onClick={() => table.setPageIndex(0)} disabled={!table.getCanPreviousPage()}>
            «
          </button>
          <button className="btn-ghost px-2 py-1" onClick={() => table.previousPage()} disabled={!table.getCanPreviousPage()}>
            ‹
          </button>
          <span className="font-mono">
            Page {table.getState().pagination.pageIndex + 1} / {table.getPageCount() || 1}
          </span>
          <button className="btn-ghost px-2 py-1" onClick={() => table.nextPage()} disabled={!table.getCanNextPage()}>
            ›
          </button>
          <button
            className="btn-ghost px-2 py-1"
            onClick={() => table.setPageIndex(table.getPageCount() - 1)}
            disabled={!table.getCanNextPage()}
          >
            »
          </button>
        </div>
      </div>
    </div>
  )
}
