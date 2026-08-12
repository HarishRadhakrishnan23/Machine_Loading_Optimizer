import { createContext, useCallback, useContext, useState } from 'react'
import clsx from 'clsx'

const ToastContext = createContext(null)

let idCounter = 0

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])

  const dismiss = useCallback((id) => {
    setToasts((t) => t.filter((x) => x.id !== id))
  }, [])

  const push = useCallback(
    (message, tone = 'info', timeout = 4000) => {
      const id = ++idCounter
      setToasts((t) => [...t, { id, message, tone }])
      if (timeout) setTimeout(() => dismiss(id), timeout)
      return id
    },
    [dismiss],
  )

  const toast = {
    success: (msg) => push(msg, 'success'),
    error: (msg) => push(msg, 'error', 6000),
    info: (msg) => push(msg, 'info'),
  }

  return (
    <ToastContext.Provider value={toast}>
      {children}
      <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 w-80">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={clsx(
              'card px-4 py-3 text-sm shadow-popover flex items-start gap-2 animate-[fadeIn_0.15s_ease-out]',
              t.tone === 'success' && 'border-l-4 border-l-risk-safe',
              t.tone === 'error' && 'border-l-4 border-l-risk-breach',
              t.tone === 'info' && 'border-l-4 border-l-brand-500',
            )}
          >
            <span className="flex-1 text-slate-700">{t.message}</span>
            <button className="text-slate-400 hover:text-slate-600" onClick={() => dismiss(t.id)}>
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used within ToastProvider')
  return ctx
}
