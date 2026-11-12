import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, CheckCircle2, FileInput, Plus, Scale, Sprout } from 'lucide-react'
import { useAccount, useAccounts, useHoldings, useTransactions } from '../api/hooks'
import type { Account, AccountType } from '../api/types'
import { Chart, useChartTheme } from '../components/Chart'
import { TxEntry } from '../components/TxForm'
import { TxTable } from '../components/TxTable'
import { accountColor } from '../components/shared'
import { Async, Badge, Delta, Drawer, EmptyState, Money, Panel, Seg, Skeleton, SkeletonRows, SplitBar } from '../components/ui'
import { timeOption } from '../lib/charts'
import { addDays, fmtDate, fmtPct, fmtQty, formatIban, todayIso } from '../lib/format'
import { ACCOUNT_TYPE, institutionLabel } from '../lib/labels'

const GROUPS: { title: string; types: AccountType[] }[] = [
  { title: 'Girokonten', types: ['checking', 'credit_card', 'other'] },
  { title: 'Tagesgeld', types: ['savings'] },
  { title: 'Depots', types: ['brokerage'] },
]

export function Accounts() {
  const accounts = useAccounts()
  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Konten</h1><p className="page-head__sub">Salden aus dem Journal, Depots zum aktuellen Kurs.</p></div>
        <div className="page-head__actions"><Link className="btn" to="/einstellungen#eroeffnung">Eröffnungssalden setzen</Link><Link className="btn" to="/einstellungen#konten"><Plus size={15} aria-hidden /> Konto anlegen</Link></div>
      </div>
      <Async query={accounts} what="Die Konten" skeleton={<Panel><SkeletonRows rows={7} pad={false} /></Panel>}
        empty={<Panel><EmptyState title="Noch keine Konten" text="Lege zuerst ein Giro-, Tagesgeld- oder Depotkonto an."><Link className="btn btn--primary" to="/einstellungen#konten">Konto anlegen</Link></EmptyState></Panel>}>
        {(list) => {
          const active = list.filter((a) => a.is_active)
          const total = active.reduce((s, a) => s + a.total_value, 0)
          return (
            <>
              <section className="stack stack--sm" aria-label="Summe aller Konten">
                <div className="lead"><span className="lead__label">Summe aller aktiven Konten</span><Money className="lead__value" value={Math.round(total * 100) / 100} /></div>
                {total === 0 && active.every((a) => a.total_value === 0) && <p className="small muted">Alle Konten stehen noch auf 0 €. Eröffnungssalden eintragen oder Kontoauszüge <Link to="/import">importieren</Link>, dann stimmen die Salden.</p>}
                {total > 0 && <SplitBar label="Verteilung auf Konten" parts={active.map((a, i) => ({ label: a.name, value: a.total_value, color: accountColor(a, i) }))} />}
              </section>
              {GROUPS.map((g) => {
                const rows = list.filter((a) => g.types.includes(a.type))
                if (!rows.length) return null
                const sum = rows.filter((a) => a.is_active).reduce((s, a) => s + a.total_value, 0)
                const depot = g.types.includes('brokerage')
                return (
                  <Panel key={g.title} title={g.title} flush actions={<span className="strong"><Money value={sum} /></span>}>
                    <div className="table-wrap">
                      <table className="table">
                        <thead>
                          <tr><th>Konto</th><th className="hide-sm">{depot ? 'Verrechnungskonto' : 'IBAN'}</th><th className="num hide-sm">{depot ? 'Anteil' : 'Zins p. a.'}</th><th className="num">{depot ? 'Depotwert' : 'Saldo'}</th></tr>
                        </thead>
                        <tbody>
                          {rows.map((a) => (
                            <tr key={a.id}>
                              <td className="cell-name">
                                <Link to={`/konten/${a.id}`} className="name row" style={{ flexWrap: 'nowrap' }}>
                                  <span className="acc-dot" style={{ backgroundColor: accountColor(a, list.indexOf(a)) }} /><span className="truncate">{a.name}</span>
                                  {!a.is_active && <Badge>inaktiv</Badge>}
                                </Link>
                                <span className="meta" style={{ paddingLeft: 18 }}>{institutionLabel(a.institution)}</span>
                              </td>
                              <td className="hide-sm muted nowrap">{depot ? list.find((x) => x.id === a.settlement_account_id)?.name ?? '–' : formatIban(a.iban)}</td>
                              <td className="num hide-sm muted">{depot ? fmtPct(total ? (a.total_value / total) * 100 : 0) : a.interest_rate != null ? fmtPct(a.interest_rate, { decimals: 2 }) : '–'}</td>
                              <td className="num"><Money value={a.total_value} /></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </Panel>
                )
              })}
            </>
          )
        }}
      </Async>
    </div>
  )
}

type Span = '3M' | '1J' | '2J'
const SPAN_DAYS: Record<Span, number> = { '3M': 92, '1J': 365, '2J': 731 }

export function AccountDetail() {
  const id = Number(useParams().id)
  const account = useAccount(id)
  const accounts = useAccounts()
  const [span, setSpan] = useState<Span>('1J')
  const [entry, setEntry] = useState(false)
  const tx = useTransactions({ account_id: id, limit: 15 })
  const t = useChartTheme()
  const a = account.data
  const isDepot = a?.type === 'brokerage'
  const holdings = useHoldings(isDepot ? id : undefined)

  const option = useMemo(() => {
    const from = addDays(todayIso(), -SPAN_DAYS[span])
    const h = (a?.history ?? []).filter((p) => p.date >= from)
    return timeOption(t, { dates: h.map((p) => p.date), series: [{ name: isDepot ? 'Depotwert' : 'Saldo', data: h.map((p) => p.value), color: t.c[0], area: true, step: !isDepot }] })
  }, [a, span, t, isDepot])
  const change = useMemo(() => {
    const from = addDays(todayIso(), -SPAN_DAYS[span])
    const h = (a?.history ?? []).filter((p) => p.date >= from)
    if (h.length < 2) return null
    return { abs: h[h.length - 1].value - h[0].value, pct: h[0].value ? ((h[h.length - 1].value - h[0].value) / Math.abs(h[0].value)) * 100 : null }
  }, [a, span])

  return (
    <div className="page">
      <Async query={account} what="Das Konto" skeleton={<div className="stack"><Skeleton h={60} w={320} /><Skeleton h={300} /></div>}>
        {(acc: Account & { history: { date: string; value: number }[]; snapshots?: { date: string; balance: number; computed_balance?: number | null }[] }) => (
          <>
            <div className="page-head">
              <div>
                <Link className="crumb" to="/konten"><ArrowLeft size={14} aria-hidden /> Konten</Link>
                <h1>{acc.name}</h1>
                <p className="page-head__sub">
                  {institutionLabel(acc.institution)} · {ACCOUNT_TYPE[acc.type]}{acc.iban ? ` · ${formatIban(acc.iban)}` : ''}
                  {acc.interest_rate != null ? ` · ${fmtPct(acc.interest_rate, { decimals: 2 })} p. a.` : ''}
                  {acc.settlement_account_id ? ` · Verrechnung über ${accounts.data?.find((x) => x.id === acc.settlement_account_id)?.name ?? 'Verrechnungskonto'}` : ''}
                </p>
              </div>
              <div className="page-head__actions">
                <button className="btn btn--primary" onClick={() => setEntry(true)}><Plus size={15} aria-hidden /> Transaktion erfassen</button>
              </div>
            </div>
            <div className="lead">
              <span className="lead__label">{isDepot ? 'Depotwert' : 'Saldo'}</span>
              <div className="lead__row">
                <Money className="lead__value" value={acc.total_value} />
                {change && <span className="row" style={{ gap: 6 }}><Delta pct={change.pct} amount={change.abs} decimals={1} /><span className="faint small">im Zeitraum</span></span>}
              </div>
            </div>

            <Panel title={isDepot ? 'Wertverlauf' : 'Saldo-Verlauf'} actions={<Seg label="Zeitraum" value={span} onChange={setSpan} options={['3M', '1J', '2J']} />}>
              {acc.history.length < 2
                ? <EmptyState compact icon={<Sprout size={22} aria-hidden />} title="Noch kein Verlauf" text={isDepot ? 'Mit dem ersten Kauf oder der ersten Einlieferung in dieses Depot entsteht der Verlauf.' : 'Mit dem Eröffnungssaldo oder der ersten Buchung auf diesem Konto entsteht der Verlauf.'}>{!isDepot && <Link className="btn" to="/einstellungen#eroeffnung">Eröffnungssaldo setzen</Link>}</EmptyState>
                : <Chart option={option} height={260} label={`${isDepot ? 'Wertverlauf' : 'Saldo-Verlauf'} ${acc.name}`} />}
            </Panel>

            {isDepot && (
              <Panel title="Positionen in diesem Depot" flush>
                <Async query={holdings} what="Die Positionen" empty={<EmptyState compact title="Keine Positionen" text="Käufe in diesem Depot erscheinen hier." />}>
                  {(hs) => (
                    <div className="table-wrap">
                      <table className="table">
                        <thead><tr><th>Wertpapier</th><th className="num hide-sm">Stück</th><th className="num hide-sm">Einstand</th><th className="num">Wert</th><th className="num">Buchgewinn</th></tr></thead>
                        <tbody>
                          {hs.map((h) => (
                            <tr key={h.instrument_id}>
                              <td className="cell-name"><Link className="name truncate" to={`/wertpapier/${h.instrument_id}`}>{h.name}</Link><span className="meta">{h.isin}<span className="show-sm"> · {fmtQty(h.quantity)} Stk.</span></span></td>
                              <td className="num hide-sm">{fmtQty(h.quantity)}</td>
                              <td className="num hide-sm"><Money value={h.cost_basis} /></td>
                              <td className="num"><Money value={h.market_value} /></td>
                              <td className="num"><Delta pct={h.unrealized_gain_pct} decimals={1} /></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </Async>
              </Panel>
            )}

            <div className={isDepot ? 'stack' : 'grid cols-main-side'}>
              <Panel title="Letzte Transaktionen" flush foot={tx.data && tx.data.total > 0 ? <><span>{Math.min(15, tx.data.total)} von {tx.data.total}</span><Link to={`/transaktionen?konto=${acc.id}`}>Alle anzeigen</Link></> : undefined}>
                <Async query={tx} what="Die Transaktionen" isEmpty={(d) => d.items.length === 0}
                  empty={<EmptyState compact title="Noch keine Transaktionen" text="Dokument importieren oder manuell erfassen."><Link className="btn" to="/import"><FileInput size={15} aria-hidden /> Importieren</Link><button className="btn" onClick={() => setEntry(true)}>Manuell erfassen</button></EmptyState>}>
                  {(d) => <TxTable items={d.items} showAccount={false} />}
                </Async>
              </Panel>
              {!isDepot && (
                <Panel title="Abgleich mit Auszug" hint="Kontostände aus importierten Auszügen">
                  {acc.snapshots && acc.snapshots.length > 0 ? (
                    <ul className="legend legend--rows">
                      {[...acc.snapshots].sort((x, y) => y.date.localeCompare(x.date)).map((s) => {
                        const diff = s.computed_balance != null ? Math.round((s.computed_balance - s.balance) * 100) / 100 : null
                        const ok = diff != null && Math.abs(diff) < 0.005
                        return (
                          <li key={s.date} style={{ gridTemplateColumns: 'auto minmax(0,1fr) auto' }}>
                            {ok ? <CheckCircle2 size={16} aria-hidden style={{ color: 'var(--gain)' }} /> : <Scale size={16} aria-hidden style={{ color: 'var(--warn)' }} />}
                            <span><span className="strong">{fmtDate(s.date)}</span><span className="faint xs" style={{ display: 'block' }}>Auszug <Money value={s.balance} />{s.computed_balance != null && <> · Journal <Money value={s.computed_balance} /></>}</span></span>
                            {diff == null ? <Badge>nur Auszug</Badge> : ok ? <Badge tone="gain">stimmt</Badge> : <Badge tone="warn" title="Differenz Journal − Auszug">Differenz <Money value={diff} signed /></Badge>}
                          </li>
                        )
                      })}
                    </ul>
                  ) : (
                    <EmptyState compact icon={<Scale size={22} aria-hidden />} title="Noch kein Abgleich möglich" text="Beim Import eines Kontoauszugs wird der ausgewiesene Kontostand gespeichert und hier mit dem Journal verglichen.">
                      <Link className="btn" to="/import">Kontoauszug importieren</Link>
                    </EmptyState>
                  )}
                </Panel>
              )}
            </div>
            {entry && <Drawer title="Transaktion erfassen" onClose={() => setEntry(false)}><TxEntry defaults={{ account_id: acc.id, type: isDepot ? 'buy' : 'withdrawal' }} onDone={() => setEntry(false)} /></Drawer>}
          </>
        )}
      </Async>
    </div>
  )
}
