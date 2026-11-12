import { useMemo, useState, type FormEvent } from 'react'
import { ApiError, api } from '../api/client'
import { useAccounts, useApiMutation, useCategories, useInstruments } from '../api/hooks'
import type { Transaction, TransactionCreate, TxType } from '../api/types'
import { fmtMoney, parseDecimal, todayIso } from '../lib/format'
import { SECURITY_TX, TX_TYPE, TX_TYPES } from '../lib/labels'
import { useToast } from './Toast'
import { Field, Seg } from './ui'

const NEGATIVE: TxType[] = ['buy', 'withdrawal', 'fee', 'transfer_out']
const NO_AMOUNT: TxType[] = ['split', 'delivery_in', 'delivery_out']
const num = (s: string) => parseDecimal(s)
const str = (n: number | null | undefined) => (n == null ? '' : String(n).replace('.', ','))

export interface TxFormDefaults { account_id?: number; instrument_id?: number; type?: TxType }

export function TxEntry({ edit, defaults, onDone }: { edit?: Transaction; defaults?: TxFormDefaults; onDone: () => void }) {
  const [mode, setMode] = useState<'tx' | 'transfer'>('tx')
  return (
    <div className="stack stack--sm">
      {!edit && <Seg label="Art der Erfassung" value={mode} onChange={setMode} options={[{ value: 'tx', label: 'Buchung' }, { value: 'transfer', label: 'Umbuchung zwischen Konten' }]} />}
      {mode === 'tx' ? <TxForm edit={edit} defaults={defaults} onDone={onDone} /> : <TransferForm defaults={defaults} onDone={onDone} />}
    </div>
  )
}

