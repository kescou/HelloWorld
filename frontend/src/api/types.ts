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
  color: string | null
  balance: number
  market_value: number
  total_value: number
  settlement_account_id: number | null
  is_active: boolean
  interest_rate: number | null
  sort_order: number
}
/** OPTIONAL contract addition: reconciliation against statement balances (`balance_snapshots`). */
export interface BalanceSnapshot {
  date: string
  balance: number
  /** ledger balance computed for that date; if missing the UI only lists the snapshot */
  computed_balance?: number | null
}
export interface AccountDetail extends Account {
  history: { date: string; value: number }[]
  /** OPTIONAL (not in contract yet) */
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
  name?: string | null
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
}
export type CategoryInput = Omit<Category, 'id'>

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
  /** list endpoint may embed the latest quote (optional) */
  quote?: Quote | null
}
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
}
export interface HistoryPoint { date: string; net_worth: number; invested: number; cost_basis: number; cash: number }
export interface Performance { twr: number | null; xirr: number | null; absolute_gain: number; series: { date: string; twr_index: number }[] }
export interface AllocationSlice { key: string; value: number; weight: number }

export interface DividendForecastItem { instrument_id: number; name: string; amount: number; ex_date: string | null; pay_date: string | null }
export interface DividendAnalytics {
  received_by_month: { month: string; amount: number }[]
  received_by_year: { year: number; amount: number }[]
  forecast_12m: { month: string; amount: number; items: DividendForecastItem[] }[]
  yield_on_cost: number | null
  portfolio_yield: number | null
}
export interface CashflowMonth { month: string; income: number; expenses: number; savings: number; savings_rate: number }
export interface ExpenseCategory { category_id: number | null; name: string; amount: number; share: number }
export interface WealthForecast { series: { year: number; p10: number; p50: number; p90: number; contributions: number }[] }
export interface RealizedGain { instrument: string; date: string; quantity: number; proceeds: number; cost: number; gain: number; taxes: number }

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
  position_quantity: number | null
  expected_payment: number | null
}
/** OPTIONAL contract addition: `POST /market/events` for manual events. */
export interface MarketEventInput { instrument_id: number; type: EventType; date: string; title?: string | null; amount?: number | null; currency?: string | null; url?: string | null }

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
export interface EtfHoldings { as_of: string | null; source: string | null; coverage_pct: number; holdings: EtfHoldingRow[] }
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
}
/** Shape of `/market/status` is not fixed by the contract; every field is optional. */
export interface MarketStatus {
  source?: string | null
  online?: boolean | null
  last_quote_update?: string | null
  jobs?: { job: string; status: string; detail?: string | null; created_at: string }[]
  errors?: string[]
}
export type RefreshScope = 'quotes' | 'events' | 'history' | 'etf' | 'all'
export interface SearchResult { symbol: string; name: string; isin?: string | null; exchange?: string | null; type?: string | null; currency?: string | null }

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
}
export interface Draft {
  id: number
  kind: 'transaction' | 'balance' | string
  payload: DraftPayload
  confidence: number | null
  status: DraftStatus
  transaction_id: number | null
}
export interface DocumentDetail extends DocumentSummary { drafts: Draft[]; raw_text: string | null; mime_type?: string | null }
export interface LlmStatus { available: boolean; models: string[]; text_model: string | null; vision_model: string | null }
