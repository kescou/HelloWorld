import { Link } from 'react-router-dom'
import { Banknote, CalendarClock, Megaphone, Presentation, Scissors, Users, type LucideIcon } from 'lucide-react'
import type { Account, AllocationSlice, EventType, MarketEvent, Range } from '../api/types'
import { dayOfMonth, daysUntil, fmtMonShort, fmtMoney, fmtPct } from '../lib/format'
import { EVENT_TYPE, allocationLabel } from '../lib/labels'
import { Badge, Money, SplitBar, type Part } from './ui'

export const RANGES: readonly Range[] = ['1M', '3M', 'YTD', '1Y', '3Y', '5Y', 'MAX']
const PALETTE = ['var(--c1)', 'var(--c2)', 'var(--c3)', 'var(--c4)', 'var(--c5)', 'var(--c6)', 'var(--c7)', 'var(--c8)']
export const paletteVar = (i: number) => PALETTE[i % PALETTE.length]
export const accountColor = (a: Pick<Account, 'color' | 'id'>, index: number) => a.color || paletteVar(index)

/** Collapses a long allocation into the top N plus "Sonstige". */
export function topSlices(slices: AllocationSlice[], max = 7): AllocationSlice[] {
  const sorted = [...slices].filter((s) => s.value > 0).sort((a, b) => b.value - a.value)
  if (sorted.length <= max) return sorted
  const head = sorted.slice(0, max - 1)
  const rest = sorted.slice(max - 1)
  return [...head, { key: 'Sonstige', value: rest.reduce((s, r) => s + r.value, 0), weight: rest.reduce((s, r) => s + r.weight, 0) }]
}

export function AllocationList({ slices, label, max = 7 }: { slices: AllocationSlice[]; label: string; max?: number }) {
  const top = topSlices(slices, max)
  const isUnknown = (k: string) => allocationLabel(k) === 'Nicht zugeordnet' || k.includes('nicht aufgeschlüsselt')
  const parts: Part[] = top.map((s, i) => ({ label: allocationLabel(s.key), value: s.value, color: s.key === 'Sonstige' || isUnknown(s.key) ? 'var(--c8)' : paletteVar(i), hatched: isUnknown(s.key) }))
  return (
    <div className="stack stack--sm">
      <SplitBar parts={parts} label={label} />
      <ul className="legend legend--rows">
        {top.map((s, i) => (
          <li key={s.key}>
            <span className="swatch" style={{ backgroundColor: parts[i].color }} />
            <span className="truncate">{allocationLabel(s.key)}</span>
            <span className="val amt">{fmtMoney(s.value, { decimals: 0 })}</span>
            <span className="pct">{fmtPct(s.weight)}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export const EVENT_ICON: Record<EventType, LucideIcon> = { ex_dividend: Scissors, dividend_payment: Banknote, earnings: Megaphone, agm: Users, investor_day: Presentation, split: Scissors, other: CalendarClock }

export function EventIcon({ type, size = 15 }: { type: EventType; size?: number }) {
  const I = EVENT_ICON[type] ?? CalendarClock
  return <span className={`ev-ico ev-ico--${type}`}><I size={size} aria-hidden /></span>
}

export function relDay(iso: string): string {
  const d = daysUntil(iso)
  if (d === 0) return 'heute'
  if (d === 1) return 'morgen'
  if (d === -1) return 'gestern'
  return d > 0 ? `in ${d} Tagen` : `vor ${-d} Tagen`
}

export function EventList({ events, showInstrument = true }: { events: MarketEvent[]; showInstrument?: boolean }) {
  return (
    <ul className="list">
      {events.map((e) => (
        <li key={e.id}>
          <div className="datebox" aria-hidden="true"><b>{dayOfMonth(e.date)}</b><span>{fmtMonShort(e.date)}</span></div>
          <div style={{ minWidth: 0 }}>
            {showInstrument
              ? <Link className="title truncate" to={`/wertpapier/${e.instrument_id}`} style={{ color: 'inherit' }}>{e.instrument_name}</Link>
              : <span className="title truncate">{EVENT_TYPE[e.type]}{e.title ? ` · ${e.title}` : ''}</span>}
            <span className="meta truncate">
              {showInstrument && <>{EVENT_TYPE[e.type]}{e.title ? ` · ${e.title}` : ''} · </>}{relDay(e.date)}{e.amount != null ? ` · ${fmtMoney(e.amount, { currency: e.currency ?? 'EUR' })} je Stück` : ''}
            </span>
          </div>
          <div className="end">
            {e.expected_payment != null && <Money value={e.expected_payment} signed />}
            {e.is_estimate && <div><Badge tone="est" title="Termin oder Betrag geschätzt">Schätzung</Badge></div>}
          </div>
        </li>
      ))}
    </ul>
  )
}
