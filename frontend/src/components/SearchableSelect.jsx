import { useEffect, useMemo, useRef, useState } from 'react'
import clsx from 'clsx'

/**
 * Type-to-filter combobox — behaves like a native <select> (click to open,
 * click an option to choose) but also lets you type to filter the list
 * (e.g. type "Oct" to narrow a long list of timestamped runs down to just
 * October), since a plain <select> only supports jump-to-letter, not
 * substring search. Options: [{ value, label }]. Controlled via value/onChange.
 */
export default function SearchableSelect({ options, value, onChange, placeholder = 'Select…', className }) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const rootRef = useRef(null)

  const selected = options.find((o) => o.value === value)

  useEffect(() => {
    function onClickOutside(e) {
      if (rootRef.current && !rootRef.current.contains(e.target)) {
        setOpen(false)
        setQuery('')
      }
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [])

  const filtered = useMemo(() => {
    if (!query) return options
    const q = query.toLowerCase()
    return options.filter((o) => o.label.toLowerCase().includes(q))
  }, [options, query])

  return (
    <div ref={rootRef} className={clsx('relative', className)}>
      <input
        className="input cursor-pointer"
        value={open ? query : selected?.label ?? ''}
        placeholder={placeholder}
        onFocus={() => {
          setOpen(true)
          setQuery('')
        }}
        onChange={(e) => {
          setQuery(e.target.value)
          setOpen(true)
        }}
      />
      {open && (
        <div className="absolute z-30 mt-1 w-full max-h-64 overflow-y-auto thin-scroll card py-1 shadow-popover">
          {filtered.length === 0 && (
            <p className="px-3 py-2 text-xs text-text-faint">No matches</p>
          )}
          {filtered.map((o) => (
            <button
              key={o.value}
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => {
                onChange(o.value)
                setOpen(false)
                setQuery('')
              }}
              className={clsx(
                'w-full text-left px-3 py-1.5 text-sm transition-colors',
                o.value === value ? 'bg-accent text-ink-950' : 'text-text hover:bg-ink-700',
              )}
            >
              {o.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
