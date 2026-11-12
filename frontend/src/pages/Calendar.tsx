import { useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { CalendarDays, ChevronLeft, ChevronRight, Plus } from 'lucide-react'
import { api } from '../api/client'
import { useApiMutation, useEvents, useInstruments } from '../api/hooks'
import type { EventType, MarketEvent } from '../api/types'
import { useToast } from '../components/Toast'
import { EVENT_ICON, EventList } from '../components/shared'
import { Async, Drawer, EmptyState, Field, Panel, Skeleton, cx } from '../components/ui'
import { fmtMonth, isoDate, parseDecimal, todayIso } from '../lib/format'
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
        <div className="page-head__actions"><button className="btn btn--primary" onClick={() => setAdd(true)}><Plus size={15} aria-hidden /> Termin eintragen</button></div>
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
            text="Termine kommen automatisch von Yahoo Finance für gehaltene Wertpapiere und die Watchlist. Fehlende Termine lassen sich von Hand eintragen.">
            <button className="btn" onClick={() => setAdd(true)}>Termin eintragen</button><Link className="btn btn--ghost" to="/watchlist">Watchlist pflegen</Link></EmptyState>}>
          {() => <EventList events={inMonth} />}
        </Async>
      </Panel>
      {add && <Drawer title="Termin eintragen" onClose={() => setAdd(false)}><EventForm onDone={() => setAdd(false)} defaultDate={monthKey === today.slice(0, 7) ? today : `${monthKey}-01`} /></Drawer>}
    </div>
  )
}

function EventForm({ onDone, defaultDate }: { onDone: () => void; defaultDate: string }) {
  const instruments = useInstruments()
  const toast = useToast()
  const [instrumentId, setInstrumentId] = useState('')
  const [type, setType] = useState<EventType>('earnings')
  const [date, setDate] = useState(defaultDate)
  const [title, setTitle] = useState('')
  const [amount, setAmount] = useState('')
  const [url, setUrl] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const save = useApiMutation(api.createEvent, [['market', 'events'], ['analytics', 'dividends']])
  const isDiv = type === 'ex_dividend' || type === 'dividend_payment'

  function submit(e: FormEvent) {
    e.preventDefault()
    const err: Record<string, string> = {}
    if (!instrumentId) err.instrument = 'Wertpapier wählen.'
    if (!date) err.date = 'Datum angeben.'
    setErrors(err)
    if (Object.keys(err).length) return
    save.mutate({ instrument_id: Number(instrumentId), type, date, title: title.trim() || null, amount: isDiv ? parseDecimal(amount) : null, currency: isDiv ? instruments.data?.find((i) => i.id === Number(instrumentId))?.currency ?? 'EUR' : null, url: url.trim() || null },
      { onSuccess: () => { toast.ok('Termin eingetragen'); onDone() }, onError: (er) => toast.error(er, 'Termin konnte nicht gespeichert werden:') })
  }
  return (
    <form className="form-grid" onSubmit={submit} noValidate>
      <Field label="Wertpapier" className="span-all" error={errors.instrument} hint={instruments.data && instruments.data.length === 0 ? 'Noch keine Wertpapiere vorhanden – zuerst eines kaufen oder auf die Watchlist setzen.' : undefined}>
        <select className="select" value={instrumentId} onChange={(e) => setInstrumentId(e.target.value)}>
          <option value="">Bitte wählen …</option>
          {(instruments.data ?? []).map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
        </select>
      </Field>
      <Field label="Art"><select className="select" value={type} onChange={(e) => setType(e.target.value as EventType)}>{(Object.keys(EVENT_TYPE) as EventType[]).map((k) => <option key={k} value={k}>{EVENT_TYPE[k]}</option>)}</select></Field>
      <Field label="Datum" error={errors.date}><input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} /></Field>
      {isDiv && <Field label="Dividende je Stück" hint="in der Währung des Wertpapiers"><input className="input num" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="0,00" /></Field>}
      <Field label="Titel" className="span-all" hint="optional, z. B. „Capital Markets Day“"><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
      <Field label="Link" className="span-all" hint="optional, z. B. zur IR-Seite"><input className="input" type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://" /></Field>
      <div className="form-actions span-all">
        <button className="btn btn--primary" type="submit" disabled={save.isPending}>{save.isPending ? 'Speichert …' : 'Termin eintragen'}</button>
        <button className="btn" type="button" onClick={onDone}>Abbrechen</button>
      </div>
    </form>
  )
}
