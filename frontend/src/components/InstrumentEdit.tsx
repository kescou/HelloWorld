import { useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, Loader2, Search } from 'lucide-react'
import { api } from '../api/client'
import { useApiMutation, useMarketRefresh } from '../api/hooks'
import type { Instrument, InstrumentType, InstrumentUpdate, SearchResult } from '../api/types'
import { INSTRUMENT_TYPE, SOURCE } from '../lib/labels'
import { useToast } from './Toast'
import { Badge, Field } from './ui'

const TYPES = Object.keys(INSTRUMENT_TYPE) as InstrumentType[]
const ISSUERS = ['ishares', 'xtrackers', 'vanguard', 'amundi', 'spdr', 'invesco']
const issuerLabel = (slug: string) => (SOURCE[slug] ?? slug).replace(' (Emittent)', '')
const isUrl = (v: string) => /^https?:\/\/\S+\.\S+/i.test(v)
const SYMBOL_RE = /^[A-Z0-9.\-=^]{1,20}$/

/** Yahoo lookup inside the editor: by ISIN (most reliable) or name; a hit only fills the symbol field. */
function SymbolSearch({ seed, current, onPick }: { seed: string; current: string; onPick: (r: SearchResult) => void }) {
  const [q, setQ] = useState(seed)
  const [term, setTerm] = useState('')
  const search = useQuery({ queryKey: ['market', 'search', term], queryFn: () => api.search(term), enabled: term.length >= 2, retry: false, staleTime: 60_000 })
  const run = () => { const next = q.trim(); if (next === term) void search.refetch(); else setTerm(next) }
  return (
    <div className="field span-all">
      <label className="field__label" htmlFor="symbol-search">Symbol bei Yahoo Finance suchen</label>
      <div className="row" style={{ flexWrap: 'nowrap' }}>
        <div className="search grow">
          {search.isFetching ? <Loader2 size={15} aria-hidden className="spin" /> : <Search size={15} aria-hidden />}
          <input id="symbol-search" className="input" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="ISIN oder Name"
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); run() } }} />
        </div>
        <button type="button" className="btn" onClick={run} disabled={q.trim().length < 2 || search.isFetching}>{search.isFetching ? 'Sucht …' : 'Suchen'}</button>
      </div>
      {term.length < 2 ? <span className="field__hint">Mit der ISIN klappt die Suche am zuverlässigsten. Ein Treffer wird nur ins Feld „Yahoo-Symbol“ übernommen.</span>
        : search.isFetching && !search.data ? <span className="field__hint" role="status">Suche bei Yahoo Finance läuft …</span>
        : search.isError ? (
          <div className="notice notice--warn" role="alert" style={{ marginTop: 6 }}>
            <AlertTriangle size={17} aria-hidden />
            <div><strong>Die Suche ist gerade nicht möglich.</strong> Das Symbol lässt sich oben von Hand eintragen – es steht auf finance.yahoo.com hinter dem Namen, z. B. <code>SAP.DE</code> (Xetra), <code>MSFT</code> (US-Börse), <code>EUNL.DE</code>.</div>
          </div>
        ) : !search.data?.length ? <span className="field__hint">Kein Treffer für „{term}“. Mit der ISIN oder dem englischen Namen erneut versuchen.</span>
        : (
          <ul className="list" style={{ margin: '6px 0 0', border: '1px solid var(--border)', borderRadius: 'var(--r-md)' }} aria-label="Suchtreffer">
            {search.data.map((r) => (
              <li key={r.symbol} style={{ gridTemplateColumns: 'minmax(0,1fr) auto' }}>
                <span style={{ minWidth: 0 }}>
                  <span className="title truncate">{r.symbol} {r.preferred && <Badge tone="accent" title="Diese Notierung würde die App automatisch wählen (Xetra, sonst Heimatbörse)">empfohlen</Badge>}</span>
                  <span className="meta truncate">{[r.name, r.exchange, r.currency].filter(Boolean).join(' · ')}</span>
                </span>
                {r.symbol.toUpperCase() === current ? <Badge>gewählt</Badge> : <button type="button" className="btn btn--sm" onClick={() => onPick(r)}>Übernehmen</button>}
              </li>
            ))}
          </ul>
        )}
    </div>
  )
}

