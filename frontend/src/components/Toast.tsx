import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react'
import { AlertTriangle, Check } from 'lucide-react'

interface ToastItem { id: number; text: string; kind: 'ok' | 'error' }
interface ToastApi { ok: (text: string) => void; error: (err: unknown, prefix?: string) => void }
const Ctx = createContext<ToastApi | null>(null)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const seq = useRef(0)
  const push = useCallback((text: string, kind: ToastItem['kind']) => {
    const id = ++seq.current
    setItems((x) => [...x.slice(-2), { id, text, kind }])
    setTimeout(() => setItems((x) => x.filter((i) => i.id !== id)), kind === 'error' ? 7000 : 3500)
  }, [])
  const api = useMemo<ToastApi>(() => ({
    ok: (text) => push(text, 'ok'),
    error: (err, prefix) => push((prefix ? prefix + ' ' : '') + (err instanceof Error ? err.message : String(err)), 'error'),
  }), [push])
  return (
    <Ctx.Provider value={api}>
      {children}
      <div className="toast-region" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast${t.kind === 'error' ? ' toast--error' : ''}`}>
            {t.kind === 'error' ? <AlertTriangle size={16} aria-hidden /> : <Check size={16} aria-hidden />}
            <span>{t.text}</span>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  )
}
export function useToast(): ToastApi {
  const v = useContext(Ctx)
  if (!v) throw new Error('useToast must be used inside ToastProvider')
  return v
}
