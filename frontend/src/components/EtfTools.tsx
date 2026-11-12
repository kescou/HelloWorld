import { useRef } from 'react'
import { CloudDownload, Trash2, Upload } from 'lucide-react'
import { api } from '../api/client'
import { cleanError, useApiMutation } from '../api/hooks'
import type { EtfRefreshResult } from '../api/types'
import { SOURCE } from '../lib/labels'
import { useToast } from './Toast'
import { ConfirmButton } from './ui'

export const ETF_KEYS = [['market', 'etf'], ['market', 'lookthrough'], ['portfolio', 'allocation'], ['market', 'status'], ['instruments']]
export const sourceLabel = (source: string | null | undefined) => (source ? SOURCE[source] ?? (source === 'custom_url' ? 'eigene Adresse' : source) : 'keine Daten')
/** What the user should know before uploading a holdings list. */
export const ETF_FORMAT_HINT = 'CSV, XLSX oder JSON von der Produktseite des Anbieters. Erkannt werden die Spalten Name, ISIN und Gewichtung (in %), optional Sektor, Land und Ticker; Kopfzeilen über der Tabelle stören nicht.'

function refreshMessage(r: EtfRefreshResult): { ok: boolean; text: string } {
  if (r.status === 'ok') return { ok: true, text: `${r.rows ?? 0} Positionen von ${sourceLabel(r.source)} geladen` }
  if (r.status === 'kept') return { ok: true, text: 'Die hochgeladene Liste bleibt erhalten.' }
  const tried = (r.attempts ?? []).filter((a) => !a.ok && !/^übersprungen/.test(a.error ?? ''))
  const why = cleanError(tried[0]?.error ?? r.detail ?? null)
  const offline = tried.length > 0 && tried.every((a) => /nicht erreichbar|Forbidden|timed? ?out|Connection|ProxyError|Max retries|resolve|HTTP 4|HTTP 5/i.test(a.error ?? ''))
  return { ok: false, text: `Keine Positionsliste erhalten${offline ? ' – Anbieter nicht erreichbar' : why ? ` – ${why.length > 110 ? why.slice(0, 109) + '…' : why}` : ''}. ${r.source ? 'Die vorhandenen Daten bleiben.' : 'Die Liste lässt sich als Datei hochladen.'}` }
}

/** The three things one can do with an ETF's holdings: fetch from the issuer, upload a file, delete. */
export function EtfActions({ id, name, source, hasHoldings, compact, wrap }: { id: number; name: string; source: string | null | undefined; hasHoldings: boolean; compact?: boolean; wrap?: boolean }) {
  const toast = useToast()
  const fileRef = useRef<HTMLInputElement>(null)
  // a manual upload is protected from the automatic job; clicking the button here is an explicit wish to replace it
  const refresh = useApiMutation(() => api.refreshEtfHoldings(id, source === 'manual'), ETF_KEYS)
  const upload = useApiMutation((file: File) => api.uploadEtfHoldings(id, file), ETF_KEYS)
  const remove = useApiMutation(() => api.deleteEtfHoldings(id), ETF_KEYS)
  const busy = refresh.isPending || upload.isPending || remove.isPending
  const cls = compact ? 'btn btn--sm btn--ghost' : 'btn btn--sm'
  return (
    <span className="row" style={{ gap: 4, flexWrap: compact && !wrap ? 'nowrap' : 'wrap', justifyContent: wrap ? 'flex-start' : 'flex-end' }}>
      <input ref={fileRef} type="file" accept=".csv,.xlsx,.json,text/csv" hidden aria-label={`Positionsliste für ${name} hochladen`}
        onChange={(e) => {
          const file = e.target.files?.[0]
          if (file) upload.mutate(file, { onSuccess: (r) => toast.ok(`${r.count ?? r.holdings.length} Positionen aus ${file.name} übernommen`), onError: (er) => toast.error(er, 'Import fehlgeschlagen:') })
          e.target.value = ''
        }} />
      <button type="button" className={cls} disabled={busy} title={source === 'manual' ? 'Ersetzt die hochgeladene Liste, wenn der Anbieter antwortet' : 'Positionsliste beim Emittenten abrufen (sonst Yahoo Top 10)'}
        onClick={() => refresh.mutate(undefined, { onSuccess: (r) => { const m = refreshMessage(r); if (m.ok) toast.ok(m.text); else toast.error(m.text) }, onError: (er) => toast.error(er, 'Abruf fehlgeschlagen:') })}>
        <CloudDownload size={14} aria-hidden /> {refresh.isPending ? 'Lädt …' : 'Vom Anbieter laden'}
      </button>
      <button type="button" className={cls} disabled={busy} title={ETF_FORMAT_HINT} onClick={() => fileRef.current?.click()}>
        <Upload size={14} aria-hidden /> {upload.isPending ? 'Lädt hoch …' : compact ? 'Hochladen' : 'Positionsliste hochladen'}
      </button>
      {hasHoldings && <ConfirmButton label={compact ? '' : 'Löschen'} icon={<Trash2 size={14} aria-hidden />} confirmLabel="Positionsdaten löschen?" disabled={busy}
        onConfirm={() => remove.mutate(undefined, { onSuccess: () => toast.ok(`Positionsdaten von ${name} gelöscht`), onError: (er) => toast.error(er, 'Löschen fehlgeschlagen:') })} />}
    </span>
  )
}
