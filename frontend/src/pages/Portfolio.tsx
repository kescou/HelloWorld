import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Layers, PieChart, Plus } from 'lucide-react'
import { useAccounts, useAllocation, useHoldings, usePerformance, useRealizedGains, useSummary } from '../api/hooks'
import type { AllocationBy, Holding, Range } from '../api/types'
import { Chart, useChartTheme } from '../components/Chart'
import { AllocationList, RANGES } from '../components/shared'
import { Async, Badge, Delta, EmptyState, Money, Panel, Seg, Skeleton, SkeletonRows, SortTh, sortBy, useSort } from '../components/ui'
import { timeOption } from '../lib/charts'
import { fmtDate, fmtMoney, fmtPct, fmtQty } from '../lib/format'
import { ALLOCATION_BY, INSTRUMENT_TYPE } from '../lib/labels'

type SortKey = 'name' | 'quantity' | 'avg_cost' | 'price' | 'market_value' | 'day_change_pct' | 'unrealized_gain' | 'weight'

export function Portfolio() {
  const accounts = useAccounts()
  const [accountId, setAccountId] = useState<number | undefined>()
  const holdings = useHoldings(accountId)
  const summary = useSummary()
  const [range, setRange] = useState<Range>('1Y')
  const perf = usePerformance(range)
  const [by, setBy] = useState<AllocationBy>('type')
  const [lookthrough, setLookthrough] = useState(false)
  const allocation = useAllocation(by, lookthrough)
  const realized = useRealizedGains(new Date().getFullYear())
  const { sort, onSort } = useSort<SortKey>('market_value')
  const t = useChartTheme()

  const perfOption = useMemo(() => {
    const s = perf.data?.series ?? []
    return timeOption(t, { dates: s.map((p) => p.date), series: [{ name: 'TWR-Index', data: s.map((p) => p.twr_index), color: t.c[0] }], unit: 'index', markLine: { value: 100, label: 'Start = 100' } })
  }, [perf.data, t])
  const rows = useMemo(() => sortBy<Holding>(holdings.data ?? [], (h) => h[sort.key], sort.dir), [holdings.data, sort])
  const totals = useMemo(() => {
    const h = holdings.data ?? []
    const mv = h.reduce((s, x) => s + x.market_value, 0), cb = h.reduce((s, x) => s + x.cost_basis, 0), dc = h.reduce((s, x) => s + x.day_change, 0)
    return { mv, cb, gain: mv - cb, gainPct: cb ? ((mv - cb) / cb) * 100 : null, dc, dcPct: mv - dc ? (dc / (mv - dc)) * 100 : null }
  }, [holdings.data])
  const depots = (accounts.data ?? []).filter((a) => a.type === 'brokerage')
  const canLookthrough = by === 'sector' || by === 'country'

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Depot</h1><p className="page-head__sub">Alle Wertpapierpositionen zum aktuellen Kurs in Euro.</p></div>
        <div className="page-head__actions">
          <label className="sr-only" htmlFor="depot-filter">Depot auswählen</label>
          <select id="depot-filter" className="select" style={{ width: 'auto' }} value={accountId ?? ''} onChange={(e) => setAccountId(e.target.value ? Number(e.target.value) : undefined)}>
            <option value="">Alle Depots</option>
            {depots.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
          <Link className="btn" to="/durchschau"><Layers size={15} aria-hidden /> Durchschau</Link>
          <Link className="btn btn--primary" to="/transaktionen?neu=buy"><Plus size={15} aria-hidden /> Kauf erfassen</Link>
        </div>
      </div>

      <div className="kpis">
        <div className="kpi"><span className="kpi__label">Depotwert</span><span className="kpi__value"><Money value={holdings.data ? totals.mv : null} decimals={0} /></span><span className="kpi__sub"><Delta pct={totals.dcPct} amount={holdings.data?.length ? totals.dc : null} /> heute</span></div>
        <div className="kpi"><span className="kpi__label">Einstand</span><span className="kpi__value"><Money value={holdings.data ? totals.cb : null} decimals={0} /></span><span className="kpi__sub">inkl. Gebühren</span></div>
        <div className="kpi"><span className="kpi__label">Buchgewinn</span><span className="kpi__value"><Money value={holdings.data ? totals.gain : null} decimals={0} tone /></span><span className="kpi__sub"><Delta pct={totals.gainPct} decimals={1} /></span></div>
        <div className="kpi"><span className="kpi__label">Rendite (TWR) {range}</span><span className="kpi__value">{perf.data?.twr != null ? <Delta pct={perf.data.twr} decimals={1} /> : '–'}</span><span className="kpi__sub">zeitgewichtet, alle Depots</span></div>
        <div className="kpi"><span className="kpi__label">Rendite (XIRR) {range}</span><span className="kpi__value">{perf.data?.xirr != null ? <Delta pct={perf.data.xirr} decimals={1} /> : '–'}</span><span className="kpi__sub">geldgewichtet p. a.</span></div>
        <div className="kpi"><span className="kpi__label">Dividenden {new Date().getFullYear()}</span><span className="kpi__value"><Money value={summary.data?.dividends_ytd} decimals={0} /></span><span className="kpi__sub"><Link to="/dividenden">Dividenden ansehen</Link></span></div>
      </div>

      <Panel title="Positionen" flush hint={holdings.data?.length ? `${holdings.data.length} Positionen` : undefined}>
        <Async query={holdings} what="Die Positionen" skeleton={<SkeletonRows rows={8} />}
          empty={<EmptyState icon={<PieChart size={24} aria-hidden />} title="Noch keine Wertpapiere im Depot" text="Wertpapierabrechnung importieren oder einen Kauf manuell erfassen – danach erscheinen Positionen, Kurse und Gewinne hier.">
            <Link className="btn btn--primary" to="/import">Abrechnung importieren</Link><Link className="btn" to="/transaktionen?neu=buy">Kauf erfassen</Link></EmptyState>}>
          {() => (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <SortTh k="name" sort={sort} onSort={onSort}>Wertpapier</SortTh>
                    <SortTh k="quantity" sort={sort} onSort={onSort} className="num hide-sm">Stück</SortTh>
                    <SortTh k="avg_cost" sort={sort} onSort={onSort} className="num hide-md">Ø Einstand</SortTh>
                    <SortTh k="price" sort={sort} onSort={onSort} className="num hide-sm">Kurs</SortTh>
                    <SortTh k="day_change_pct" sort={sort} onSort={onSort} className="num hide-md">Heute</SortTh>
                    <SortTh k="market_value" sort={sort} onSort={onSort} className="num">Wert</SortTh>
                    <SortTh k="unrealized_gain" sort={sort} onSort={onSort} className="num">Buchgewinn</SortTh>
                    <SortTh k="weight" sort={sort} onSort={onSort} className="num hide-md">Anteil</SortTh>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((h) => (
                    <tr key={h.instrument_id}>
                      <td className="cell-name">
                        <Link className="name truncate" to={`/wertpapier/${h.instrument_id}`}>{h.name}</Link>
                        <span className="meta truncate">{INSTRUMENT_TYPE[h.type]}<span className="hide-sm"> · {h.isin ?? h.symbol}</span><span className="show-sm"> · {fmtQty(h.quantity)} Stk.</span></span>
                      </td>
                      <td className="num hide-sm">{fmtQty(h.quantity)}</td>
                      <td className="num hide-md muted"><span className="amt">{fmtMoney(h.avg_cost)}</span></td>
                      <td className="num hide-sm">{fmtMoney(h.price, { currency: h.price_currency })}{h.price_source === 'transaction' && <span className="meta" title="Noch kein Marktkurs geladen – gerechnet wird mit dem Kurs der letzten Transaktion">letzter Handelskurs</span>}</td>
                      <td className="num hide-md">{h.price_source === 'transaction' ? <span className="faint">–</span> : <Delta pct={h.day_change_pct} />}</td>
                      <td className="num"><Money value={h.market_value} /><span className="meta show-sm">{h.price_source === 'transaction' ? 'ohne Marktkurs' : <Delta pct={h.day_change_pct} />}</span></td>
                      <td className="num"><span className="hide-sm"><Money value={h.unrealized_gain} tone /></span><span className="meta" style={{ fontSize: 'inherit' }}><Delta pct={h.unrealized_gain_pct} decimals={1} /></span></td>
                      <td className="num hide-md muted">{fmtPct(h.weight)}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <td>Summe</td><td className="hide-sm" /><td className="hide-md" /><td className="hide-sm" />
                    <td className="num hide-md"><Delta pct={totals.dcPct} /></td>
                    <td className="num"><Money value={totals.mv} /></td>
                    <td className="num"><span className="hide-sm"><Money value={totals.gain} tone /></span><span className="show-sm"><Delta pct={totals.gainPct} decimals={1} /></span></td>
                    <td className="num hide-md">{fmtPct(100)}</td>
                  </tr>
                </tfoot>
              </table>
            </div>
          )}
        </Async>
      </Panel>

      <div className="grid cols-main-side">
        <Panel title="Wertentwicklung" hint="zeitgewichtet, ohne Ein- und Auszahlungen" actions={<Seg label="Zeitraum" value={range} onChange={setRange} options={RANGES} />}>
          <Async query={perf} what="Die Wertentwicklung" skeleton={<Skeleton h={260} />} isEmpty={(d) => d.series.length < 2}
            empty={<EmptyState compact title="Noch keine Wertentwicklung" text="Die Rendite wird berechnet, sobald Positionen und Kurshistorie vorhanden sind." />}>
            {(d) => (
              <div className="stack stack--sm">
                <Chart option={perfOption} height={260} label="Zeitgewichtete Wertentwicklung als Index" />
                <p className="small muted">Absoluter Gewinn im Zeitraum: <Money value={d.absolute_gain} tone /></p>
              </div>
            )}
          </Async>
        </Panel>
        <Panel title="Allokation">
          <div className="stack stack--sm">
            <Seg label="Aufteilung nach" value={by} onChange={setBy} options={(Object.keys(ALLOCATION_BY) as AllocationBy[]).map((k) => ({ value: k, label: ALLOCATION_BY[k] }))} />
            <label className="check" title={canLookthrough ? undefined : 'Durchschau wirkt bei Sektor und Land'}>
              <input type="checkbox" checked={lookthrough && canLookthrough} disabled={!canLookthrough} onChange={(e) => setLookthrough(e.target.checked)} />
              <span className={canLookthrough ? undefined : 'faint'}>ETFs durchschauen{canLookthrough ? '' : ' (bei Sektor und Land)'}</span>
            </label>
            <Async query={allocation} what="Die Allokation" compact empty={<EmptyState compact title="Keine Daten" text="Ohne Positionen gibt es nichts aufzuteilen." />}>
              {(d) => <AllocationList slices={d} label={`Depot nach ${ALLOCATION_BY[by]}`} max={8} />}
            </Async>
          </div>
        </Panel>
      </div>

      {realized.data && realized.data.length > 0 && (
        <Panel title={`Realisierte Gewinne ${new Date().getFullYear()}`} flush>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Wertpapier</th><th className="hide-sm">Verkauf am</th><th className="num hide-sm">Stück</th><th className="num hide-md">Erlös</th><th className="num hide-md">Einstand</th><th className="num">Ergebnis</th></tr></thead>
              <tbody>
                {realized.data.map((r, i) => (
                  <tr key={i}>
                    <td><span className="name">{r.instrument}</span><span className="meta show-sm">{fmtDate(r.date)} · {fmtQty(r.quantity)} Stk.</span></td>
                    <td className="hide-sm muted">{fmtDate(r.date)}</td><td className="num hide-sm">{fmtQty(r.quantity)}</td>
                    <td className="num hide-md"><Money value={r.proceeds} /></td><td className="num hide-md"><Money value={r.cost} /></td>
                    <td className="num"><Money value={r.gain} tone /> {r.gain < 0 && <Badge tone="loss">Verlust</Badge>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </div>
  )
}
