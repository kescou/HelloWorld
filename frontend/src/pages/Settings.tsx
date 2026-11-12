import { useEffect, useState, type FormEvent } from 'react'
import { useLocation } from 'react-router-dom'
import { Download, Pencil, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { api } from '../api/client'
import { useAccounts, useApiMutation, useLlmStatus, useMarketStatus } from '../api/hooks'
import type { Account, AccountInput, AccountType, RefreshScope } from '../api/types'
import { CategoryManager } from '../components/CategoryManager'
import { useToast } from '../components/Toast'
import { accountColor } from '../components/shared'
import { Async, Badge, ConfirmButton, Drawer, EmptyState, Field, Money, Panel, Seg } from '../components/ui'
import { fmtDateTime, fmtRelative, formatIban, parseDecimal, todayIso } from '../lib/format'
import { ACCOUNT_TYPE, INSTITUTION, institutionLabel } from '../lib/labels'
import { usePrefs, type ThemeChoice } from '../lib/prefs'

const COLORS = ['#14607a', '#bd861b', '#b4553d', '#5b8a58', '#6c8db0', '#93536a', '#7b783a', '#79838a']
const JOB_LABEL: Record<string, string> = { quotes: 'Kurse', events: 'Termine', history: 'Kurshistorie', etf: 'ETF-Zusammensetzung', all: 'Alles' }

function AccountForm({ edit, accounts, onDone }: { edit?: Account; accounts: Account[]; onDone: () => void }) {
  const toast = useToast()
  const [name, setName] = useState(edit?.name ?? '')
  const [institution, setInstitution] = useState(edit?.institution ?? '')
  const [type, setType] = useState<AccountType>(edit?.type ?? 'checking')
  const [iban, setIban] = useState(edit?.iban ?? '')
  const [color, setColor] = useState(edit?.color ?? COLORS[accounts.length % COLORS.length])
  const [rate, setRate] = useState(edit?.interest_rate != null ? String(edit.interest_rate).replace('.', ',') : '')
  const [opening, setOpening] = useState('')
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
    const ob = parseDecimal(opening)
    if (ob != null) body.opening_balance = ob
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
        <Field label="Anfangssaldo (€)" hint={edit ? 'Leer lassen, um den bisherigen Wert zu behalten.' : 'Saldo vor der ersten erfassten Buchung.'}><input className="input num" inputMode="decimal" value={opening} onChange={(e) => setOpening(e.target.value)} placeholder="0,00" /></Field>
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

function download(name: string, content: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }))
  const a = document.createElement('a')
  a.href = url; a.download = name
  document.body.appendChild(a); a.click(); a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function Settings() {
  const accounts = useAccounts()
  const llm = useLlmStatus()
  const market = useMarketStatus()
  const toast = useToast()
  const loc = useLocation()
  const { theme, setTheme, privacy, togglePrivacy } = usePrefs()
  const [form, setForm] = useState<{ edit?: Account } | null>(null)
  const [exporting, setExporting] = useState(false)
  const remove = useApiMutation((id: number) => api.deleteAccount(id))
  const refresh = useApiMutation((scope: RefreshScope) => api.refresh(scope), [['market'], ['portfolio'], ['accounts'], ['instruments']])

  useEffect(() => { if (loc.hash) document.getElementById(loc.hash.slice(1))?.scrollIntoView() }, [loc.hash])

  async function exportData(kind: 'csv' | 'json') {
    setExporting(true)
    try {
      const all = []
      for (let offset = 0; ; offset += 500) {
        const page = await api.transactions({ limit: 500, offset })
        all.push(...page.items)
        if (all.length >= page.total || page.items.length === 0) break
      }
      if (kind === 'csv') {
        const cols = ['booking_date', 'account_name', 'type', 'instrument_name', 'instrument_isin', 'quantity', 'price', 'amount', 'fees', 'taxes', 'currency', 'category_name', 'counterparty', 'description'] as const
        const cell = (v: unknown) => { const s = v == null ? '' : typeof v === 'number' ? String(v).replace('.', ',') : String(v); return /[";\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s }
        download(`vermoegen-transaktionen-${todayIso()}.csv`, '﻿' + [cols.join(';'), ...all.map((t) => cols.map((c) => cell(t[c])).join(';'))].join('\n'), 'text/csv;charset=utf-8')
      } else {
        const [accs, cats, instruments] = await Promise.all([api.accounts(), api.categories(), api.instruments()])
        download(`vermoegen-export-${todayIso()}.json`, JSON.stringify({ exported_at: new Date().toISOString(), accounts: accs, categories: cats, instruments, transactions: all }, null, 2), 'application/json')
      }
      toast.ok(`${all.length} Transaktionen exportiert`)
    } catch (e) { toast.error(e, 'Export fehlgeschlagen:') } finally { setExporting(false) }
  }
  const runRefresh = (scope: RefreshScope) => refresh.mutate(scope, { onSuccess: () => toast.ok(`${JOB_LABEL[scope]} aktualisiert`), onError: (e) => toast.error(e, 'Aktualisierung fehlgeschlagen:') })
  const m = market.data
  const marketOk = m ? m.online !== false && !(m.errors?.length) : false

  return (
    <div className="page">
      <div className="page-head"><div><h1>Einstellungen</h1><p className="page-head__sub">Konten, Kategorien, lokales Modell und Marktdaten.</p></div></div>

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
                          <ConfirmButton label="" icon={<Trash2 size={15} aria-hidden />} confirmLabel="Konto löschen?" onConfirm={() => remove.mutate(a.id, { onSuccess: () => toast.ok(`${a.name} gelöscht`), onError: (e) => toast.error(e) })} />
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

      <section id="kategorien"><CategoryManager title="Kategorien" /></section>

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
                {!llm.data?.available && <p className="small muted">Ollama mit <code>ollama serve</code> starten und fehlende Modelle mit <code>ollama pull {llm.data?.text_model ?? 'qwen2.5:7b-instruct'}</code> laden. Bekannte PDF-Formate funktionieren auch ohne Modell.</p>}
              </div>
            )}
          </Panel>
        </section>
        <section id="marktdaten">
          <Panel title="Marktdaten" actions={<button className="btn btn--sm btn--primary" disabled={refresh.isPending} onClick={() => runRefresh('all')}><RefreshCw size={14} aria-hidden className={refresh.isPending ? 'spin' : undefined} /> Alles aktualisieren</button>}>
            <Async query={market} what="Der Marktdaten-Status" compact>
              {(st) => (
                <div className="stack stack--sm">
                  <p className="row"><i className={`dot ${marketOk ? 'dot--ok' : 'dot--off'}`} style={marketOk ? undefined : { boxShadow: 'inset 0 0 0 2px var(--warn)' }} />
                    <strong>{marketOk ? 'Yahoo Finance erreichbar' : 'Yahoo Finance nicht erreichbar'}</strong>
                    <span className="muted small">· {st.last_quote_update ? `Kurse ${fmtRelative(st.last_quote_update)} (${fmtDateTime(st.last_quote_update)})` : 'noch keine Kurse abgerufen'}</span></p>
                  {st.errors && st.errors.length > 0 && <p className="small neg">{st.errors.join(' · ')}</p>}
                  <div className="row">
                    {(['quotes', 'events', 'history', 'etf'] as RefreshScope[]).map((s) => <button key={s} className="btn btn--sm" disabled={refresh.isPending} onClick={() => runRefresh(s)}>{JOB_LABEL[s]}</button>)}
                  </div>
                  {st.jobs && st.jobs.length > 0 ? (
                    <ul className="legend legend--rows">
                      {st.jobs.slice(0, 6).map((j, i) => (
                        <li key={i} style={{ gridTemplateColumns: 'minmax(0,1fr) auto' }}>
                          <span><span className="strong">{JOB_LABEL[j.job] ?? j.job}</span>{j.detail && <span className="faint xs" style={{ display: 'block' }}>{j.detail}</span>}</span>
                          <span className="row" style={{ gap: 6 }}><span className="faint xs">{fmtRelative(j.created_at)}</span>{j.status === 'ok' ? <Badge tone="gain">ok</Badge> : <Badge tone="loss">Fehler</Badge>}</span>
                        </li>
                      ))}
                    </ul>
                  ) : <p className="small muted">Noch kein Abruf protokolliert. Kurse werden geladen, sobald Wertpapiere im Depot oder auf der Watchlist stehen.</p>}
                </div>
              )}
            </Async>
          </Panel>
        </section>
      </div>

      <div className="grid cols-2">
        <Panel title="Darstellung">
          <div className="stack stack--sm">
            <div className="field"><span className="field__label">Farbschema</span><Seg<ThemeChoice> label="Farbschema" value={theme} onChange={setTheme} options={[{ value: 'system', label: 'Wie System' }, { value: 'light', label: 'Hell' }, { value: 'dark', label: 'Dunkel' }]} /></div>
            <label className="check"><input type="checkbox" checked={privacy} onChange={togglePrivacy} /> Privatmodus: Beträge weichzeichnen</label>
          </div>
        </Panel>
        <section id="export">
          <Panel title="Datenexport">
            <div className="stack stack--sm">
              <p className="small muted">Der Export wird im Browser aus dem Journal erzeugt und verlässt den Rechner nicht.</p>
              <div className="row">
                <button className="btn" disabled={exporting} onClick={() => void exportData('csv')}><Download size={15} aria-hidden /> Transaktionen als CSV</button>
                <button className="btn" disabled={exporting} onClick={() => void exportData('json')}><Download size={15} aria-hidden /> Alles als JSON</button>
              </div>
            </div>
          </Panel>
        </section>
      </div>
      {form && accounts.data && <Drawer title={form.edit ? `${form.edit.name} bearbeiten` : 'Konto anlegen'} onClose={() => setForm(null)}><AccountForm edit={form.edit} accounts={accounts.data} onDone={() => setForm(null)} /></Drawer>}
    </div>
  )
}
