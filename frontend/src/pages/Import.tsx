import { useCallback, useEffect, useRef, useState, type DragEvent } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, ClipboardPaste, FileImage, FileText, Inbox, Loader2, RotateCw, Trash2, UploadCloud } from 'lucide-react'
import { api } from '../api/client'
import { useAccounts, useApiMutation, useDocuments, useLlmStatus } from '../api/hooks'
import type { DocumentSummary } from '../api/types'
import { useToast } from '../components/Toast'
import { Async, Badge, ConfirmButton, EmptyState, Field, Panel, SkeletonRows, cx } from '../components/ui'
import { fmtNumber, fmtRelative } from '../lib/format'
import { DOC_STATUS, docTypeLabel, institutionLabel } from '../lib/labels'

const DOC_KEYS = [['documents']]

export function StatusBadge({ d }: { d: Pick<DocumentSummary, 'status' | 'pending_count' | 'duplicate_count' | 'draft_count' | 'accepted_count'> }) {
  if (d.status === 'processing' || d.status === 'uploaded') return <Badge><Loader2 size={12} aria-hidden className="spin" /> {DOC_STATUS[d.status]}</Badge>
  if (d.status === 'review') return <Badge tone="accent">{d.pending_count > 0 ? `${d.pending_count} zu prüfen` : d.duplicate_count ? `${d.duplicate_count} ${d.duplicate_count === 1 ? 'Duplikat' : 'Duplikate'}` : DOC_STATUS.review}</Badge>
  if (d.status === 'failed') return <Badge tone="loss"><AlertTriangle size={12} aria-hidden /> {DOC_STATUS.failed}</Badge>
  // "imported" only means every draft is decided – say so when nothing was actually booked
  if (d.status === 'imported' && d.accepted_count === 0) return <Badge tone={d.duplicate_count ? 'warn' : undefined}>{d.duplicate_count ? (d.duplicate_count === 1 ? 'Duplikat' : 'Nur Duplikate') : d.draft_count ? 'Verworfen' : 'Ohne Buchung'}</Badge>
  return <Badge tone="gain">{DOC_STATUS[d.status]}</Badge>
}

export function LlmNotice() {
  const llm = useLlmStatus()
  if (llm.isPending) return null
  if (llm.isError || !llm.data?.available) {
    return (
      <div className="notice notice--warn" role="status">
        <AlertTriangle size={17} aria-hidden />
        <div>
          <strong>Ollama ist nicht erreichbar.</strong> Bekannte PDF- und CSV-Formate (ING, Postbank, Scalable, Trade Republic) sowie eingefügte Umsatzlisten werden weiterhin gelesen. Screenshots, Scans und unbekannte Belege brauchen das lokale Modell –
          mit <code>ollama serve</code> starten{llm.data?.text_model ? <> und einmalig <code>ollama pull {llm.data.text_model}</code>{llm.data.vision_model ? <> sowie <code>ollama pull {llm.data.vision_model}</code></> : null} ausführen</> : null}.
        </div>
        <div className="notice__actions"><button className="btn btn--sm" onClick={() => void llm.refetch()} disabled={llm.isFetching}>Erneut prüfen</button></div>
      </div>
    )
  }
  return <p className="small muted row" style={{ flexWrap: 'nowrap' }}><i className="dot dot--ok" /><span className="grow">Lokales Modell bereit: {llm.data.text_model ?? '–'} (Text){llm.data.vision_model ? `, ${llm.data.vision_model} (Bild)` : ''}</span></p>
}

