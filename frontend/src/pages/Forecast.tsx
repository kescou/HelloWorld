import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useCashflow, useDividends, useForecast, useSummary } from '../api/hooks'
import { Chart, useChartTheme } from '../components/Chart'
import { Async, Badge, EmptyState, Legend, Money, Panel, Skeleton } from '../components/ui'
import { barOption, monthLabels, timeOption } from '../lib/charts'
import { fmtMoney, fmtMonth, fmtPct, isoDate, todayIso } from '../lib/format'

function useDebounced<V>(value: V, ms = 250): V {
  const [v, setV] = useState(value)
  useEffect(() => { const id = setTimeout(() => setV(value), ms); return () => clearTimeout(id) }, [value, ms])
  return v
}

function Slider({ label, value, onChange, min, max, step, format }: { label: string; value: number; onChange: (n: number) => void; min: number; max: number; step: number; format: (n: number) => string }) {
  return (
    <label className="slider-row">
      <span className="slider-row__head"><span className="muted">{label}</span><output>{format(value)}</output></span>
      <input className="range" type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} aria-valuetext={format(value)} />
    </label>
  )
}

export function Forecast() {
  const summary = useSummary()
  const cashflow = useCashflow(12)
  const dividends = useDividends()
  const [savings, setSavings] = useState<number | null>(null)
  const [ret, setRet] = useState(5)
  const [years, setYears] = useState(15)
  const t = useChartTheme()

  // Default savings rate: average monthly surplus of the last 12 months, rounded to 50 €.
  const suggested = useMemo(() => {
    const key = todayIso().slice(0, 7)
    const m = (cashflow.data ?? []).filter((x) => x.month !== key)
    if (!m.length) return 500
    return Math.max(0, Math.round(m.reduce((s, x) => s + x.savings, 0) / m.length / 50) * 50)
  }, [cashflow.data])
  const monthly = savings ?? suggested
  const params = useDebounced({ years, monthly_savings: monthly, expected_return: ret })
  const forecast = useForecast(params)
  const series = forecast.data?.series ?? []
  const last = series[series.length - 1]

  // `series[].year` is an offset (0 = today); the calendar year is start_year + offset.
  const startYear = forecast.data?.start_year ?? new Date().getFullYear()
  const option = useMemo(() => {
    const now = new Date()
    const dates = series.map((s) => isoDate(new Date(now.getFullYear() + s.year, now.getMonth(), Math.min(now.getDate(), 28))))
    return timeOption(t, {
      dates, zeroBased: true,
      series: [{ name: 'Erwartet (Median)', data: series.map((s) => s.p50), color: t.c[0], width: 2.5 }, { name: 'Eingezahlt', data: series.map((s) => s.contributions), color: t.c[7], dashed: true, width: 1.5 }],
      band: { name: 'Spanne 10–90 %', lower: series.map((s) => s.p10), upper: series.map((s) => s.p90), color: t.c[0] },
    })
  }, [series, t])

  // Dividend forecast: taken from the backend (`/analytics/dividends`), no second model in the browser.
  const fc = dividends.data?.forecast_12m ?? []
  const base = dividends.data?.forecast_12m_total ?? fc.reduce((s, m) => s + m.amount, 0)
  const estimated = fc.reduce((s, m) => s + m.items.filter((x) => x.is_estimate).reduce((a, x) => a + x.amount, 0), 0)
  const divOption = useMemo(() => barOption(t, {
    categories: fc.map((m) => m.month), labels: monthLabels(fc.map((m) => m.month)), maxBarWidth: 22,
    series: [{ name: 'Erwartet (brutto)', data: fc.map((m) => m.amount), color: t.c[1], hatchedFrom: 0 }], tipTitle: (i) => fmtMonth(fc[i]?.month, true),
    extraRows: (i) => (fc[i]?.items ?? []).slice(0, 5).map((x) => ({ label: x.name.length > 26 ? x.name.slice(0, 25) + '…' : x.name, value: t.privacy ? '•••' : fmtMoney(x.amount) })),
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [dividends.data, t])
  const tableRows = series.every((x) => x.p90 === 0) ? [] : series.filter((_s, i) => i > 0 && (series.length <= 17 || i % 5 === 0 || i === series.length - 1))

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Prognose</h1><p className="page-head__sub">Wohin sich das Vermögen entwickeln kann – eine Spanne, kein Versprechen.</p></div>
      </div>

      <div className="grid cols-side-main">
        <Panel title="Annahmen">
          <div className="stack stack--sm">
            <Slider label="Monatliche Sparrate" value={monthly} onChange={setSavings} min={0} max={5000} step={50} format={(n) => fmtMoney(n, { decimals: 0 })} />
            {savings != null && savings !== suggested && cashflow.data && <button className="btn btn--ghost btn--sm" style={{ justifySelf: 'start' }} onClick={() => setSavings(null)}>Auf Ø-Überschuss zurücksetzen ({fmtMoney(suggested, { decimals: 0 })})</button>}
            <Slider label="Erwartete Rendite p. a." value={ret} onChange={setRet} min={0} max={12} step={0.5} format={(n) => fmtPct(n)} />
            <Slider label="Zeitraum" value={years} onChange={setYears} min={1} max={40} step={1} format={(n) => `${n} ${n === 1 ? 'Jahr' : 'Jahre'}`} />
            <p className="xs muted">Startwert ist das heutige Nettovermögen{summary.data ? <> von <Money value={summary.data.net_worth} decimals={0} /></> : ''}. Die Sparrate ist mit dem durchschnittlichen Überschuss der letzten 12 Monate vorbelegt. Inflation und Steuern sind nicht berücksichtigt.</p>
          </div>
        </Panel>
        <Panel title="Vermögensprognose" hint="Monte-Carlo-Spanne">
          <Async query={forecast} what="Die Prognose" skeleton={<Skeleton h={320} />} isEmpty={(d) => d.series.length < 2 || d.series.every((x) => x.p90 === 0)} empty={<EmptyState compact title="Noch nichts zu prognostizieren" text="Startwert und Sparrate sind beide 0 €. Links eine monatliche Sparrate einstellen – oder zuerst Kontostände eintragen bzw. Belege importieren."><Link className="btn" to="/einstellungen#eroeffnung">Kontostände eintragen</Link><Link className="btn" to="/import">Belege importieren</Link></EmptyState>}>
            {() => (
              <div className="stack stack--sm">
                <Chart option={option} height={320} label="Vermögensprognose mit 10-, 50- und 90-Prozent-Quantil" />
                <Legend items={[{ label: 'Erwartet (Median)', color: 'var(--c1)', kind: 'line' }, { label: 'Spanne 10–90 %', color: 'color-mix(in srgb, var(--c1) 30%, transparent)' }, { label: 'Eingezahlt (ohne Rendite)', color: 'var(--c8)', kind: 'dash' }]} />
              </div>
            )}
          </Async>
        </Panel>
      </div>

      {last && last.p90 > 0 && (
        <div className="kpis">
          <div className="kpi"><span className="kpi__label">Erwartet {startYear + last.year}</span><span className="kpi__value"><Money value={last.p50} decimals={0} /></span><span className="kpi__sub">Median</span></div>
          <div className="kpi"><span className="kpi__label">Vorsichtig</span><span className="kpi__value"><Money value={last.p10} decimals={0} /></span><span className="kpi__sub">in 9 von 10 Fällen mehr</span></div>
          <div className="kpi"><span className="kpi__label">Optimistisch</span><span className="kpi__value"><Money value={last.p90} decimals={0} /></span><span className="kpi__sub">in 1 von 10 Fällen mehr</span></div>
          <div className="kpi"><span className="kpi__label">Davon eingezahlt</span><span className="kpi__value"><Money value={last.contributions} decimals={0} /></span><span className="kpi__sub">Erträge im Median <Money value={last.p50 - last.contributions} decimals={0} tone /></span></div>
        </div>
      )}

      <div className="grid cols-2">
        <Panel title="Verlauf in Zahlen" flush>
          {tableRows.length === 0 ? <EmptyState compact title="Noch keine Werte" text="Die Tabelle füllt sich, sobald es einen Startwert oder eine Sparrate gibt." /> : (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Jahr</th><th className="num hide-sm">Vorsichtig</th><th className="num">Erwartet</th><th className="num hide-sm">Optimistisch</th><th className="num">Eingezahlt</th></tr></thead>
                <tbody>{tableRows.map((s) => <tr key={s.year}><td>{startYear + s.year}<span className="faint xs"> · in {s.year} {s.year === 1 ? 'Jahr' : 'Jahren'}</span></td><td className="num hide-sm muted"><Money value={s.p10} decimals={0} /></td><td className="num strong"><Money value={s.p50} decimals={0} /></td><td className="num hide-sm muted"><Money value={s.p90} decimals={0} /></td><td className="num muted"><Money value={s.contributions} decimals={0} /></td></tr>)}</tbody>
              </table>
            </div>
          )}
        </Panel>
        <Panel title="Dividendenprognose" hint="nächste 12 Monate, aktueller Bestand" actions={<Badge tone="est" title="Vor Steuern; Termine und Beträge teils aus der Historie geschätzt">brutto · Schätzung</Badge>}>
          {dividends.isPending ? <Skeleton h={260} /> : dividends.isError ? <EmptyState compact title="Dividendenprognose nicht verfügbar" text={dividends.error instanceof Error ? dividends.error.message : undefined}><button className="btn" onClick={() => void dividends.refetch()}>Erneut versuchen</button></EmptyState> : base === 0 ? (
            <EmptyState compact title="Noch keine Dividenden zu erwarten" text="Die Prognose entsteht aus den Ex- und Zahltagen der gehaltenen Wertpapiere. Dafür braucht es ausschüttende Titel im Depot und geladene Termine."><Link className="btn" to="/dividenden">Zu den Dividenden</Link></EmptyState>
          ) : (
            <div className="stack stack--sm">
              <Chart option={divOption} height={220} label="Erwartete Bruttodividenden der nächsten 12 Monate" />
              <dl className="facts">
                <div><dt>Summe 12 Monate <span className="faint">(brutto)</span></dt><dd><Money value={base} /></dd></div>
                <div><dt>Ø pro Monat <span className="faint">(brutto)</span></dt><dd><Money value={base / 12} /></dd></div>
                <div><dt>Rendite auf Depotwert</dt><dd>{fmtPct(dividends.data?.portfolio_yield, { decimals: 2 })}</dd></div>
                <div><dt>Rendite auf Einstand</dt><dd>{fmtPct(dividends.data?.yield_on_cost, { decimals: 2 })}</dd></div>
              </dl>
              <p className="xs muted">Vor Steuern, gerechnet mit dem heutigen Bestand; künftige Käufe und Dividendenerhöhungen sind nicht enthalten.{estimated > 0 && <> Davon <Money value={estimated} decimals={0} /> aus geschätzten Terminen oder Beträgen.</>} <Link to="/dividenden">Einzelne Zahlungen ansehen</Link></p>
            </div>
          )}
        </Panel>
      </div>
    </div>
  )
}
