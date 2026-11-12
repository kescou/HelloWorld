import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

export type ThemeChoice = 'system' | 'light' | 'dark'
interface Prefs {
  theme: ThemeChoice
  resolvedTheme: 'light' | 'dark'
  setTheme: (t: ThemeChoice) => void
  privacy: boolean
  togglePrivacy: () => void
}
const Ctx = createContext<Prefs | null>(null)

const read = (k: string) => { try { return localStorage.getItem(k) } catch { return null } }
const write = (k: string, v: string | null) => { try { if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v) } catch { /* storage unavailable */ } }

export function PrefsProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeChoice>(() => { const t = read('vermoegen.theme'); return t === 'light' || t === 'dark' ? t : 'system' })
  const [privacy, setPrivacy] = useState(() => read('vermoegen.privacy') === '1')
  const [systemDark, setSystemDark] = useState(() => typeof matchMedia !== 'undefined' && matchMedia('(prefers-color-scheme: dark)').matches)

  useEffect(() => {
    const mq = matchMedia('(prefers-color-scheme: dark)')
    const on = (e: MediaQueryListEvent) => setSystemDark(e.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])
  useEffect(() => {
    const el = document.documentElement
    if (theme === 'system') delete el.dataset.theme
    else el.dataset.theme = theme
  }, [theme])
  useEffect(() => {
    const el = document.documentElement
    if (privacy) el.dataset.privacy = 'on'
    else delete el.dataset.privacy
  }, [privacy])

  const setTheme = useCallback((t: ThemeChoice) => { setThemeState(t); write('vermoegen.theme', t === 'system' ? null : t) }, [])
  const togglePrivacy = useCallback(() => setPrivacy((p) => { write('vermoegen.privacy', p ? null : '1'); return !p }), [])
  const resolvedTheme = theme === 'system' ? (systemDark ? 'dark' : 'light') : theme
  const value = useMemo(() => ({ theme, resolvedTheme, setTheme, privacy, togglePrivacy }), [theme, resolvedTheme, setTheme, privacy, togglePrivacy])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function usePrefs(): Prefs {
  const v = useContext(Ctx)
  if (!v) throw new Error('usePrefs must be used inside PrefsProvider')
  return v
}
