import { useEffect, useState, type FormEvent } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { AlertTriangle, Download, Loader2, Pencil, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { api } from '../api/client'
import { cleanError, summarizeRefresh, useAccounts, useApiMutation, useLlmStatus, useMarketRefresh, useMarketStatus, useSettings } from '../api/hooks'
import type { Account, AccountInput, AccountType, MarketJobLog, RefreshScope } from '../api/types'
import { CategoryManager } from '../components/CategoryManager'
import { useToast } from '../components/Toast'
import { accountColor } from '../components/shared'
import { Async, Badge, ConfirmButton, Drawer, EmptyState, Field, Money, Panel, Seg } from '../components/ui'
import { fmtDate, fmtDateTime, fmtRelative, formatIban, parseDecimal } from '../lib/format'
import { ACCOUNT_TYPE, INSTITUTION, MARKET_JOB, institutionLabel } from '../lib/labels'
import { usePrefs, type ThemeChoice } from '../lib/prefs'

const COLORS = ['#14607a', '#bd861b', '#b4553d', '#5b8a58', '#6c8db0', '#93536a', '#7b783a', '#79838a']
const jobLabel = (scope: string) => MARKET_JOB[scope] ?? scope
const dec = (n: number | null | undefined) => (n == null ? '' : n.toFixed(2).replace('.', ','))

function AccountForm({ edit, accounts, onDone }: { edit?: Account; accounts: Account[]; onDone: () => void }) {
  const toast = useToast()
  const [name, setName] = useState(edit?.name ?? '')
  const [institution, setInstitution] = useState(edit?.institution ?? '')
  const [type, setType] = useState<AccountType>(edit?.type ?? 'checking')
  const [iban, setIban] = useState(edit?.iban ?? '')
  const [color, setColor] = useState(edit?.color ?? COLORS[accounts.length % COLORS.length])
  const [rate, setRate] = useState(edit?.interest_rate != null ? String(edit.interest_rate).replace('.', ',') : '')
  const [opening, setOpening] = useState(edit ? dec(edit.opening_balance) : '')
  const [openingDate, setOpeningDate] = useState(edit?.opening_date ?? '')
  const [settlement, setSettlement] = useState(String(edit?.settlement_account_id ?? ''))
  const [active, setActive] = useState(edit?.is_active ?? true)
  const [error, setError] = useState<string | null>(null)
  const save = useApiMutation((b: AccountInput) => (edit ? api.updateAccount(edit.id, b) : api.createAccount(b)))

  function submit(e: FormEvent) {
    e.preventDefault()
    if (!name.trim()) { setError('Bitte einen Namen angeben.'); return }
    const body: AccountInput = {
      name: name.trim(), institution: institution.trim() || 'other', type, iban: iban.replace(/\s/g, '') || null, color, is_active: active,
      interest_rate: type === 'savings' ? parseDecimal(rate) : null, settlement_account_id: type === 'brokerage' && settlement ? Number(settlement) : null,
    }
    if (type !== 'brokerage') {
      body.opening_balance = parseDecimal(opening) ?? 0
      body.opening_date = openingDate || null
    }
    save.mutate(body, { onSuccess: () => { toast.ok(edit ? 'Konto gespeichert' : 'Konto angelegt'); onDone() }, onError: (er) => toast.error(er, 'Speichern fehlgeschlagen:') })
  }
  return (
    <form className="form-grid" onSubmit={submit} noValidate>
      <Field label="Name" className="span-all" error={error}><input className="input" value={name} onChange={(e) => { setName(e.target.value); setError(null) }} placeholder="z. B. ING Extra-Konto" aria-invalid={!!error} /></Field>
      <Field label="Art">
        <select className="select" value={type} onChange={(e) => setType(e.target.value as AccountType)}>{(Object.keys(ACCOUNT_TYPE) as AccountType[]).map((k) => <option key={k} value={k}>{ACCOUNT_TYPE[k]}</option>)}</select>
      </Field>
      <Field label="Institut" hint="Steuert, welcher Parser Belege liest.">
        <input className="input" list="institutions" value={institution} onChange={(e) => setInstitution(e.target.value)} placeholder="ing" />
        <datalist id="institutions">{Object.entries(INSTITUTION).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</datalist>
      </Field>
      {type !== 'brokerage' && <Field label="IBAN" className="span-all"><input className="input" value={iban} onChange={(e) => setIban(e.target.value)} placeholder="DE00 0000 0000 0000 0000 00" spellCheck={false} /></Field>}
      {type === 'savings' && <Field label="Zinssatz p. a. (%)"><input className="input num" inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} placeholder="2,00" /></Field>}
      {type === 'brokerage' ? (
        <Field label="Verrechnungskonto" className="span-all" hint="Käufe, Verkäufe und Dividenden werden hier gebucht.">
          <select className="select" value={settlement} onChange={(e) => setSettlement(e.target.value)}>
            <option value="">Keines</option>
            {accounts.filter((a) => a.type !== 'brokerage' && a.id !== edit?.id).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </Field>
      ) : (
        <>
          <Field label="Eröffnungssaldo (€)" hint="Saldo vor der ältesten erfassten Buchung."><input className="input num" inputMode="decimal" value={opening} onChange={(e) => setOpening(e.target.value)} placeholder="0,00" /></Field>
          <Field label="Stichtag" hint="Ab diesem Tag zählt der Saldo im Verlauf."><input className="input" type="date" value={openingDate} max="2100-12-31" onChange={(e) => setOpeningDate(e.target.value)} /></Field>
        </>
      )}
      <div className="field span-all">
        <span className="field__label" id="color-label">Farbe</span>
        <div className="row" role="radiogroup" aria-labelledby="color-label">
          {COLORS.map((c) => (
            <button key={c} type="button" role="radio" aria-checked={color === c} aria-label={`Farbe ${c}`} onClick={() => setColor(c)}
              style={{ width: 26, height: 26, borderRadius: 6, background: c, border: 0, cursor: 'pointer', outline: color === c ? '2px solid var(--text)' : 'none', outlineOffset: 2 }} />
          ))}
        </div>
      </div>
      {edit && <label className="check span-all"><input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /> Konto ist aktiv (inaktive Konten zählen nicht zum Vermögen)</label>}
      <div className="form-actions span-all">
        <button className="btn btn--primary" type="submit" disabled={save.isPending}>{save.isPending ? 'Speichert …' : edit ? 'Konto speichern' : 'Konto anlegen'}</button>
        <button className="btn" type="button" onClick={onDone}>Abbrechen</button>
      </div>
    </form>
  )
}

/** Opening balance + date for all cash accounts in one table. Either the opening balance itself or today's balance can be entered. */
function OpeningBalances({ accounts }: { accounts: Account[] }) {
  const toast = useToast()
  const cash = accounts.filter((a) => a.is_active && a.type !== 'brokerage')
  const [mode, setMode] = useState<'opening' | 'current'>('opening')
  const [rows, setRows] = useState<Record<number, { value?: string; date?: string }>>({})
  const [saving, setSaving] = useState(false)
  const save = useApiMutation((v: { id: number; body: Partial<AccountInput> }) => api.updateAccount(v.id, v.body))
  const shown = (a: Account) => rows[a.id]?.value ?? dec(mode === 'opening' ? a.opening_balance : a.balance)
  const date = (a: Account) => rows[a.id]?.date ?? a.opening_date ?? ''
  const changed = cash.filter((a) => rows[a.id] && (shown(a) !== dec(mode === 'opening' ? a.opening_balance : a.balance) || date(a) !== (a.opening_date ?? '')))
  const invalid = changed.filter((a) => shown(a).trim() !== '' && parseDecimal(shown(a)) == null)
  const set = (id: number, patch: { value?: string; date?: string }) => setRows((r) => ({ ...r, [id]: { ...r[id], ...patch } }))

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!changed.length || invalid.length) return
    setSaving(true)
    try {
      for (const a of changed) {
        const entered = parseDecimal(shown(a)) ?? 0
        // "today's balance" mode: shift the opening balance by the difference to the ledger balance
        const opening = mode === 'opening' ? entered : Math.round((a.opening_balance + (entered - a.balance)) * 100) / 100
        await save.mutateAsync({ id: a.id, body: { opening_balance: opening, opening_date: date(a) || null } })
      }
      setRows({})
      toast.ok(changed.length === 1 ? `Eröffnungssaldo für ${changed[0].name} gespeichert` : `Eröffnungssalden für ${changed.length} Konten gespeichert`)
    } catch (er) { toast.error(er, 'Speichern fehlgeschlagen:') } finally { setSaving(false) }
  }
  if (!cash.length) return null
  return (
    <Panel title="Eröffnungssalden" flush hint="Giro- und Tagesgeldkonten"
      actions={<Seg label="Eingabe als" value={mode} onChange={(m) => { setMode(m); setRows({}) }} options={[{ value: 'opening', label: 'Eröffnungssaldo' }, { value: 'current', label: 'Heutiger Kontostand' }]} />}>
      <form onSubmit={submit}>
        <p className="small muted" style={{ padding: '0 16px 12px', maxWidth: '75ch' }}>
          {mode === 'opening'
            ? 'Der Eröffnungssaldo ist der Kontostand vor der ältesten erfassten Buchung. Alle Buchungen im Journal werden hinzugerechnet; der Stichtag legt fest, ab wann der Saldo im Verlauf zählt.'
            : 'Den Kontostand von heute eintragen (z. B. aus der Banking-App) – der Eröffnungssaldo wird so angepasst, dass das Journal genau diesen Stand ergibt. Werden später ältere Auszüge importiert, bitte erneut abgleichen.'}
        </p>
        <div className="table-wrap">
          <table className="table table--stack-sm">
            <thead><tr><th>Konto</th><th className="num">{mode === 'opening' ? 'Eröffnungssaldo (€)' : 'Kontostand heute (€)'}</th><th>Stichtag</th><th className="num hide-sm">{mode === 'opening' ? 'Saldo laut Journal' : 'Eröffnungssaldo bisher'}</th></tr></thead>
            <tbody>
              {cash.map((a) => (
                <tr key={a.id}>
                  <td className="cell-name"><span className="name truncate">{a.name}</span><span className="meta">{ACCOUNT_TYPE[a.type]}</span></td>
                  <td className="num"><input className="input num" style={{ width: 130, marginLeft: 'auto' }} inputMode="decimal" aria-label={`${mode === 'opening' ? 'Eröffnungssaldo' : 'Kontostand heute'} ${a.name}`} aria-invalid={invalid.includes(a)} value={shown(a)} onChange={(e) => set(a.id, { value: e.target.value })} placeholder="0,00" /></td>
                  <td><input className="input" style={{ width: 150 }} type="date" max="2100-12-31" aria-label={`Stichtag ${a.name}`} value={date(a)} onChange={(e) => set(a.id, { date: e.target.value })} /></td>
                  <td className="num hide-sm muted"><Money value={mode === 'opening' ? a.balance : a.opening_balance} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="form-actions" style={{ padding: '12px 16px' }}>
          <button className="btn btn--primary" type="submit" disabled={!changed.length || invalid.length > 0 || saving}>{saving ? 'Speichert …' : changed.length > 1 ? `${changed.length} Konten speichern` : 'Speichern'}</button>
          {changed.length > 0 && <button className="btn" type="button" onClick={() => setRows({})}>Verwerfen</button>}
          {invalid.length > 0 && <span className="field__error" role="alert">Bitte Beträge wie 1.234,56 eingeben.</span>}
          <span className="small muted">Depotbestände kommen über Käufe oder Einlieferungen ins Depot – <Link to="/transaktionen?neu=delivery_in">Einlieferung erfassen</Link> oder Abrechnungen <Link to="/import">importieren</Link>.</span>
        </div>
      </form>
    </Panel>
  )
}

/** Sparerpauschbetrag for the tax overview: the two legal amounts as a switch, anything else as a number. */
function TaxSettings() {
  const settings = useSettings()
  const toast = useToast()
  const save = useApiMutation((v: number) => api.updateSettings({ sparerpauschbetrag: v }), [['settings'], ['analytics', 'taxes']])
  const current = settings.data?.sparerpauschbetrag
  const [custom, setCustom] = useState('')
  const put = (v: number) => save.mutate(v, { onSuccess: () => { toast.ok(`Sparerpauschbetrag: ${v.toLocaleString('de-DE')} €`); setCustom('') }, onError: (e) => toast.error(e, 'Speichern fehlgeschlagen:') })
  const preset = current === 1000 ? '1000' : current === 2000 ? '2000' : 'custom'
  const parsed = parseDecimal(custom)
  return (
    <Panel title="Steuern">
      <Async query={settings} what="Die Einstellungen" compact>
        {() => (
          <div className="stack stack--sm">
            <div className="field">
              <span className="field__label">Sparerpauschbetrag</span>
              <Seg label="Sparerpauschbetrag" value={preset} onChange={(v) => { if (v !== 'custom') put(Number(v)) }}
                options={[{ value: '1000', label: '1.000 € (allein)' }, { value: '2000', label: '2.000 € (gemeinsam veranlagt)' }, ...(preset === 'custom' ? [{ value: 'custom', label: `${(current ?? 0).toLocaleString('de-DE')} € (eigener Wert)` }] : [])]} />
              <span className="field__hint">So viel Kapitalertrag bleibt pro Jahr steuerfrei. Die <Link to="/steuern">Steuerübersicht</Link> rechnet mit diesem Betrag.</span>
            </div>
            <form className="row" onSubmit={(e) => { e.preventDefault(); if (parsed != null && parsed >= 0) put(Math.round(parsed * 100) / 100) }}>
              <label className="sr-only" htmlFor="allowance-custom">Anderer Betrag in Euro</label>
              <input id="allowance-custom" className="input num" style={{ width: 140 }} inputMode="decimal" value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="anderer Betrag" />
              <button className="btn" type="submit" disabled={save.isPending || parsed == null || parsed < 0}>Übernehmen</button>
              <span className="xs muted">z. B. wenn nur ein Teil per Freistellungsauftrag verteilt ist</span>
            </form>
          </div>
        )}
      </Async>
    </Panel>
  )
}

/** One line of the job log: "3 von 8 aktualisiert" + the errors behind a disclosure instead of a wall of text. */
function JobRow({ j }: { j: MarketJobLog }) {
  const detail = j.detail ?? ''
  const processed = /processed=(\d+)/.exec(detail)?.[1]
  const total = /total=(\d+)/.exec(detail)?.[1]
  const errors = detail.split(/;\s*(?=\[)/).filter((x) => x.startsWith('[')).map((x) => x.replace(/^\[-\]\s*/, '').replace(/^\[([^\]]+)\]\s*/, '$1: ')).map((x) => { const m = /^([^:]+): (.*)$/.exec(x); return m && !/Error|abgebrochen/.test(m[1]) ? `${m[1]}: ${cleanError(m[2])}` : cleanError(x) ?? x })
  const ok = j.status === 'ok'
  return (
    <li style={{ gridTemplateColumns: 'minmax(0,1fr) auto', alignItems: 'start' }}>
      <span style={{ minWidth: 0 }}>
        <span className="strong">{jobLabel(j.job)}</span>
        <span className="faint xs"> · {fmtRelative(j.created_at)}{processed != null && total != null ? ` · ${processed} von ${total} verarbeitet` : ''}</span>
        {errors.length > 0 && (
          <details className="job-errors">
            <summary>{errors.length === 1 ? errors[0] : `${errors.length} Meldungen – ${errors[0]}`}</summary>
            {errors.length > 1 && <ul>{errors.map((e, i) => <li key={i}>{e}</li>)}</ul>}
          </details>
        )}
      </span>
      {ok ? <Badge tone="gain">ok</Badge> : j.status === 'partial' ? <Badge tone="warn">teilweise</Badge> : <Badge tone="loss">Fehler</Badge>}
    </li>
  )
}

export function Settings() {
  const accounts = useAccounts()
  const llm = useLlmStatus()
  const market = useMarketStatus()
  const toast = useToast()
  const loc = useLocation()
  const { theme, setTheme, privacy, togglePrivacy } = usePrefs()
  const [form, setForm] = useState<{ edit?: Account } | null>(null)
  const [fullBackup, setFullBackup] = useState(false)
  const remove = useApiMutation((id: number) => api.deleteAccount(id))
  const refresh = useMarketRefresh()

  // scroll to #konten / #eroeffnung / #marktdaten … once the sections exist
  const ready = !!accounts.data
  useEffect(() => { if (loc.hash && ready) document.getElementById(loc.hash.slice(1))?.scrollIntoView() }, [loc.hash, ready])

  const runRefresh = (scope: RefreshScope) => refresh.start(scope, { onError: (e) => toast.error(e, 'Aktualisierung konnte nicht gestartet werden:') })
  const lastRun = refresh.last ? summarizeRefresh(refresh.last, jobLabel) : null
  const m = market.data
  const marketOnline = m ? m.online !== false : false

  return (
    <div className="page">
      <div className="page-head"><div><h1>Einstellungen</h1><p className="page-head__sub">Konten, Kategorien, lokales Modell, Marktdaten und Steuern.</p></div></div>

      <section id="konten">
        <Panel title="Konten" flush actions={<button className="btn btn--sm" onClick={() => setForm({})}><Plus size={14} aria-hidden /> Konto anlegen</button>}>
          <Async query={accounts} what="Die Konten" empty={<EmptyState compact title="Noch keine Konten" text="Ohne Konto lässt sich nichts verbuchen."><button className="btn btn--primary" onClick={() => setForm({})}>Erstes Konto anlegen</button></EmptyState>}>
            {(list) => (
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>Konto</th><th className="hide-sm">Art</th><th className="hide-md">IBAN / Verrechnung</th><th className="num">Wert</th><th style={{ width: 84 }}><span className="sr-only">Aktionen</span></th></tr></thead>
                  <tbody>
                    {list.map((a, i) => (
                      <tr key={a.id}>
                        <td className="cell-name"><span className="name row" style={{ flexWrap: 'nowrap' }}><span className="acc-dot" style={{ backgroundColor: accountColor(a, i) }} /><span className="truncate">{a.name}</span>{!a.is_active && <Badge>inaktiv</Badge>}</span><span className="meta" style={{ paddingLeft: 18 }}>{institutionLabel(a.institution)}<span className="show-sm"> · {ACCOUNT_TYPE[a.type]}</span></span></td>
                        <td className="hide-sm muted">{ACCOUNT_TYPE[a.type]}{a.interest_rate != null && <span className="meta">{String(a.interest_rate).replace('.', ',')} % p. a.</span>}</td>
                        <td className="hide-md muted nowrap">{a.type === 'brokerage' ? list.find((x) => x.id === a.settlement_account_id)?.name ?? '–' : formatIban(a.iban)}</td>
                        <td className="num"><Money value={a.total_value} /></td>
                        <td className="num nowrap">
                          <button className="btn btn--ghost btn--sm btn--icon" onClick={() => setForm({ edit: a })} aria-label={`${a.name} bearbeiten`} title="Bearbeiten"><Pencil size={15} aria-hidden /></button>
                          <ConfirmButton label="" icon={<Trash2 size={15} aria-hidden />} confirmLabel="Konto löschen?" onConfirm={() => remove.mutate(a.id, { onSuccess: (r) => toast.ok(r?.deactivated ? `${a.name} hat ${r.transaction_count} Buchungen und wurde deshalb nur deaktiviert` : `${a.name} gelöscht`), onError: (e) => toast.error(e) })} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Async>
        </Panel>
      </section>

      <section id="eroeffnung">{accounts.data && <OpeningBalances accounts={accounts.data} />}</section>

      <section id="kategorien"><CategoryManager title="Kategorien" collapsible /></section>

      <div className="grid cols-2">
        <section id="modell">
          <Panel title="Lokales Modell (Ollama)" actions={<button className="btn btn--sm" onClick={() => void llm.refetch()} disabled={llm.isFetching}><RefreshCw size={14} aria-hidden className={llm.isFetching ? 'spin' : undefined} /> Prüfen</button>}>
            {llm.isPending ? <div className="skeleton" style={{ height: 120 }} /> : (
              <div className="stack stack--sm">
                <p className="row"><i className={`dot ${llm.data?.available ? 'dot--ok' : 'dot--off'}`} style={llm.data?.available ? undefined : { boxShadow: 'inset 0 0 0 2px var(--warn)' }} />
                  <strong>{llm.isError ? 'Status nicht abrufbar' : llm.data?.available ? 'Ollama ist erreichbar' : 'Ollama ist nicht erreichbar'}</strong></p>
                <dl className="facts facts--rows">
                  <div><dt>Textmodell</dt><dd>{llm.data?.text_model ?? '–'} {llm.data?.text_model && (llm.data.models.includes(llm.data.text_model) ? <Badge tone="gain">installiert</Badge> : <Badge tone="warn">fehlt</Badge>)}</dd></div>
                  <div><dt>Bildmodell</dt><dd>{llm.data?.vision_model ?? '–'} {llm.data?.vision_model && (llm.data.models.includes(llm.data.vision_model) ? <Badge tone="gain">installiert</Badge> : <Badge tone="warn">fehlt</Badge>)}</dd></div>
                  <div><dt>Installierte Modelle</dt><dd>{llm.data?.models.length ?? 0}</dd></div>
                </dl>
                {llm.data?.ocr_available != null && <p className="small muted">Texterkennung (Tesseract): {llm.data.ocr_available ? 'installiert' : 'nicht installiert (optional)'}</p>}
                {!llm.data?.available && <p className="small muted">{llm.data?.url ? <>Erwartet unter <code>{llm.data.url}</code>. </> : null}Ollama mit <code>ollama serve</code> starten und die Modelle mit <code>ollama pull {llm.data?.text_model ?? 'qwen2.5:7b-instruct'}</code>{llm.data?.vision_model ? <> und <code>ollama pull {llm.data.vision_model}</code></> : null} laden. Bekannte PDF- und CSV-Formate funktionieren auch ohne Modell.</p>}
                {llm.data?.available && (llm.data.text_model_available === false || llm.data.vision_model_available === false) && <p className="small" style={{ color: 'var(--warn)' }}>Ollama läuft, aber ein Modell fehlt: <code>ollama pull {llm.data.text_model_available === false ? llm.data.text_model : llm.data.vision_model}</code></p>}
              </div>
            )}
          </Panel>
        </section>
        <section id="marktdaten">
          <Panel title="Marktdaten" actions={<button className="btn btn--sm btn--primary" disabled={refresh.running} onClick={() => runRefresh('all')}><RefreshCw size={14} aria-hidden className={refresh.running ? 'spin' : undefined} /> {refresh.running ? 'Läuft …' : 'Alles aktualisieren'}</button>}>
            <Async query={market} what="Der Marktdaten-Status" compact>
              {(st) => (
                <div className="stack stack--sm">
                  <p className="row"><i className={`dot ${marketOnline ? 'dot--ok' : 'dot--off'}`} style={marketOnline ? undefined : { boxShadow: 'inset 0 0 0 2px var(--warn)' }} />
                    <strong>{st.online == null ? 'Noch kein Abruf' : marketOnline ? 'Yahoo Finance erreichbar' : 'Yahoo Finance nicht erreichbar'}</strong>
                    <span className="muted small">· {st.last_quote_update ? `Kurse ${fmtRelative(st.last_quote_update)} (${fmtDateTime(st.last_quote_update)})` : 'noch keine Kurse abgerufen'}{st.fx_latest_date ? ` · Wechselkurse vom ${fmtDate(st.fx_latest_date)}` : ''}</span></p>
                  {refresh.running ? (
                    <div className="notice" role="status" aria-live="polite">
                      <Loader2 size={17} aria-hidden className="spin" />
                      <div><strong>Aktualisierung läuft …</strong> {refresh.current ? <>{jobLabel(refresh.current.scope)}{refresh.current.progress && refresh.current.progress.total > 1 ? ` · Schritt ${Math.min(refresh.current.progress.done + 1, refresh.current.progress.total)} von ${refresh.current.progress.total}` : ''}{refresh.current.progress?.current ? ` (${jobLabel(refresh.current.progress.current)})` : ''}</> : 'wird gestartet'}. Ohne Internet dauert das bis zu zwei Minuten; die Seite kann währenddessen verlassen werden.</div>
                    </div>
                  ) : lastRun && !lastRun.ok ? (
                    <div className="notice notice--warn" role="status">
                      <AlertTriangle size={17} aria-hidden />
                      <div><strong>{lastRun.text}</strong>{lastRun.failed[0]?.error ? <> Grund: {lastRun.failed[0].error}.</> : null} Beendet {fmtRelative(refresh.last?.finished_at)}.</div>
                    </div>
                  ) : lastRun ? (
                    <p className="small pos">{lastRun.text} ({fmtRelative(refresh.last?.finished_at)}).</p>
                  ) : !marketOnline && st.online === false ? (
                    <p className="small muted">Die App rechnet mit den zuletzt gespeicherten Kursen. Sobald wieder eine Verbindung besteht, holt der Hintergrunddienst die Daten nach.</p>
                  ) : null}
                  <div className="row" role="group" aria-label="Einzelne Marktdaten aktualisieren">
                    {(['quotes', 'events', 'history', 'etf', 'fundamentals', 'fx'] as RefreshScope[]).map((sc) => <button key={sc} className="btn btn--sm" disabled={refresh.running} onClick={() => runRefresh(sc)}>{jobLabel(sc)}</button>)}
                  </div>
                  {st.jobs && st.jobs.length > 0 ? (
                    <ul className="legend legend--rows">{st.jobs.slice(0, 8).map((j) => <JobRow key={j.job} j={j} />)}</ul>
                  ) : <p className="small muted">Noch kein Abruf protokolliert. Kurse werden geladen, sobald Wertpapiere im Depot oder auf der Watchlist stehen{st.scheduler?.enabled === false ? ' (der automatische Abruf ist abgeschaltet: ENABLE_SCHEDULER=false)' : ''}.</p>}
                  {st.counts && <p className="xs faint">{st.counts.tracked_instruments ?? 0} Wertpapiere beobachtet · {st.counts.quotes ?? 0} mit Kurs · {st.counts.events ?? 0} Termine · {st.counts.etfs_with_holdings ?? 0} ETFs mit Zusammensetzung{st.counts.without_symbol ? ` · ${st.counts.without_symbol} ohne Yahoo-Symbol` : ''}</p>}
                </div>
              )}
            </Async>
          </Panel>
        </section>
      </div>

      <section id="steuern"><TaxSettings /></section>

      <div className="grid cols-2">
        <Panel title="Darstellung">
          <div className="stack stack--sm">
            <div className="field"><span className="field__label">Farbschema</span><Seg<ThemeChoice> label="Farbschema" value={theme} onChange={setTheme} options={[{ value: 'system', label: 'Wie System' }, { value: 'light', label: 'Hell' }, { value: 'dark', label: 'Dunkel' }]} /></div>
            <label className="check"><input type="checkbox" checked={privacy} onChange={togglePrivacy} /> Privatmodus: Beträge weichzeichnen</label>
          </div>
        </Panel>
        <section id="export">
          <Panel title="Datenexport und Backup">
            <div className="stack stack--sm">
              <p className="small muted">Beide Dateien erzeugt das Backend direkt aus der Datenbank; nichts verlässt den Rechner.</p>
              <div className="row">
                <a className="btn" href="/api/export/transactions.csv" download><Download size={15} aria-hidden /> Transaktionen als CSV</a>
                <a className="btn" href={`/api/export/backup.json${fullBackup ? '?include_market_history=true' : ''}`} download><Download size={15} aria-hidden /> Backup als JSON</a>
              </div>
              <label className="check"><input type="checkbox" checked={fullBackup} onChange={(e) => setFullBackup(e.target.checked)} /> Kurshistorie ins JSON-Backup aufnehmen (groß, lässt sich neu laden)</label>
              <p className="xs muted">CSV: Semikolon und Dezimalkomma, öffnet direkt in Excel/LibreOffice. Das JSON enthält alle Tabellen zum Nachschlagen oder Weiterverarbeiten. Für ein vollständig wiederherstellbares Backup <code>make backup</code> (pg_dump) verwenden und den Ordner <code>data/uploads</code> mitsichern.</p>
            </div>
          </Panel>
        </section>
      </div>
      {form && accounts.data && <Drawer title={form.edit ? `${form.edit.name} bearbeiten` : 'Konto anlegen'} onClose={() => setForm(null)}><AccountForm edit={form.edit} accounts={accounts.data} onDone={() => setForm(null)} /></Drawer>}
    </div>
  )
}
