import { Link } from 'react-router-dom'
import { AlertTriangle } from 'lucide-react'
import { api } from '../api/client'
import { useApiMutation, useSummary } from '../api/hooks'
import type { UnbookedSplit } from '../api/types'
import { fmtDate, fmtNumber, fmtQty } from '../lib/format'
import { useToast } from './Toast'

const ratioText = (r: number) => (r >= 1 ? `${fmtNumber(r, 4)}:1` : `1:${fmtNumber(1 / r, 4)}`)

/** Things that make the depot figures wrong until the user acts: unbooked splits, deliveries without cost basis.
 *  `instrumentId` narrows it to one security (Wertpapier-Detail). Renders nothing when all is fine. */
export function PortfolioWarnings({ instrumentId }: { instrumentId?: number }) {
  const summary = useSummary()
  const toast = useToast()
  const book = useApiMutation((s: UnbookedSplit) => api.bookSplit({ instrument_id: s.instrument_id, date: s.date, ratio: s.ratio }))
  const mine = <X extends { instrument_id: number }>(list: X[] | undefined) => (list ?? []).filter((x) => instrumentId == null || x.instrument_id === instrumentId)
  const splits = mine(summary.data?.unbooked_splits)
  const missing = mine(summary.data?.missing_cost)
  const oversold = instrumentId == null ? (summary.data?.warnings ?? []).filter((w) => /übersteigt Bestand/.test(w)) : []
  if (!splits.length && !missing.length && !oversold.length) return null
  return (
    <div className="stack stack--sm" role="status">
      {splits.map((s) => (
        <div className="notice notice--warn" key={s.event_id}>
          <AlertTriangle size={17} aria-hidden />
          <div><strong>Split nicht gebucht: {s.name} {ratioText(s.ratio)} am {fmtDate(s.date)}.</strong> Die Kurse sind bereits auf den Split umgerechnet, der Bestand noch nicht – Stückzahl und Wert dieser Position sind deshalb um den Faktor {fmtNumber(s.ratio, 4)} {s.ratio >= 1 ? 'zu niedrig' : 'zu hoch'}. Wurden die Käufe schon mit den Stückzahlen nach dem Split erfasst, ist nichts zu tun.</div>
          <div className="notice__actions">
            <button className="btn btn--sm btn--primary" disabled={book.isPending}
              onClick={() => book.mutate(s, { onSuccess: (r) => toast.ok(`Split ${ratioText(s.ratio)} für ${s.name} gebucht${r.booked > 1 ? ` (${r.booked} Depots)` : ''}`), onError: (e) => toast.error(e, 'Split konnte nicht gebucht werden:') })}>
              {book.isPending ? 'Bucht …' : 'Split buchen'}
            </button>
          </div>
        </div>
      ))}
      {missing.map((m) => (
        <div className="notice notice--warn" key={m.transaction_id}>
          <AlertTriangle size={17} aria-hidden />
          <div><strong>Einstand fehlt: {m.name}.</strong> Die Einlieferung von {fmtQty(m.quantity)} Stück am {fmtDate(m.date)} hat keinen Kurs und keine passende Auslieferung aus einem anderen Depot. Der Einstand zählt solange als 0 €, der Buchgewinn ist entsprechend zu hoch.</div>
          <div className="notice__actions"><Link className="btn btn--sm btn--primary" to={`/transaktionen?buchung=${m.transaction_id}`}>Einstandskurs nachtragen</Link></div>
        </div>
      ))}
      {oversold.length > 0 && (
        <div className="notice notice--warn">
          <AlertTriangle size={17} aria-hidden />
          <div><strong>Es wurden mehr Stücke verkauft als gebucht waren.</strong> Vermutlich fehlt ein Kauf oder eine Einlieferung; der Bestand wird ab dem Verkauf als 0 geführt. <Link to="/transaktionen?typ=sell">Verkäufe prüfen</Link></div>
        </div>
      )}
    </div>
  )
}
