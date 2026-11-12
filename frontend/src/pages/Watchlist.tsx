import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQueries, useQuery } from '@tanstack/react-query'
import { Plus, Search, Star, Trash2 } from 'lucide-react'
import { api } from '../api/client'
import { useApiMutation, useEvents, useInstruments, useQuotes } from '../api/hooks'
import type { InstrumentType, SearchResult } from '../api/types'
import { useToast } from '../components/Toast'
import { Async, Badge, ConfirmButton, Delta, EmptyState, ErrorState, Panel, SkeletonRows } from '../components/ui'
import { addDays, fmtDateShort, fmtMoney, fmtPct, todayIso } from '../lib/format'
import { EVENT_TYPE, INSTRUMENT_TYPE } from '../lib/labels'

/** Position of the price relative to fair value: marker left of the centre = cheaper than fair value. */
export function FairValueGauge({ price, fair }: { price: number | null | undefined; fair: number | null | undefined }) {
  if (!price || !fair) return <span className="faint">keine Daten</span>
  const dev = ((price - fair) / fair) * 100
  const pos = Math.max(4, Math.min(96, 50 + dev))
  return (
    <span className="fv" title={`Fairer Wert ${fmtMoney(fair)} · Kurs ${fmtMoney(price)}`}>
      <span className="fv__track" aria-hidden="true"><span className="fv__mark" style={{ left: `${pos}%` }} /></span>
      <span className={dev > 0 ? 'neg' : 'pos'} style={{ whiteSpace: 'nowrap' }}>{fmtPct(Math.abs(dev), { decimals: 0 })} {dev > 0 ? 'darüber' : 'darunter'}</span>
    </span>
  )
}

const KEYS = [['instruments'], ['market', 'events'], ['market', 'quotes']]

function AddForm({ existing }: { existing: Set<string> }) {
  const toast = useToast()
  const [q, setQ] = useState('')
  const [term, setTerm] = useState('')
  useEffect(() => { const id = setTimeout(() => setTerm(q.trim()), 300); return () => clearTimeout(id) }, [q])
  const search = useQuery({ queryKey: ['market', 'search', term], queryFn: () => api.search(term), enabled: term.length >= 2, retry: false, staleTime: 60_000 })
  const add = useApiMutation((r: SearchResult) => api.createInstrument({ isin: r.isin ?? null, symbol: r.symbol, name: r.name, type: (['stock', 'etf', 'fund', 'bond', 'crypto', 'commodity'].includes(r.type ?? '') ? r.type : 'stock') as InstrumentType, currency: r.currency ?? 'EUR', exchange: r.exchange ?? null, is_watchlist: true }), KEYS)
  return (
    <Panel title="Wertpapier hinzufügen">
      <div className="stack stack--sm">
        <div className="search" style={{ maxWidth: 420 }}>
          <Search size={15} aria-hidden />
          <input className="input" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, ISIN oder Symbol, z. B. „Nestlé“ oder „US0378331005“" aria-label="Wertpapier suchen" />
        </div>
        {term.length >= 2 && (
          search.isPending ? <SkeletonRows rows={3} pad={false} />
            : search.isError ? <ErrorState compact error={search.error} onRetry={() => void search.refetch()} what="Suchergebnisse" />
            : search.data.length === 0 ? <p className="small muted">Kein Treffer für „{term}“. Mit der ISIN klappt die Suche am zuverlässigsten.</p>
            : (
              <ul className="list" style={{ margin: '0 -16px' }}>
                {search.data.map((r) => {
                  const known = existing.has(r.isin ?? '') || existing.has(r.symbol)
                  return (
                    <li key={r.symbol}>
                      <Star size={16} aria-hidden style={{ color: 'var(--text-3)' }} />
                      <span style={{ minWidth: 0 }}><span className="title truncate">{r.name}</span><span className="meta truncate">{[r.symbol, r.isin, r.exchange, r.currency].filter(Boolean).join(' · ')}</span></span>
                      {known ? <Badge>bereits vorhanden</Badge> : (
                        <button className="btn btn--sm" disabled={add.isPending} onClick={() => add.mutate(r, { onSuccess: () => { toast.ok(`${r.name} steht jetzt auf der Watchlist`); setQ('') }, onError: (e) => toast.error(e, 'Hinzufügen fehlgeschlagen:') })}><Plus size={14} aria-hidden /> Beobachten</button>
                      )}
                    </li>
                  )
                })}
              </ul>
            )
        )}
      </div>
    </Panel>
  )
}

