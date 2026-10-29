import { useMemo, useState } from 'react'

/**
 * Per-column filter row for tables: a small input under each column header.
 *
 * `cols` must be a stable array (module constant): [{ label, get: (row) => text }]. `get` should return the text the
 * user SEES in that column, so typing what you read finds the row. A box matches case-insensitively on "contains";
 * several boxes combine with AND.
 *
 *   const cf = useColumnFilters(rows, COLS)
 *   <th><FilterHeader cf={cf} i={0} /></th>  ...  cf.rows.map(...)
 */
export function useColumnFilters(rows, cols) {
  const [filters, setFilters] = useState({})

  const filtered = useMemo(() => {
    const active = cols
      .map((_, i) => [i, (filters[i] || '').trim().toLowerCase()])
      .filter(([, q]) => q)
    if (active.length === 0) return rows
    return rows.filter((r) => active.every(([i, q]) => String(cols[i].get(r) ?? '').toLowerCase().includes(q)))
  }, [rows, cols, filters])

  return {
    cols,
    rows: filtered,
    filters,
    isFiltering: Object.values(filters).some((v) => (v || '').trim()),
    setFilter: (i, v) => setFilters((f) => ({ ...f, [i]: v })),
    clear: () => setFilters({}),
  }
}

/** Header label with its filter box underneath. Put inside a <th>; `sortable` clicks on the box never trigger a sort. */
export function FilterHeader({ cf, i, bare = false }) {
  return (
    <>
      {!bare && <div>{cf.cols[i].label}</div>}
      <input
        value={cf.filters[i] || ''}
        onChange={(e) => cf.setFilter(i, e.target.value)}
        onClick={(e) => e.stopPropagation()}
        placeholder="Filter…"
        aria-label={`Filter ${cf.cols[i].label}`}
        className="mt-1.5 block w-full min-w-[4.5rem] rounded border border-ink-600 bg-ink-800 px-1.5 py-1 text-[11px] font-normal normal-case tracking-normal text-text placeholder:text-text-faint outline-none focus:border-accent"
      />
    </>
  )
}

/** "No rows match" row + a clear button, for when the column filters hide everything. */
export function NoMatchRow({ cf, colSpan }) {
  return (
    <tr>
      <td colSpan={colSpan} className="px-3 py-8 text-center text-text-faint">
        No rows match the column filters.{' '}
        <button className="text-accent underline" onClick={cf.clear}>Clear filters</button>
      </td>
    </tr>
  )
}
