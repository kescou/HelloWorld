import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Info, Landmark } from 'lucide-react'
import { useRealizedGains, useTaxes } from '../api/hooks'
import { Async, Badge, EmptyState, Money, Panel, Skeleton, SplitBar } from '../components/ui'
import { fmtDate, fmtMoney, fmtNumber, fmtPct, fmtQty } from '../lib/format'

const CLASS_LABEL: Record<string, string> = { stock: 'Aktien', fund: 'ETFs und Fonds', other: 'Sonstige Wertpapiere' }

export function Taxes() {
  const thisYear = new Date().getFullYear()
  const [year, setYear] = useState(thisYear)
  const taxes = useTaxes(year)
  const gains = useRealizedGains(year)

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Steuern</h1><p className="page-head__sub">Kapitalerträge eines Jahres im Überblick: realisierte Gewinne, Dividenden, Zinsen und der Sparerpauschbetrag.</p></div>
        <div className="page-head__actions">
          <label className="sr-only" htmlFor="tax-year">Jahr</label>
          <select id="tax-year" className="select" style={{ width: 'auto' }} value={year} onChange={(e) => setYear(Number(e.target.value))}>
            {(taxes.data?.years ?? [thisYear]).map((y) => <option key={y} value={y}>{y}</option>)}
          </select>
        </div>
      </div>

      <div className="notice" role="note">
        <Info size={17} aria-hidden />
        <div><strong>Überblick und Schätzung, keine Steuerberatung.</strong> Die Zahlen stammen aus den erfassten Buchungen. Nicht berücksichtigt sind die Teilfreistellung von Fonds, die Vorabpauschale, der getrennte Verlusttopf für Aktien, anrechenbare Quellensteuer, Kirchensteuer und die Aufteilung von Freistellungsaufträgen auf mehrere Banken. Maßgeblich ist die Jahressteuerbescheinigung der Bank.</div>
      </div>

      <Async query={taxes} what="Die Steuerübersicht" skeleton={<div className="stack"><Skeleton h={90} /><Skeleton h={220} /></div>}>
        {(t) => {
          const nothing = t.realized.count === 0 && t.dividends.count === 0 && t.interest.count === 0 && t.other_taxes.count === 0
          if (nothing) return (
            <Panel><EmptyState icon={<Landmark size={24} aria-hidden />} title={`Keine Kapitalerträge in ${t.year}`} text="Verkäufe, Dividenden und Zinsen dieses Jahres erscheinen hier, sobald sie gebucht sind.">
              <Link className="btn btn--primary" to="/import">Abrechnung importieren</Link><Link className="btn" to="/transaktionen">Zu den Transaktionen</Link></EmptyState></Panel>
          )
          const over = t.taxable_income > 0
          return (
            <>
              <section className="stack stack--sm" aria-label="Sparerpauschbetrag">
                <div className="lead">
                  <span className="lead__label">Kapitalerträge {t.year} vor Steuern</span>
                  <div className="lead__row">
                    <Money className="lead__value" value={t.capital_income} />
                    <span className="muted small">{over
                      ? <>davon <span className="amt">{fmtMoney(t.taxable_income)}</span> über dem Sparerpauschbetrag</>
                      : t.capital_income > 0 ? <>vollständig durch den Sparerpauschbetrag gedeckt</> : <>kein steuerpflichtiger Ertrag</>}</span>
                  </div>
                </div>
                <SplitBar label="Sparerpauschbetrag" parts={[
                  { label: 'genutzt', value: t.allowance_used, color: 'var(--c1)' },
                  { label: 'noch frei', value: t.allowance_remaining, color: 'var(--c8)', hatched: true },
                  { label: 'über dem Pauschbetrag', value: t.taxable_income, color: 'var(--c2)' },
                ]} />
                <ul className="legend">
                  <li><span className="swatch" style={{ backgroundColor: 'var(--c1)' }} />Pauschbetrag genutzt&nbsp;<span className="amt strong">{fmtMoney(t.allowance_used)}</span>&nbsp;von {fmtMoney(t.allowance, { decimals: 0 })}</li>
                  {t.allowance_remaining > 0 && <li><span className="swatch" style={{ backgroundColor: 'var(--c8)' }} />noch frei&nbsp;<span className="amt strong">{fmtMoney(t.allowance_remaining)}</span></li>}
                  {over && <li><span className="swatch" style={{ backgroundColor: 'var(--c2)' }} />darüber&nbsp;<span className="amt strong">{fmtMoney(t.taxable_income)}</span></li>}
                </ul>
                <p className="xs muted">Sparerpauschbetrag {fmtMoney(t.allowance, { decimals: 0 })} – <Link to="/einstellungen#steuern">in den Einstellungen ändern</Link> (1.000 € allein, 2.000 € bei Zusammenveranlagung).</p>
              </section>

              <div className="kpis">
                <div className="kpi"><span className="kpi__label">Realisierte Gewinne</span><span className="kpi__value"><Money value={t.realized.net} tone /></span><span className="kpi__sub">{t.realized.count} {t.realized.count === 1 ? 'Verkauf' : 'Verkäufe'}</span></div>
                <div className="kpi"><span className="kpi__label">Dividenden brutto</span><span className="kpi__value"><Money value={t.dividends.gross} /></span><span className="kpi__sub">netto <span className="amt">{fmtMoney(t.dividends.net)}</span></span></div>
                <div className="kpi"><span className="kpi__label">Zinsen brutto</span><span className="kpi__value"><Money value={t.interest.gross} /></span><span className="kpi__sub">netto <span className="amt">{fmtMoney(t.interest.net)}</span></span></div>
                <div className="kpi"><span className="kpi__label">Einbehaltene Steuern</span><span className="kpi__value"><Money value={t.taxes_withheld} /></span><span className="kpi__sub">laut Buchungen</span></div>
                <div className="kpi"><span className="kpi__label">Steuer geschätzt</span><span className="kpi__value"><Money value={t.estimated_tax} /></span><span className="kpi__sub"><Badge tone="est" title="Abgeltungsteuer 25 % + Solidaritätszuschlag auf den Betrag über dem Sparerpauschbetrag">Schätzung · {fmtNumber(t.tax_rate, 3)} %</Badge></span></div>
              </div>

              <div className="grid cols-main-side">
                <Panel title={`Erträge ${t.year}`} flush>
                  <div className="table-wrap">
                    <table className="table">
                      <thead><tr><th>Art</th><th className="num">Vor Steuern</th><th className="num hide-sm">Steuern einbehalten</th><th className="num">Nach Steuern</th></tr></thead>
                      <tbody>
                        <tr><td><span className="name">Realisierte Gewinne</span><span className="meta">{t.realized.count ? `${t.realized.count} Verkäufe, Einstand nach FIFO` : 'keine Verkäufe'}</span></td><td className="num"><Money value={t.realized.gains} tone /></td><td className="num hide-sm"><Money value={t.realized.taxes} /></td><td className="num"><Money value={t.realized.net - t.realized.taxes} tone /></td></tr>
                        {t.realized.losses !== 0 && <tr><td><span className="name">Realisierte Verluste</span><span className="meta">mindern die Erträge</span></td><td className="num"><Money value={t.realized.losses} tone /></td><td className="num hide-sm"><span className="faint">–</span></td><td className="num"><span className="faint">–</span></td></tr>}
                        <tr><td><span className="name">Dividenden</span><span className="meta">{t.dividends.count} {t.dividends.count === 1 ? 'Gutschrift' : 'Gutschriften'}</span></td><td className="num"><Money value={t.dividends.gross} /></td><td className="num hide-sm"><Money value={t.dividends.taxes} /></td><td className="num"><Money value={t.dividends.net} /></td></tr>
                        <tr><td><span className="name">Zinsen</span><span className="meta">{t.interest.count} {t.interest.count === 1 ? 'Gutschrift' : 'Gutschriften'}</span></td><td className="num"><Money value={t.interest.gross} /></td><td className="num hide-sm"><Money value={t.interest.taxes} /></td><td className="num"><Money value={t.interest.net} /></td></tr>
                        {t.other_taxes.count > 0 && <tr><td><span className="name">Separat gebuchte Steuern</span><span className="meta">{t.other_taxes.count} Buchungen vom Typ „Steuer“</span></td><td className="num"><span className="faint">–</span></td><td className="num hide-sm"><Money value={t.other_taxes.amount} /></td><td className="num"><span className="faint">–</span></td></tr>}
                      </tbody>
                      <tfoot><tr><td>Summe</td><td className="num"><Money value={t.capital_income} /></td><td className="num hide-sm"><Money value={t.taxes_withheld} /></td><td className="num"><Money value={t.capital_income - t.taxes_withheld} /></td></tr></tfoot>
                    </table>
                  </div>
                </Panel>
                <Panel title="Rechnung">
                  <dl className="facts facts--rows">
                    <div><dt>Kapitalerträge</dt><dd><Money value={t.capital_income} /></dd></div>
                    <div><dt>− Sparerpauschbetrag</dt><dd><Money value={-t.allowance_used} /></dd></div>
                    <div><dt>= Steuerpflichtig</dt><dd><Money value={t.taxable_income} /></dd></div>
                    <div><dt>× {fmtNumber(t.tax_rate, 3)} % (Schätzung)</dt><dd><Money value={t.estimated_tax} /></dd></div>
                    <div><dt>Bereits einbehalten</dt><dd><Money value={t.taxes_withheld} /></dd></div>
                  </dl>
                  <p className="xs muted" style={{ marginTop: 10 }}>{Math.abs(t.difference) < 1 ? 'Einbehalt und Schätzung liegen gleichauf.'
                    : t.difference > 0 ? <>Die Banken haben rund <span className="amt">{fmtMoney(t.difference, { decimals: 0 })}</span> mehr einbehalten als die Schätzung ergibt – z. B. weil kein Freistellungsauftrag vorlag. Das lässt sich über die Steuererklärung zurückholen.</>
                    : <>Die Schätzung liegt rund <span className="amt">{fmtMoney(-t.difference, { decimals: 0 })}</span> über dem Einbehalt – etwa weil Freistellungsaufträge bei mehreren Banken zusammen den Pauschbetrag übersteigen oder Erträge ohne Steuerabzug zuflossen.</>}</p>
                </Panel>
              </div>

              {t.realized.by_class.length > 1 && (
                <Panel title="Realisierte Gewinne nach Art" flush hint="Verluste aus Aktien lassen sich steuerlich nur mit Aktiengewinnen verrechnen">
                  <div className="table-wrap"><table className="table">
                    <thead><tr><th>Art</th><th className="num hide-sm">Gewinne</th><th className="num hide-sm">Verluste</th><th className="num">Saldo</th><th className="num">Steuern</th></tr></thead>
                    <tbody>{t.realized.by_class.map((c) => <tr key={c.key}><td>{CLASS_LABEL[c.key] ?? c.key}<span className="meta">{c.count} {c.count === 1 ? 'Verkauf' : 'Verkäufe'}</span></td><td className="num hide-sm"><Money value={c.gains} /></td><td className="num hide-sm"><Money value={c.losses} /></td><td className="num"><Money value={c.net} tone /></td><td className="num"><Money value={c.taxes} /></td></tr>)}</tbody>
                  </table></div>
                </Panel>
              )}

              <Panel title={`Verkäufe ${t.year}`} flush hint={gains.data?.length ? `${gains.data.length} Verkäufe` : undefined}>
                <Async query={gains} what="Die Verkäufe" compact empty={<EmptyState compact title="Keine Verkäufe in diesem Jahr" />}>
                  {(list) => (
                    <div className="table-wrap"><table className="table">
                      <thead><tr><th>Wertpapier</th><th className="hide-sm">Datum</th><th className="num hide-md">Stück</th><th className="num hide-sm">Erlös</th><th className="num hide-sm">Einstand</th><th className="num">Gewinn</th><th className="num hide-md">Steuern</th></tr></thead>
                      <tbody>{list.map((g, n) => (
                        <tr key={n}>
                          <td className="cell-name" style={{ maxWidth: 280 }}>{g.instrument_id ? <Link className="name truncate" to={`/wertpapier/${g.instrument_id}`}>{g.instrument}</Link> : <span className="name truncate">{g.instrument}</span>}<span className="meta show-sm">{fmtDate(g.date)}</span></td>
                          <td className="hide-sm muted">{fmtDate(g.date)}</td><td className="num hide-md">{fmtQty(g.quantity)}</td>
                          <td className="num hide-sm"><Money value={g.proceeds} /></td><td className="num hide-sm"><Money value={g.cost} /></td>
                          <td className="num"><Money value={g.gain} tone /><span className="meta show-sm">{g.cost ? fmtPct((g.gain / g.cost) * 100, { signed: true, decimals: 1 }) : ''}</span></td><td className="num hide-md"><Money value={g.taxes} /></td>
                        </tr>
                      ))}</tbody>
                    </table></div>
                  )}
                </Async>
              </Panel>
            </>
          )
        }}
      </Async>
    </div>
  )
}
