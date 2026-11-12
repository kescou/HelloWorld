// Types mirroring docs/ARCHITECTURE.md (REST contract). Amounts are numbers in EUR unless noted.
// Percent fields (`*_pct`, `weight`, `share`, `savings_rate`, `twr`, `xirr`, yields, CAGR) are
// percent values (12.3 means 12,3 %), not fractions.

export type AccountType = 'checking' | 'savings' | 'brokerage' | 'credit_card' | 'other'
export type InstrumentType = 'stock' | 'etf' | 'fund' | 'bond' | 'crypto' | 'commodity' | 'other'
export type TxType =
  | 'buy' | 'sell' | 'dividend' | 'interest' | 'deposit' | 'withdrawal'
  | 'transfer_in' | 'transfer_out' | 'fee' | 'tax' | 'split' | 'delivery_in' | 'delivery_out'
export type CategoryKind = 'income' | 'expense' | 'transfer' | 'investment'
export type EventType = 'ex_dividend' | 'dividend_payment' | 'earnings' | 'agm' | 'investor_day' | 'split' | 'other'
export type DocumentStatus = 'uploaded' | 'processing' | 'review' | 'imported' | 'failed'
export type DraftStatus = 'pending' | 'accepted' | 'rejected' | 'duplicate'
export type Range = '1M' | '3M' | 'YTD' | '1Y' | '3Y' | '5Y' | 'MAX'
export type AllocationBy = 'type' | 'sector' | 'country' | 'account' | 'currency'

export interface Account {
  id: number
  name: string
  institution: string
  type: AccountType
  currency: string
  iban: string | null
  account_number?: string | null
  color: string | null
  opening_balance: number
  opening_date: string | null
  notes?: string | null
  balance: number
  market_value: number
  total_value: number
  settlement_account_id: number | null
  is_active: boolean
  interest_rate: number | null
  sort_order: number
}
/** Reconciliation against statement balances (`balance_snapshots`). */
export interface BalanceSnapshot {
  id?: number
  date: string
  balance: number
  /** ledger balance at the end of that day */
  computed_balance?: number | null
  /** balance − computed_balance */
  difference?: number | null
  source_document_id?: number | null
}
export interface AccountDetail extends Account {
  history: { date: string; value: number }[]
  snapshots?: BalanceSnapshot[]
}
export interface AccountInput {
  name: string
  institution: string
  type: AccountType
  currency?: string
  iban?: string | null
  color?: string | null
  opening_balance?: number
  opening_date?: string | null
  interest_rate?: number | null
  settlement_account_id?: number | null
  is_active?: boolean
  sort_order?: number
}

export interface Transaction {
  id: number
  account_id: number
  cash_account_id: number | null
  instrument_id: number | null
  type: TxType
  booking_date: string
  value_date: string | null
  quantity: number | null
  price: number | null
  amount: number
  fees: number
  taxes: number
  currency: string
  fx_rate: number | null
  category_id: number | null
  counterparty: string | null
  description: string | null
  transfer_peer_id: number | null
  source_document_id: number | null
  notes: string | null
  account_name: string
  instrument_name: string | null
  instrument_isin: string | null
  category_name: string | null
}
export interface TransactionCreate {
  account_id: number
  type: TxType
  booking_date: string
  value_date?: string | null
  instrument_id?: number | null
  isin?: string | null
  symbol?: string | null
  name?: string | null
  instrument_type?: InstrumentType | null
  quantity?: number | null
  price?: number | null
  amount?: number | null
  fees?: number
  taxes?: number
  currency?: string
  category_id?: number | null
  counterparty?: string | null
  description?: string | null
  notes?: string | null
  /** the amount is signed on purpose (fee refund): the server must not normalise the sign */
  keep_sign?: boolean
}
export interface TransactionFilters {
  account_id?: number
  instrument_id?: number
  type?: TxType
  category_id?: number
  q?: string
  date_from?: string
  date_to?: string
  limit?: number
  offset?: number
}
export interface TransactionPage { items: Transaction[]; total: number }
export interface TransferInput { from_account_id: number; to_account_id: number; amount: number; date: string; description?: string }

export interface Category {
  id: number
  name: string
  kind: CategoryKind
  parent_id: number | null
  color: string | null
  match_rules: string[] | null
  transaction_count?: number
}
export type CategoryInput = Omit<Category, 'id' | 'transaction_count'>

