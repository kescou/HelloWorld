import { useMutation, useQuery, useQueryClient, type QueryKey } from '@tanstack/react-query'
import { api } from './client'
import type * as T from './types'

export const useAccounts = () => useQuery({ queryKey: ['accounts'], queryFn: api.accounts })
export const useAccount = (id: number) => useQuery({ queryKey: ['accounts', id], queryFn: () => api.account(id), enabled: Number.isFinite(id) })
export const useCategories = () => useQuery({ queryKey: ['categories'], queryFn: api.categories, staleTime: 5 * 60_000 })
export const useTransactions = (f: T.TransactionFilters) => useQuery({ queryKey: ['transactions', f], queryFn: () => api.transactions(f), placeholderData: (prev) => prev })
export const useSummary = () => useQuery({ queryKey: ['portfolio', 'summary'], queryFn: api.summary })
export const useHoldings = (accountId?: number) => useQuery({ queryKey: ['portfolio', 'holdings', accountId ?? null], queryFn: () => api.holdings(accountId) })
export const useHistory = (range: T.Range, accountId?: number) => useQuery({ queryKey: ['portfolio', 'history', range, accountId ?? null], queryFn: () => api.history(range, accountId), placeholderData: (p) => p })
export const usePerformance = (range: T.Range) => useQuery({ queryKey: ['portfolio', 'performance', range], queryFn: () => api.performance(range), placeholderData: (p) => p })
export const useAllocation = (by: T.AllocationBy, lookthrough: boolean) => useQuery({ queryKey: ['portfolio', 'allocation', by, lookthrough], queryFn: () => api.allocation(by, lookthrough), placeholderData: (p) => p })
export const useDividends = (year?: number) => useQuery({ queryKey: ['analytics', 'dividends', year ?? null], queryFn: () => api.dividends(year), placeholderData: (p) => p })
export const useCashflow = (months: number) => useQuery({ queryKey: ['analytics', 'cashflow', months], queryFn: () => api.cashflow(months), placeholderData: (p) => p })
export const useExpenses = (from?: string, to?: string) => useQuery({ queryKey: ['analytics', 'expenses', from, to], queryFn: () => api.expensesByCategory(from, to), placeholderData: (p) => p })
export const useForecast = (p: { years: number; monthly_savings: number; expected_return: number }) => useQuery({ queryKey: ['analytics', 'forecast', p], queryFn: () => api.forecast(p), placeholderData: (prev) => prev })
export const useRealizedGains = (year?: number) => useQuery({ queryKey: ['analytics', 'realized', year ?? null], queryFn: () => api.realizedGains(year) })
export const useInstruments = (q: { q?: string; watchlist?: boolean } = {}) => useQuery({ queryKey: ['instruments', q], queryFn: () => api.instruments(q) })
export const useInstrument = (id: number) => useQuery({ queryKey: ['instruments', id], queryFn: () => api.instrument(id), enabled: Number.isFinite(id) })
export const useQuotes = (ids: number[]) => useQuery({ queryKey: ['market', 'quotes', ids], queryFn: () => api.quotes(ids), enabled: ids.length > 0 })
export const usePriceHistory = (id: number, range: T.Range) => useQuery({ queryKey: ['market', 'history', id, range], queryFn: () => api.priceHistory(id, range), placeholderData: (p) => p, enabled: Number.isFinite(id) })
export const useEvents = (q: { from?: string; to?: string; types?: string }) => useQuery({ queryKey: ['market', 'events', q], queryFn: () => api.events(q), placeholderData: (p) => p })
export const useFundamentals = (id: number, enabled = true) => useQuery({ queryKey: ['market', 'fundamentals', id], queryFn: () => api.fundamentals(id), enabled: enabled && Number.isFinite(id), staleTime: 10 * 60_000, retry: false })
export const useEtfHoldings = (id: number, enabled = true) => useQuery({ queryKey: ['market', 'etf', id], queryFn: () => api.etfHoldings(id), enabled, retry: false })
export const useLookthrough = (accountId?: number) => useQuery({ queryKey: ['market', 'lookthrough', accountId ?? null], queryFn: () => api.lookthrough(accountId), placeholderData: (p) => p })
/** Polls every 1.5 s while a background refresh is running. */
export const useMarketStatus = () => useQuery({ queryKey: ['market', 'status'], queryFn: api.marketStatus, retry: false, refetchInterval: (q) => (q.state.data?.refresh?.running ? 1500 : false) })
export const useLlmStatus = () => useQuery({ queryKey: ['documents', 'llm'], queryFn: api.llmStatus, retry: false, staleTime: 60_000 })