function TxForm({ edit, defaults, onDone }: { edit?: Transaction; defaults?: TxFormDefaults; onDone: () => void }) {
  const accounts = useAccounts()
  const categories = useCategories()
  const instruments = useInstruments()
  const toast = useToast()
  const [type, setType] = useState<TxType>(edit?.type ?? defaults?.type ?? 'withdrawal')
  const [accountId, setAccountId] = useState<string>(String(edit?.account_id ?? defaults?.account_id ?? ''))
  const [date, setDate] = useState(edit?.booking_date ?? todayIso())
  const [instrumentId, setInstrumentId] = useState<string>(String(edit?.instrument_id ?? defaults?.instrument_id ?? ''))
  const [isin, setIsin] = useState('')
  const [newName, setNewName] = useState('')
  const [dupBody, setDupBody] = useState<TransactionCreate | null>(null)
  const [quantity, setQuantity] = useState(str(edit?.quantity))
  const [price, setPrice] = useState(str(edit?.price))
  const [fees, setFees] = useState(edit?.fees ? str(edit.fees) : '')
  const [taxes, setTaxes] = useState(edit?.taxes ? str(edit.taxes) : '')
  const [amount, setAmount] = useState(edit ? str(type === 'tax' ? edit.amount : Math.abs(edit.amount)) : '')
  const [counterparty, setCounterparty] = useState(edit?.counterparty ?? '')
  const [description, setDescription] = useState(edit?.description ?? '')
  const [categoryId, setCategoryId] = useState<string>(String(edit?.category_id ?? ''))
  const [errors, setErrors] = useState<Record<string, string>>({})

  const isSecurity = SECURITY_TX.includes(type)
  const accountOptions = useMemo(() => (accounts.data ?? []).filter((a) => a.is_active && (isSecurity ? a.type === 'brokerage' : a.type !== 'brokerage')), [accounts.data, isSecurity])
  const validAccount = accountOptions.some((a) => String(a.id) === accountId) ? accountId : ''
  const computed = useMemo(() => {
    const q = num(quantity), p = num(price)
    if (q == null || p == null) return null
    const f = num(fees) ?? 0, t = num(taxes) ?? 0
    return type === 'buy' ? -(q * p + f + t) : type === 'sell' || type === 'dividend' ? q * p - f - t : null
  }, [quantity, price, fees, taxes, type])

  const save = useApiMutation((b: TransactionCreate & { allow_duplicate?: boolean }) => (edit ? api.updateTransaction(edit.id, b) : api.createTransaction(b)))
  const send = (body: TransactionCreate & { allow_duplicate?: boolean }) => save.mutate(body, {
    onSuccess: () => { toast.ok(edit ? 'Transaktion gespeichert' : 'Transaktion erfasst'); onDone() },
    // 409: an identical booking exists – explain and let the user decide instead of a dead end
    onError: (er) => { if (er instanceof ApiError && er.status === 409 && !edit) setDupBody(body); else toast.error(er, 'Speichern fehlgeschlagen:') },
  })

  function submit(e: FormEvent) {
    e.preventDefault()
    const err: Record<string, string> = {}
    if (!validAccount) err.account = 'Bitte ein Konto wählen.'
    if (!date) err.date = 'Bitte ein Datum angeben.'
    const amt = num(amount)
    if (isSecurity) {
      if (!instrumentId && !/^[A-Z]{2}[A-Z0-9]{9}\d$/.test(isin.trim().toUpperCase())) err.instrument = 'Wertpapier wählen oder eine gültige ISIN (12 Zeichen) eingeben.'
      if (!(num(quantity) ?? 0) || (num(quantity) ?? 0) <= 0) err.quantity = type === 'split' ? 'Split-Faktor angeben, z. B. 4 für 1:4.' : 'Stückzahl größer 0 angeben.'
      if ((type === 'buy' || type === 'sell') && num(price) == null && amt == null) err.price = 'Kurs oder Gesamtbetrag angeben.'
      if (type === 'dividend' && amt == null && computed == null) err.amount = 'Gutschrift angeben.'
    } else if (amt == null || amt === 0) err.amount = 'Betrag angeben, z. B. 49,90.'
    setErrors(err)
    if (Object.keys(err).length) return
    const signed = amt == null ? null : type === 'tax' ? amt : NEGATIVE.includes(type) ? -Math.abs(amt) : Math.abs(amt)
    const body: TransactionCreate = {
      account_id: Number(validAccount), type, booking_date: date,
      instrument_id: isSecurity && instrumentId ? Number(instrumentId) : null,
      isin: isSecurity && !instrumentId ? isin.trim().toUpperCase() : null,
      name: isSecurity && !instrumentId && newName.trim() ? newName.trim() : undefined,
      quantity: isSecurity ? num(quantity) : null, price: isSecurity ? num(price) : null,
      fees: num(fees) ?? 0, taxes: num(taxes) ?? 0,
      amount: NO_AMOUNT.includes(type) ? 0 : signed ?? undefined,
      counterparty: counterparty.trim() || null, description: description.trim() || null,
      category_id: !isSecurity && categoryId ? Number(categoryId) : null,
    }
    setDupBody(null)
    send(body)
  }

  return (
    <form className="form-grid" onSubmit={submit} noValidate>
      <Field label="Typ">
        <select className="select" value={type} onChange={(e) => setType(e.target.value as TxType)}>
          {TX_TYPES.filter((t) => edit || (t !== 'transfer_in' && t !== 'transfer_out')).map((t) => <option key={t} value={t}>{TX_TYPE[t]}</option>)}
        </select>
      </Field>
      <Field label="Buchungstag" error={errors.date}>
        <input className="input" type="date" value={date} max="2100-12-31" onChange={(e) => setDate(e.target.value)} aria-invalid={!!errors.date} />
      </Field>
      <Field label={isSecurity ? 'Depot' : 'Konto'} error={errors.account} className="span-all" hint={isSecurity ? 'Der Gegenwert wird auf dem Verrechnungskonto des Depots gebucht.' : undefined}>
        <select className="select" value={validAccount} onChange={(e) => setAccountId(e.target.value)} aria-invalid={!!errors.account}>
          <option value="">Bitte wählen …</option>
          {accountOptions.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
      </Field>

      {isSecurity ? (
        <>
          <Field label="Wertpapier" className="span-all" error={errors.instrument}>
            <select className="select" value={instrumentId} onChange={(e) => setInstrumentId(e.target.value)}>
              <option value="">Neu per ISIN anlegen …</option>
              {(instruments.data ?? []).map((i) => <option key={i.id} value={i.id}>{i.name}{i.isin ? ` (${i.isin})` : ''}</option>)}
            </select>
          </Field>
          {!instrumentId && (
            <>
              <Field label="ISIN" hint="Unbekannte Wertpapiere werden automatisch angelegt.">
                <input className="input" value={isin} onChange={(e) => setIsin(e.target.value)} placeholder="z. B. DE0007164600" maxLength={12} autoCapitalize="characters" spellCheck={false} />
              </Field>
              <Field label="Name" hint="optional – sonst von Yahoo ergänzt">
                <input className="input" value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="z. B. SAP SE" />
              </Field>
            </>
          )}
          <Field label={type === 'split' ? 'Split-Faktor' : 'Stück'} error={errors.quantity}>
            <input className="input num" inputMode="decimal" value={quantity} onChange={(e) => setQuantity(e.target.value)} placeholder={type === 'split' ? '4' : '10'} aria-invalid={!!errors.quantity} />
          </Field>
          {type !== 'split' && (
            <Field label={type === 'dividend' ? 'Dividende je Stück (€)' : 'Kurs (€)'} error={errors.price}>
              <input className="input num" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} placeholder="0,00" aria-invalid={!!errors.price} />
            </Field>
          )}
          {!NO_AMOUNT.includes(type) && (
            <>
              <Field label="Gebühren (€)"><input className="input num" inputMode="decimal" value={fees} onChange={(e) => setFees(e.target.value)} placeholder="0,00" /></Field>
              <Field label="Steuern (€)"><input className="input num" inputMode="decimal" value={taxes} onChange={(e) => setTaxes(e.target.value)} placeholder="0,00" /></Field>
              <Field label={type === 'buy' ? 'Belastung gesamt (€)' : 'Gutschrift gesamt (€)'} className="span-all" error={errors.amount}
                hint={computed != null ? `Leer lassen für den berechneten Betrag: ${fmtMoney(computed, { signed: true })}` : 'Optional – wird aus Stück × Kurs ± Gebühren/Steuern berechnet.'}>
                <input className="input num" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder={computed != null ? str(Math.round(Math.abs(computed) * 100) / 100) : '0,00'} />
              </Field>
            </>
          )}
        </>
      ) : (
        <>
          <Field label="Betrag (€)" error={errors.amount} hint={type === 'tax' ? 'Negativ = Steuerzahlung, positiv = Erstattung.' : NEGATIVE.includes(type) ? 'Wird als Abgang gebucht.' : 'Wird als Zugang gebucht.'}>
            <input className="input num" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="0,00" aria-invalid={!!errors.amount} />
          </Field>
          <Field label="Kategorie">
            <select className="select" value={categoryId} onChange={(e) => setCategoryId(e.target.value)}>
              <option value="">Ohne Kategorie</option>
              {(categories.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </Field>
          <Field label="Empfänger / Auftraggeber" className="span-all">
            <input className="input" value={counterparty} onChange={(e) => setCounterparty(e.target.value)} placeholder="z. B. REWE Markt GmbH" />
          </Field>
        </>
      )}
      <Field label="Verwendungszweck / Notiz" className="span-all">
        <input className="input" value={description} onChange={(e) => setDescription(e.target.value)} />
      </Field>
      {dupBody && (
        <div className="notice notice--warn span-all" role="alert">
          <div><strong>Diese Buchung gibt es schon.</strong> Im Journal steht bereits eine Buchung mit gleichem Konto, Typ, Datum und Betrag. Wenn es wirklich eine zweite ist (z. B. zwei gleiche Kartenzahlungen am selben Tag), lässt sie sich trotzdem speichern.</div>
          <div className="notice__actions"><button className="btn btn--sm" type="button" disabled={save.isPending} onClick={() => send({ ...dupBody, allow_duplicate: true })}>Trotzdem speichern</button></div>
        </div>
      )}
      <div className="form-actions span-all">
        <button className="btn btn--primary" type="submit" disabled={save.isPending}>{save.isPending ? 'Speichert …' : edit ? 'Änderungen speichern' : 'Transaktion erfassen'}</button>
        <button className="btn" type="button" onClick={onDone}>Abbrechen</button>
      </div>
    </form>
  )
}

function TransferForm({ defaults, onDone }: { defaults?: TxFormDefaults; onDone: () => void }) {
  const accounts = useAccounts()
  const toast = useToast()
  const cashAccounts = (accounts.data ?? []).filter((a) => a.is_active && a.type !== 'brokerage')
  const [from, setFrom] = useState(String(defaults?.account_id ?? ''))
  const [to, setTo] = useState('')
  const [amount, setAmount] = useState('')
  const [date, setDate] = useState(todayIso())
  const [description, setDescription] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const save = useApiMutation(api.transfer)
  const validFrom = cashAccounts.some((a) => String(a.id) === from) ? from : ''

  function submit(e: FormEvent) {
    e.preventDefault()
    const err: Record<string, string> = {}
    const amt = num(amount)
    if (!validFrom) err.from = 'Quellkonto wählen.'
    if (!to) err.to = 'Zielkonto wählen.'
    else if (to === validFrom) err.to = 'Quell- und Zielkonto müssen verschieden sein.'
    if (amt == null || amt <= 0) err.amount = 'Betrag größer 0 angeben.'
    setErrors(err)
    if (Object.keys(err).length) return
    save.mutate({ from_account_id: Number(validFrom), to_account_id: Number(to), amount: amt!, date, description: description.trim() || 'Umbuchung' },
      { onSuccess: () => { toast.ok('Umbuchung erfasst'); onDone() }, onError: (er) => toast.error(er, 'Umbuchung fehlgeschlagen:') })
  }
  return (
    <form className="form-grid" onSubmit={submit} noValidate>
      <Field label="Von" error={errors.from} className="span-all">
        <select className="select" value={validFrom} onChange={(e) => setFrom(e.target.value)}>
          <option value="">Bitte wählen …</option>
          {cashAccounts.map((a) => <option key={a.id} value={a.id}>{a.name} ({fmtMoney(a.balance)})</option>)}
        </select>
      </Field>
      <Field label="Nach" error={errors.to} className="span-all">
        <select className="select" value={to} onChange={(e) => setTo(e.target.value)}>
          <option value="">Bitte wählen …</option>
          {cashAccounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
      </Field>
      <Field label="Betrag (€)" error={errors.amount}><input className="input num" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="0,00" /></Field>
      <Field label="Datum"><input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} /></Field>
      <Field label="Verwendungszweck" className="span-all"><input className="input" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="z. B. Sparrate" /></Field>
      <div className="form-actions span-all">
        <button className="btn btn--primary" type="submit" disabled={save.isPending}>{save.isPending ? 'Speichert …' : 'Umbuchung erfassen'}</button>
        <button className="btn" type="button" onClick={onDone}>Abbrechen</button>
      </div>
    </form>
  )
}
