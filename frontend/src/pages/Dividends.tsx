import { Fragment, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronDown, ChevronRight, Coins } from 'lucide-react'
import { useDividends, useEvents } from '../api/hooks'
import { Chart, useChartTheme } from '../components/Chart'
import { EventList } from '../components/shared'
import { Async, Badge, EmptyState, Money, Panel, Skeleton } from '../components/ui'
import { barOption, monthLabels } from '../lib/charts'
import { addDays, fmtDateShort, fmtMonth, fmtPct, todayIso } from '../lib/format'

export function Dividends() {
  const thisYear = new Date().getFullYear()
  const [year, setYear] = useState(thisYear)
  const div = useDividends(year)
  const today = todayIso()
  const events = useEvents({ from: today, to: addDays(today, 90), types: 'ex_dividend,dividend_payment' })
  const [open, setOpen] = useState<string | null>(null)
  const t = useChartTheme()
  const d = div.data

  const years = useMemo(() => {
    const set = new Set<number>([thisYear, ...(d?.received_by_year.map((y) => y.year) ?? [])])
    return [...set].sort((a, b) => b - a)
  }, [d, thisYear])
  const monthOption = useMemo(() => barOption(t, { categories: d?.received_by_month.map((m) => m.month) ?? [], labels: monthLabels(d?.received_by_month.map((m) => m.month) ?? []).map((l) => l.split(' ')[0]),
    series: [{ name: 'Erhalten (netto)', data: d?.received_by_month.map((m) => m.amount) ?? [], color: t.c[1] }], tipTitle: (i) => fmtMonth(d?.received_by_month[i].month, true) }), [d, t])
  const yearOption = useMemo(() => barOption(t, { categories: d?.received_by_year.map((y) => String(y.year)) ?? [], series: [{ name: 'Erhalten (netto)', data: d?.received_by_year.map((y) => y.amount) ?? [], color: t.c[1] }], maxBarWidth: 44 }), [d, t])
  const fcOption = useMemo(() => barOption(t, { categories: d?.forecast_12m.map((m) => m.month) ?? [], labels: monthLabels(d?.forecast_12m.map((m) => m.month) ?? []),
    series: [{ name: 'Erwartet (brutto)', data: d?.forecast_12m.map((m) => m.amount) ?? [], color: t.c[1], hatchedFrom: 0 }], tipTitle: (i) => fmtMonth(d?.forecast_12m[i].month, true),
    extraRows: (i) => (d?.forecast_12m[i].items ?? []).slice(0, 5).map((x) => ({ label: x.name.length > 26 ? x.name.slice(0, 25) + '…' : x.name, value: t.privacy ? '•••' : `${x.amount.toFixed(2).replace('.', ',')} €` })) }), [d, t])

  const received = d?.received_by_month.reduce((s, m) => s + m.amount, 0) ?? 0
  const forecast = d?.forecast_12m.reduce((s, m) => s + m.amount, 0) ?? 0
  const monthsElapsed = year === thisYear ? new Date().getMonth() + 1 : 12
  const nothing = d && d.received_by_year.length === 0 && forecast === 0

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Dividenden</h1><p className="page-head__sub">Erhaltene Ausschüttungen netto, Prognose brutto auf Basis des aktuellen Bestands.</p></div>
        <div className="page-head__actions">
          <label className="sr-only" htmlFor="div-year">Jahr</label>
          <select id="div-year" className="select" style={{ width: 'auto' }} value={year} onChange={(e) => setYear(Number(e.target.value))}>{years.map((y) => <option key={y} value={y}>{y}</option>)}</select>
        </div>
      </div>
      <Async query={div} what="Die Dividenden" skeleton={<div className="stack"><Skeleton h={76} /><Skeleton h={260} /></div>}>
        {(dd) => nothing ? (
          <Panel><EmptyState icon={<Coins size={24} aria-hidden />} title="Noch keine Dividenden" text="Dividendengutschriften erscheinen nach dem Import der Abrechnung. Die Prognose entsteht automatisch aus den Ex-Tagen der gehaltenen Wertpapiere.">
            <Link className="btn btn--primary" to="/import">Dividendenabrechnung importieren</Link><Link className="btn" to="/transaktionen?neu=dividend">Manuell erfassen</Link></EmptyState></Panel>
        ) : (
          <>
            <div className="kpis">
              <div className="kpi"><span className="kpi__label">Erhalten {year}</span><span className="kpi__value"><Money value={received} /></span><span className="kpi__sub">netto nach Steuern</span></div>
              <div className="kpi"><span className="kpi__label">Ø pro Monat</span><span className="kpi__value"><Money value={received / monthsElapsed} /></span><span className="kpi__sub">{year === thisYear ? `über ${monthsElapsed} Monate` : 'über 12 Monate'}</span></div>
              <div className="kpi"><span className="kpi__label">Prognose 12 Monate</span><span className="kpi__value"><Money value={forecast} /></span><span className="kpi__sub">brutto, <Badge tone="est">Schätzung</Badge></span></div>
              <div className="kpi"><span className="kpi__label">Rendite auf Einstand</span><span className="kpi__value">{fmtPct(dd.yield_on_cost, { decimals: 2 })}</span><span className="kpi__sub">Yield on Cost</span></div>
              <div className="kpi"><span className="kpi__label">Dividendenrendite Depot</span><span className="kpi__value">{fmtPct(dd.portfolio_yield, { decimals: 2 })}</span><span className="kpi__sub">auf aktuellen Wert</span></div>
            </div>
            <div className="grid cols-main-side">
              <Panel title={`Erhalten je Monat ${year}`}><Chart option={monthOption} height={240} label={`Erhaltene Dividenden je Monat ${year}`} /></Panel>
              <Panel title="Erhalten je Jahr">{dd.received_by_year.length ? <Chart option={yearOption} height={240} label="Erhaltene Dividenden je Jahr" /> : <EmptyState compact title="Noch keine Gutschriften" />}</Panel>
            </div>
            <div className="grid cols-main-side">
              <Panel title="Prognose der nächsten 12 Monate" hint="auf Basis des aktuellen Bestands" flush>
                <div style={{ padding: '0 16px 12px' }}><Chart option={fcOption} height={220} label="Erwartete Dividenden der nächsten 12 Monate" /></div>
                <div className="table-wrap">
                  <table className="table">
                    <thead><tr><th>Monat</th><th className="hide-sm">Zahler</th><th className="num">Erwartet</th></tr></thead>
                    <tbody>
                      {dd.forecast_12m.map((m) => {
                        const isOpen = open === m.month
                        return (
                          <Fragment key={m.month}>
                            <tr className={isOpen ? 'is-open' : undefined}>
                              <td>
                                <button type="button" className="row-toggle" disabled={!m.items.length} aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : m.month)}>
                                  {m.items.length ? (isOpen ? <ChevronDown size={15} aria-hidden /> : <ChevronRight size={15} aria-hidden />) : <span style={{ width: 15 }} />}{fmtMonth(m.month, true)}
                                </button>
                              </td>
                              <td className="hide-sm muted truncate" style={{ maxWidth: 320 }}>{m.items.length ? m.items.map((x) => x.name.split(' ').slice(0, 2).join(' ')).join(', ') : <span className="faint">keine Zahlung erwartet</span>}</td>
                              <td className="num">{m.amount ? <Money value={m.amount} /> : <span className="faint">–</span>}</td>
                            </tr>
                            {isOpen && m.items.map((x, n) => (
                              <tr key={n} className="sub-row">
                                <td colSpan={2} style={{ paddingLeft: 38, paddingTop: 6, paddingBottom: 6 }}>
                                  <Link to={`/wertpapier/${x.instrument_id}`}>{x.name}</Link>
                                  <span className="meta">{x.ex_date ? `Ex-Tag ${fmtDateShort(x.ex_date)} · ` : ''}Zahltag {fmtDateShort(x.pay_date)}</span>
                                </td>
                                <td className="num" style={{ paddingTop: 6, paddingBottom: 6 }}><Money value={x.amount} /></td>
                              </tr>
                            ))}
                          </Fragment>
                        )
                      })}
                    </tbody>
                    <tfoot><tr><td>Summe</td><td className="hide-sm" /><td className="num"><Money value={forecast} /></td></tr></tfoot>
                  </table>
                </div>
              </Panel>
              <Panel title="Dividendenkalender" hint="nächste 90 Tage" flush actions={<Link className="small" to="/kalender">Kalender</Link>}>
                <Async query={events} what="Die Termine" compact empty={<EmptyState compact title="Keine Ex- oder Zahltage in Sicht" text="In den nächsten 90 Tagen ist kein Dividendentermin bekannt." />}>
                  {(list) => <EventList events={list.slice(0, 10)} />}
                </Async>
              </Panel>
            </div>
          </>
        )}
      </Async>
    </div>
  )
}
