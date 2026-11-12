import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { AlertTriangle, ArrowDown, ArrowUp, ChevronsUpDown, RefreshCw, X } from 'lucide-react'
import type { UseQueryResult } from '@tanstack/react-query'
import { fmtMoney, fmtPct, type MoneyOpts } from '../lib/format'

export const cx = (...parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(' ')

/** Amount with privacy blur. `tone` colours by sign (always together with an explicit +/− sign). */
export function Money({ value, tone, className, ...opts }: { value: number | null | undefined; tone?: boolean; className?: string } & MoneyOpts) {
  const signed = opts.signed ?? tone
  const cls = tone && typeof value === 'number' && Math.abs(value) >= 0.005 ? (value > 0 ? 'pos' : 'neg') : ''
  return <span className={cx('amt', cls, className)}>{fmtMoney(value, { ...opts, signed })}</span>
}

function Tri({ up }: { up: boolean }) {
  return (
    <svg width="9" height="8" viewBox="0 0 9 8" aria-hidden="true">
      <path d={up ? 'M4.5 0.5 8.5 7.5H0.5Z' : 'M4.5 7.5 0.5 0.5H8.5Z'} fill="currentColor" />
    </svg>
  )
}
/** Percent change with direction triangle + sign, so gain/loss reads without colour. */
export function Delta({ pct, amount, decimals = 2 }: { pct: number | null | undefined; amount?: number | null; decimals?: number }) {
  if (pct == null || !Number.isFinite(pct)) return <span className="delta delta--flat">–</span>
  const flat = Math.abs(pct) < 0.005
  return (
    <span className={cx('delta', flat ? 'delta--flat' : pct > 0 ? 'delta--gain' : 'delta--loss')}>
      {!flat && <Tri up={pct > 0} />}
      {amount != null && <span className="amt">{fmtMoney(amount, { signed: true })}</span>}
      <span>{amount != null ? `(${fmtPct(pct, { signed: true, decimals })})` : fmtPct(pct, { signed: true, decimals })}</span>
    </span>
  )
}

export function Panel({ title, hint, actions, children, flush, className, foot }: { title?: ReactNode; hint?: ReactNode; actions?: ReactNode; children: ReactNode; flush?: boolean; className?: string; foot?: ReactNode }) {
  return (
    <section className={cx('panel', className)}>
      {(title || actions) && (
        <header className="panel__head">
          {title && <h2>{title}</h2>}
          {hint && <span className="panel__hint">{hint}</span>}
          {actions}
        </header>
      )}
      <div className={cx('panel__body', flush && 'panel__body--flush')}>{children}</div>
      {foot && <footer className="panel__foot">{foot}</footer>}
    </section>
  )
}

export function Skeleton({ h = 16, w = '100%', style }: { h?: number | string; w?: number | string; style?: CSSProperties }) {
  return <div className="skeleton" style={{ height: h, width: w, ...style }} aria-hidden="true" />
}
export function SkeletonRows({ rows = 5, pad = true }: { rows?: number; pad?: boolean }) {
  return (
    <div style={{ display: 'grid', gap: 10, padding: pad ? '12px 16px 16px' : 0 }} aria-busy="true" aria-label="Lädt">
      {Array.from({ length: rows }, (_, i) => <Skeleton key={i} h={18} w={`${92 - ((i * 13) % 30)}%`} />)}
    </div>
  )
}

export function EmptyState({ icon, title, text, children, compact }: { icon?: ReactNode; title: string; text?: ReactNode; children?: ReactNode; compact?: boolean }) {
  return (
    <div className={cx('state', compact && 'state--compact')}>
      {icon}
      <p className="state__title">{title}</p>
      {text && <p className="state__text">{text}</p>}
      {children && <div className="state__actions">{children}</div>}
    </div>
  )
}
export function ErrorState({ error, onRetry, compact, what = 'Daten' }: { error: unknown; onRetry?: () => void; compact?: boolean; what?: string }) {
  return (
    <div className={cx('state', compact && 'state--compact')} role="alert">
      <AlertTriangle size={22} aria-hidden />
      <p className="state__title">{what} konnten nicht geladen werden</p>
      <p className="state__text">{error instanceof Error ? error.message : 'Unbekannter Fehler.'}</p>
      {onRetry && (
        <div className="state__actions">
          <button className="btn" onClick={onRetry}><RefreshCw size={15} aria-hidden /> Erneut versuchen</button>
        </div>
      )}
    </div>
  )
}

/** Renders loading skeleton / error with retry / empty state / content for a query. */
export function Async<D>({ query, children, skeleton, empty, isEmpty, what, compact }: {
  query: UseQueryResult<D>; children: (data: D) => ReactNode; skeleton?: ReactNode; empty?: ReactNode; isEmpty?: (d: D) => boolean; what?: string; compact?: boolean
}) {
  if (query.isPending) return <>{skeleton ?? <SkeletonRows />}</>
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} what={what} compact={compact} />
  const data = query.data as D
  const emptyNow = isEmpty ? isEmpty(data) : Array.isArray(data) && data.length === 0
  if (emptyNow && empty) return <>{empty}</>
  return <>{children(data)}</>
}

export function Seg<V extends string>({ value, onChange, options, label }: { value: V; onChange: (v: V) => void; options: readonly (V | { value: V; label: string })[]; label: string }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => {
        const v = typeof o === 'string' ? o : o.value
        const l = typeof o === 'string' ? o : o.label
        return <button key={v} type="button" aria-pressed={v === value} onClick={() => onChange(v)}>{l}</button>
      })}
    </div>
  )
}