export function Watchlist() {
  const watch = useInstruments({ watchlist: true })
  const all = useInstruments()
  const toast = useToast()
  const list = watch.data ?? []
  const ids = useMemo(() => list.map((i) => i.id), [list])
  const quotes = useQuotes(ids)
  const today = todayIso()
  const events = useEvents({ from: today, to: addDays(today, 180) })
  const fundamentals = useQueries({ queries: list.map((i) => ({ queryKey: ['market', 'fundamentals', i.id], queryFn: () => api.fundamentals(i.id), retry: false, staleTime: 10 * 60_000, enabled: i.type === 'stock' })) })
  const remove = useApiMutation((id: number) => api.updateInstrument(id, { is_watchlist: false }), KEYS)
  const existing = useMemo(() => new Set((all.data ?? []).filter((i) => i.is_watchlist).flatMap((i) => [i.isin ?? '', i.symbol ?? ''])), [all.data])

  return (
    <div className="page">
      <div className="page-head"><div><h1>Watchlist</h1><p className="page-head__sub">Beobachtete Wertpapiere mit Kurs, nächstem Termin und Abstand zum fairen Wert.</p></div></div>
      <AddForm existing={existing} />
      <Panel title="Beobachtet" flush hint={list.length ? `${list.length} Wertpapiere` : undefined}>
        <Async query={watch} what="Die Watchlist"
          empty={<EmptyState icon={<Star size={24} aria-hidden />} title="Die Watchlist ist leer" text="Oben nach Name oder ISIN suchen und „Beobachten“ wählen. Für beobachtete Titel werden Kurse, Termine und Fundamentaldaten geladen." />}>
          {(rows) => (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Wertpapier</th><th className="num">Kurs</th><th className="num hide-sm">Heute</th><th>Kurs zu fairem Wert</th><th className="num hide-md">Div.-Rendite</th><th className="hide-md">Nächster Termin</th><th style={{ width: 44 }}><span className="sr-only">Aktionen</span></th></tr></thead>
                <tbody>
                  {rows.map((i, n) => {
                    const quote = quotes.data?.find((x) => x.instrument_id === i.id) ?? i.quote ?? null
                    const f = fundamentals[n]?.data
                    const next = events.data?.find((e) => e.instrument_id === i.id)
                    return (
                      <tr key={i.id}>
                        <td className="cell-name"><Link className="name truncate" to={`/wertpapier/${i.id}`}>{i.name}</Link><span className="meta truncate">{INSTRUMENT_TYPE[i.type]} · {i.isin ?? i.symbol}</span></td>
                        <td className="num">{quote ? fmtMoney(quote.price, { currency: quote.currency }) : <span className="faint" title="Noch kein Kurs geladen">–</span>}<span className="meta show-sm">{quote && <Delta pct={quote.change_pct} />}</span></td>
                        <td className="num hide-sm">{quote ? <Delta pct={quote.change_pct} /> : '–'}</td>
                        <td>{i.type !== 'stock' ? <span className="faint">nur für Aktien</span> : fundamentals[n]?.isPending ? <span className="faint">lädt …</span> : <FairValueGauge price={quote?.price} fair={f?.fair_value?.value} />}</td>
                        <td className="num hide-md">{fmtPct(f?.valuation?.dividend_yield, { decimals: 2 })}</td>
                        <td className="hide-md muted">{next ? <>{fmtDateShort(next.date)} · {EVENT_TYPE[next.type]}{next.is_estimate && <> <Badge tone="est">Schätzung</Badge></>}</> : <span className="faint">–</span>}</td>
                        <td className="num"><ConfirmButton label="" icon={<Trash2 size={15} aria-hidden />} confirmLabel="Entfernen?" onConfirm={() => remove.mutate(i.id, { onSuccess: () => toast.ok(`${i.name} entfernt`), onError: (e) => toast.error(e) })} /></td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Async>
      </Panel>
      <p className="xs faint">Fairer Wert = durchschnittliches KGV × erwarteter Gewinn je Aktie. Der Marker links der Mitte bedeutet: Kurs unter fairem Wert. Keine Anlageempfehlung.</p>
    </div>
  )
}