export function Import() {
  const docs = useDocuments()
  const accounts = useAccounts()
  const toast = useToast()
  const [over, setOver] = useState(false)
  const [accountId, setAccountId] = useState('')
  const [text, setText] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)
  const textRef = useRef<HTMLTextAreaElement>(null)
  const account = accountId ? Number(accountId) : undefined
  const upload = useApiMutation((files: File[]) => api.uploadDocuments(files, account), DOC_KEYS)
  const paste = useApiMutation((t: string) => api.pasteDocument(t, account), DOC_KEYS)
  const reprocess = useApiMutation((id: number) => api.reprocess(id), DOC_KEYS)
  const remove = useApiMutation((id: number) => api.deleteDocument(id), DOC_KEYS)

  const send = useCallback((files: File[], what = 'Datei') => {
    if (!files.length) return
    upload.mutate(files, {
      onSuccess: (docs) => {
        const known = docs.filter((d) => d.duplicate_upload).length
        if (known && known === docs.length) toast.error(known === 1 ? `${what} wurde schon einmal hochgeladen – der vorhandene Beleg steht in der Liste.` : `Alle ${known} Dateien wurden schon einmal hochgeladen.`)
        else toast.ok((files.length === 1 ? `${what} wird verarbeitet` : `${files.length} Dateien werden verarbeitet`) + (known ? ` (${known} davon schon vorhanden)` : ''))
      },
      onError: (e) => toast.error(e, 'Upload fehlgeschlagen:'),
    })
  }, [upload, toast])

  // Clipboard: images (screenshots) are uploaded right away; plain text lands in the text box.
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const items = [...(e.clipboardData?.files ?? [])].filter((f) => f.type.startsWith('image/') || f.type === 'application/pdf')
      if (items.length) {
        e.preventDefault()
        const stamp = new Date().toISOString().slice(0, 16).replace('T', ' ')
        send(items.map((f) => (f.name && f.name !== 'image.png' ? f : new File([f], `Screenshot ${stamp}.png`, { type: f.type }))), 'Screenshot')
        return
      }
      const target = e.target as HTMLElement | null
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT')) return
      const pasted = e.clipboardData?.getData('text/plain')
      if (pasted?.trim()) { e.preventDefault(); setText(pasted); textRef.current?.focus() }
    }
    document.addEventListener('paste', onPaste)
    return () => document.removeEventListener('paste', onPaste)
  }, [send])

  const onDrop = (e: DragEvent) => { e.preventDefault(); setOver(false); send([...e.dataTransfer.files]) }

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Import</h1><p className="page-head__sub">Beleg hochladen, Entwürfe prüfen, übernehmen. Alles bleibt lokal auf diesem Rechner.</p></div>
      </div>
      <LlmNotice />

      <div className="grid cols-2">
        <div className={cx('dropzone', over && 'is-over')} onDragOver={(e) => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)} onDrop={onDrop}>
          <UploadCloud size={28} aria-hidden />
          <p className="strong">PDF, Bild oder CSV hierher ziehen</p>
          <p className="small muted">Kontoauszüge, Wertpapier- und Dividendenabrechnungen, Screenshots aus der Broker-App. Ein Screenshot in der Zwischenablage lässt sich direkt mit Strg+V einfügen.</p>
          <input ref={fileRef} type="file" hidden multiple accept=".pdf,.csv,.txt,image/*" onChange={(e) => { send([...(e.target.files ?? [])]); e.target.value = '' }} />
          <div className="row" style={{ justifyContent: 'center' }}>
            <button className="btn btn--primary" onClick={() => fileRef.current?.click()} disabled={upload.isPending}>{upload.isPending ? 'Lädt hoch …' : 'Dateien auswählen'}</button>
            <label className="sr-only" htmlFor="imp-acc">Konto für den Import (optional)</label>
            <select id="imp-acc" className="select" style={{ width: 'auto', maxWidth: 220 }} value={accountId} onChange={(e) => setAccountId(e.target.value)}>
              <option value="">Konto automatisch erkennen</option>
              {(accounts.data ?? []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          </div>
        </div>
        <Panel title="Text einfügen">
          <form className="stack stack--sm" onSubmit={(e) => { e.preventDefault(); if (text.trim()) paste.mutate(text, { onSuccess: () => { setText(''); toast.ok('Text wird verarbeitet') }, onError: (er) => toast.error(er, 'Text konnte nicht gesendet werden:') }) }}>
            <Field label="Kopierter Text aus Online-Banking, E-Mail oder PDF">
              <textarea ref={textRef} className="textarea" rows={5} value={text} onChange={(e) => setText(e.target.value)} placeholder={'z. B.\n12.11.  Kartenzahlung REWE  -45,90 EUR\n13.11.  Gehalt Nordwind Software  +4.350,00 EUR'} />
            </Field>
            <div className="form-actions">
              <button className="btn" type="submit" disabled={!text.trim() || paste.isPending}><ClipboardPaste size={15} aria-hidden /> {paste.isPending ? 'Sendet …' : 'Text verarbeiten'}</button>
              {text && <button className="btn btn--ghost" type="button" onClick={() => setText('')}>Leeren</button>}
            </div>
          </form>
        </Panel>
      </div>

      <Panel title="Belege" flush hint={docs.data?.length ? `${docs.data.length} Belege` : undefined}>
        <Async query={docs} what="Die Belege" skeleton={<SkeletonRows rows={5} />}
          empty={<EmptyState icon={<Inbox size={24} aria-hidden />} title="Noch keine Belege" text="Der erste Upload erscheint hier mit seinem Verarbeitungsstatus. Erkannte Buchungen landen als Entwürfe in der Prüfansicht – nichts wird ungeprüft verbucht." />}>
          {(list) => (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Beleg</th><th className="hide-sm">Erkannt als</th><th className="hide-md">Gelesen mit</th><th>Status</th><th className="num hide-md" title="übernommen / erkannt">Übernommen</th><th style={{ width: 210 }} className="hide-sm"><span className="sr-only">Aktionen</span></th></tr></thead>
                <tbody>
                  {list.map((d) => (
                    <tr key={d.id}>
                      <td className="cell-name">
                        <Link className="name row" style={{ flexWrap: 'nowrap', gap: 8 }} to={`/import/${d.id}`}>
                          {d.source_kind === 'image' ? <FileImage size={16} aria-hidden style={{ flex: 'none', color: 'var(--text-3)' }} /> : <FileText size={16} aria-hidden style={{ flex: 'none', color: 'var(--text-3)' }} />}
                          <span className="truncate">{d.filename}</span>
                        </Link>
                        <span className="meta truncate" style={{ paddingLeft: 24 }} title={d.error ?? undefined}>{fmtRelative(d.created_at)}{d.error ? ` · ${d.error}` : ''}<span className="show-sm">{d.detected_doc_type ? ` · ${docTypeLabel(d.detected_doc_type)}` : ''}</span></span>
                      </td>
                      <td className="hide-sm muted">{d.detected_doc_type || d.detected_institution ? <>{docTypeLabel(d.detected_doc_type)}<span className="meta">{institutionLabel(d.detected_institution)}</span></> : <span className="faint">–</span>}</td>
                      <td className="hide-md muted">{d.parser ?? '–'}{d.processing_ms != null && <span className="meta">{fmtNumber(d.processing_ms / 1000, 1)} s</span>}</td>
                      <td><StatusBadge d={d} /></td>
                      <td className="num hide-md">{d.draft_count ? <>{d.accepted_count ?? d.draft_count - d.pending_count} / {d.draft_count}{d.duplicate_count ? <span className="meta">{d.duplicate_count} {d.duplicate_count === 1 ? 'Duplikat' : 'Duplikate'}</span> : null}</> : '–'}</td>
                      <td className="num hide-sm nowrap">
                        {d.status === 'review' && <Link className="btn btn--sm" to={`/import/${d.id}`}>Prüfen</Link>}
                        {d.status === 'failed' && <button className="btn btn--sm" disabled={reprocess.isPending} onClick={() => reprocess.mutate(d.id, { onSuccess: () => toast.ok('Beleg wird erneut verarbeitet'), onError: (e) => toast.error(e) })}><RotateCw size={14} aria-hidden /> Erneut verarbeiten</button>}
                        {d.status === 'imported' && <Link className="btn btn--ghost btn--sm" to={`/import/${d.id}`}>Ansehen</Link>}
                        <ConfirmButton label="" icon={<Trash2 size={15} aria-hidden />} confirmLabel="Beleg löschen?" onConfirm={() => remove.mutate(d.id, { onSuccess: () => toast.ok('Beleg gelöscht'), onError: (e) => toast.error(e) })} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Async>
      </Panel>
    </div>
  )
}
