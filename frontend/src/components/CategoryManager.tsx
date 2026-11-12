import { useState, type FormEvent } from 'react'
import { Plus, Trash2, Wand2, X } from 'lucide-react'
import { api } from '../api/client'
import { useApiMutation, useCategories } from '../api/hooks'
import type { Category, CategoryKind } from '../api/types'
import { CATEGORY_KIND } from '../lib/labels'
import { useToast } from './Toast'
import { Async, ConfirmButton, EmptyState, Panel } from './ui'

const KEYS = [['categories'], ['transactions'], ['analytics']]

function RuleEditor({ c }: { c: Category }) {
  const toast = useToast()
  const [value, setValue] = useState('')
  const update = useApiMutation((rules: string[]) => api.updateCategory(c.id, { match_rules: rules }), KEYS)
  const rules = c.match_rules ?? []
  const add = (e: FormEvent) => {
    e.preventDefault()
    const v = value.trim().toLowerCase()
    if (!v || rules.includes(v)) return
    update.mutate([...rules, v], { onSuccess: () => setValue(''), onError: (er) => toast.error(er) })
  }
  return (
    <div className="chips">
      {rules.map((r) => (
        <span className="chip" key={r}>{r}
          <button type="button" aria-label={`Regel „${r}“ entfernen`} onClick={() => update.mutate(rules.filter((x) => x !== r), { onError: (er) => toast.error(er) })}><X size={12} aria-hidden /></button>
        </span>
      ))}
      <form onSubmit={add} className="row" style={{ gap: 4, flexWrap: 'nowrap' }}>
        <input className="input" style={{ height: 24, width: 130, fontSize: 'var(--fs-xs)' }} value={value} onChange={(e) => setValue(e.target.value)} placeholder="Textbaustein …" aria-label={`Neue Regel für ${c.name}`} />
        {value.trim() && <button className="btn btn--sm" style={{ height: 24 }} type="submit">Hinzufügen</button>}
      </form>
    </div>
  )
}

/** Categories with their auto-assign rules (substrings matched on counterparty / description). */
export function CategoryManager({ title = 'Kategorien und Regeln' }: { title?: string }) {
  const categories = useCategories()
  const toast = useToast()
  const [name, setName] = useState('')
  const [kind, setKind] = useState<CategoryKind>('expense')
  const create = useApiMutation(api.createCategory, KEYS)
  const remove = useApiMutation((id: number) => api.deleteCategory(id), KEYS)
  const assign = useApiMutation(() => api.autoAssign(), KEYS)

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return
    create.mutate({ name: name.trim(), kind, parent_id: null, color: null, match_rules: [] }, { onSuccess: () => { setName(''); toast.ok('Kategorie angelegt') }, onError: (er) => toast.error(er) })
  }
  return (
    <Panel title={title} flush hint="Regeln sind Textbausteine, die in Empfänger oder Verwendungszweck gesucht werden"
      actions={<button className="btn btn--sm" disabled={assign.isPending} onClick={() => assign.mutate(undefined, {
        onSuccess: (r) => toast.ok(r?.assigned != null ? `${r.assigned} ${r.assigned === 1 ? 'Buchung' : 'Buchungen'} kategorisiert` : 'Regeln angewendet'), onError: (er) => toast.error(er, 'Regeln konnten nicht angewendet werden:') })}>
        <Wand2 size={14} aria-hidden /> {assign.isPending ? 'Wendet an …' : 'Regeln auf offene Buchungen anwenden'}</button>}>
      <Async query={categories} what="Die Kategorien" empty={<EmptyState compact title="Noch keine Kategorien" text="Lege unten die erste Kategorie an, z. B. „Lebensmittel“ mit der Regel „rewe“." />}>
        {(list) => (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Kategorie</th><th className="hide-sm">Art</th><th>Regeln</th><th style={{ width: 44 }}><span className="sr-only">Aktionen</span></th></tr></thead>
              <tbody>
                {(['income', 'expense', 'investment', 'transfer'] as CategoryKind[]).flatMap((k) => list.filter((c) => c.kind === k)).map((c) => (
                  <tr key={c.id}>
                    <td style={{ verticalAlign: 'top', paddingTop: 10 }}><span className="name nowrap">{c.name}</span><span className="meta show-sm">{CATEGORY_KIND[c.kind]}</span></td>
                    <td className="hide-sm muted" style={{ verticalAlign: 'top', paddingTop: 10 }}>{CATEGORY_KIND[c.kind]}</td>
                    <td><RuleEditor c={c} /></td>
                    <td className="num" style={{ verticalAlign: 'top' }}><ConfirmButton label="" icon={<Trash2 size={15} aria-hidden />} confirmLabel="Löschen?" onConfirm={() => remove.mutate(c.id, { onSuccess: () => toast.ok(`Kategorie „${c.name}“ gelöscht`), onError: (er) => toast.error(er) })} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Async>
      <form onSubmit={submit} className="row" style={{ padding: '12px 16px', borderTop: '1px solid var(--border)' }}>
        <input className="input" style={{ flex: '1 1 160px', maxWidth: 260 }} value={name} onChange={(e) => setName(e.target.value)} placeholder="Neue Kategorie" aria-label="Name der neuen Kategorie" />
        <select className="select" style={{ width: 'auto' }} value={kind} onChange={(e) => setKind(e.target.value as CategoryKind)} aria-label="Art der Kategorie">
          {(Object.keys(CATEGORY_KIND) as CategoryKind[]).map((k) => <option key={k} value={k}>{CATEGORY_KIND[k]}</option>)}
        </select>
        <button className="btn" type="submit" disabled={!name.trim() || create.isPending}><Plus size={15} aria-hidden /> Kategorie anlegen</button>
      </form>
    </Panel>
  )
}
