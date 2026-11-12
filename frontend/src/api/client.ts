import type * as T from './types'

export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(status: number, message: string, detail?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

type Query = Record<string, string | number | boolean | null | undefined>

/** Build `/api/...` URL; empty/undefined params are dropped. Always relative so FastAPI can serve the SPA. */
export function buildUrl(path: string, query?: Query): string {
  const url = '/api' + (path.startsWith('/') ? path : '/' + path)
  if (!query) return url
  const parts: string[] = []
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === '') continue
    parts.push(encodeURIComponent(k) + '=' + encodeURIComponent(String(v)))
  }
  return parts.length ? url + '?' + parts.join('&') : url
}

function messageFor(status: number, detail: unknown): string {
  if (typeof detail === 'string' && detail) return detail
  // 409 responses carry an object: {message, existing_id | transaction_id, ...}
  if (detail && typeof detail === 'object' && !Array.isArray(detail) && typeof (detail as { message?: unknown }).message === 'string') return (detail as { message: string }).message
  if (Array.isArray(detail) && detail.length) {
    // FastAPI validation errors: [{loc, msg, type}]
    return detail
      .map((d) => (d && typeof d === 'object' && 'msg' in d ? `${(d as { loc?: unknown[] }).loc?.slice(1).join('.') ?? ''}: ${(d as { msg: string }).msg}` : String(d)))
      .join('; ')
  }
  if (status === 0) return 'Server nicht erreichbar. Läuft das Backend?'
  if (status === 404) return 'Nicht gefunden.'
  if (status === 503) return 'Die Datenbank ist nicht erreichbar. Läuft PostgreSQL?'
  if (status >= 500) return 'Serverfehler. Bitte später erneut versuchen.'
  return `Anfrage fehlgeschlagen (${status}).`
}

export async function request<R>(method: string, path: string, opts: { query?: Query; body?: unknown; form?: FormData; raw?: boolean } = {}): Promise<R> {
  const init: RequestInit = { method, headers: { Accept: 'application/json' } }
  if (opts.form) init.body = opts.form
  else if (opts.body !== undefined) {
    init.body = JSON.stringify(opts.body)
    ;(init.headers as Record<string, string>)['Content-Type'] = 'application/json'
  }
  let res: Response
  try {
    res = await fetch(buildUrl(path, opts.query), init)
  } catch {
    throw new ApiError(0, messageFor(0, null))
  }
  if (!res.ok) {
    let detail: unknown = null
    try {
      const data = await res.json()
      detail = data && typeof data === 'object' && 'detail' in data ? (data as { detail: unknown }).detail : data
    } catch { /* non-JSON error body */ }
    throw new ApiError(res.status, messageFor(res.status, detail), detail)
  }
  if (opts.raw) return (await res.blob()) as R
  if (res.status === 204) return undefined as R
  const text = await res.text()
  return (text ? JSON.parse(text) : undefined) as R
}

const get = <R>(path: string, query?: Query) => request<R>('GET', path, { query })
const post = <R>(path: string, body?: unknown) => request<R>('POST', path, { body })
const patch = <R>(path: string, body?: unknown) => request<R>('PATCH', path, { body })
const del = <R = void>(path: string) => request<R>('DELETE', path)

