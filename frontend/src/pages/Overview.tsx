import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { CalendarDays, FileInput, Inbox, Plus, Sprout } from 'lucide-react'
import { useAccounts, useAllocation, useDocuments, useEvents, useHistory, useSummary } from '../api/hooks'
import type { Range } from '../api/types'
import { Chart, useChartTheme } from '../components/Chart'
import { AllocationList, EventList, RANGES, accountColor } from '../components/shared'
import { Async, Badge, Delta, EmptyState, Legend, Money, Panel, Seg, Skeleton, SplitBar } from '../components/ui'
import { timeOption } from '../lib/charts'
import { addDays, fmtDateTime, fmtRelative, todayIso } from '../lib/format'
import { ACCOUNT_TYPE, DOC_STATUS, docTypeLabel } from '../lib/labels'

export function Overview() {
  const summary = useSummary()
  const accounts = useAccounts()
  const [range, setRange] = useState<Range>('1Y')
  const history = useHistory(range)
  const allocation = useAllocation('type', false)
  const today = todayIso()
  const events = useEvents({ from: today, to: addDays(today, 45) })
  const docs = useDocuments()
  const t = useChartTheme()

  const historyOption = useMemo(() => {
    const h = history.data ?? []
    return timeOption(t, {
      dates: h.map((p) => p.date),
      series: [
        { name: 'Nettovermögen', data: h.map((p) => p.net_worth), color: t.c[0], area: true },
        { name: 'Depotwert', data: h.map((p) => p.invested), color: t.c[1] },
        { name: 'Einstand Depot', data: h.map((p) => p.cost_basis), color: t.c[1], dashed: true, width: 1.5 },
      ],
    })
  }, [history.data, t])

  const allocWithCash = useMemo(() => {
    const cash = summary.data?.cash_total ?? 0
    const slices = [...(allocation.data ?? []), ...(cash > 0 ? [{ key: 'cash', value: cash, weight: 0 }] : [])]
    const total = slices.reduce((s, x) => s + x.value, 0)
    return slices.map((s) => ({ ...s, weight: total ? (s.value / total) * 100 : 0 }))
  }, [allocation.data, summary.data])
  const active = (accounts.data ?? []).filter((a) => a.is_active)
  const fresh = summary.data && accounts.data && summary.data.net_worth === 0 && summary.data.cost_basis === 0 && active.every((a) => a.total_value === 0)
  const openDocs = (docs.data ?? []).filter((d) => d.status === 'review' || d.status === 'processing' || d.status === 'uploaded' || d.status === 'failed').slice(0, 5)

  return (
    <div className="page">
      {fresh && (
        <Panel title="Willkommen – so kommt Leben in die Zahlen">
          <div className="stack stack--sm">
            <p className="prose">Die sieben Konten sind angelegt, aber noch leer. Am schnellsten geht es mit Belegen: Kontoauszüge, Wertpapierabrechnungen oder Screenshots importieren, prüfen, übernehmen.</p>
            <div className="row">
              <Link className="btn btn--primary" to="/import"><FileInput size={15} aria-hidden /> Beleg importieren</Link>
              <Link className="btn" to="/transaktionen?neu=1"><Plus size={15} aria-hidden /> Transaktion manuell erfassen</Link>
              <Link className="btn btn--ghost" to="/einstellungen">Anfangssalden eintragen</Link>
            </div>
          </div>
        </Panel>
      )}

      <section className="stack stack--sm" aria-label="Nettovermögen">
        <Async query={summary} what="Die Vermögensübersicht" skeleton={<div className="stack stack--sm"><Skeleton h={14} w={120} /><Skeleton h={38} w={260} /><Skeleton h={10} /></div>}>
          {(s) => (
            <>
              <div className="lead">
                <h1 className="lead__label">Nettovermögen</h1>
                <div className="lead__row">
                  <Money className="lead__value" value={s.net_worth} />
                  <span className="row" style={{ gap: 6 }}><Delta pct={s.day_change_pct} amount={s.day_change} /><span className="faint small">heute</span></span>
                </div>
              </div>
              {active.length > 0 && s.net_worth > 0 && (
                <>
                  <SplitBar label="Zusammensetzung nach Konto" parts={active.map((a, i) => ({ label: a.name, value: a.total_value, color: accountColor(a, i) }))} />
                  <Legend items={active.map((a, i) => ({ label: a.name, color: accountColor(a, i), value: a.total_value })).filter((x) => x.value > 0)} />
                </>
              )}
              <div className="kpis">
                <div className="kpi"><span className="kpi__label">Liquidität</span><span className="kpi__value"><Money value={s.cash_total} decimals={0} /></span><span className="kpi__sub">Giro + Tagesgeld</span></div>
                <div className="kpi"><span className="kpi__label">Depotwert</span><span className="kpi__value"><Money value={s.invested_value} decimals={0} /></span><span className="kpi__sub">Einstand <Money value={s.cost_basis} decimals={0} /></span></div>
                <div className="kpi"><span className="kpi__label">Buchgewinn</span><span className="kpi__value"><Money value={s.unrealized_gain} decimals={0} tone /></span><span className="kpi__sub"><Delta pct={s.unrealized_gain_pct} decimals={1} /></span></div>
                <div className="kpi"><span className="kpi__label">Dividenden {new Date().getFullYear()}</span><span className="kpi__value"><Money value={s.dividends_ytd} decimals={0} /></span><span className="kpi__sub">netto erhalten</span></div>
                <div className="kpi"><span className="kpi__label">Zinsen {new Date().getFullYear()}</span><span className="kpi__value"><Money value={s.interest_ytd} decimals={0} /></span><span className="kpi__sub">Tagesgeld</span></div>
                <div className="kpi"><span className="kpi__label">Realisiert {new Date().getFullYear()}</span><span className="kpi__value"><Money value={s.realized_gain_ytd} decimals={0} tone /></span><span className="kpi__sub">aus Verkäufen</span></div>
              </div>
            </>
          )}
        </Async>
      </section>

      <div className="grid cols-main-side">
        <Panel title="Vermögensverlauf" actions={<Seg label="Zeitraum" value={range} onChange={setRange} options={RANGES} />}>
          <Async query={history} what="Der Verlauf" skeleton={<Skeleton h={280} />} isEmpty={(d) => d.length < 2}
            empty={<EmptyState compact icon={<Sprout size={22} aria-hidden />} title="Noch kein Verlauf" text="Sobald Transaktionen vorhanden sind, entsteht hier der Verlauf des Nettovermögens." />}>
            {() => (
              <div className="stack stack--sm">
                <Chart option={historyOption} height={280} label="Verlauf von Nettovermögen, Depotwert und Einstand" />
                <Legend items={[{ label: 'Nettovermögen', color: 'var(--c1)', kind: 'line' }, { label: 'Depotwert', color: 'var(--c2)', kind: 'line' }, { label: 'Einstand Depot', color: 'var(--c2)', kind: 'dash' }]} />
              </div>
            )}
          </Async>
        </Panel>
        <Panel title="Allokation" actions={<Link className="small" to="/depot">Details</Link>}>
          <Async query={allocation} what="Die Allokation" compact
            isEmpty={() => allocWithCash.length === 0} empty={<EmptyState compact title="Noch nichts zu verteilen" text="Mit den ersten Buchungen erscheint hier die Aufteilung in Liquidität, ETFs und Aktien." />}>
            {() => <AllocationList slices={allocWithCash} label="Vermögen nach Anlageklasse" />}
          </Async>
        </Panel>
      </div>

      <div className="grid cols-3">
        <Panel title="Konten" flush actions={<Link className="small" to="/konten">Alle Konten</Link>}>
          <Async query={accounts} what="Die Konten" empty={<EmptyState compact title="Keine Konten" text="Konten lassen sich in den Einstellungen anlegen."><Link className="btn" to="/einstellungen">Konto anlegen</Link></EmptyState>}>
            {(list) => (
              <div className="list">
                {list.filter((a) => a.is_active).map((a, i) => (
                  <Link key={a.id} to={`/konten/${a.id}`}>
                    <span className="acc-dot" style={{ backgroundColor: accountColor(a, i) }} />
                    <span style={{ minWidth: 0 }}><span className="title truncate">{a.name}</span><span className="meta">{ACCOUNT_TYPE[a.type]}{a.interest_rate ? ` · ${String(a.interest_rate).replace('.', ',')} % p. a.` : ''}</span></span>
                    <span className="end"><Money value={a.total_value} /></span>
                  </Link>
                ))}
              </div>
            )}
          </Async>
        </Panel>
        <Panel title="Nächste Termine" flush actions={<Link className="small" to="/kalender">Kalender</Link>}>
          <Async query={events} what="Die Termine" compact
            empty={<EmptyState compact icon={<CalendarDays size={22} aria-hidden />} title="Keine Termine in den nächsten 45 Tagen" text="Termine erscheinen für gehaltene Wertpapiere und die Watchlist." />}>
            {(list) => <EventList events={list.slice(0, 6)} />}
          </Async>
        </Panel>
        <Panel title="Offene Belege" flush actions={<Link className="small" to="/import">Import</Link>}>
          <Async query={docs} what="Die Belege" compact isEmpty={() => openDocs.length === 0}
            empty={<EmptyState compact icon={<Inbox size={22} aria-hidden />} title="Alles verbucht" text="Keine Belege warten auf Prüfung."><Link className="btn" to="/import">Beleg importieren</Link></EmptyState>}>
            {() => (
              <div className="list">
                {openDocs.map((d) => (
                  <Link key={d.id} to={`/import/${d.id}`}>
                    <FileInput size={18} aria-hidden style={{ color: 'var(--text-3)' }} />
                    <span style={{ minWidth: 0 }}><span className="title truncate">{d.filename}</span><span className="meta truncate">{docTypeLabel(d.detected_doc_type)} · {fmtRelative(d.created_at)}</span></span>
                    <span className="end">
                      {d.status === 'review' ? <Badge tone="accent">{d.pending_count} offen</Badge> : <Badge tone={d.status === 'failed' ? 'loss' : undefined}>{DOC_STATUS[d.status]}</Badge>}
                    </span>
                  </Link>
                ))}
              </div>
            )}
          </Async>
        </Panel>
      </div>
      {summary.data?.updated_at && <p className="faint xs">Stand der Kurse: {fmtDateTime(summary.data.updated_at)}</p>}
    </div>
  )
}