export interface Quote {
  instrument_id: number
  price: number
  previous_close: number | null
  change: number | null
  change_pct: number | null
  currency: string
  market_time: string | null
  updated_at: string
}
export interface Instrument {
  id: number
  isin: string | null
  wkn: string | null
  symbol: string | null
  name: string
  type: InstrumentType
  currency: string
  exchange: string | null
  sector: string | null
  industry: string | null
  country: string | null
  fund_provider: string | null
  ter: number | null
  distribution: string | null
  website: string | null
  ir_url: string | null
  is_watchlist: boolean
  /** free-form bookkeeping; `meta.market.holdings_url` = custom download URL of an ETF's holdings list */
  meta?: { market?: { holdings_url?: string | null; [key: string]: unknown } | null; [key: string]: unknown } | null
  /** list endpoint may embed the latest quote (optional) */
  quote?: Quote | null
  /** held quantity (list/detail endpoint), null when not held */
  quantity?: number | null
}
/** `PATCH /instruments/{id}`: only sent fields change; `meta` is merged (null removes a key). A changed `symbol`
 *  drops quote, price history, provider events and fundamentals of the old symbol. */
export type InstrumentUpdate = Partial<Omit<Instrument, 'id' | 'quote' | 'quantity' | 'meta'>> & { meta?: { market?: Record<string, string | null> } }
export interface Holding {
  instrument_id: number
  isin: string | null
  symbol: string | null
  name: string
  type: InstrumentType
  quantity: number
  avg_cost: number
  cost_basis: number
  price: number
  price_currency: string
  market_value: number
  day_change: number
  day_change_pct: number
  unrealized_gain: number
  unrealized_gain_pct: number
  weight: number
  dividends_received: number
  /** where the price comes from; `transaction` = no market price yet, last trade price is used */
  price_source?: 'quote' | 'history' | 'transaction' | null
  price_eur?: number | null
  realized_gain?: number
  unbooked_splits?: UnbookedSplit[]
  missing_cost?: MissingCost[]
  sector: string | null
  country: string | null
  accounts: { account_id: number; quantity: number }[]
}
export interface InstrumentDetail extends Instrument {
  quote: Quote | null
  position: Holding | null
  transactions: Transaction[]
}

export interface PortfolioSummary {
  net_worth: number
  cash_total: number
  invested_value: number
  cost_basis: number
  unrealized_gain: number
  unrealized_gain_pct: number
  realized_gain_ytd: number
  dividends_ytd: number
  interest_ytd: number
  day_change: number
  day_change_pct: number
  by_account: { account_id: number; name: string; value: number }[]
  updated_at: string | null
  fx_warning?: string | null
  warnings?: string[]
  /** provider split events the ledger does not know: quantity and value are wrong until booked */
  unbooked_splits?: (UnbookedSplit & { name: string })[]
  /** deliveries into a depot without price and without a matching delivery out: cost basis unknown */
  missing_cost?: (MissingCost & { name: string })[]
}
export interface UnbookedSplit { event_id: number; instrument_id: number; date: string; /** new shares per old share */ ratio: number; title: string; account_ids: number[] }
export interface MissingCost { transaction_id: number; instrument_id: number; account_id: number; date: string; quantity: number }
/** A possible own-account transfer that rests on amount + date only – never linked without the user's confirmation. */
export interface TransferSuggestion {
  out_id: number; in_id: number; amount: number; from_account_id: number; to_account_id: number
  date: string; in_date: string; out_text: string | null; in_text: string | null; ambiguous: boolean; reason: string
}
export interface HistoryPoint { date: string; net_worth: number; invested: number; cost_basis: number; cash: number }
export interface Performance { twr: number | null; xirr: number | null; absolute_gain: number; series: { date: string; twr_index: number }[] }
export interface AllocationSlice { key: string; value: number; weight: number; /** German display name, if the backend has one */ label?: string | null }

export interface DividendForecastItem {
  instrument_id: number; name: string; symbol?: string | null; amount: number; per_share?: number | null; currency?: string | null; quantity?: number
  ex_date: string | null; pay_date: string | null; is_estimate?: boolean
  /** `events` = known/estimated event, `extrapolated` = last year's payment projected, `received` = already paid this month */
  source?: 'events' | 'extrapolated' | 'received' | string
}
export interface DividendAnalytics {
  year?: number
  /** `amount` net, `gross` before tax */
  received_by_month: { month: string; amount: number; gross?: number; taxes?: number }[]
  received_by_year: { year: number; amount: number; gross?: number; taxes?: number }[]
  /** exactly 12 calendar months: the current month + the 11 following; gross amounts */
  forecast_12m: { month: string; amount: number; items: DividendForecastItem[] }[]
  forecast_12m_total?: number
  yield_on_cost: number | null
  portfolio_yield: number | null
}
export interface CashflowMonth { month: string; income: number; expenses: number; savings: number; savings_rate: number }
export interface ExpenseCategory { category_id: number | null; name: string; amount: number; share: number }
/** `series[].year` is the offset in years (0 = today); the calendar year is `start_year + year`. */
export interface WealthForecast {
  series: { year: number; p10: number; p50: number; p90: number; contributions: number }[]
  start_value?: number; monthly_savings?: number; expected_return?: number; volatility?: number; years?: number; start_year?: number
}
export interface RealizedGain { instrument: string; instrument_id?: number; date: string; quantity: number; proceeds: number; cost: number; gain: number; taxes: number }

