import { Link } from 'react-router-dom'
import { Pencil, Trash2 } from 'lucide-react'
import type { Transaction } from '../api/types'
import { fmtDate, fmtDateShort, fmtMoney, fmtQty } from '../lib/format'
import { TX_TYPE } from '../lib/labels'
import { ConfirmButton, Money } from './ui'

export function txTitle(t: Transaction): string {
  return t.instrument_name ?? t.counterparty ?? t.description ?? TX_TYPE[t.type]
}

export function TxTable({ items, showAccount = true, onEdit, onDelete }: { items: Transaction[]; showAccount?: boolean; onEdit?: (t: Transaction) => void; onDelete?: (t: Transaction) => void }) {
  const actions = !!(onEdit || onDelete)
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th style={{ width: 96 }}>Datum</th>
            <th>Buchung</th>
            {showAccount && <th className="hide-sm">Konto</th>}
            <th className="hide-md">Kategorie</th>
            <th className="num hide-md">Stück × Kurs</th>
            <th className="num">Betrag</th>
            {actions && <th style={{ width: 84 }}><span className="sr-only">Aktionen</span></th>}
          </tr>
        </thead>
        <tbody>
          {items.map((t) => {
            const title = txTitle(t)
            const detail = t.description && t.description !== title && t.description !== TX_TYPE[t.type] ? t.description : null
            return (
              <tr key={t.id}>
                <td className="nowrap muted"><span className="hide-sm">{fmtDate(t.booking_date)}</span><span className="show-sm">{fmtDateShort(t.booking_date)}</span></td>
                <td className="cell-name">
                  {t.instrument_id ? <Link className="name truncate" to={`/wertpapier/${t.instrument_id}`}>{title}</Link> : <span className="name truncate">{title}</span>}
                  <span className="meta truncate">
                    {TX_TYPE[t.type]}{detail ? ` · ${detail}` : ''}
                    {showAccount && <span className="show-sm"> · {t.account_name}</span>}
                  </span>
                </td>
                {showAccount && <td className="hide-sm muted nowrap">{t.account_name}</td>}
                <td className="hide-md muted">{t.category_name ?? (t.instrument_id ? '' : <span className="faint">ohne</span>)}</td>
                <td className="num hide-md muted">{t.quantity != null && t.price != null ? <span className="amt">{fmtQty(t.quantity)} × {fmtMoney(t.price)}</span> : ''}</td>
                <td className="num"><Money value={t.amount} tone /></td>
                {actions && (
                  <td className="num nowrap">
                    {onEdit && <button className="btn btn--ghost btn--sm btn--icon" onClick={() => onEdit(t)} aria-label={`${title} bearbeiten`} title="Bearbeiten"><Pencil size={15} aria-hidden /></button>}
                    {onDelete && <span className="hide-sm"><ConfirmButton label="" icon={<Trash2 size={15} aria-hidden />} confirmLabel="Löschen?" onConfirm={() => onDelete(t)} /></span>}
                  </td>
                )}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
