import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowLeftRight, ChevronLeft, ChevronRight, FileInput, Plus, Search, X } from 'lucide-react'
import { api } from '../api/client'
import { useAccounts, useApiMutation, useCategories, useTransactions } from '../api/hooks'
import type { Transaction, TransactionFilters, TxType } from '../api/types'
import { useToast } from '../components/Toast'
import { TxEntry, type TxFormDefaults } from '../components/TxForm'
import { TxTable } from '../components/TxTable'
import { Async, Drawer, EmptyState, Field, Panel, SkeletonRows } from '../components/ui'
import { fmtNumber } from '../lib/format'
import { TX_TYPE, TX_TYPES } from '../lib/labels'

const PAGE = 50

export function Transactions() {
  const [params, setParams] = useSearchParams()
  const accounts = useAccounts()
  const categories = useCategories()
  const toast = useToast()
  const [q, setQ] = useState(params.get('q') ?? '')
  const [debounced, setDebounced] = useState(q)
  const [offset, setOffset] = useState(0)
  const [entry, setEntry] = useState<{ edit?: Transaction; defaults?: TxFormDefaults } | null>(null)
  useEffect(() => { const id = setTimeout(() => setDebounced(q.trim()), 250); return () => clearTimeout(id) }, [q])

  const get = (k: string) => params.get(k) ?? ''
  const set = (k: string, v: string) => { const n = new URLSearchParams(params); if (v) n.set(k, v); else n.delete(k); n.delete('neu'); setParams(n, { replace: true }); setOffset(0) }
  useEffect(() => {
    const neu = params.get('neu')
    if (neu) { setEntry({ defaults: { type: (TX_TYPES as string[]).includes(neu) ? (neu as TxType) : undefined } }); const n = new URLSearchParams(params); n.delete('neu'); setParams(n, { replace: true }) }
  }, [params, setParams])

  const filters: TransactionFilters = {
    account_id: get('konto') ? Number(get('konto')) : undefined, type: (get('typ') as TxType) || undefined, category_id: get('kategorie') ? Number(get('kategorie')) : undefined,
    q: debounced || undefined, date_from: get('von') || undefined, date_to: get('bis') || undefined, limit: PAGE, offset,
  }
  const tx = useTransactions(filters)
  const del = useApiMutation((id: number) => api.deleteTransaction(id))
  const hasFilter = !!(filters.account_id || filters.type || filters.category_id || filters.q || filters.date_from || filters.date_to)
  const reset = () => { setQ(''); setParams({}, { replace: true }); setOffset(0) }
  const total = tx.data?.total ?? 0

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Transaktionen</h1><p className="page-head__sub">Ein Journal für alles: Kontoumsätze, Käufe, Dividenden, Umbuchungen.</p></div>
        <div className="page-head__actions">
          <Link className="btn" to="/import"><FileInput size={15} aria-hidden /> Importieren</Link>
          <button className="btn btn--primary" onClick={() => setEntry({ defaults: { account_id: filters.account_id } })}><Plus size={15} aria-hidden /> Transaktion erfassen</button>
        </div>
      </div>

      <div className="toolbar" role="search" aria-label="Transaktionen filtern">
        <Field label="Suche" className="field--wide">
          <span className="search"><Search size={15} aria-hidden /><input className="input" type="search" value={q} onChange={(e) => { setQ(e.target.value); setOffset(0) }} placeholder="Empfänger, Zweck, Wertpapier, ISIN" /></span>
        </Field>
        <Field label="Konto">
          <select className="select" value={get('konto')} onChange={(e) => set('konto', e.target.value)}><option value="">Alle Konten</option>{(accounts.data ?? []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select>
        </Field>
        <Field label="Typ">
          <select className="select" value={get('typ')} onChange={(e) => set('typ', e.target.value)}><option value="">Alle Typen</option>{TX_TYPES.map((t) => <option key={t} value={t}>{TX_TYPE[t]}</option>)}</select>
        </Field>
        <Field label="Kategorie">
          <select className="select" value={get('kategorie')} onChange={(e) => set('kategorie', e.target.value)}><option value="">Alle Kategorien</option>{(categories.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
        </Field>
        <Field label="Von"><input className="input" type="date" value={get('von')} onChange={(e) => set('von', e.target.value)} /></Field>
        <Field label="Bis"><input className="input" type="date" value={get('bis')} onChange={(e) => set('bis', e.target.value)} /></Field>
        {hasFilter && <button className="btn btn--ghost" onClick={reset}><X size={15} aria-hidden /> Filter zurücksetzen</button>}
      </div>

      <Panel flush title="Journal" hint={tx.data ? `${fmtNumber(total)} ${total === 1 ? 'Buchung' : 'Buchungen'}${hasFilter ? ' gefiltert' : ''}` : undefined}
        foot={total > PAGE ? (
          <>
            <span>{offset + 1}–{Math.min(offset + PAGE, total)} von {fmtNumber(total)}</span>
            <span className="pager">
              <button className="btn btn--sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}><ChevronLeft size={15} aria-hidden /> Neuere</button>
              <button className="btn btn--sm" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Ältere <ChevronRight size={15} aria-hidden /></button>
            </span>
          </>
        ) : undefined}>
        <Async query={tx} what="Die Transaktionen" skeleton={<SkeletonRows rows={10} />} isEmpty={(d) => d.items.length === 0}
          empty={hasFilter
            ? <EmptyState title="Keine Buchung passt zu diesen Filtern" text="Suchbegriff kürzen oder den Zeitraum erweitern."><button className="btn" onClick={reset}>Filter zurücksetzen</button></EmptyState>
            : <EmptyState icon={<ArrowLeftRight size={24} aria-hidden />} title="Noch keine Transaktionen" text="Dokument importieren oder manuell erfassen – jede Buchung landet in diesem Journal.">
                <Link className="btn btn--primary" to="/import"><FileInput size={15} aria-hidden /> Dokument importieren</Link><button className="btn" onClick={() => setEntry({})}><Plus size={15} aria-hidden /> Manuell erfassen</button></EmptyState>}>
          {(d) => <TxTable items={d.items} onEdit={(t) => setEntry({ edit: t })}
            onDelete={(t) => del.mutate(t.id, { onSuccess: () => toast.ok('Transaktion gelöscht'), onError: (e) => toast.error(e, 'Löschen fehlgeschlagen:') })} />}
        </Async>
      </Panel>
      {entry && <Drawer title={entry.edit ? 'Transaktion bearbeiten' : 'Transaktion erfassen'} onClose={() => setEntry(null)}><TxEntry edit={entry.edit} defaults={entry.defaults} onDone={() => setEntry(null)} /></Drawer>}
    </div>
  )
}
