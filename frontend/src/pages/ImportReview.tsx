import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, ArrowLeft, Check, CheckCheck, Copy, FileQuestion, Loader2, RotateCw, Sparkles, Trash2, X } from 'lucide-react'
import { ApiError, api } from '../api/client'
import { LEDGER_KEYS, useAccounts, useApiMutation, useDocument, useLlmStatus } from '../api/hooks'
import type { AcceptAllResult, DocumentDetail, Draft, DraftPayload, TxType } from '../api/types'
import { useToast } from '../components/Toast'
import { Async, Badge, ConfirmButton, EmptyState, Field, Panel, Seg, Skeleton, cx } from '../components/ui'
import { fmtDateTime, fmtNumber, fmtPct, parseDecimal } from '../lib/format'
import { DRAFT_STATUS, SECURITY_TX, TX_TYPE, TX_TYPES, docTypeLabel, institutionLabel } from '../lib/labels'
import { StatusBadge } from './Import'

const KEYS = [['documents'], ...LEDGER_KEYS]

function SourceView({ doc }: { doc: DocumentDetail }) {
  const hasFile = doc.has_file ?? doc.source_kind !== 'paste'
  // text-like sources (CSV, TXT, pasted text) have no visual original – show the raw text right away
  const visual = hasFile && (doc.source_kind === 'pdf' || doc.source_kind === 'image')
  const [view, setView] = useState<'file' | 'text'>(visual ? 'file' : 'text')
  const file = useQuery({ queryKey: ['documents', 'file', doc.id], queryFn: () => api.documentFile(doc.id), enabled: visual, retry: false, staleTime: Infinity })
  const [url, setUrl] = useState<string | null>(null)
  useEffect(() => {
    if (!file.data) return
    const u = URL.createObjectURL(file.data)
    setUrl(u)
    return () => URL.revokeObjectURL(u)
  }, [file.data])
  const type = file.data?.type || doc.mime_type || ''
  // PDFs are embedded straight from the API (most reliable across browsers); the blob is only needed in mock mode
  const pdfSrc = import.meta.env.VITE_MOCK === '1' ? url : `/api/documents/${doc.id}/file`
  return (
    <section className="panel review__source" aria-label="Originalbeleg">
      <header className="panel__head">
        <h2>Original</h2>
        {visual ? <Seg label="Ansicht" value={view} onChange={setView} options={[{ value: 'file', label: 'Dokument' }, { value: 'text', label: 'Erkannter Text' }]} /> : hasFile ? <a className="small" href={`/api/documents/${doc.id}/file`} target="_blank" rel="noreferrer">Datei öffnen</a> : null}
      </header>
      <div className="review__viewer">
        {view === 'file' && visual ? (
          file.isPending ? <div style={{ padding: 16 }}><Skeleton h={320} /></div>
            : file.isError || !url ? <EmptyState compact icon={<FileQuestion size={22} aria-hidden />} title="Original nicht verfügbar" text={file.error instanceof Error ? file.error.message : 'Die Datei konnte nicht geladen werden.'}>{doc.raw_text && <button className="btn" onClick={() => setView('text')}>Erkannten Text anzeigen</button>}</EmptyState>
            : type.startsWith('image/') ? <img src={url} alt={`Originalbeleg ${doc.filename}`} />
            : type === 'application/pdf' ? <iframe src={pdfSrc ?? url} title={`Originalbeleg ${doc.filename}`} />
            : <EmptyState compact title="Keine Vorschau für diesen Dateityp"><a className="btn" href={url} download={doc.filename}>Datei herunterladen</a></EmptyState>
        ) : doc.raw_text ? <pre className="rawtext">{doc.raw_text}</pre>
          : <EmptyState compact title="Kein Text erkannt" text="Aus diesem Beleg konnte noch kein Text gelesen werden." />}
      </div>
    </section>
  )
}

const str = (v: number | null | undefined) => (v == null ? '' : String(v).replace('.', ','))
/** money fields always with two decimals (−325,30 instead of −325,3) */
const money = (v: number | null | undefined) => (v == null ? '' : (Math.round(v * 100) / 100).toFixed(2).replace('.', ','))
/** Only the fields the form edits go back to the server; flags like warnings/verified are recomputed there. */
function editable(p: DraftPayload): DraftPayload {
  const { warnings: _w, review_required: _r, verified: _v, duplicate_of: _d, ...rest } = p
  return rest
}

