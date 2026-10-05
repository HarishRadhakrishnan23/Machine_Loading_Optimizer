/** Consistent page header across all views: title + subtitle + right-aligned actions. */
export default function PageHeader({ title, subtitle, actions }) {
  return (
    <div className="sticky top-0 z-20 bg-ink-950/95 backdrop-blur border-b border-ink-600 px-6 py-4 flex items-center justify-between">
      <div>
        <h1 className="text-lg font-semibold text-text">{title}</h1>
        {subtitle && <p className="text-sm text-text-muted mt-0.5">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}
