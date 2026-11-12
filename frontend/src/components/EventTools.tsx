import { useRef, useState, type FormEvent } from 'react'
import { FileUp } from 'lucide-react'
import { api } from '../api/client'
import { useApiMutation, useInstruments } from '../api/hooks'
import type { EventType, IcsImportInput, MarketEvent, MarketEventInput } from '../api/types'
import { parseDecimal } from '../lib/format'
import { EVENT_TYPE } from '../lib/labels'
import { useToast } from './Toast'
import { ConfirmButton, Field, Seg } from './ui'

const EVENT_KEYS = [['market', 'events'], ['analytics', 'dividends']]
/** Events the user owns (entered by hand or imported from a calendar file) – only these can be edited and deleted. */
export const isOwnEvent = (e: Pick<MarketEvent, 'source'>) => e.source === 'manual' || e.source === 'ics'

function InstrumentSelect({ value, onChange, error }: { value: string; onChange: (v: string) => void; error?: string }) {
  const instruments = useInstruments()
  return (
    <Field label="Wertpapier" className="span-all" error={error} hint={instruments.data && instruments.data.length === 0 ? 'Noch keine Wertpapiere vorhanden – zuerst eines kaufen oder auf die Watchlist setzen.' : undefined}>
      <select className="select" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Bitte wählen …</option>
        {(instruments.data ?? []).map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
      </select>
    </Field>
  )
}

/** Create a manual event or edit/delete an own one. `instrumentId` fixes the instrument (Wertpapier-Detail). */
export function EventForm({ onDone, defaultDate, edit, instrumentId: fixedId }: { onDone: () => void; defaultDate: string; edit?: MarketEvent; instrumentId?: number }) {
  const instruments = useInstruments()
  const toast = useToast()
  const [instrumentId, setInstrumentId] = useState(String(edit?.instrument_id ?? fixedId ?? ''))
  const [type, setType] = useState<EventType>(edit?.type ?? 'earnings')
  const [date, setDate] = useState(edit?.date ?? defaultDate)
  const [title, setTitle] = useState(edit?.title ?? '')
  const [amount, setAmount] = useState(edit?.amount != null ? String(edit.amount).replace('.', ',') : '')
  const [url, setUrl] = useState(edit?.url ?? '')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const save = useApiMutation((b: MarketEventInput) => (edit ? api.updateEvent(edit.id, b) : api.createEvent(b)), EVENT_KEYS)
  const remove = useApiMutation((id: number) => api.deleteEvent(id), EVENT_KEYS)
  const isDiv = type === 'ex_dividend' || type === 'dividend_payment'

  function submit(e: FormEvent) {
    e.preventDefault()
    const err: Record<string, string> = {}
    if (!instrumentId) err.instrument = 'Wertpapier wählen.'
    if (!date) err.date = 'Datum angeben.'
    if (url.trim() && !/^https?:\/\//i.test(url.trim())) err.url = 'Vollständige Adresse mit https:// angeben.'
    setErrors(err)
    if (Object.keys(err).length) return
    const currency = isDiv ? edit?.currency ?? instruments.data?.find((i) => i.id === Number(instrumentId))?.currency ?? 'EUR' : null
    save.mutate({ instrument_id: Number(instrumentId), type, date, title: title.trim() || null, amount: isDiv ? parseDecimal(amount) : null, currency, url: url.trim() || null },
      { onSuccess: () => { toast.ok(edit ? 'Termin gespeichert' : 'Termin eingetragen'); onDone() }, onError: (er) => toast.error(er, 'Termin konnte nicht gespeichert werden:') })
  }
  return (
    <form className="form-grid" onSubmit={submit} noValidate>
      {edit || fixedId
        ? <p className="small muted span-all">{edit?.instrument_name ?? instruments.data?.find((i) => i.id === fixedId)?.name ?? ''}</p>
        : <InstrumentSelect value={instrumentId} onChange={setInstrumentId} error={errors.instrument} />}
      <Field label="Art"><select className="select" value={type} onChange={(e) => setType(e.target.value as EventType)}>{(Object.keys(EVENT_TYPE) as EventType[]).map((k) => <option key={k} value={k}>{EVENT_TYPE[k]}</option>)}</select></Field>
      <Field label="Datum" error={errors.date}><input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} /></Field>
      {isDiv && <Field label="Dividende je Stück" hint="in der Währung des Wertpapiers"><input className="input num" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="0,00" /></Field>}
      <Field label="Titel" className="span-all" hint="optional, z. B. „Capital Markets Day“"><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
      <Field label="Link" className="span-all" error={errors.url} hint="optional, z. B. zur IR-Seite"><input className="input" type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://" /></Field>
      <div className="form-actions span-all">
        <button className="btn btn--primary" type="submit" disabled={save.isPending}>{save.isPending ? 'Speichert …' : edit ? 'Termin speichern' : 'Termin eintragen'}</button>
        <button className="btn" type="button" onClick={onDone}>Abbrechen</button>
        {edit && <span style={{ marginLeft: 'auto' }}><ConfirmButton small={false} label="Termin löschen" confirmLabel="Wirklich löschen?" disabled={remove.isPending}
          onConfirm={() => remove.mutate(edit.id, { onSuccess: () => { toast.ok('Termin gelöscht'); onDone() }, onError: (er) => toast.error(er, 'Löschen fehlgeschlagen:') })} /></span>}
      </div>
    </form>
  )
}

type IcsMode = 'file' | 'text' | 'url'
/** Import an investor-relations calendar (ICS): upload the file, paste its text or give its address. */
export function IcsImport({ onDone, instrumentId: fixedId }: { onDone: () => void; instrumentId?: number }) {
  const toast = useToast()
  const [instrumentId, setInstrumentId] = useState(String(fixedId ?? ''))
  const [mode, setMode] = useState<IcsMode>('file')
  const [file, setFile] = useState<File | null>(null)
  const [text, setText] = useState('')
  const [url, setUrl] = useState('')
  const [onlyFuture, setOnlyFuture] = useState(true)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const fileRef = useRef<HTMLInputElement>(null)
  const run = useApiMutation((b: IcsImportInput) => api.importIcs(b), EVENT_KEYS)

  function submit(e: FormEvent) {
    e.preventDefault()
    const err: Record<string, string> = {}
    if (!instrumentId) err.instrument = 'Wertpapier wählen.'
    if (mode === 'file' && !file) err.source = 'Bitte eine .ics-Datei auswählen.'
    if (mode === 'text' && !/BEGIN:VEVENT/i.test(text)) err.source = 'Der Text enthält keinen Termin (BEGIN:VEVENT fehlt).'
    if (mode === 'url' && !/^(https?|webcal):\/\/\S+/i.test(url.trim())) err.source = 'Adresse mit https:// oder webcal:// angeben.'
    setErrors(err)
    if (Object.keys(err).length) return
    run.mutate({ instrument_id: Number(instrumentId), file: mode === 'file' ? file : null, text: mode === 'text' ? text : null, url: mode === 'url' ? url.trim() : null, only_future: onlyFuture }, {
      onSuccess: (r) => {
        toast.ok(r.imported === 0 ? `${r.parsed} Termine gelesen, keiner davon neu${onlyFuture ? ' oder in der Zukunft' : ''}` : `${r.imported} ${r.imported === 1 ? 'Termin' : 'Termine'} importiert${r.parsed > r.imported ? ` (${r.parsed} gelesen)` : ''}`)
        onDone()
      },
      onError: (er) => setErrors({ source: er instanceof Error ? er.message : 'Import fehlgeschlagen.' }),
    })
  }
  return (
    <form className="form-grid" onSubmit={submit} noValidate>
      <p className="small muted span-all">Viele Unternehmen bieten ihren Finanzkalender auf der Investor-Relations-Seite als Kalenderdatei (.ics) an. Die Termine werden dem gewählten Wertpapier zugeordnet und lassen sich danach bearbeiten oder löschen.</p>
      {!fixedId && <InstrumentSelect value={instrumentId} onChange={setInstrumentId} error={errors.instrument} />}
      <div className="field span-all">
        <span className="field__label">Quelle</span>
        <Seg<IcsMode> label="Quelle des Kalenders" value={mode} onChange={(m) => { setMode(m); setErrors({}) }} options={[{ value: 'file', label: 'Datei' }, { value: 'text', label: 'Text einfügen' }, { value: 'url', label: 'Adresse' }]} />
      </div>
      {mode === 'file' && (
        <div className="field span-all">
          <span className="field__label">Kalenderdatei</span>
          <input ref={fileRef} type="file" accept=".ics,.ical,text/calendar" hidden onChange={(e) => { setFile(e.target.files?.[0] ?? null); setErrors({}) }} />
          <div className="row"><button type="button" className="btn" onClick={() => fileRef.current?.click()}><FileUp size={15} aria-hidden /> Datei wählen</button><span className="small muted truncate">{file ? file.name : 'Noch keine Datei gewählt (.ics)'}</span></div>
          {errors.source && <span className="field__error" role="alert">{errors.source}</span>}
        </div>
      )}
      {mode === 'text' && <Field label="Inhalt der Kalenderdatei" className="span-all" error={errors.source} hint="Den gesamten Text einfügen, von BEGIN:VCALENDAR bis END:VCALENDAR."><textarea className="textarea" rows={8} value={text} onChange={(e) => setText(e.target.value)} placeholder={'BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART;VALUE=DATE:20270218\nSUMMARY:Geschäftsbericht 2026\nEND:VEVENT\nEND:VCALENDAR'} spellCheck={false} /></Field>}
      {mode === 'url' && <Field label="Adresse des Kalenders" className="span-all" error={errors.source} hint="Der Server lädt die Datei einmalig von dieser Adresse – dafür ist eine Internetverbindung nötig."><input className="input" type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://… .ics" spellCheck={false} /></Field>}
      <label className="check span-all"><input type="checkbox" checked={onlyFuture} onChange={(e) => setOnlyFuture(e.target.checked)} /> Nur künftige Termine übernehmen</label>
      <div className="form-actions span-all">
        <button className="btn btn--primary" type="submit" disabled={run.isPending}>{run.isPending ? 'Importiert …' : 'Termine importieren'}</button>
        <button className="btn" type="button" onClick={onDone}>Abbrechen</button>
      </div>
    </form>
  )
}
