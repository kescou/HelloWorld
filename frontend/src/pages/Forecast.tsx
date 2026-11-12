import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Info } from 'lucide-react'
import { useCashflow, useDividends, useForecast, useSummary } from '../api/hooks'
import { Chart, useChartTheme } from '../components/Chart'
import { Async, Badge, EmptyState, Legend, Money, Panel, Skeleton } from '../components/ui'
import { barOption, timeOption } from '../lib/charts'
import { fmtMoney, fmtPct } from '../lib/format'

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
  const [divGrowth, setDivGrowth] = useState(5)
  const t = useChartTheme()

  // Default savings rate: average monthly surplus of the last 12 months, rounded to 50 €.
  const suggested = useMemo(() => {
    const m = cashflow.data ?? []
    if (!m.length) return 500
    return Math.max(0, Math.round(m.reduce((s, x) => s + x.savings, 0) / m.length / 50) * 50)
  }, [cashflow.data])
  const monthly = savings ?? suggested
  const params = useDebounced({ years, monthly_savings: monthly, expected_return: ret })
  const forecast = useForecast(params)
  const series = forecast.data?.series ?? []
  const last = series[series.length - 1]

  const option = useMemo(() => timeOption(t, {
    dates: series.map((s) => `${s.year}-12-31`), zeroBased: true,
    series: [{ name: 'Erwartet (Median)', data: series.map((s) => s.p50), color: t.c[0], width: 2.5 }, { name: 'Eingezahlt', data: series.map((s) => s.contributions), color: t.c[7], dashed: true, width: 1.5 }],
    band: { name: 'Spanne 10–90 %', lower: series.map((s) => s.p10), upper: series.map((s) => s.p90), color: t.c[0] },
  }), [series, t])

  const base = dividends.data?.forecast_12m.reduce((s, m) => s + m.amount, 0) ?? 0
  const yieldPct = dividends.data?.portfolio_yield ?? 0
  const divSeries = useMemo(() => {
    const now = new Date().getFullYear()
    return Array.from({ length: Math.min(years, 30) + 1 }, (_, i) => {
      const fromExisting = base * Math.pow(1 + divGrowth / 100, i)
      // new savings buy shares at the current portfolio yield; their dividends grow at the same rate
      let fromNew = 0
      for (let k = 1; k <= i; k++) fromNew += monthly * 12 * (yieldPct / 100) * Math.pow(1 + divGrowth / 100, i - k)
      return { year: now + i, existing: fromExisting, added: fromNew }
    })
  }, [base, divGrowth, years, monthly, yieldPct])
  const divOption = useMemo(() => barOption(t, {
    categories: divSeries.map((d) => String(d.year)), maxBarWidth: 22,
    series: [{ name: 'Aus heutigem Bestand', data: divSeries.map((d) => d.existing), color: t.c[1], stack: 'd' }, { name: 'Aus neuen Sparraten', data: divSeries.map((d) => d.added), color: t.c[4], stack: 'd' }],
    extraRows: (i) => [{ label: 'Summe im Jahr', value: t.privacy ? '•••' : fmtMoney(divSeries[i].existing + divSeries[i].added) }, { label: 'Ø pro Monat', value: t.privacy ? '•••' : fmtMoney((divSeries[i].existing + divSeries[i].added) / 12) }],
  }), [divSeries, t])
  const lastDiv = divSeries[divSeries.length - 1]
  const tableRows = series.filter((_s, i) => i > 0 && (series.length <= 12 || i % 5 === 0 || i === series.length - 1))

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
          <Async query={forecast} what="Die Prognose" skeleton={<Skeleton h={320} />} isEmpty={(d) => d.series.length < 2} empty={<EmptyState compact title="Keine Prognose möglich" text="Der Server hat keine Prognosewerte geliefert." />}>
            {() => (
              <div className="stack stack--sm">
                <Chart option={option} height={320} label="Vermögensprognose mit 10-, 50- und 90-Prozent-Quantil" />
                <Legend items={[{ label: 'Erwartet (Median)', color: 'var(--c1)', kind: 'line' }, { label: 'Spanne 10–90 %', color: 'color-mix(in srgb, var(--c1) 30%, transparent)' }, { label: 'Eingezahlt (ohne Rendite)', color: 'var(--c8)', kind: 'dash' }]} />
              </div>
            )}
          </Async>
        </Panel>
      </div>

      {last && (
        <div className="kpis">
          <div className="kpi"><span className="kpi__label">Erwartet {last.year}</span><span className="kpi__value"><Money value={last.p50} decimals={0} /></span><span className="kpi__sub">Median</span></div>
          <div className="kpi"><span className="kpi__label">Vorsichtig</span><span className="kpi__value"><Money value={last.p10} decimals={0} /></span><span className="kpi__sub">in 9 von 10 Fällen mehr</span></div>
          <div className="kpi"><span className="kpi__label">Optimistisch</span><span className="kpi__value"><Money value={last.p90} decimals={0} /></span><span className="kpi__sub">in 1 von 10 Fällen mehr</span></div>
          <div className="kpi"><span className="kpi__label">Davon eingezahlt</span><span className="kpi__value"><Money value={last.contributions} decimals={0} /></span><span className="kpi__sub">Erträge im Median <Money value={last.p50 - last.contributions} decimals={0} tone /></span></div>
        </div>
      )}

      <div className="grid cols-2">
        <Panel title="Verlauf in Zahlen" flush>
          {tableRows.length === 0 ? <EmptyState compact title="Noch keine Werte" /> : (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Jahr</th><th className="num hide-sm">Vorsichtig</th><th className="num">Erwartet</th><th className="num hide-sm">Optimistisch</th><th className="num">Eingezahlt</th></tr></thead>
                <tbody>{tableRows.map((s) => <tr key={s.year}><td>{s.year}</td><td className="num hide-sm muted"><Money value={s.p10} decimals={0} /></td><td className="num strong"><Money value={s.p50} decimals={0} /></td><td className="num hide-sm muted"><Money value={s.p90} decimals={0} /></td><td className="num muted"><Money value={s.contributions} decimals={0} /></td></tr>)}</tbody>
              </table>
            </div>
          )}
        </Panel>
        <Panel title="Dividendenprognose" hint={<Badge tone="est">vereinfachtes Modell</Badge>}>
          {dividends.isPending ? <Skeleton h={260} /> : base === 0 ? (
            <EmptyState compact title="Noch keine Dividendenbasis" text="Die Prognose baut auf den erwarteten Dividenden der nächsten 12 Monate auf. Dafür braucht es ausschüttende Wertpapiere im Depot."><Link className="btn" to="/dividenden">Zu den Dividenden</Link></EmptyState>
          ) : (
            <div className="stack stack--sm">
              <Slider label="Dividendenwachstum p. a." value={divGrowth} onChange={setDivGrowth} min={0} max={12} step={0.5} format={(n) => fmtPct(n)} />
              <Chart option={divOption} height={220} label="Prognose der jährlichen Bruttodividenden" />
              <Legend items={[{ label: 'Aus heutigem Bestand', color: 'var(--c2)' }, { label: 'Aus neuen Sparraten', color: 'var(--c5)' }]} />
              <p className="small">In {lastDiv.year} wären das etwa <strong><Money value={lastDiv.existing + lastDiv.added} decimals={0} /></strong> brutto im Jahr, also <strong><Money value={(lastDiv.existing + lastDiv.added) / 12} decimals={0} /></strong> pro Monat.</p>
              <div className="notice"><Info size={16} aria-hidden /><span className="xs">Basis: erwartete Dividenden der nächsten 12 Monate (<Money value={base} decimals={0} />), neue Sparraten zur heutigen Depotrendite von {fmtPct(yieldPct, { decimals: 2 })}. Im Browser gerechnet, ohne Wiederanlage.</span></div>
            </div>
          )}
        </Panel>
      </div>
    </div>
  )
}
