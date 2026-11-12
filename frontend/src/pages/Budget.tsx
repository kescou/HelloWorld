import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Wallet } from 'lucide-react'
import { useCashflow, useExpenses } from '../api/hooks'
import { CategoryManager } from '../components/CategoryManager'
import { Chart, useChartTheme } from '../components/Chart'
import { paletteVar } from '../components/shared'
import { Async, Badge, EmptyState, Legend, Money, Panel, Seg, Skeleton, SplitBar } from '../components/ui'
import { barOption, monthLabels } from '../lib/charts'
import { fmtMonth, fmtPct, isoDate } from '../lib/format'

type Span = '6' | '12' | '24'

export function Budget() {
  const [span, setSpan] = useState<Span>('12')
  const cashflow = useCashflow(Number(span))
  const months = cashflow.data ?? []
  const currentKey = isoDate(new Date()).slice(0, 7)
  // The running month is incomplete (salary may still be missing), so it is shown but kept out of averages and the rate line.
  const complete = months.filter((m) => m.month !== currentKey)
  const basis = complete.length ? complete : months
  const [month, setMonth] = useState<string>('')
  const selected = month || basis[basis.length - 1]?.month || ''
  const range = useMemo(() => {
    if (!selected) return { from: undefined, to: undefined }
    const [y, m] = selected.split('-').map(Number)
    return { from: `${selected}-01`, to: isoDate(new Date(y, m, 0)) }
  }, [selected])
  const expenses = useExpenses(range.from, range.to)
  const t = useChartTheme()

  const option = useMemo(() => barOption(t, {
    categories: months.map((m) => m.month), labels: monthLabels(months.map((m) => m.month)), tipTitle: (i) => fmtMonth(months[i].month, true),
    series: [{ name: 'Einnahmen', data: months.map((m) => m.income), color: t.c[0] }, { name: 'Ausgaben', data: months.map((m) => m.expenses), color: t.c[2] }],
    line: { name: 'Sparquote', data: months.map((m) => (m.month === currentKey || m.income <= 0 ? null : m.savings_rate)), color: t.c[1], unit: 'pct' }, maxBarWidth: 18,
  }), [months, t])
  const sum = useMemo(() => {
    const income = basis.reduce((s, m) => s + m.income, 0), exp = basis.reduce((s, m) => s + m.expenses, 0)
    const n = basis.length || 1
    return { income: income / n, expenses: exp / n, savings: (income - exp) / n, rate: income ? ((income - exp) / income) * 100 : null }
  }, [months])
  const nothing = cashflow.data && months.every((m) => m.income === 0 && m.expenses === 0)

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Haushaltsbuch</h1><p className="page-head__sub">Einnahmen und Ausgaben ohne Umbuchungen und Wertpapierkäufe.</p></div>
        <div className="page-head__actions"><Seg label="Zeitraum" value={span} onChange={setSpan} options={[{ value: '6', label: '6 Monate' }, { value: '12', label: '12 Monate' }, { value: '24', label: '24 Monate' }]} /></div>
      </div>
      <Async query={cashflow} what="Das Haushaltsbuch" skeleton={<div className="stack"><Skeleton h={76} /><Skeleton h={280} /></div>}>
        {() => nothing ? (
          <Panel><EmptyState icon={<Wallet size={24} aria-hidden />} title="Noch keine Einnahmen oder Ausgaben" text="Kontoauszug importieren – Gehalt, Miete und Kartenzahlungen werden erkannt und über Regeln kategorisiert.">
            <Link className="btn btn--primary" to="/import">Kontoauszug importieren</Link><Link className="btn" to="/transaktionen?neu=withdrawal">Ausgabe erfassen</Link></EmptyState></Panel>
        ) : (
          <>
            <div className="kpis">
              <div className="kpi"><span className="kpi__label">Ø Einnahmen</span><span className="kpi__value"><Money value={sum.income} decimals={0} /></span><span className="kpi__sub">pro Monat</span></div>
              <div className="kpi"><span className="kpi__label">Ø Ausgaben</span><span className="kpi__value"><Money value={sum.expenses} decimals={0} /></span><span className="kpi__sub">pro Monat</span></div>
              <div className="kpi"><span className="kpi__label">Ø Überschuss</span><span className="kpi__value"><Money value={sum.savings} decimals={0} tone /></span><span className="kpi__sub">pro Monat</span></div>
              <div className="kpi"><span className="kpi__label">Sparquote</span><span className="kpi__value">{fmtPct(sum.rate)}</span><span className="kpi__sub">über {basis.length} abgeschlossene Monate</span></div>
            </div>
            <Panel title="Einnahmen und Ausgaben je Monat">
              <div className="stack stack--sm">
                <Chart option={option} height={270} label="Einnahmen, Ausgaben und Sparquote je Monat" />
                <Legend items={[{ label: 'Einnahmen', color: 'var(--c1)' }, { label: 'Ausgaben', color: 'var(--c3)' }, { label: 'Sparquote (rechte Achse)', color: 'var(--c2)', kind: 'line' }]} />
              </div>
            </Panel>
            <div className="grid cols-2">
              <Panel title="Ausgaben nach Kategorie" actions={<>
                <label className="sr-only" htmlFor="budget-month">Monat</label>
                <select id="budget-month" className="select" style={{ width: 'auto' }} value={selected} onChange={(e) => setMonth(e.target.value)}>
                  {[...months].reverse().map((m) => <option key={m.month} value={m.month}>{fmtMonth(m.month, true)}</option>)}
                </select></>}>
                <Async query={expenses} what="Die Ausgaben" compact empty={<EmptyState compact title="Keine Ausgaben in diesem Monat" />}>
                  {(list) => {
                    const colored = list.map((e, i) => ({ ...e, color: e.category_id == null ? 'var(--c8)' : paletteVar(i) }))
                    return (
                      <div className="stack stack--sm">
                        <SplitBar label="Ausgaben nach Kategorie" parts={colored.map((e) => ({ label: e.name, value: e.amount, color: e.color, hatched: e.category_id == null }))} />
                        <ul className="legend legend--rows">
                          {colored.map((e) => (
                            <li key={e.category_id ?? 'none'}>
                              <span className="swatch" style={{ backgroundColor: e.color }} />
                              <Link className="truncate" style={{ color: 'inherit' }} to={`/transaktionen?${e.category_id ? `kategorie=${e.category_id}&` : ''}von=${range.from}&bis=${range.to}`}>{e.name}</Link>
                              <span className="val"><Money value={e.amount} /></span><span className="pct">{fmtPct(e.share)}</span>
                            </li>
                          ))}
                        </ul>
                        {colored.some((e) => e.category_id == null) && <p className="xs muted">Buchungen ohne Kategorie lassen sich unten über Regeln automatisch zuordnen.</p>}
                      </div>
                    )
                  }}
                </Async>
              </Panel>
              <Panel title="Monatsübersicht" flush hint="laufender Monat zählt nicht in die Durchschnitte">
                <div className="table-wrap" style={{ maxHeight: 420, overflowY: 'auto' }}>
                  <table className="table">
                    <thead><tr><th>Monat</th><th className="num hide-sm">Einnahmen</th><th className="num hide-sm">Ausgaben</th><th className="num">Überschuss</th><th className="num">Sparquote</th></tr></thead>
                    <tbody>
                      {[...months].reverse().map((m) => (
                        <tr key={m.month} className={m.month === selected ? 'is-open' : undefined}>
                          <td><button type="button" className="row-toggle" onClick={() => setMonth(m.month)} aria-pressed={m.month === selected}>{fmtMonth(m.month)}{m.month === currentKey && <Badge>laufend</Badge>}</button></td>
                          <td className="num hide-sm"><Money value={m.income} decimals={0} /></td><td className="num hide-sm"><Money value={m.expenses} decimals={0} /></td>
                          <td className="num"><Money value={m.savings} decimals={0} tone /></td><td className="num muted">{m.income > 0 && m.savings_rate > -100 ? fmtPct(m.savings_rate, { decimals: 0 }) : '–'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Panel>
            </div>
          </>
        )}
      </Async>
      <CategoryManager />
    </div>
  )
}
