/** Consistent page header across all 4 views: title + subtitle + right-aligned actions. */
export default function PageHeader({ title, subtitle, actions }) {
  return (
    <div className="sticky top-0 z-20 bg-slate-50/95 backdrop-blur border-b border-slate-200 px-6 py-4 flex items-center justify-between">
      <div>
        <h1 className="text-lg font-semibold text-slate-900">{title}</h1>
        {subtitle && <p className="text-sm text-slate-500 mt-0.5">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}
