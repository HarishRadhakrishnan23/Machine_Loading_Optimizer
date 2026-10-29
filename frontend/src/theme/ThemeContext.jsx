import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

import { CHART_COLORS } from './chartColors'

const KEY = 'tov_theme'

function readSaved() {
  try {
    const v = localStorage.getItem(KEY)
    return v === 'light' || v === 'dark' ? v : null
  } catch {
    return null
  }
}

function osPrefersLight() {
  try {
    return window.matchMedia('(prefers-color-scheme: light)').matches
  } catch {
    return false
  }
}

const ThemeContext = createContext({ theme: 'dark', toggle: () => {}, colors: CHART_COLORS.dark })

/**
 * Theme = the planner's saved choice, else the computer's light/dark setting (and it keeps following the
 * computer until the first manual toggle). index.html applies data-theme before first paint; this keeps it
 * in sync afterwards and hands chart code its colours.
 */
export function ThemeProvider({ children }) {
  const [saved, setSaved] = useState(readSaved)
  const [osLight, setOsLight] = useState(osPrefersLight)

  useEffect(() => {
    let mq
    try {
      mq = window.matchMedia('(prefers-color-scheme: light)')
    } catch {
      return undefined
    }
    const onChange = (e) => setOsLight(e.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  const theme = saved ?? (osLight ? 'light' : 'dark')

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
  }, [theme])

  const toggle = useCallback(() => {
    const next = theme === 'light' ? 'dark' : 'light'
    setSaved(next)
    try {
      localStorage.setItem(KEY, next)
    } catch {
      /* private window / blocked storage: the choice just lasts for this visit */
    }
  }, [theme])

  const value = useMemo(() => ({ theme, toggle, colors: CHART_COLORS[theme] }), [theme, toggle])
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export const useTheme = () => useContext(ThemeContext)
