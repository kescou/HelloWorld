import { Fragment, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronDown, ChevronRight, Info, Layers, Search } from 'lucide-react'
import { useAccounts, useLookthrough } from '../api/hooks'
import type { LookthroughItem } from '../api/types'
import { AllocationList } from '../components/shared'
import { Async, EmptyState, Legend, Money, Panel, SkeletonRows, SortTh, SplitBar, sortBy, useSort } from '../components/ui'
import { fmtPct } from '../lib/format'

type SortKey = 'name' | 'weight' | 'total_value' | 'direct_value' | 'etf_value'
const PAGE = 25

export function Lookthrough() {
  const accounts = useAccounts()
  const [accountId, setAccountId] = useState<number | undefined>()
  const lt = useLookthrough(accountId)
  const [q, setQ] = useState('')
  const [open, setOpen] = useState<Set<string>>(new Set())
  const [all, setAll] = useState(false)
  const { sort, onSort } = useSort<SortKey>('total_value')
  const depots = (accounts.data ?? []).filter((a) => a.type === 'brokerage')

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase()
    const items = (lt.data?.items ?? []).filter((i) => !needle || [i.name, i.isin, i.symbol].some((s) => s?.toLowerCase().includes(needle)))
    return sortBy<LookthroughItem>(items, (i) => i[sort.key], sort.dir)
  }, [lt.data, q, sort])
  const visible = all || q ? filtered : filtered.slice(0, PAGE)
  const toggle = (key: string) => setOpen((s) => { const n = new Set(s); if (n.has(key)) n.delete(key); else n.add(key); return n })

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Durchschau</h1>
          <p className="page-head__sub">Welche Unternehmen stecken wirklich im Depot – direkt gehalten und über ETFs, in einer Liste.</p>
        </div>
        <div className="page-head__actions">
          <label className="sr-only" htmlFor="lt-depot">Depot auswählen</label>
          <select id="lt-depot" className="select" style={{ width: 'auto' }} value={accountId ?? ''} onChange={(e) => setAccountId(e.target.value ? Number(e.target.value) : undefined)}>
            <option value="">Alle Depots</option>
            {depots.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </div>
      </div>

      <Async query={lt} what="Die Durchschau" skeleton={<Panel><SkeletonRows rows={10} pad={false} /></Panel>} isEmpty={(d) => d.items.length === 0}
        empty={<Panel><EmptyState icon={<Layers size={24} aria-hidden />} title="Noch nichts zum Durchschauen" text="Sobald ETFs oder Einzelaktien im Depot liegen, werden sie hier in ihre Unternehmen zerlegt.">
          <Link className="btn btn--primary" to="/import">Abrechnung importieren</Link><Link className="btn" to="/depot">Zum Depot</Link></EmptyState></Panel>}>
        {(d) => {
          const direct = d.items.reduce((s, i) => s + i.direct_value, 0)
          const viaEtf = d.items.reduce((s, i) => s + i.etf_value, 0)
          const rest = Math.max(0, d.total_value - direct - viaEtf)
          const pct = (v: number) => (d.total_value ? (v / d.total_value) * 100 : 0)
          const etfSources = new Map<number, string>()
          for (const i of d.items) for (const s of i.sources) if (s.weight_in_etf != null) etfSources.set(s.instrument_id, s.name)
          return (
            <>
              <section className="stack stack--sm" aria-label="Abdeckung">
                <div className="lead">
                  <span className="lead__label">Depotwert in der Durchschau</span>
                  <div className="lead__row"><Money className="lead__value" value={d.total_value} /><span className="muted small">{fmtPct(d.coverage_pct)} Unternehmen zugeordnet</span></div>
                </div>
                <SplitBar label="Abdeckung der Durchschau" parts={[
                  { label: 'Direkt gehalten', value: direct, color: 'var(--c1)' },
                  { label: 'Über ETF zugeordnet', value: viaEtf, color: 'var(--c2)' },
                  { label: 'Nicht aufgeschlüsselt', value: rest, color: 'var(--c8)', hatched: true },
                ]} />
                <ul className="legend">
                  <li><span className="swatch" style={{ backgroundColor: 'var(--c1)' }} />Direkt gehalten <span className="amt strong">&nbsp;<Money value={direct} decimals={0} /></span>&nbsp;· {fmtPct(pct(direct))}</li>
                  <li><span className="swatch" style={{ backgroundColor: 'var(--c2)' }} />Über ETF <span className="strong">&nbsp;<Money value={viaEtf} decimals={0} /></span>&nbsp;· {fmtPct(pct(viaEtf))}</li>
                  {rest > 1 && <li><span className="swatch" style={{ backgroundColor: 'var(--c8)' }} />Nicht aufgeschlüsselt <span className="strong">&nbsp;<Money value={rest} decimals={0} /></span>&nbsp;· {fmtPct(pct(rest))}</li>}
                </ul>
              </section>
              {d.coverage_pct < 85 && (
                <div className="notice notice--warn">
                  <Info size={17} aria-hidden />
                  <div>
                    <strong>{fmtPct(100 - d.coverage_pct)} des Depotwerts sind keinem Unternehmen zugeordnet.</strong> Für manche ETFs liegen nur die größten Positionen vor. Eine vollständige Liste lässt sich als CSV des Emittenten hochladen:{' '}
                    {[...etfSources.entries()].map(([id, name], i) => <Fragment key={id}>{i > 0 && ', '}<Link to={`/wertpapier/${id}#zusammensetzung`}>{name}</Link></Fragment>)}.
                  </div>
                </div>
              )}

              <div className="grid cols-main-side">
                <Panel title="Unternehmen" flush hint={`${filtered.length} ${q ? 'Treffer' : 'Unternehmen'}`}
                  actions={<div className="search" style={{ flex: '1 1 220px', maxWidth: 280 }}><Search size={15} aria-hidden /><input className="input" type="search" placeholder="Name oder ISIN suchen" aria-label="Unternehmen suchen" value={q} onChange={(e) => setQ(e.target.value)} /></div>}
                  foot={!q && filtered.length > PAGE ? <><span>{visible.length} von {filtered.length} angezeigt</span><button className="btn btn--sm" onClick={() => setAll((v) => !v)}>{all ? 'Nur die größten 25' : 'Alle anzeigen'}</button></> : undefined}>
                  {filtered.length === 0 ? <EmptyState compact title="Kein Treffer" text={`Kein Unternehmen passt zu „${q}“.`}><button className="btn" onClick={() => setQ('')}>Suche zurücksetzen</button></EmptyState> : (
                    <div className="table-wrap">
                      <table className="table">
                        <thead>
                          <tr>
                            <SortTh k="name" sort={sort} onSort={onSort}>Unternehmen</SortTh>
                            <SortTh k="weight" sort={sort} onSort={onSort} className="num">Anteil</SortTh>
                            <SortTh k="total_value" sort={sort} onSort={onSort} className="num">Gesamt</SortTh>
                            <SortTh k="direct_value" sort={sort} onSort={onSort} className="num hide-sm">Direkt</SortTh>
                            <SortTh k="etf_value" sort={sort} onSort={onSort} className="num hide-sm">Über ETF</SortTh>
                            <th className="hide-md" style={{ width: 110 }}>Aufteilung</th>
                          </tr>
                        </thead>
                        <tbody>
                          {visible.map((i) => {
                            const isOpen = open.has(i.key)
                            return (
                              <Fragment key={i.key}>
                                <tr className={isOpen ? 'is-open' : undefined}>
                                  <td className="cell-name">
                                    <button type="button" className="row-toggle" aria-expanded={isOpen} onClick={() => toggle(i.key)}>
                                      {isOpen ? <ChevronDown size={15} aria-hidden /> : <ChevronRight size={15} aria-hidden />}<span className="truncate">{i.name}</span>
                                    </button>
                                    <span className="meta truncate" style={{ paddingLeft: 18 }}>
                                      {i.isin ?? i.symbol ?? ''}
                                      <span className="show-sm"> · {i.direct_value > 0 && i.etf_value > 0 ? 'direkt + ETF' : i.direct_value > 0 ? 'direkt' : 'über ETF'}</span>
                                    </span>
                                  </td>
                                  <td className="num">{fmtPct(i.weight, { decimals: 2 })}</td>
                                  <td className="num"><Money value={i.total_value} /></td>
                                  <td className="num hide-sm">{i.direct_value > 0 ? <Money value={i.direct_value} /> : <span className="faint">–</span>}</td>
                                  <td className="num hide-sm">{i.etf_value > 0 ? <Money value={i.etf_value} /> : <span className="faint">–</span>}</td>
                                  <td className="hide-md"><SplitBar small label={`Aufteilung ${i.name}`} parts={[{ label: 'direkt', value: i.direct_value, color: 'var(--c1)' }, { label: 'über ETF', value: i.etf_value, color: 'var(--c2)' }]} /></td>
                                </tr>
                                {isOpen && (
                                  <tr className="sub-row">
                                    <td colSpan={6}>
                                      <ul className="legend legend--rows" style={{ paddingLeft: 18, paddingBottom: 6 }}>
                                        {i.sources.map((s, n) => (
                                          <li key={n}>
                                            <span className="swatch" style={{ backgroundColor: s.weight_in_etf == null ? 'var(--c1)' : 'var(--c2)' }} />
                                            <span className="truncate"><Link to={`/wertpapier/${s.instrument_id}`}>{s.name}</Link></span>
                                            <span className="val"><Money value={s.value} /></span>
                                            <span className="pct" style={{ minWidth: '9.5em' }}>{s.weight_in_etf != null ? `${fmtPct(s.weight_in_etf, { decimals: 2 })} des ETF` : 'Einzelaktie'}</span>
                                          </li>
                                        ))}
                                      </ul>
                                    </td>
                                  </tr>
                                )}
                              </Fragment>
                            )
                          })}
                        </tbody>
                        {!q && rest > 1 && (
                          <tfoot>
                            <tr><td className="muted" style={{ fontWeight: 400 }}>Nicht aufgeschlüsselt (übrige ETF-Positionen)</td><td className="num">{fmtPct(pct(rest), { decimals: 2 })}</td><td className="num"><Money value={rest} /></td><td className="hide-sm" /><td className="num hide-sm"><Money value={rest} /></td><td className="hide-md" /></tr>
                          </tfoot>
                        )}
                      </table>
                    </div>
                  )}
                </Panel>
                <div className="stack">
                  <Panel title="Nach Sektor"><AllocationList slices={d.by_sector} label="Durchschau nach Sektor" max={8} /></Panel>
                  <Panel title="Nach Land"><AllocationList slices={d.by_country} label="Durchschau nach Land" max={8} /></Panel>
                </div>
              </div>
              <Legend items={[{ label: 'direkt gehalten', color: 'var(--c1)' }, { label: 'über ETF', color: 'var(--c2)' }]} />
            </>
          )
        }}
      </Async>
    </div>
  )
}
