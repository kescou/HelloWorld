import { Fragment, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronDown, ChevronRight, Info, Layers, Search } from 'lucide-react'
import { useAccounts, useLookthrough } from '../api/hooks'
import type { LookthroughItem } from '../api/types'
import { ETF_FORMAT_HINT, EtfActions, sourceLabel } from '../components/EtfTools'
import { AllocationList } from '../components/shared'
import { Async, Badge, EmptyState, Legend, Money, Panel, SkeletonRows, SortTh, SplitBar, sortBy, useSort } from '../components/ui'
import { fmtDate, fmtPct } from '../lib/format'

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
    // `_other` is the part of the ETFs that is not broken down – shown as the remainder, not as a company
    const items = (lt.data?.items ?? []).filter((i) => i.key !== '_other').filter((i) => !needle || [i.name, i.isin, i.symbol].some((s) => s?.toLowerCase().includes(needle)))
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
          const companies = d.items.filter((i) => i.key !== '_other')
          const direct = companies.reduce((s, i) => s + i.direct_value, 0)
          const viaEtf = companies.reduce((s, i) => s + i.etf_value, 0)
          const rest = Math.max(0, d.total_value - direct - viaEtf)
          const pct = (v: number) => (d.total_value ? (v / d.total_value) * 100 : 0)
          const etfSources = new Map<number, string>()
          for (const e of d.etfs ?? []) if (e.coverage_pct < 85) etfSources.set(e.instrument_id, e.name)
          if (!d.etfs) for (const i of d.items) for (const s of i.sources) if (s.weight_in_etf != null) etfSources.set(s.instrument_id, s.name)
          return (
            <>
              <section className="stack stack--sm" aria-label="Abdeckung">
                <div className="lead">
                  <span className="lead__label">Depotwert in der Durchschau</span>
                  <div className="lead__row"><Money className="lead__value" value={d.total_value} /><span className="muted small">{fmtPct(pct(direct + viaEtf))} einzelnen Unternehmen zugeordnet</span></div>
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
              {pct(rest) > 15 && (
                <div className="notice notice--warn">
                  <Info size={17} aria-hidden />
                  <div>
                    <strong>{fmtPct(pct(rest))} des Depotwerts sind keinem Unternehmen zugeordnet.</strong> Für manche ETFs liegen nur die größten Positionen vor. Die vollständige Positionsliste des Emittenten (CSV/XLSX) lässt sich beim jeweiligen ETF hochladen:{' '}
                    {[...etfSources.entries()].map(([id, name], i) => <Fragment key={id}>{i > 0 && ', '}<Link to={`/wertpapier/${id}#zusammensetzung`}>{name}</Link></Fragment>)}. Der Status aller ETFs steht <a href="#etfs">unten</a>.
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
                                  <td className="cell-name" style={{ maxWidth: 240 }}>
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
              {(d.etfs?.length ?? 0) > 0 && (
                <section id="etfs">
                  <Panel title="ETFs im Depot" flush hint="Woher die Durchschau-Daten kommen">
                    <div className="table-wrap">
                      <table className="table">
                        <thead><tr><th>ETF</th><th className="num hide-sm">Wert</th><th className="hide-sm">Quelle</th><th className="hide-md">Stand</th><th style={{ width: 150 }}>Abdeckung</th><th className="hide-sm"><span className="sr-only">Aktionen</span></th></tr></thead>
                        <tbody>
                          {d.etfs!.map((e) => (
                            <tr key={e.instrument_id}>
                              <td className="cell-name" style={{ maxWidth: 260 }}><Link className="name truncate" to={`/wertpapier/${e.instrument_id}#zusammensetzung`}>{e.name}</Link><span className="meta truncate">{e.isin}<span className="show-sm"> · {sourceLabel(e.source)}</span></span><div className="show-sm" style={{ marginTop: 6 }}><EtfActions compact wrap id={e.instrument_id} name={e.name} source={e.source} hasHoldings={!!e.source} /></div></td>
                              <td className="num hide-sm"><Money value={e.value} /></td>
                              <td className="hide-sm">{e.source ? sourceLabel(e.source) : <Badge tone="warn">keine Daten</Badge>}{e.count ? <span className="meta">{e.count} Positionen</span> : null}</td>
                              <td className="hide-md muted">{fmtDate(e.as_of)}</td>
                              <td><SplitBar small label={`Abdeckung ${e.name}`} parts={[{ label: 'bekannt', value: e.coverage_pct, color: 'var(--c2)' }, { label: 'nicht aufgeschlüsselt', value: Math.max(0, 100 - e.coverage_pct), color: 'var(--c8)', hatched: true }]} /><span className="meta">{fmtPct(e.coverage_pct)}</span></td>
                              <td className="num hide-sm"><EtfActions compact id={e.instrument_id} name={e.name} source={e.source} hasHoldings={!!e.source} /></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <p className="xs muted" style={{ padding: '10px 16px 14px' }}>„Vom Anbieter laden“ ruft die vollständige Liste beim Emittenten ab (sonst die Top 10 von Yahoo). Hochladen: {ETF_FORMAT_HINT}</p>
                  </Panel>
                </section>
              )}
            </>
          )
        }}
      </Async>
    </div>
  )
}
