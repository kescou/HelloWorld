import { useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { AlertTriangle, ArrowLeft, CalendarDays, ExternalLink, FileUp, LineChart, Pencil, Plus, Star } from 'lucide-react'
import { api } from '../api/client'
import { useApiMutation, useEtfHoldings, useEvents, useFundamentals, useInstrument, useMarketRefresh, usePriceHistory } from '../api/hooks'
import type { Fundamentals, InstrumentDetail as Detail, MarketEvent, Range } from '../api/types'
import { Chart, useChartTheme } from '../components/Chart'
import { ETF_FORMAT_HINT, EtfActions, sourceLabel } from '../components/EtfTools'
import { EventForm, IcsImport } from '../components/EventTools'
import { InstrumentEdit } from '../components/InstrumentEdit'
import { PortfolioWarnings } from '../components/PortfolioWarnings'
import { useToast } from '../components/Toast'
import { TxEntry } from '../components/TxForm'
import { TxTable } from '../components/TxTable'
import { EventList, RANGES } from '../components/shared'
import { Async, Badge, Delta, Drawer, EmptyState, Legend, Money, Panel, Seg, Skeleton, SplitBar } from '../components/ui'
import { barOption, timeOption } from '../lib/charts'
import { addDays, fmtCompact, fmtDate, fmtDateTime, fmtMoney, fmtNumber, fmtPct, fmtQty, todayIso } from '../lib/format'
import { INSTRUMENT_TYPE, SOURCE } from '../lib/labels'

type Side = null | 'entry' | 'edit' | 'event' | 'ics' | MarketEvent

const quantile = (sorted: number[], q: number) => {
  if (!sorted.length) return null
  const pos = (sorted.length - 1) * q
  const lo = Math.floor(pos), hi = Math.ceil(pos)
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo)
}

/** EPS × P/E band, aktienfinder style. EPS is interpolated between fiscal year ends (incl. estimates). */
function fairValueSeries(f: Fundamentals, prices: { date: string; close: number }[]) {
  const pts = f.years.filter((y) => y.eps != null).map((y) => ({ t: Date.parse(`${y.year}-12-31`), eps: y.eps as number, est: !!y.is_estimate })).sort((a, b) => a.t - b.t)
  const pes = f.pe_history.map((p) => p.pe).filter((p) => p > 0 && Number.isFinite(p)).sort((a, b) => a - b)
  const avg = f.fair_value?.pe_avg ?? (pes.length ? pes.reduce((s, p) => s + p, 0) / pes.length : null)
  if (pts.length < 2 || avg == null) return null
  const lowPe = quantile(pes, 0.2) ?? avg * 0.85
  const highPe = quantile(pes, 0.8) ?? avg * 1.15
  const epsAt = (t: number) => {
    if (t <= pts[0].t) return pts[0].eps
    for (let i = 1; i < pts.length; i++) if (t <= pts[i].t) { const a = pts[i - 1], b = pts[i]; return a.eps + ((b.eps - a.eps) * (t - a.t)) / (b.t - a.t) }
    return pts[pts.length - 1].eps
  }
  const last = prices.length ? prices[prices.length - 1].date : todayIso()
  const future = pts.filter((p) => p.t > Date.parse(last)).map((p) => new Date(p.t).toISOString().slice(0, 10))
  const dates = [...prices.map((p) => p.date), ...future]
  const fair = dates.map((d) => epsAt(Date.parse(d)) * avg)
  return {
    dates, avg, lowPe, highPe, firstFuture: prices.length,
    price: [...prices.map((p) => p.close), ...future.map(() => null)],
    fair, lower: dates.map((d) => epsAt(Date.parse(d)) * lowPe), upper: dates.map((d) => epsAt(Date.parse(d)) * highPe),
  }
}

export function InstrumentDetail() {
  const id = Number(useParams().id)
  const instrument = useInstrument(id)
  return (
    <div className="page">
      <Async query={instrument} what="Das Wertpapier" skeleton={<div className="stack"><Skeleton h={64} w={360} /><Skeleton h={320} /></div>}>
        {(i) => <Loaded i={i} />}
      </Async>
    </div>
  )
}