function DraftCard({ draft, index }: { draft: Draft; index: number }) {
  const accounts = useAccounts()
  const toast = useToast()
  const [p, setP] = useState<DraftPayload>(draft.payload)
  const [dirty, setDirty] = useState(false)
  // Numeric fields are edited as text so "12," does not get eaten while typing.
  const [nums, setNums] = useState<Record<string, string>>(() => ({ amount: money(draft.payload.amount), quantity: str(draft.payload.quantity), price: str(draft.payload.price), fees: money(draft.payload.fees), taxes: money(draft.payload.taxes), balance: money(draft.payload.balance) }))
  useEffect(() => { if (!dirty) setP(draft.payload) /* server refresh */ }, [draft.payload, dirty])
  const patch = useApiMutation((payload: DraftPayload) => api.updateDraft(draft.id, payload), [['documents']])
  const accept = useApiMutation((force: boolean) => api.acceptDraft(draft.id, force), KEYS)
  const reject = useApiMutation(() => api.rejectDraft(draft.id), [['documents']])
  const reopen = useApiMutation(() => api.reopenDraft(draft.id), [['documents']])
  const pending = draft.status === 'pending'
  const duplicate = draft.status === 'duplicate'
  const flagged = pending && !!draft.payload.review_required
  const warnings = draft.payload.warnings ?? []
  const busy = patch.isPending || accept.isPending || reject.isPending || reopen.isPending
  const isBalance = draft.kind === 'balance'
  const isSec = !!p.type && SECURITY_TX.includes(p.type)
  const conf = draft.confidence
  const set = <K extends keyof DraftPayload>(k: K, v: DraftPayload[K]) => { setP((x) => ({ ...x, [k]: v })); setDirty(true) }
  const setNum = (k: 'amount' | 'quantity' | 'price' | 'fees' | 'taxes' | 'balance', v: string) => { setNums((n) => ({ ...n, [k]: v })); set(k, parseDecimal(v)) }
  const missingAccount = !p.account_id

  async function onAccept(force = false) {
    try {
      if (dirty) { await patch.mutateAsync(editable(p)); setDirty(false) }
      await accept.mutateAsync(force)
      toast.ok(isBalance ? 'Kontostand übernommen' : force ? 'Buchung trotz Duplikat übernommen' : 'Buchung übernommen')
    } catch (e) {
      // 409 = the booking already exists; the draft is now marked as duplicate (card below explains the options)
      if (e instanceof ApiError && e.status === 409) { toast.error(`${e.message} Der Entwurf ist jetzt als Duplikat markiert.`); void accept.reset() }
      else toast.error(e, 'Übernehmen fehlgeschlagen:')
    }
  }
  const num = (k: 'amount' | 'quantity' | 'price' | 'fees' | 'taxes' | 'balance', label: string) => (
    <Field label={label}><input className="input num" inputMode="decimal" value={nums[k]} disabled={!pending} onChange={(e) => setNum(k, e.target.value)} /></Field>
  )

  return (
    <article className={cx('draft', !pending && 'draft--done')} aria-label={`Entwurf ${index + 1}`}>
      <div className="draft__head">
        <h3>{isBalance ? 'Kontostand' : p.type ? TX_TYPE[p.type] : 'Buchung'}{!isBalance && (p.name || p.counterparty) ? ` · ${p.name ?? p.counterparty}` : ''}</h3>
        <span className="grow" />
        {conf != null && (
          <span className={cx('conf', conf < 0.75 ? 'conf--low' : conf < 0.9 ? 'conf--mid' : '')} title="Sicherheit der Erkennung">
            <span className="conf__bar"><i style={{ width: `${Math.round(conf * 100)}%` }} /></span>{fmtPct(conf * 100, { decimals: 0 })} sicher
          </span>
        )}
        {flagged && <Badge tone="warn" title="Wird bei „Alle übernehmen“ übersprungen"><AlertTriangle size={12} aria-hidden /> Bitte prüfen</Badge>}
        {pending && draft.payload.verified && <Badge tone="gain" title="Stück × Kurs ± Gebühren/Steuern ergibt den Betrag"><Check size={12} aria-hidden /> Rechenprobe ok</Badge>}
        {!pending && <Badge tone={draft.status === 'accepted' ? 'gain' : duplicate ? 'warn' : undefined}>{duplicate && <Copy size={12} aria-hidden />}{DRAFT_STATUS[draft.status]}</Badge>}
      </div>
      {pending && warnings.length > 0 && (
        <ul className="draft__warnings">{warnings.map((w, n) => <li key={n}><AlertTriangle size={12} aria-hidden /> {w}</li>)}</ul>
      )}
      {pending && conf != null && conf < 0.75 && <p className="xs" style={{ color: 'var(--warn)' }}><AlertTriangle size={12} aria-hidden style={{ display: 'inline', verticalAlign: '-2px' }} /> Unsichere Erkennung – bitte Betrag, Datum und Konto mit dem Original vergleichen.</p>}
      {duplicate && (
        <div className="notice notice--warn" role="status">
          <Copy size={16} aria-hidden />
          <div className="small"><strong>Duplikat – nicht gebucht.</strong> Im Journal gibt es bereits eine Buchung mit gleichem Konto, Datum und Betrag{draft.payload.duplicate_of ? <> (<Link to={`/transaktionen?q=${encodeURIComponent(draft.payload.isin ?? draft.payload.counterparty ?? '')}`}>Nr. {draft.payload.duplicate_of}</Link>)</> : ''}. Wenn es wirklich eine zweite Buchung ist, lässt sie sich trotzdem übernehmen.</div>
        </div>
      )}
      <div className="form-grid">
        <Field label="Konto" error={pending && missingAccount ? 'Konto wählen, um zu übernehmen.' : null}>
          <select className="select" value={p.account_id ?? ''} disabled={!pending} onChange={(e) => set('account_id', e.target.value ? Number(e.target.value) : null)}>
            <option value="">Bitte wählen …</option>
            {(accounts.data ?? []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </Field>
        {isBalance ? (
          <>
            <Field label="Stichtag"><input className="input" type="date" value={p.date ?? ''} disabled={!pending} onChange={(e) => set('date', e.target.value)} /></Field>
            {num('balance', 'Kontostand (€)')}
          </>
        ) : (
          <>
            <Field label="Typ">
              <select className="select" value={p.type ?? ''} disabled={!pending} onChange={(e) => set('type', (e.target.value || null) as TxType | null)}>
                {TX_TYPES.map((t) => <option key={t} value={t}>{TX_TYPE[t]}</option>)}
              </select>
            </Field>
            <Field label="Buchungstag"><input className="input" type="date" value={p.booking_date ?? ''} disabled={!pending} onChange={(e) => set('booking_date', e.target.value)} /></Field>
            {num('amount', 'Betrag (€, mit Vorzeichen)')}
            {isSec ? (
              <>
                <Field label="Wertpapier" className="span-2"><input className="input" value={p.name ?? ''} disabled={!pending} onChange={(e) => set('name', e.target.value)} /></Field>
                <Field label="ISIN"><input className="input" value={p.isin ?? ''} disabled={!pending} maxLength={12} spellCheck={false} onChange={(e) => set('isin', e.target.value.toUpperCase())} /></Field>
                {num('quantity', 'Stück')}{num('price', 'Kurs')}{num('fees', 'Gebühren (€)')}{num('taxes', 'Steuern (€)')}
              </>
            ) : (
              <Field label="Empfänger / Auftraggeber" className="span-2"><input className="input" value={p.counterparty ?? ''} disabled={!pending} onChange={(e) => set('counterparty', e.target.value)} /></Field>
            )}
            <Field label="Verwendungszweck" className="span-2"><input className="input" value={p.description ?? ''} disabled={!pending} onChange={(e) => set('description', e.target.value)} /></Field>
            {!isSec && <Field label="Kategorie-Vorschlag"><input className="input" value={p.category_hint ?? ''} disabled={!pending} onChange={(e) => set('category_hint', e.target.value || null)} placeholder="keiner" /></Field>}
          </>
        )}
      </div>
      {pending ? (
        <div className="form-actions">
          <button className="btn btn--primary" onClick={() => void onAccept()} disabled={busy || missingAccount}><Check size={15} aria-hidden /> {accept.isPending ? 'Übernimmt …' : dirty ? 'Speichern und übernehmen' : 'Übernehmen'}</button>
          <button className="btn" onClick={() => reject.mutate(undefined, { onSuccess: () => toast.ok('Entwurf verworfen'), onError: (e) => toast.error(e) })} disabled={busy}><X size={15} aria-hidden /> Verwerfen</button>
          {dirty && <button className="btn btn--ghost" disabled={busy} onClick={() => patch.mutate(editable(p), { onSuccess: () => { setDirty(false); toast.ok('Korrektur gespeichert') }, onError: (e) => toast.error(e) })}>Nur Korrektur speichern</button>}
        </div>
      ) : duplicate ? (
        <div className="form-actions">
          <button className="btn" onClick={() => void onAccept(true)} disabled={busy}><Check size={15} aria-hidden /> Trotzdem übernehmen</button>
          <button className="btn btn--ghost" onClick={() => reopen.mutate(undefined, { onSuccess: () => toast.ok('Entwurf wieder geöffnet'), onError: (e) => toast.error(e) })} disabled={busy}>Bearbeiten</button>
          <button className="btn btn--ghost" onClick={() => reject.mutate(undefined, { onSuccess: () => toast.ok('Entwurf verworfen'), onError: (e) => toast.error(e) })} disabled={busy}><X size={15} aria-hidden /> Verwerfen</button>
        </div>
      ) : draft.status === 'rejected' ? (
        <div className="form-actions"><button className="btn btn--ghost btn--sm" onClick={() => reopen.mutate(undefined, { onSuccess: () => toast.ok('Entwurf wieder geöffnet'), onError: (e) => toast.error(e) })} disabled={busy}>Wieder öffnen</button></div>
      ) : draft.transaction_id ? <p className="xs muted">Als Buchung Nr. {draft.transaction_id} im <Link to="/transaktionen">Journal</Link>.</p> : null}
    </article>
  )
}

export function ImportReview() {
  const id = Number(useParams().id)
  const doc = useDocument(id)
  const toast = useToast()
  const nav = useNavigate()
  const acceptAll = useApiMutation((includeFlagged: boolean) => api.acceptAll(id, includeFlagged), KEYS)
  const llm = useLlmStatus()
  const [lastRun, setLastRun] = useState<AcceptAllResult | null>(null)
  const runAcceptAll = (includeFlagged: boolean) => acceptAll.mutate(includeFlagged, {
    onSuccess: (r) => {
      setLastRun(r)
      const parts = [`${r.accepted} übernommen`, r.skipped ? `${r.skipped} übersprungen – bitte einzeln prüfen` : '', r.duplicates ? `${r.duplicates} ${r.duplicates === 1 ? 'Duplikat' : 'Duplikate'} nicht gebucht` : '', r.errors.length ? `${r.errors.length} mit Fehler` : ''].filter(Boolean)
      if (r.skipped || r.errors.length || (r.duplicates && !r.accepted)) toast.error(parts.join(', ')); else toast.ok(parts.join(', '))
    },
    onError: (e) => toast.error(e, 'Übernehmen fehlgeschlagen:'),
  })
  const reprocess = useApiMutation((force: boolean) => api.reprocess(id, force), [['documents']])
  const remove = useApiMutation(() => api.deleteDocument(id), [['documents', 'list']])

  return (
    <div className="page">
      <Async query={doc} what="Der Beleg" skeleton={<div className="stack"><Skeleton h={56} w={380} /><Skeleton h={420} /></div>}>
        {(d) => {
          const working = d.status === 'uploaded' || d.status === 'processing'
          const pending = d.drafts.filter((x) => x.status === 'pending')
          const needsAccount = pending.some((x) => !x.payload.account_id)
          const flaggedCount = pending.filter((x) => x.payload.review_required).length
          const dupCount = d.drafts.filter((x) => x.status === 'duplicate').length
          const clean = pending.length - flaggedCount
          const ollamaIssue = d.status === 'failed' && (/ollama|sprachmodell/i.test(d.error ?? '') || (d.source_kind === 'image' && llm.data?.available === false))
          return (
            <>
              <div className="page-head">
                <div style={{ minWidth: 0 }}>
                  <Link className="crumb" to="/import"><ArrowLeft size={14} aria-hidden /> Import</Link>
                  <h1 style={{ overflowWrap: 'anywhere' }}>{d.filename}</h1>
                  <p className="page-head__sub row" style={{ gap: 8 }}>
                    <StatusBadge d={d} />
                    <span>{[d.detected_doc_type && docTypeLabel(d.detected_doc_type), d.detected_institution && institutionLabel(d.detected_institution), d.parser && `gelesen mit ${d.parser}`, d.processing_ms != null && `${fmtNumber(d.processing_ms / 1000, 1)} s`, fmtDateTime(d.created_at)].filter(Boolean).join(' · ')}</span>
                  </p>
                </div>
                <div className="page-head__actions">
                  <button className="btn btn--primary" disabled={!clean || acceptAll.isPending || needsAccount} title={needsAccount ? 'Mindestens einem Entwurf fehlt das Konto' : flaggedCount ? `${flaggedCount} markierte Entwürfe werden übersprungen` : undefined}
                    onClick={() => runAcceptAll(false)}>
                    <CheckCheck size={15} aria-hidden /> {acceptAll.isPending ? 'Übernimmt …' : `Alle übernehmen${clean ? ` (${clean})` : ''}`}
                  </button>
                  <button className="btn" disabled={working || reprocess.isPending} onClick={() => reprocess.mutate(false, { onSuccess: () => toast.ok('Beleg wird neu verarbeitet'), onError: (e) => toast.error(e) })}><RotateCw size={15} aria-hidden /> Neu verarbeiten</button>
                  <button className="btn" disabled={working || reprocess.isPending || llm.data?.available === false} title={llm.data?.available === false ? 'Ollama ist nicht erreichbar' : 'Parser überspringen und das lokale Modell lesen lassen'} onClick={() => reprocess.mutate(true, { onSuccess: () => toast.ok('Beleg wird mit dem lokalen Modell gelesen'), onError: (e) => toast.error(e) })}><Sparkles size={15} aria-hidden /> Mit Modell lesen</button>
                  <ConfirmButton small={false} label="Löschen" icon={<Trash2 size={15} aria-hidden />} confirmLabel="Beleg endgültig löschen" onConfirm={() => remove.mutate(undefined, { onSuccess: () => { toast.ok('Beleg gelöscht'); nav('/import') }, onError: (e) => toast.error(e) })} />
                </div>
              </div>
              {d.status === 'failed' && (
                <div className="notice notice--loss" role="alert">
                  <AlertTriangle size={17} aria-hidden />
                  <div>
                    <strong>{ollamaIssue ? 'Dieser Beleg braucht das lokale Modell – Ollama ist nicht erreichbar.' : 'Die Verarbeitung ist fehlgeschlagen.'}</strong> {ollamaIssue ? (/kein parser/i.test(d.error ?? '') ? 'Kein fester Parser passt auf dieses Dokument.' : '') : d.error ?? 'Unbekannter Fehler.'}
                    {ollamaIssue && <span style={{ display: 'block', marginTop: 4 }}>So geht es weiter: Ollama starten (<code>ollama serve</code> oder die Ollama-App öffnen), einmalig das Bildmodell laden (<code>ollama pull {llm.data?.vision_model ?? 'qwen2.5vl:7b'}</code>) und dann „Erneut verarbeiten“ wählen. Der Beleg bleibt gespeichert. Alternativ die Buchung <Link to="/transaktionen?neu=1">manuell erfassen</Link>.</span>}
                    {!ollamaIssue && <> Erneut versuchen oder die Buchung <Link to="/transaktionen?neu=1">manuell erfassen</Link>.</>}
                  </div>
                  <div className="notice__actions"><button className="btn btn--sm" disabled={reprocess.isPending} onClick={() => { void llm.refetch(); reprocess.mutate(false, { onSuccess: () => toast.ok('Beleg wird erneut verarbeitet'), onError: (e) => toast.error(e) }) }}><RotateCw size={14} aria-hidden /> Erneut verarbeiten</button></div>
                </div>
              )}
              {d.duplicate_upload && <div className="notice" role="status"><Copy size={17} aria-hidden /><div>Diese Datei wurde schon einmal hochgeladen – angezeigt wird der vorhandene Beleg.</div></div>}
              {(d.warnings ?? []).length > 0 && <div className="notice notice--warn" role="status"><AlertTriangle size={17} aria-hidden /><div>{(d.warnings ?? []).join(' ')}</div></div>}
              {flaggedCount > 0 && !working && (
                <div className="notice notice--warn" role="status">
                  <AlertTriangle size={17} aria-hidden />
                  <div>
                    <strong>{lastRun?.skipped ? `${lastRun.skipped} übersprungen, bitte einzeln prüfen.` : `${flaggedCount} ${flaggedCount === 1 ? 'Entwurf ist' : 'Entwürfe sind'} mit „Bitte prüfen“ markiert.`}</strong> Bei diesen Entwürfen passt etwas nicht sicher (z. B. Rechenprobe, ISIN, unsichere Erkennung). „Alle übernehmen“ lässt sie aus.
                  </div>
                  <div className="notice__actions"><button className="btn btn--sm" disabled={acceptAll.isPending || needsAccount} onClick={() => runAcceptAll(true)}>Auch markierte übernehmen ({pending.length})</button></div>
                </div>
              )}
              {dupCount > 0 && !working && (
                <div className="notice" role="status"><Copy size={17} aria-hidden /><div><strong>{dupCount} {dupCount === 1 ? 'Entwurf ist ein Duplikat' : 'Entwürfe sind Duplikate'}</strong> einer vorhandenen Buchung und {dupCount === 1 ? 'wird' : 'werden'} nicht gebucht – unten als „Duplikat“ gekennzeichnet.</div></div>
              )}
              {lastRun && lastRun.errors.length > 0 && (
                <div className="notice notice--loss" role="alert"><AlertTriangle size={17} aria-hidden /><div><strong>{lastRun.errors.length} {lastRun.errors.length === 1 ? 'Entwurf konnte' : 'Entwürfe konnten'} nicht übernommen werden:</strong> {lastRun.errors.map((e) => e.error).filter((v, i, a) => a.indexOf(v) === i).join(' · ')}</div></div>
              )}
              <div className="review">
                <SourceView doc={d} />
                <Panel title="Entwürfe" flush hint={d.drafts.length ? `${d.drafts.length - pending.length - dupCount} von ${d.drafts.length} entschieden${dupCount ? `, ${dupCount} ${dupCount === 1 ? 'Duplikat' : 'Duplikate'}` : ''}` : undefined}>
                  {working ? (
                    <EmptyState icon={<Loader2 size={24} aria-hidden className="spin" />} title="Beleg wird gelesen …" text="Text wird extrahiert und in Buchungen übersetzt. Mit dem lokalen Modell dauert das je nach Rechner 5 bis 60 Sekunden. Diese Ansicht aktualisiert sich von selbst." />
                  ) : d.drafts.length === 0 ? (
                    <EmptyState title="Keine Buchung erkannt" text={d.status === 'failed' ? 'Aus diesem Beleg sind keine Entwürfe entstanden.' : 'Im Beleg wurde nichts gefunden, das sich verbuchen lässt.'}>
                      <button className="btn" onClick={() => reprocess.mutate(false, { onSuccess: () => toast.ok('Beleg wird erneut verarbeitet'), onError: (e) => toast.error(e) })}><RotateCw size={15} aria-hidden /> Erneut verarbeiten</button><Link className="btn" to="/transaktionen?neu=1">Manuell erfassen</Link>
                    </EmptyState>
                  ) : d.drafts.map((x, i) => <DraftCard key={x.id} draft={x} index={i} />)}
                </Panel>
              </div>
            </>
          )
        }}
      </Async>
    </div>
  )
}