const ACTIVE: T.DocumentStatus[] = ['uploaded', 'processing']
/** Polls every 2 s while any document is still being processed. */
export const useDocuments = () =>
  useQuery({ queryKey: ['documents', 'list'], queryFn: () => api.documents(), refetchInterval: (q) => (q.state.data?.some((d) => ACTIVE.includes(d.status)) ? 2000 : false) })
export const useDocument = (id: number) =>
  useQuery({ queryKey: ['documents', 'detail', id], queryFn: () => api.document(id), enabled: Number.isFinite(id), refetchInterval: (q) => (q.state.data && ACTIVE.includes(q.state.data.status) ? 2000 : false) })

/** Mutation helper that invalidates the given query-key prefixes on success. Ledger changes affect almost everything. */
export const LEDGER_KEYS: QueryKey[] = [['accounts'], ['transactions'], ['portfolio'], ['analytics'], ['market', 'lookthrough'], ['market', 'events'], ['instruments']]
export function useApiMutation<V, R>(fn: (v: V) => Promise<R>, invalidate: QueryKey[] = LEDGER_KEYS) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: async () => { await Promise.all(invalidate.map((k) => qc.invalidateQueries({ queryKey: k }))) },
  })
}

/** Starts market-data refreshes as background jobs; progress comes from the polled `/market/status`. */
export function useMarketRefresh() {
  const qc = useQueryClient()
  const status = useMarketStatus()
  const start = useMutation({
    mutationFn: (scope: T.RefreshScope) => api.refreshBackground(scope),
    onSuccess: (job) => {
      // show "läuft …" at once and let the status query take over (it polls while `refresh.running`)
      qc.setQueryData<T.MarketStatus>(['market', 'status'], (old) => (old ? { ...old, refresh: { running: true, current: job, last: old.refresh?.last ?? null } } : old))
      void qc.invalidateQueries({ queryKey: ['market', 'status'] })
    },
  })
  const refresh = status.data?.refresh
  return { start: start.mutate, starting: start.isPending, running: start.isPending || !!refresh?.running, current: refresh?.running ? refresh.current : null, last: refresh?.last ?? null }
}

/** Human summary of a finished refresh job: which steps failed and (shortened) why. */
export function summarizeRefresh(job: T.RefreshJob | null | undefined, label: (scope: string) => string): { ok: boolean; text: string; failed: { scope: string; error: string | null }[] } {
  const result = job?.result
  if (!job || !result) return { ok: false, text: 'Aktualisierung ohne Ergebnis beendet.', failed: [] }
  const steps: [string, T.RefreshResult][] = result.jobs ? Object.entries(result.jobs) : [[result.scope, result]]
  const failed = steps.filter(([, r]) => r.status !== 'ok').map(([scope, r]) => ({ scope, error: cleanError(r.errors?.[0]?.error) }))
  if (!failed.length) return { ok: true, text: `${label(job.scope)} aktualisiert`, failed }
  const names = failed.map((f) => label(f.scope)).join(', ')
  const offline = failed.some((f) => /nicht erreichbar|offline|failed|Forbidden|timed? ?out|Connection/i.test(f.error ?? ''))
  if (failed.length === steps.length) return { ok: false, text: `${label(job.scope)} nicht aktualisiert${offline ? ' – Datenquelle nicht erreichbar' : ''}. Es gelten die gespeicherten Werte.`, failed }
  return { ok: false, text: `Teilweise aktualisiert. Fehlgeschlagen: ${names}.`, failed }
}
/** "ProviderError: history X: keine Kursdaten (…)" → "keine Kursdaten (…)" */
export function cleanError(e: string | null | undefined): string | null {
  if (!e) return null
  return e.replace(/^\w*(Error|Exception):\s*/, '').replace(/^(history|profile|fundamentals|quote|fast_info|info|search)\s+[^:]+:\s*/, '').trim()
}
