import { useEffect, useRef } from 'react'

/**
 * Consistent page header across all views: title + subtitle + right-aligned actions.
 * Pinned: the wheel over the header does nothing (it never scrolls the page behind it); the page scrolls
 * when the pointer is over the page body, and a table scrolls when the pointer is over that table.
 */
export default function PageHeader({ title, subtitle, actions }) {
  const ref = useRef(null)

  useEffect(() => {
    const el = ref.current
    if (!el) return undefined
    const stop = (e) => e.preventDefault()
    el.addEventListener('wheel', stop, { passive: false }) // React's onWheel is passive and cannot cancel
    return () => el.removeEventListener('wheel', stop)
  }, [])

  return (
    <div ref={ref} className="sticky top-0 z-20 bg-ink-950/95 backdrop-blur border-b border-ink-600 px-6 py-4 flex items-center justify-between">
      <div>
        <h1 className="text-lg font-semibold text-text">{title}</h1>
        {subtitle && <p className="text-sm text-text-muted mt-0.5">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}