export interface TaxIncomeBlock { gross: number; taxes: number; net: number; count: number }
/** `GET /analytics/taxes?year=` – overview for a German private investor; an estimate, not tax advice. */
export interface TaxOverview {
  year: number
  /** years with capital income, newest first */
  years: number[]
  is_estimate: boolean
  realized: { gains: number; losses: number; net: number; taxes: number; count: number; by_class: { key: 'stock' | 'fund' | 'other' | string; gains: number; losses: number; net: number; taxes: number; count: number }[] }
  dividends: TaxIncomeBlock
  interest: TaxIncomeBlock
  /** separately booked taxes (type `tax`), positive = paid */
  other_taxes: { amount: number; count: number }
  /** realised net + dividends gross + interest gross */
  capital_income: number
  allowance: number
  allowance_used: number
  allowance_remaining: number
  taxable_income: number
  /** percent: 25 % Abgeltungsteuer + 5,5 % Soli */
  tax_rate: number
  estimated_tax: number
  taxes_withheld: number
  /** taxes_withheld − estimated_tax */
  difference: number
}
export interface AppSettings { sparerpauschbetrag: number }

export interface RecurringItem {
  key: string
  kind: 'fixed' | 'savings_plan'
  name: string
  instrument_id: number | null
  category_id: number | null
  category_name: string | null
  account_id: number
  account_name: string | null
  interval: 'monthly' | 'quarterly' | 'yearly'
  /** typical amount per booking (positive) */
  amount: number
  /** amount spread over the months of the interval */
  monthly_amount: number
  last_amount: number
  count: number
  last_date: string
  next_date: string
}
/** `GET /analytics/recurring` – detected recurring bookings (same counterparty, regular rhythm, similar amount). */
export interface Recurring { items: RecurringItem[]; fixed_monthly_total: number; fixed_count: number; savings_plan_monthly_total: number; savings_plan_count: number }

export interface MarketEvent {
  id: number
  instrument_id: number
  instrument_name: string
  symbol: string | null
  type: EventType
  date: string
  amount: number | null
  currency: string | null
  title: string | null
  url: string | null
  is_estimate: boolean
  source?: string | null
  position_quantity: number | null
  expected_payment: number | null
}
export interface MarketEventInput { instrument_id: number; type: EventType; date: string; title?: string | null; amount?: number | null; currency?: string | null; url?: string | null }
export type MarketEventUpdate = Partial<Omit<MarketEventInput, 'instrument_id'>>
/** `POST /market/events/import-ics`: exactly one of file / text / url. */
export interface IcsImportInput { instrument_id: number; file?: File | null; text?: string | null; url?: string | null; only_future?: boolean }
export interface IcsImportResult { parsed: number; imported: number }

