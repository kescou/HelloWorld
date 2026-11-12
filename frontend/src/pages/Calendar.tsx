import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { CalendarDays, ChevronLeft, ChevronRight, FileUp, Plus } from 'lucide-react'
import { useEvents } from '../api/hooks'
import { EventForm, IcsImport } from '../components/EventTools'
import type { EventType, MarketEvent } from '../api/types'
import { EVENT_ICON, EventList } from '../components/shared'
import { Async, Drawer, EmptyState, Panel, Skeleton, cx } from '../components/ui'
import { fmtMonth, isoDate, todayIso } from '../lib/format'
import { EVENT_TYPE } from '../lib/labels'

const FILTERS: { key: string; label: string; types: EventType[] }[] = [
  { key: 'div', label: 'Dividenden', types: ['ex_dividend', 'dividend_payment'] },
  { key: 'earn', label: 'Quartalszahlen', types: ['earnings'] },
  { key: 'corp', label: 'HV und IR', types: ['agm', 'investor_day', 'split', 'other'] },
]
const DOW = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']

export function Calendar() {
  const now = new Date()
  const [cursor, setCursor] = useState({ y: now.getFullYear(), m: now.getMonth() })
  const [active, setActive] = useState<Set<string>>(new Set(FILTERS.map((f) => f.key)))
  const [add, setAdd] = useState(false)
  const [ics, setIcs] = useState(false)
  const [edit, setEdit] = useState<MarketEvent | null>(null)
  const grid = useMemo(() => {
    const first = new Date(cursor.y, cursor.m, 1)
    const start = new Date(first); start.setDate(1 - ((first.getDay() + 6) % 7))
    const last = new Date(cursor.y, cursor.m + 1, 0)
    const weeks = Math.ceil((((first.getDay() + 6) % 7) + last.getDate()) / 7)
    return Array.from({ length: weeks * 7 }, (_, i) => { const d = new Date(start); d.setDate(start.getDate() + i); return d })
  }, [cursor])
  const from = isoDate(grid[0]), to = isoDate(grid[grid.length - 1])
  const events = useEvents({ from, to })
  const types = new Set(FILTERS.filter((f) => active.has(f.key)).flatMap((f) => f.types))
  const filtered = (events.data ?? []).filter((e) => types.has(e.type))
  const byDay = useMemo(() => {
    const m = new Map<string, MarketEvent[]>()
    for (const e of filtered) m.set(e.date, [...(m.get(e.date) ?? []), e])
    return m
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events.data, active])
  const monthKey = `${cursor.y}-${String(cursor.m + 1).padStart(2, '0')}`
  const inMonth = filtered.filter((e) => e.date.startsWith(monthKey))
  const today = todayIso()
  const move = (n: number) => setCursor((c) => { const d = new Date(c.y, c.m + n, 1); return { y: d.getFullYear(), m: d.getMonth() } })
  const toggle = (k: string) => setActive((s) => { const n = new Set(s); if (n.has(k)) n.delete(k); else n.add(k); return n })

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Kalender</h1><p className="page-head__sub">Ex-Tage, Zahltage, Quartalszahlen und Hauptversammlungen für Depot und Watchlist.</p></div>
        <div className="page-head__actions"><button className="btn" onClick={() => setIcs(true)}><FileUp size={15} aria-hidden /> Kalenderdatei importieren</button><button className="btn btn--primary" onClick={() => setAdd(true)}><Plus size={15} aria-hidden /> Termin eintragen</button></div>
      </div>

      <Panel flush title={<span aria-live="polite">{fmtMonth(monthKey, true)}</span>}
        actions={<div className="row">
          <div className="row" role="group" aria-label="Terminarten filtern" style={{ gap: 4 }}>
            {FILTERS.map((f) => <button key={f.key} className="btn btn--sm" aria-pressed={active.has(f.key)} onClick={() => toggle(f.key)}>{f.label}</button>)}
          </div>
          <div className="row" style={{ gap: 2 }}>
            <button className="btn btn--sm btn--icon" onClick={() => move(-1)} aria-label="Vorheriger Monat"><ChevronLeft size={16} aria-hidden /></button>
            <button className="btn btn--sm" onClick={() => setCursor({ y: now.getFullYear(), m: now.getMonth() })}>Heute</button>
            <button className="btn btn--sm btn--icon" onClick={() => move(1)} aria-label="Nächster Monat"><ChevronRight size={16} aria-hidden /></button>
          </div>
        </div>}>
        {events.isPending ? <div style={{ padding: 16 }}><Skeleton h={380} /></div> : (
          <div className="month-grid" aria-hidden={false}>
            {DOW.map((d) => <div key={d} className="dow">{d}</div>)}
            {grid.map((d) => {
              const iso = isoDate(d)
              const list = byDay.get(iso) ?? []
              return (
                <div key={iso} className={cx('day', d.getMonth() !== cursor.m && 'day--out', iso === today && 'day--today')}>
                  <span className="day__num">{d.getDate()}</span>
                  {list.slice(0, 3).map((e) => {
                    const I = EVENT_ICON[e.type]
                    return (
                      <Link key={e.id} to={`/wertpapier/${e.instrument_id}`} className={cx('ev', e.is_estimate && 'ev--est')} title={`${EVENT_TYPE[e.type]}: ${e.instrument_name}${e.is_estimate ? ' (Schätzung)' : ''}`}>
                        <span className={`ev-ico--${e.type}`} style={{ display: 'grid' }}><I size={12} aria-hidden /></span>
                        <span>{e.symbol?.split('.')[0] ?? e.instrument_name}</span>
                      </Link>
                    )
                  })}
                  {list.length > 3 && <span className="xs faint">+{list.length - 3} weitere</span>}
                </div>
              )
            })}
          </div>
        )}
      </Panel>

      <Panel title={`Termine im ${fmtMonth(monthKey, true)}`} hint={inMonth.length ? `${inMonth.length} Termine` : undefined} flush>
        <Async query={events} what="Die Termine" isEmpty={() => inMonth.length === 0}
          empty={<EmptyState compact icon={<CalendarDays size={22} aria-hidden />} title={(events.data?.length ?? 0) > 0 && inMonth.length === 0 && active.size < FILTERS.length ? 'Keine Termine für diese Filter' : 'Keine Termine in diesem Monat'}
            text="Termine kommen automatisch von Yahoo Finance für gehaltene Wertpapiere und die Watchlist. Fehlende Termine lassen sich von Hand eintragen oder aus dem Finanzkalender (.ics) der IR-Seite importieren.">
            <button className="btn" onClick={() => setAdd(true)}>Termin eintragen</button><button className="btn" onClick={() => setIcs(true)}>Kalenderdatei importieren</button><Link className="btn btn--ghost" to="/watchlist">Watchlist pflegen</Link></EmptyState>}>
          {() => <EventList events={inMonth} onEdit={setEdit} />}
        </Async>
      </Panel>
      {add && <Drawer title="Termin eintragen" onClose={() => setAdd(false)}><EventForm onDone={() => setAdd(false)} defaultDate={monthKey === today.slice(0, 7) ? today : `${monthKey}-01`} /></Drawer>}
      {edit && <Drawer title="Termin bearbeiten" onClose={() => setEdit(null)}><EventForm edit={edit} onDone={() => setEdit(null)} defaultDate={edit.date} /></Drawer>}
      {ics && <Drawer title="Kalenderdatei importieren" onClose={() => setIcs(false)}><IcsImport onDone={() => setIcs(false)} /></Drawer>}
    </div>
  )
}