function Loaded({ i }: { i: Detail }) {
  const id = i.id
  const t = useChartTheme()
  const toast = useToast()
  const [range, setRange] = useState<Range>('1Y')
  const [params, setParams] = useSearchParams()
  const [side, setSide] = useState<Side>(params.get('bearbeiten') ? 'edit' : null)
  const close = () => { setSide(null); if (params.has('bearbeiten')) setParams({}, { replace: true }) }
  const setEntry = (v: boolean) => setSide(v ? 'entry' : null)
  const refresh = useMarketRefresh()
  const [showAllTx, setShowAllTx] = useState(false)
  const history = usePriceHistory(id, range)
  const isEtf = i.type === 'etf' || i.type === 'fund'
  const fundamentals = useFundamentals(id, !isEtf)
  const longHistory = usePriceHistory(id, 'MAX')
  const etf = useEtfHoldings(id, isEtf)
  const today = todayIso()
  const events = useEvents({ from: addDays(today, -14), to: addDays(today, 365), instrument_id: id })
  const watch = useApiMutation((v: boolean) => api.updateInstrument(id, { is_watchlist: v }), [['instruments'], ['market', 'events']])
  const cur = i.quote?.currency ?? i.currency
  const pos = i.position
  const f = fundamentals.data
  const unit = { currency: cur }

  const priceOption = useMemo(() => {
    const h = history.data ?? []
    return timeOption(t, {
      dates: h.map((p) => p.date), unit, series: [{ name: 'Kurs', data: h.map((p) => p.close), color: t.c[0], area: true }],
      markLine: pos && pos.price_currency === 'EUR' ? { value: pos.avg_cost, label: 'Ø Einstand' } : undefined,
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [history.data, t, pos, cur])
  const periodChange = useMemo(() => {
    const h = history.data ?? []
    return h.length > 1 && h[0].close ? ((h[h.length - 1].close - h[0].close) / h[0].close) * 100 : null
  }, [history.data])

  const fv = useMemo(() => (f && longHistory.data && longHistory.data.length > 1 ? fairValueSeries(f, longHistory.data) : null), [f, longHistory.data])
  const fvOption = useMemo(() => {
    if (!fv) return null
    return timeOption(t, {
      dates: fv.dates, unit,
      series: [{ name: 'Kurs', data: fv.price, color: t.c[0] }, { name: 'Fairer Wert (Ø-KGV)', data: fv.fair, color: t.c[1], width: 2, dashed: false }],
      band: { name: 'KGV-Band (20.–80. Perzentil)', lower: fv.lower, upper: fv.upper, color: t.c[1] },
      extraRows: (idx) => (idx >= fv.firstFuture ? [{ label: 'Basis', value: 'Gewinnschätzung' }] : []),
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fv, t, cur])
  const fairNow = f?.fair_value?.value ?? null
  const price = i.quote?.price ?? pos?.price ?? null
  const fairDev = fairNow && price ? ((price - fairNow) / fairNow) * 100 : null

  const divOption = useMemo(() => {
    const ys = (f?.years ?? []).filter((y) => y.dps != null)
    const firstEst = ys.findIndex((y) => y.is_estimate)
    return barOption(t, { categories: ys.map((y) => String(y.year)), unit, series: [{ name: 'Dividende je Aktie', data: ys.map((y) => y.dps), color: t.c[1], hatchedFrom: firstEst >= 0 ? firstEst : undefined }],
      line: { name: 'Ausschüttungsquote', data: ys.map((y) => y.payout_ratio), color: t.c[5], unit: 'pct' },
      extraRows: (idx) => (ys[idx]?.is_estimate ? [{ label: 'Status', value: 'Schätzung' }] : []) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [f, t, cur])
  const finOption = useMemo(() => {
    const ys = (f?.years ?? []).filter((y) => y.revenue != null || y.net_income != null)
    const firstEst = ys.findIndex((y) => y.is_estimate)
    return barOption(t, { categories: ys.map((y) => String(y.year)), unit, maxBarWidth: 16,
      series: [
        { name: 'Umsatz', data: ys.map((y) => y.revenue), color: t.c[4], hatchedFrom: firstEst >= 0 ? firstEst : undefined },
        { name: 'Gewinn', data: ys.map((y) => y.net_income), color: t.c[0], hatchedFrom: firstEst >= 0 ? firstEst : undefined },
      ],
      line: { name: 'Gewinn je Aktie', data: ys.map((y) => y.eps), color: t.c[2], unit: 'number' } })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [f, t, cur])
  const hasEstimates = (f?.years ?? []).some((y) => y.is_estimate)
  const myEvents = (events.data ?? []).filter((e) => e.instrument_id === id)
  const noQuote = !i.quote
  const reload = () => refresh.start({ scope: 'quotes', instrument_ids: [id] }, { onError: (e) => toast.error(e, 'Der Abruf konnte nicht gestartet werden:') })

  return (
    <>
      <div className="page-head">
        <div style={{ minWidth: 0 }}>
          <Link className="crumb" to={pos ? '/depot' : '/watchlist'}><ArrowLeft size={14} aria-hidden /> {pos ? 'Depot' : 'Watchlist'}</Link>
          <h1>{i.name}</h1>
          <p className="page-head__sub">{[INSTRUMENT_TYPE[i.type], i.isin, i.wkn && `WKN ${i.wkn}`, i.symbol, i.exchange, i.sector, i.country].filter(Boolean).join(' · ')}</p>
        </div>
        <div className="page-head__actions">
          <button className="btn" aria-pressed={i.is_watchlist} disabled={watch.isPending}
            onClick={() => watch.mutate(!i.is_watchlist, { onSuccess: () => toast.ok(i.is_watchlist ? 'Von der Watchlist entfernt' : 'Zur Watchlist hinzugefügt'), onError: (e) => toast.error(e) })}>
            <Star size={15} aria-hidden fill={i.is_watchlist ? 'currentColor' : 'none'} /> {i.is_watchlist ? 'Auf der Watchlist' : 'Beobachten'}
          </button>
          <button className="btn" onClick={() => setSide('edit')}><Pencil size={15} aria-hidden /> Bearbeiten</button>
          {i.ir_url && <a className="btn" href={i.ir_url} target="_blank" rel="noreferrer noopener">Investor Relations <ExternalLink size={14} aria-hidden /></a>}
          <button className="btn btn--primary" onClick={() => setEntry(true)}><Plus size={15} aria-hidden /> Transaktion</button>
        </div>
      </div>

      <div className="lead">
        <span className="lead__label">Kurs</span>
        <div className="lead__row">
          <span className="lead__value">{price != null ? fmtMoney(price, { currency: cur }) : '–'}</span>
          {i.quote ? <span className="row" style={{ gap: 6 }}><Delta pct={i.quote.change_pct} /><span className="faint small">heute · Stand {fmtDateTime(i.quote.market_time ?? i.quote.updated_at)}</span></span>
            : <Badge tone="warn">{price != null ? 'Kein Marktkurs – letzter Handelskurs' : 'Kein Kurs'}</Badge>}
        </div>
      </div>
      {noQuote && (
        <div className="notice notice--warn" role="status">
          <AlertTriangle size={17} aria-hidden />
          <div><strong>Kein Kurs – Symbol prüfen.</strong> {i.symbol
            ? <>Für das Yahoo-Symbol <code>{i.symbol}</code> liegt kein Kurs vor. Entweder wurde noch nichts abgerufen, Yahoo ist nicht erreichbar – oder das Symbol passt nicht zu diesem Wertpapier.</>
            : <>Diesem Wertpapier ist noch kein Yahoo-Symbol zugeordnet, deshalb können keine Kurse geladen werden.</>}{pos ? ' Der Bestand wird solange mit dem letzten Handelskurs bewertet.' : ''}</div>
          <div className="notice__actions">
            {i.symbol && <button className="btn btn--sm" onClick={reload} disabled={refresh.running}>{refresh.running ? 'Lädt …' : 'Kurs abrufen'}</button>}
            <button className="btn btn--sm btn--primary" onClick={() => setSide('edit')}>Symbol prüfen</button>
          </div>
        </div>
      )}

      {pos && <PortfolioWarnings instrumentId={id} />}
      {pos && (
        <div className="kpis">
          <div className="kpi"><span className="kpi__label">Bestand</span><span className="kpi__value"><span className="amt">{fmtQty(pos.quantity)}</span> Stk.</span><span className="kpi__sub">in {pos.accounts.length} {pos.accounts.length === 1 ? 'Depot' : 'Depots'}</span></div>
          <div className="kpi"><span className="kpi__label">Wert</span><span className="kpi__value"><Money value={pos.market_value} /></span><span className="kpi__sub">{fmtPct(pos.weight)} des Depots</span></div>
          <div className="kpi"><span className="kpi__label">Ø Einstand</span><span className="kpi__value"><Money value={pos.avg_cost} /></span><span className="kpi__sub">gesamt <Money value={pos.cost_basis} decimals={0} /></span></div>
          <div className="kpi"><span className="kpi__label">Buchgewinn</span><span className="kpi__value"><Money value={pos.unrealized_gain} tone /></span><span className="kpi__sub"><Delta pct={pos.unrealized_gain_pct} decimals={1} /></span></div>
          <div className="kpi"><span className="kpi__label">Dividenden erhalten</span><span className="kpi__value"><Money value={pos.dividends_received} /></span><span className="kpi__sub">netto, seit Kauf</span></div>
        </div>
      )}

      <div className="grid cols-main-side">
        <div className="stack">
          <Panel title="Kursverlauf" hint={periodChange != null ? <Delta pct={periodChange} decimals={1} /> : undefined} actions={<Seg label="Zeitraum" value={range} onChange={setRange} options={RANGES} />}>
            <Async query={history} what="Der Kursverlauf" skeleton={<Skeleton h={280} />} isEmpty={(d) => d.length < 2}
              empty={<EmptyState compact icon={<LineChart size={22} aria-hidden />} title="Keine Kurshistorie" text="Für dieses Wertpapier wurden noch keine historischen Kurse geladen. Stimmt das Yahoo-Symbol, genügt ein erneuter Abruf.">{i.symbol && <button className="btn" onClick={reload} disabled={refresh.running}>{refresh.running ? 'Lädt …' : 'Kurshistorie abrufen'}</button>}<button className="btn" onClick={() => setSide('edit')}>Symbol prüfen</button></EmptyState>}>
              {() => <Chart option={priceOption} height={280} label={`Kursverlauf ${i.name}`} />}
            </Async>
          </Panel>

          {!isEtf && (
            <Panel title="Fairer Wert" hint="Kurs gegen Gewinn je Aktie × historisches KGV">
              {fundamentals.isPending || longHistory.isPending ? <Skeleton h={300} /> : !fvOption || !fv ? (
                <EmptyState compact title="Keine Fundamentaldaten" text="Ohne Gewinnhistorie lässt sich kein fairer Wert berechnen. Für manche Titel liefert Yahoo keine Fundamentaldaten." />
              ) : (
                <div className="stack stack--sm">
                  {fairDev != null && (
                    <p className="small">
                      Der Kurs liegt <strong className={fairDev > 0 ? 'neg' : 'pos'}>{fmtPct(Math.abs(fairDev))} {fairDev > 0 ? 'über' : 'unter'}</strong> dem fairen Wert von <strong>{fmtMoney(fairNow, { currency: cur })}</strong>
                      <span className="muted"> (Ø-KGV {fmtNumber(fv.avg, 1)} × erwarteter Gewinn je Aktie {fmtMoney(f?.fair_value?.eps_forward, { currency: cur })}).</span>
                    </p>
                  )}
                  <Chart option={fvOption} height={300} label={`Kurs und fairer Wert ${i.name}`} />
                  <Legend items={[{ label: 'Kurs', color: 'var(--c1)', kind: 'line' }, { label: 'Fairer Wert (Ø-KGV)', color: 'var(--c2)', kind: 'line' }, { label: `KGV-Band ${fmtNumber(fv.lowPe, 1)}–${fmtNumber(fv.highPe, 1)}`, color: 'color-mix(in srgb, var(--c2) 35%, transparent)' }]} />
                  <p className="xs faint">Rechts vom letzten Kurs folgt die Linie den Gewinnschätzungen. Keine Anlageempfehlung.</p>
                </div>
              )}
            </Panel>
          )}

          {!isEtf && f && f.years.some((y) => y.dps) && (
            <Panel title="Dividende" hint={hasEstimates ? 'gestrichelt = Schätzung' : undefined}>
              <div className="stack stack--sm">
                <Chart option={divOption} height={220} label="Dividende je Aktie nach Jahr" />
                <Legend items={[{ label: 'Dividende je Aktie', color: 'var(--c2)' }, { label: 'Ausschüttungsquote (rechte Achse)', color: 'var(--c6)', kind: 'line' }]} />
                <dl className="facts">
                  <div><dt>Wachstum 5 Jahre p. a.</dt><dd>{fmtPct(f.dividend_growth?.cagr_5y, { signed: true })}</dd></div>
                  <div><dt>Wachstum 10 Jahre p. a.</dt><dd>{fmtPct(f.dividend_growth?.cagr_10y, { signed: true })}</dd></div>
                  <div><dt>Jahre ohne Kürzung</dt><dd>{f.dividend_growth?.years_raised ?? '–'}</dd></div>
                  <div><dt>Dividendenrendite</dt><dd>{fmtPct(f.valuation?.dividend_yield, { decimals: 2 })}</dd></div>
                </dl>
              </div>
            </Panel>
          )}
          {!isEtf && f && f.years.some((y) => y.revenue) && (
            <Panel title="Umsatz und Gewinn" hint={hasEstimates ? 'gestrichelt = Schätzung' : undefined}>
              <div className="stack stack--sm">
                <Chart option={finOption} height={240} label="Umsatz und Gewinn nach Jahr" />
                <Legend items={[{ label: 'Umsatz', color: 'var(--c5)' }, { label: 'Gewinn', color: 'var(--c1)' }, { label: 'Gewinn je Aktie (rechte Achse)', color: 'var(--c3)', kind: 'line' }]} />
              </div>
            </Panel>
          )}

          {isEtf && (
            <section id="zusammensetzung">
              <Panel title="Zusammensetzung" flush
                hint={etf.data?.holdings.length ? `${sourceLabel(etf.data.source)} · Stand ${fmtDate(etf.data.as_of)}` : undefined}
                actions={<EtfActions id={id} name={i.name} source={etf.data?.source} hasHoldings={!!etf.data?.holdings.length} />}>
                <Async query={etf} what="Die ETF-Positionen" isEmpty={(d) => d.holdings.length === 0}
                  empty={<EmptyState compact title="Keine Positionsdaten" text={<>Für diesen ETF liegen keine Bestandteile vor. „Vom Anbieter laden“ fragt den Emittenten{etf.data?.issuer ? ` (${sourceLabel(etf.data.issuer).replace(' (Emittent)', '')})` : ''} ab; klappt das nicht, lässt sich die Positionsliste hochladen. {ETF_FORMAT_HINT}</>} />}>
                  {(d) => (
                    <>
                      <div style={{ padding: '0 16px 12px' }} className="stack stack--sm">
                        <SplitBar small label="Abdeckung der Positionsdaten" parts={[{ label: 'bekannt', value: d.coverage_pct, color: 'var(--c2)' }, { label: 'nicht aufgeschlüsselt', value: Math.max(0, 100 - d.coverage_pct), color: 'var(--c8)', hatched: true }]} />
                        <p className="xs muted">{d.holdings.length} Positionen decken {fmtPct(d.coverage_pct)} des Fondsvermögens ab.{d.coverage_pct < 85 && ' Der Rest erscheint in der Durchschau als „nicht aufgeschlüsselt“.'} Datei-Upload: {ETF_FORMAT_HINT}</p>
                      </div>
                      <div className="table-wrap">
                        <table className="table">
                          <thead><tr><th>Unternehmen</th><th className="hide-sm">Sektor</th><th className="hide-sm">Land</th><th className="num">Gewicht</th></tr></thead>
                          <tbody>
                            {d.holdings.slice(0, 15).map((h) => (
                              <tr key={h.isin ?? h.name}><td><span className="name">{h.name}</span><span className="meta">{h.isin}<span className="show-sm"> · {h.sector}</span></span></td><td className="hide-sm muted">{h.sector ?? '–'}</td><td className="hide-sm muted">{h.country ?? '–'}</td><td className="num">{fmtPct(h.weight, { decimals: 2 })}</td></tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </>
                  )}
                </Async>
              </Panel>
            </section>
          )}
        </div>

        <div className="stack">
          <Panel title="Kennzahlen">
            {isEtf ? (
              <dl className="facts facts--rows">
                <div><dt>Anbieter</dt><dd>{SOURCE[i.fund_provider ?? '']?.replace(' (Emittent)', '') ?? i.fund_provider ?? '–'}</dd></div>
                <div><dt>Laufende Kosten (TER)</dt><dd>{fmtPct(i.ter, { decimals: 2 })} p. a.</dd></div>
                <div><dt>Ertragsverwendung</dt><dd>{i.distribution === 'accumulating' ? 'thesaurierend' : i.distribution === 'distributing' ? 'ausschüttend' : '–'}</dd></div>
                <div><dt>Handelswährung</dt><dd>{cur}</dd></div>
              </dl>
            ) : fundamentals.isPending ? <Skeleton h={180} /> : !f ? (
              <EmptyState compact title="Keine Kennzahlen" text="Fundamentaldaten sind für dieses Wertpapier nicht verfügbar." />
            ) : (
              <dl className="facts facts--rows">
                <div><dt>KGV</dt><dd>{fmtNumber(f.valuation?.pe, 1, 1)}</dd></div>
                <div><dt>KGV erwartet</dt><dd>{fmtNumber(f.valuation?.forward_pe, 1, 1)}</dd></div>
                <div><dt>Ø-KGV (historisch)</dt><dd>{fmtNumber(f.fair_value?.pe_avg, 1, 1)}</dd></div>
                <div><dt>Kurs-Buchwert</dt><dd>{fmtNumber(f.valuation?.pb, 1, 1)}</dd></div>
                <div><dt>Dividendenrendite</dt><dd>{fmtPct(f.valuation?.dividend_yield, { decimals: 2 })}</dd></div>
                <div><dt>Marktkapitalisierung</dt><dd>{fmtCompact(f.valuation?.market_cap, f.currency ?? cur)}</dd></div>
                <div><dt>Fairer Wert</dt><dd>{fmtMoney(fairNow, { currency: cur })}{fairDev != null && <span className={fairDev > 0 ? 'neg' : 'pos'}> ({fairDev > 0 ? 'Kurs +' : 'Kurs −'}{fmtPct(Math.abs(fairDev), { decimals: 0 })})</span>}</dd></div>
              </dl>
            )}
          </Panel>
          <Panel title="Termine" flush actions={<span className="row" style={{ gap: 4 }}>
            <button className="btn btn--sm btn--ghost" onClick={() => setSide('ics')} title="Finanzkalender (.ics) der IR-Seite importieren"><FileUp size={14} aria-hidden /> Importieren</button>
            <button className="btn btn--sm btn--ghost" onClick={() => setSide('event')}><Plus size={14} aria-hidden /> Termin</button></span>}
            foot={<Link className="small" to="/kalender">Zum Kalender</Link>}>
            <Async query={events} what="Die Termine" compact isEmpty={() => myEvents.length === 0}
              empty={<EmptyState compact icon={<CalendarDays size={22} aria-hidden />} title="Keine Termine bekannt" text="Ex-Tage, Zahltage und Quartalszahlen erscheinen, sobald Yahoo sie meldet. Eigene Termine lassen sich eintragen oder aus einer Kalenderdatei (.ics) importieren."><button className="btn" onClick={() => setSide('event')}>Termin eintragen</button><button className="btn" onClick={() => setSide('ics')}>Kalenderdatei importieren</button></EmptyState>}>
              {() => <EventList events={myEvents.slice(0, 8)} showInstrument={false} onEdit={setSide} />}
            </Async>
          </Panel>
        </div>
      </div>

      <Panel title="Eigene Transaktionen" flush hint={i.transactions.length ? `${i.transactions.length} Buchungen` : undefined}>
        {i.transactions.length === 0
          ? <EmptyState compact title="Noch keine Transaktionen in diesem Wertpapier" text={i.is_watchlist ? 'Dieses Wertpapier steht nur auf der Watchlist.' : 'Kauf importieren oder manuell erfassen.'}><button className="btn" onClick={() => setEntry(true)}>Kauf erfassen</button></EmptyState>
          : <TxTable items={showAllTx ? i.transactions : i.transactions.slice(0, 10)} />}
        {i.transactions.length > 10 && (
          <footer className="panel__foot"><span>{showAllTx ? i.transactions.length : 10} von {i.transactions.length}</span><button className="btn btn--ghost btn--sm" onClick={() => setShowAllTx(!showAllTx)}>{showAllTx ? 'Weniger anzeigen' : 'Alle anzeigen'}</button></footer>
        )}
      </Panel>
      {side === 'entry' && <Drawer title="Transaktion erfassen" onClose={close}><TxEntry defaults={{ instrument_id: id, type: 'buy', account_id: pos?.accounts[0]?.account_id }} onDone={close} /></Drawer>}
      {side === 'edit' && <Drawer title="Wertpapier bearbeiten" onClose={close}><InstrumentEdit instrument={i} onDone={close} /></Drawer>}
      {side === 'event' && <Drawer title="Termin eintragen" onClose={close}><EventForm instrumentId={id} defaultDate={today} onDone={close} /></Drawer>}
      {side === 'ics' && <Drawer title="Kalenderdatei importieren" onClose={close}><IcsImport instrumentId={id} onDone={close} /></Drawer>}
      {side && typeof side === 'object' && <Drawer title="Termin bearbeiten" onClose={close}><EventForm edit={side} defaultDate={side.date} onDone={close} /></Drawer>}
    </>
  )
}