export interface FundamentalsYear {
  year: number
  revenue: number | null
  net_income: number | null
  eps: number | null
  dps: number | null
  fcf: number | null
  payout_ratio: number | null
  shares: number | null
  /** optional marker for analyst estimates */
  is_estimate?: boolean
}
export interface Fundamentals {
  /** reporting currency of per-share figures (optional; falls back to the instrument currency) */
  currency?: string
  years: FundamentalsYear[]
  pe_history: { date: string; pe: number }[]
  fair_value: { method: string; value: number | null; pe_avg: number | null; eps_forward: number | null } | null
  dividend_growth: { cagr_5y: number | null; cagr_10y: number | null; years_raised: number | null } | null
  valuation: { pe: number | null; forward_pe: number | null; pb: number | null; dividend_yield: number | null; market_cap: number | null } | null
  earnings_quality?: Record<string, number | string | null> | null
}
export interface EtfHoldingRow { name: string; isin: string | null; symbol: string | null; weight: number; sector: string | null; country: string | null }
export interface EtfHoldings {
  as_of: string | null; source: string | null; coverage_pct: number; count?: number; holdings: EtfHoldingRow[]
  /** issuer the app would ask for the full list (`ishares`, `xtrackers`, …), null = none known */
  issuer?: string | null
  /** what the last automatic fetch tried */
  attempts?: { provider: string; ok: boolean; rows?: number; error?: string | null }[]
}
/** `POST /market/etf/{id}/holdings/refresh`: `ok` = stored, `kept` = manual upload protected, `unavailable` = no source answered. */
export interface EtfRefreshResult {
  status: 'ok' | 'kept' | 'unavailable' | 'error' | string
  source?: string | null; rows?: number; coverage_pct?: number; as_of?: string | null; detail?: string | null
  attempts?: { provider: string; ok: boolean; rows?: number; error?: string | null }[]
}
export interface LookthroughSource { instrument_id: number; name: string; value: number; weight_in_etf: number | null }
export interface LookthroughItem {
  key: string
  name: string
  isin: string | null
  symbol: string | null
  direct_value: number
  etf_value: number
  total_value: number
  weight: number
  sources: LookthroughSource[]
}
export interface Lookthrough {
  total_value: number
  coverage_pct: number
  items: LookthroughItem[]
  by_sector: AllocationSlice[]
  by_country: AllocationSlice[]
  /** held ETFs with the coverage of their holdings data */
  etfs?: { instrument_id: number; name: string; isin?: string | null; value: number; coverage_pct: number; source: string | null; as_of: string | null; count?: number }[]
}
export interface MarketJobLog { id?: number; job: string; status: string; detail?: string | null; created_at: string }
export interface RefreshError { instrument_id?: number | null; symbol?: string | null; error: string }
/** Result of one synchronous refresh job (`scope: all` nests them under `jobs`). */
export interface RefreshResult {
  scope: string
  status: 'ok' | 'partial' | 'error' | 'busy' | string
  errors?: RefreshError[]
  duration_ms?: number
  jobs?: Record<string, RefreshResult>
}
/** Background refresh job (`POST /market/refresh` with scope `all` or `?wait=false`, `GET /market/refresh/{job_id}`). */
export interface RefreshJob {
  job_id: string
  scope: string
  status: 'running' | 'ok' | 'partial' | 'error' | string
  background: true
  already_running?: boolean
  started_at: string
  finished_at: string | null
  progress?: { done: number; total: number; current: string | null } | null
  result: RefreshResult | null
}
export interface MarketStatus {
  source?: string | null
  online?: boolean | null
  last_quote_update?: string | null
  jobs?: MarketJobLog[]
  errors?: string[]
  error_log?: MarketJobLog[]
  refresh?: { running: boolean; current: RefreshJob | null; last: RefreshJob | null }
  scheduler?: { enabled: boolean; running: boolean; market_hours?: boolean }
  counts?: Record<string, number>
  fx_latest_date?: string | null
}
export type RefreshScope = 'quotes' | 'events' | 'history' | 'etf' | 'fundamentals' | 'fx' | 'profile' | 'all'
export interface SearchResult { symbol: string; name: string; isin?: string | null; exchange?: string | null; type?: string | null; currency?: string | null; preferred?: boolean }

export interface DocumentSummary {
  id: number
  filename: string
  source_kind: string
  status: DocumentStatus
  detected_institution: string | null
  detected_doc_type: string | null
  parser: string | null
  draft_count: number
  pending_count: number
  duplicate_count?: number
  accepted_count?: number
  warnings?: string[] | null
  used_llm?: boolean
  has_file?: boolean
  duplicate_upload?: boolean
  error: string | null
  created_at: string
  processing_ms: number | null
  account_id?: number | null
}
export interface DraftPayload {
  account_id?: number | null
  type?: TxType | null
  booking_date?: string | null
  value_date?: string | null
  isin?: string | null
  wkn?: string | null
  name?: string | null
  symbol?: string | null
  quantity?: number | null
  price?: number | null
  amount?: number | null
  fees?: number | null
  taxes?: number | null
  currency?: string | null
  fx_rate?: number | null
  counterparty?: string | null
  description?: string | null
  category_hint?: string | null
  // balance drafts
  date?: string | null
  balance?: number | null
  // set by the validation step (read-only for the UI)
  warnings?: string[] | null
  review_required?: boolean
  verified?: boolean
  duplicate_of?: number | null
  gross_amount?: number | null
  counterparty_iban?: string | null
}
export interface AcceptAllResult { accepted: number; skipped: number; duplicates: number; errors: { draft_id: number; error: string }[] }
export interface Draft {
  id: number
  kind: 'transaction' | 'balance' | string
  payload: DraftPayload
  confidence: number | null
  status: DraftStatus
  transaction_id: number | null
}
export interface DocumentDetail extends DocumentSummary { drafts: Draft[]; raw_text: string | null; mime_type?: string | null }
export interface LlmStatus {
  available: boolean; models: string[]; text_model: string | null; vision_model: string | null
  text_model_available?: boolean; vision_model_available?: boolean; ocr_available?: boolean; url?: string | null; error?: string | null; hint?: string | null
}