export function Field({ label, hint, error, children, className }: { label: string; hint?: ReactNode; error?: string | null; children: ReactNode; className?: string }) {
  return (
    <label className={cx('field', className)}>
      <span className="field__label">{label}</span>
      {children}
      {error ? <span className="field__error" role="alert">{error}</span> : hint ? <span className="field__hint">{hint}</span> : null}
    </label>
  )
}

export interface Part { label: string; value: number; color: string; hatched?: boolean }
/** The "Vermögensleiste": what a figure is made of. */
export function SplitBar({ parts, small, label }: { parts: Part[]; small?: boolean; label: string }) {
  const total = parts.reduce((s, p) => s + Math.max(0, p.value), 0)
  const text = parts.filter((p) => p.value > 0).map((p) => `${p.label} ${fmtPct(total ? (p.value / total) * 100 : 0)}`).join(', ')
  return (
    <div className={cx('splitbar', small && 'splitbar--sm')} role="img" aria-label={`${label}: ${text}`}>
      {total > 0 && parts.filter((p) => p.value > 0).map((p, i) => (
        <span key={i} className={p.hatched ? 'hatched' : undefined} style={{ width: `${(p.value / total) * 100}%`, backgroundColor: p.color }} title={`${p.label}: ${fmtPct((p.value / total) * 100)}`} />
      ))}
    </div>
  )
}

export function Badge({ tone, children, title }: { tone?: 'accent' | 'gain' | 'loss' | 'warn' | 'est'; children: ReactNode; title?: string }) {
  return <span className={cx('badge', tone && `badge--${tone}`)} title={title}>{children}</span>
}

export type SortDir = 'asc' | 'desc'
export function SortTh<K extends string>({ k, sort, onSort, children, className }: { k: K; sort: { key: K; dir: SortDir }; onSort: (k: K) => void; children: ReactNode; className?: string }) {
  const active = sort.key === k
  return (
    <th className={className} aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
      <button type="button" className="th-sort" data-active={active} onClick={() => onSort(k)}>
        {children}
        {active ? (sort.dir === 'asc' ? <ArrowUp size={13} aria-hidden /> : <ArrowDown size={13} aria-hidden />) : <ChevronsUpDown size={13} aria-hidden style={{ opacity: 0.45 }} />}
      </button>
    </th>
  )
}
export function useSort<K extends string>(initial: K, dir: SortDir = 'desc') {
  const [sort, setSort] = useState<{ key: K; dir: SortDir }>({ key: initial, dir })
  const onSort = (k: K) => setSort((s) => (s.key === k ? { key: k, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key: k, dir: 'desc' }))
  return { sort, onSort }
}
export function sortBy<R>(rows: R[], get: (r: R) => string | number | null | undefined, dir: SortDir): R[] {
  const f = dir === 'asc' ? 1 : -1
  return [...rows].sort((a, b) => {
    const x = get(a), y = get(b)
    if (x == null && y == null) return 0
    if (x == null) return 1
    if (y == null) return -1
    return typeof x === 'string' || typeof y === 'string' ? String(x).localeCompare(String(y), 'de') * f : (x - y) * f
  })
}

/** Side panel for create/edit forms (full width on phones). Closes on Escape and scrim click. */
export function Drawer({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null
    const el = ref.current
    el?.querySelector<HTMLElement>('input:not([type=hidden]), select, textarea, button.btn')?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      if (e.key === 'Tab' && el) {
        const f = el.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled])')
        if (!f.length) return
        const first = f[0], last = f[f.length - 1]
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
      }
    }
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('keydown', onKey); prev?.focus?.() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  return (
    <>
      <div className="sheet-scrim" onClick={onClose} />
      <div className="drawer" role="dialog" aria-modal="true" aria-label={title} ref={ref}>
        <div className="drawer__head">
          <h2>{title}</h2>
          <button className="btn btn--ghost btn--icon" onClick={onClose} aria-label="Schließen"><X size={18} aria-hidden /></button>
        </div>
        <div className="drawer__body">{children}</div>
      </div>
    </>
  )
}

/** Two-step inline confirmation for destructive actions (no modal). */
export function ConfirmButton({ label, confirmLabel, onConfirm, disabled, icon, small = true }: { label: string; confirmLabel: string; onConfirm: () => void; disabled?: boolean; icon?: ReactNode; small?: boolean }) {
  const [armed, setArmed] = useState(false)
  useEffect(() => {
    if (!armed) return
    const t = setTimeout(() => setArmed(false), 4000)
    return () => clearTimeout(t)
  }, [armed])
  if (armed) return <button type="button" className={cx('btn btn--danger', small && 'btn--sm')} onClick={() => { setArmed(false); onConfirm() }} autoFocus>{confirmLabel}</button>
  return (
    <button type="button" className={cx('btn btn--ghost', small && 'btn--sm', icon && !label ? 'btn--icon' : '')} onClick={() => setArmed(true)} disabled={disabled} aria-label={confirmLabel} title={confirmLabel}>
      {icon}{label}
    </button>
  )
}

export function Legend({ items }: { items: { label: string; color: string; kind?: 'box' | 'line' | 'dash' }[] }) {
  return (
    <ul className="legend">
      {items.map((i) => (
        <li key={i.label}>
          <span className={cx('swatch', i.kind === 'line' && 'swatch--line', i.kind === 'dash' && 'swatch--dash')} style={{ backgroundColor: i.color, borderColor: i.color }} />
          {i.label}
        </li>
      ))}
    </ul>
  )
}