/** Master data of an instrument. The Yahoo symbol decides where prices come from, so it sits at the top. */
export function InstrumentEdit({ instrument: i, onDone }: { instrument: Instrument; onDone: () => void }) {
  const toast = useToast()
  const refresh = useMarketRefresh()
  const [symbol, setSymbol] = useState(i.symbol ?? '')
  const [name, setName] = useState(i.name)
  const [type, setType] = useState<InstrumentType>(i.type)
  const [currency, setCurrency] = useState(i.currency)
  const [provider, setProvider] = useState(i.fund_provider ?? '')
  const [irUrl, setIrUrl] = useState(i.ir_url ?? '')
  const [holdingsUrl, setHoldingsUrl] = useState(i.meta?.market?.holdings_url ?? '')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const save = useApiMutation((b: InstrumentUpdate) => api.updateInstrument(i.id, b), [['instruments'], ['portfolio'], ['analytics'], ['market']])
  const isFund = type === 'etf' || type === 'fund'
  const sym = symbol.trim().toUpperCase()
  const symbolChanged = sym !== (i.symbol ?? '').toUpperCase()

  function submit(e: FormEvent) {
    e.preventDefault()
    const err: Record<string, string> = {}
    if (!name.trim()) err.name = 'Bitte einen Namen angeben.'
    if (sym && !SYMBOL_RE.test(sym)) err.symbol = 'Ein Yahoo-Symbol besteht aus Buchstaben, Ziffern und Punkt, z. B. SAP.DE.'
    if (!/^[A-Za-z]{3}$/.test(currency.trim())) err.currency = 'Dreistelliger Code, z. B. EUR oder USD.'
    if (irUrl.trim() && !isUrl(irUrl.trim())) err.ir = 'Vollständige Adresse mit https:// angeben.'
    if (isFund && holdingsUrl.trim() && !isUrl(holdingsUrl.trim())) err.holdings = 'Vollständige Adresse mit https:// angeben.'
    setErrors(err)
    if (Object.keys(err).length) return
    const body: InstrumentUpdate = { name: name.trim(), type, currency: currency.trim().toUpperCase(), ir_url: irUrl.trim() || null }
    if (symbolChanged) body.symbol = sym || null
    if (isFund) {
      body.fund_provider = provider.trim().toLowerCase() || null
      if (holdingsUrl.trim() !== (i.meta?.market?.holdings_url ?? '')) body.meta = { market: { holdings_url: holdingsUrl.trim() || null } }
    }
    save.mutate(body, {
      onSuccess: () => {
        if (symbolChanged && sym) {
          toast.ok(`Symbol ${sym} gespeichert – Kurs und Historie werden neu geladen`)
          refresh.start({ scope: 'quotes', instrument_ids: [i.id] }, { onError: (er) => toast.error(er, 'Der Abruf konnte nicht gestartet werden:') })
        } else toast.ok(symbolChanged ? 'Symbol entfernt – ohne Symbol werden keine Kurse geladen' : 'Wertpapier gespeichert')
        onDone()
      },
      onError: (er) => toast.error(er, 'Speichern fehlgeschlagen:'),
    })
  }

  return (
    <form className="form-grid" onSubmit={submit} noValidate>
      <Field label="Yahoo-Symbol" className="span-all" error={errors.symbol}
        hint={symbolChanged && i.symbol ? `Bisher ${i.symbol}. Beim Speichern werden Kurs, Kurshistorie, Termine und Kennzahlen des alten Symbols verworfen und neu geladen.` : 'Bestimmt, woher Kurse, Termine und Kennzahlen kommen. Deutsche Notierungen enden auf .DE.'}>
        <input className="input" value={symbol} onChange={(e) => setSymbol(e.target.value)} placeholder="z. B. SAP.DE" spellCheck={false} autoCapitalize="characters" autoComplete="off" aria-invalid={!!errors.symbol} />
      </Field>
      <SymbolSearch seed={i.isin ?? i.name} current={sym} onPick={(r) => { setSymbol(r.symbol); if (r.currency && !i.symbol) setCurrency(r.currency) }} />
      <Field label="Name" className="span-all" error={errors.name}><input className="input" value={name} onChange={(e) => setName(e.target.value)} aria-invalid={!!errors.name} /></Field>
      <Field label="Art"><select className="select" value={type} onChange={(e) => setType(e.target.value as InstrumentType)}>{TYPES.map((k) => <option key={k} value={k}>{INSTRUMENT_TYPE[k]}</option>)}</select></Field>
      <Field label="Handelswährung" error={errors.currency} hint="Währung der Kurse"><input className="input" value={currency} onChange={(e) => setCurrency(e.target.value)} maxLength={3} spellCheck={false} autoCapitalize="characters" aria-invalid={!!errors.currency} /></Field>
      {isFund && (
        <>
          <Field label="Fondsanbieter" className="span-all" hint="Steuert, bei welchem Emittenten die Positionsliste für die Durchschau abgerufen wird.">
            <input className="input" list="fund-providers" value={provider} onChange={(e) => setProvider(e.target.value)} placeholder="z. B. ishares" spellCheck={false} />
            <datalist id="fund-providers">{ISSUERS.map((k) => <option key={k} value={k}>{issuerLabel(k)}</option>)}</datalist>
          </Field>
          <Field label="Adresse der Positionsliste" className="span-all" error={errors.holdings} hint="Optional: direkter Download-Link (CSV/XLSX) von der Produktseite. Wird vor dem Emittenten-Abruf versucht.">
            <input className="input" type="url" value={holdingsUrl} onChange={(e) => setHoldingsUrl(e.target.value)} placeholder="https://" spellCheck={false} aria-invalid={!!errors.holdings} />
          </Field>
        </>
      )}
      <Field label="Investor-Relations-Seite" className="span-all" error={errors.ir} hint="Optional, erscheint als Link im Seitenkopf.">
        <input className="input" type="url" value={irUrl} onChange={(e) => setIrUrl(e.target.value)} placeholder="https://" spellCheck={false} aria-invalid={!!errors.ir} />
      </Field>
      <p className="xs muted span-all">ISIN{i.isin ? ` ${i.isin}` : ''} und Buchungen bleiben unverändert.</p>
      <div className="form-actions span-all">
        <button className="btn btn--primary" type="submit" disabled={save.isPending}>{save.isPending ? 'Speichert …' : 'Wertpapier speichern'}</button>
        <button className="btn" type="button" onClick={onDone}>Abbrechen</button>
      </div>
    </form>
  )
}