export const api = {
  // accounts
  accounts: () => get<T.Account[]>('/accounts'),
  account: (id: number) => get<T.AccountDetail>(`/accounts/${id}`),
  createAccount: (b: T.AccountInput) => post<T.Account>('/accounts', b),
  updateAccount: (id: number, b: Partial<T.AccountInput>) => patch<T.Account>(`/accounts/${id}`, b),
  deleteAccount: (id: number) => del<{ deleted: boolean; deactivated: boolean; transaction_count: number }>(`/accounts/${id}`),
  // transactions
  transactions: (f: T.TransactionFilters = {}) => get<T.TransactionPage>('/transactions', { ...f }),
  createTransaction: (b: T.TransactionCreate & { allow_duplicate?: boolean }) => post<T.Transaction>('/transactions', b),
  updateTransaction: (id: number, b: Partial<T.TransactionCreate>) => patch<T.Transaction>(`/transactions/${id}`, b),
  deleteTransaction: (id: number) => del(`/transactions/${id}`),
  transfer: (b: T.TransferInput) => post<T.Transaction[]>('/transactions/transfer', b),
  transaction: (id: number) => get<T.Transaction>(`/transactions/${id}`),
  transferSuggestions: () => get<T.TransferSuggestion[]>('/transactions/transfer-suggestions'),
  confirmTransfer: (out_id: number, in_id: number) => post<{ out_id: number; in_id: number }>('/transactions/link-transfers/confirm', { out_id, in_id }),
  bookSplit: (b: { instrument_id: number; date: string; ratio: number }) => post<{ booked: number; transaction_ids: number[] }>('/portfolio/splits/book', b),
  // categories
  categories: () => get<T.Category[]>('/categories'),
  createCategory: (b: T.CategoryInput) => post<T.Category>('/categories', b),
  updateCategory: (id: number, b: Partial<T.CategoryInput>) => patch<T.Category>(`/categories/${id}`, b),
  deleteCategory: (id: number) => del(`/categories/${id}`),
  autoAssign: () => post<{ assigned?: number; uncategorized?: number } | undefined>('/categories/auto-assign'),
  // instruments
  instruments: (q: { q?: string; watchlist?: boolean } = {}) => get<T.Instrument[]>('/instruments', q),
  instrument: (id: number) => get<T.InstrumentDetail>(`/instruments/${id}`),
  createInstrument: (b: Partial<T.Instrument>) => post<T.Instrument>('/instruments', b),
  updateInstrument: (id: number, b: T.InstrumentUpdate) => patch<T.Instrument>(`/instruments/${id}`, b),
  // portfolio
  summary: () => get<T.PortfolioSummary>('/portfolio/summary'),
  holdings: (account_id?: number) => get<T.Holding[]>('/portfolio/holdings', { account_id }),
  history: (range: T.Range, account_id?: number) => get<T.HistoryPoint[]>('/portfolio/history', { range, account_id }),
  performance: (range: T.Range) => get<T.Performance>('/portfolio/performance', { range }),
  allocation: (by: T.AllocationBy, lookthrough: boolean) => get<T.AllocationSlice[]>('/portfolio/allocation', { by, lookthrough }),
  // analytics
  dividends: (year?: number) => get<T.DividendAnalytics>('/analytics/dividends', { year }),
  cashflow: (months = 12) => get<T.CashflowMonth[]>('/analytics/cashflow', { months }),
  expensesByCategory: (date_from?: string, date_to?: string) => get<T.ExpenseCategory[]>('/analytics/expenses-by-category', { date_from, date_to }),
  forecast: (p: { years: number; monthly_savings: number; expected_return: number }) => get<T.WealthForecast>('/analytics/forecast', p),
  realizedGains: (year?: number) => get<T.RealizedGain[]>('/analytics/realized-gains', { year }),
  taxes: (year?: number) => get<T.TaxOverview>('/analytics/taxes', { year }),
  recurring: () => get<T.Recurring>('/analytics/recurring'),
  // settings
  settings: () => get<T.AppSettings>('/settings'),
  updateSettings: (b: Partial<T.AppSettings>) => patch<T.AppSettings>('/settings', b),
  // market
  /** Synchronous for single scopes (returns the job result); `all` runs in the background and returns a RefreshJob to poll. */
  refresh: (scope: T.RefreshScope, instrument_ids?: number[]) => post<T.RefreshResult | T.RefreshJob>('/market/refresh', { scope, instrument_ids }),
  /** Starts the refresh in the background (any scope) and returns at once; progress and result via `/market/status` → `refresh`. */
  refreshBackground: (scope: T.RefreshScope, instrument_ids?: number[]) => request<T.RefreshJob>('POST', '/market/refresh', { query: { wait: false }, body: { scope, instrument_ids } }),
  refreshJob: (jobId: string) => get<T.RefreshJob>(`/market/refresh/${jobId}`),
  quotes: (ids: number[]) => get<T.Quote[]>('/market/quotes', { instrument_ids: ids.join(',') }),
  priceHistory: (id: number, range: T.Range) => get<{ date: string; close: number }[]>(`/market/history/${id}`, { range }),
  events: (q: { from?: string; to?: string; types?: string; instrument_id?: number } = {}) => get<T.MarketEvent[]>('/market/events', q),
  createEvent: (b: T.MarketEventInput) => post<T.MarketEvent>('/market/events', b),
  updateEvent: (id: number, b: T.MarketEventUpdate) => patch<T.MarketEvent>(`/market/events/${id}`, b),
  deleteEvent: (id: number) => del(`/market/events/${id}`),
  importIcs: (b: T.IcsImportInput) => {
    const form = new FormData()
    if (b.file) form.append('file', b.file)
    else if (b.text) form.append('text', b.text)
    else if (b.url) form.append('url', b.url)
    if (b.only_future) form.append('only_future', 'true')
    return request<T.IcsImportResult>('POST', '/market/events/import-ics', { query: { instrument_id: b.instrument_id }, form })
  },
  fundamentals: (id: number) => get<T.Fundamentals>(`/market/fundamentals/${id}`),
  etfHoldings: (id: number) => get<T.EtfHoldings>(`/market/etf/${id}/holdings`),
  uploadEtfHoldings: (id: number, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<T.EtfHoldings>('POST', `/market/etf/${id}/holdings/upload`, { form })
  },
  refreshEtfHoldings: (id: number, force = false) => request<T.EtfRefreshResult>('POST', `/market/etf/${id}/holdings/refresh`, { query: { force: force || undefined } }),
  deleteEtfHoldings: (id: number) => del(`/market/etf/${id}/holdings`),
  lookthrough: (account_id?: number) => get<T.Lookthrough>('/market/lookthrough', { account_id }),
  marketStatus: () => get<T.MarketStatus>('/market/status'),
  search: (q: string) => get<T.SearchResult[]>('/market/search', { q }),
  // documents
  uploadDocuments: (files: File[], account_id?: number) => {
    const form = new FormData()
    for (const f of files) form.append('files', f, f.name)
    if (account_id) form.append('account_id', String(account_id))
    return request<T.DocumentSummary[]>('POST', '/documents', { form })
  },
  pasteDocument: (text: string, account_id?: number) => post<T.DocumentSummary>('/documents/paste', { text, account_id }),
  documents: (status?: T.DocumentStatus) => get<T.DocumentSummary[]>('/documents', { status }),
  document: (id: number) => get<T.DocumentDetail>(`/documents/${id}`),
  documentFile: (id: number) => request<Blob>('GET', `/documents/${id}/file`, { raw: true }),
  updateDraft: (id: number, payload: T.DraftPayload) => patch<T.Draft>(`/documents/drafts/${id}`, { payload }),
  acceptDraft: (id: number, force = false) => request<T.Draft>('POST', `/documents/drafts/${id}/accept`, { query: { force: force || undefined } }),
  reopenDraft: (id: number) => patch<T.Draft>(`/documents/drafts/${id}`, { payload: {}, status: 'pending' }),
  rejectDraft: (id: number) => post<T.Draft>(`/documents/drafts/${id}/reject`),
  acceptAll: (id: number, include_flagged = false) => request<T.AcceptAllResult>('POST', `/documents/${id}/accept-all`, { query: { include_flagged: include_flagged || undefined } }),
  reprocess: (id: number, force_llm = false) => post<unknown>(`/documents/${id}/reprocess`, { force_llm }),
  deleteDocument: (id: number) => del(`/documents/${id}`),
  llmStatus: () => get<T.LlmStatus>('/documents/llm/status'),
}
export type Api = typeof api
