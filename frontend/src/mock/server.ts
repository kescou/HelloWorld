// In-memory mock backend implementing the REST contract from docs/ARCHITECTURE.md.
// Only loaded when VITE_MOCK=1 (dynamic import in main.tsx) — never part of the normal build.
import type * as T from '../api/types'
import { isoDate } from '../lib/format'
import {
  ACCOUNTS, CATEGORIES, FX, HISTORY_DAYS, PE_BASE, SEEDS, TODAY, agoOf, buildDocuments, buildEvents, buildInstruments, buildTransactions,
  dpsAnnual, epsAt, etfRows, isoAgo, mulberry32, priceEur, priceNative, round2, seedOf, type RawAccount, type RawEvent,
} from './data'

export type Scenario = 'default' | 'empty' | 'offline'
type RawTx = Omit<T.Transaction, 'account_name' | 'instrument_name' | 'instrument_isin' | 'category_name'>

export class HttpError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

export function createServer(scenario: Scenario) {
  const empty = scenario === 'empty'
  const offline = scenario === 'offline'
  const accounts: RawAccount[] = ACCOUNTS.map((a) => ({ ...a, opening_balance: empty ? 0 : a.opening_balance }))
  const instruments: T.Instrument[] = empty ? [] : buildInstruments()
  const categories: T.Category[] = CATEGORIES.map((c) => ({ ...c }))
  let txs: RawTx[] = empty ? [] : buildTransactions().map((t, i) => ({ ...t, id: i + 1 }))
  let events: RawEvent[] = empty ? [] : buildEvents()
  let documents: T.DocumentDetail[] = empty ? [] : buildDocuments()
  const snapshots: { account_id: number; date: string; balance: number }[] = []
  const etfOverrides = new Map<number, { source: string; rows: T.EtfHoldingRow[] }>()
  let nextId = 100000
  let lastRefresh = new Date(Date.now() - (offline ? 3 * 86400000 : 7 * 60000))
  const jobs: NonNullable<T.MarketStatus['jobs']> = offline
    ? [{ job: 'quotes', status: 'error', detail: 'Yahoo Finance nicht erreichbar (Timeout nach 10 s)', created_at: new Date(Date.now() - 5 * 60000).toISOString() }]
    : [
        { job: 'quotes', status: 'ok', detail: '13 Kurse aktualisiert', created_at: lastRefresh.toISOString() },
        { job: 'events', status: 'ok', detail: '86 Termine', created_at: new Date(Date.now() - 6 * 3600000).toISOString() },
        { job: 'etf', status: 'ok', detail: 'iShares: 36 Positionen · Vanguard: Top 10 (Yahoo)', created_at: new Date(Date.now() - 26 * 3600000).toISOString() },
        { job: 'history', status: 'ok', detail: null, created_at: new Date(Date.now() - 26 * 3600000).toISOString() },
      ]

  const acc = (id: number) => accounts.find((a) => a.id === id)
  const instr = (id: number | null) => (id == null ? undefined : instruments.find((i) => i.id === id))
  const cat = (id: number | null) => (id == null ? undefined : categories.find((c) => c.id === id))
  const isBrokerage = (id: number) => acc(id)?.type === 'brokerage'
  const hasPrice = (id: number) => !!seedOf(id)
  const pxEur = (id: number, ago = 0) => (hasPrice(id) ? priceEur(id, ago) : 0)
  const cashEffect = (t: RawTx, accountId: number) =>
    (t.account_id === accountId && t.cash_account_id == null && !isBrokerage(accountId)) || t.cash_account_id === accountId ? t.amount : 0

  function cashBalance(accountId: number, upTo?: string) {
    let b = acc(accountId)?.opening_balance ?? 0
    for (const t of txs) if (!upTo || t.booking_date <= upTo) b += cashEffect(t, accountId)
    return round2(b)
  }

  interface Pos { instrument_id: number; quantity: number; cost: number; dividends: number; realized: T.RealizedGain[]; byAccount: Map<number, number> }
  function positions(accountId?: number): Pos[] {
    const map = new Map<number, Pos>()
    const sorted = [...txs].sort((a, b) => a.booking_date.localeCompare(b.booking_date) || a.id - b.id)
    for (const t of sorted) {
      if (t.instrument_id == null) continue
      if (accountId && t.account_id !== accountId) continue
      let p = map.get(t.instrument_id)
      if (!p) { p = { instrument_id: t.instrument_id, quantity: 0, cost: 0, dividends: 0, realized: [], byAccount: new Map() }; map.set(t.instrument_id, p) }
      const q = t.quantity ?? 0
      if (t.type === 'buy' || t.type === 'delivery_in') {
        p.quantity += q; p.cost += t.type === 'buy' ? -t.amount : q * (t.price ?? 0)
        p.byAccount.set(t.account_id, (p.byAccount.get(t.account_id) ?? 0) + q)
      } else if (t.type === 'sell' || t.type === 'delivery_out') {
        const avg = p.quantity > 0 ? p.cost / p.quantity : 0
        const cost = avg * q
        p.quantity -= q; p.cost -= cost
        p.byAccount.set(t.account_id, (p.byAccount.get(t.account_id) ?? 0) - q)
        if (t.type === 'sell') p.realized.push({ instrument: instr(t.instrument_id)?.name ?? '', date: t.booking_date, quantity: q, proceeds: round2(t.amount), cost: round2(cost), gain: round2(t.amount - cost), taxes: t.taxes })
      } else if (t.type === 'split') { p.quantity *= q || 1 }
      else if (t.type === 'dividend') p.dividends += t.amount
    }
    return [...map.values()]
  }

  function holdings(accountId?: number): T.Holding[] {
    const list = positions(accountId).filter((p) => p.quantity > 1e-6)
    const rows = list.map((p) => {
      const i = instr(p.instrument_id)!
      const priceN = hasPrice(i.id) ? priceNative(i.id, 0) : 0
      const fx = FX[i.currency] ?? 1
      const mv = p.quantity * priceN / fx
      const prev = p.quantity * (hasPrice(i.id) ? priceNative(i.id, 1) : 0) / fx
      return {
        instrument_id: i.id, isin: i.isin, symbol: i.symbol, name: i.name, type: i.type, quantity: Math.round(p.quantity * 1e4) / 1e4,
        avg_cost: round2(p.cost / p.quantity), cost_basis: round2(p.cost), price: round2(priceN), price_currency: i.currency, market_value: round2(mv),
        day_change: round2(mv - prev), day_change_pct: prev ? round2(((mv - prev) / prev) * 100) : 0,
        unrealized_gain: round2(mv - p.cost), unrealized_gain_pct: p.cost ? round2(((mv - p.cost) / p.cost) * 100) : 0, weight: 0,
        dividends_received: round2(p.dividends), sector: i.sector, country: i.country,
        accounts: [...p.byAccount.entries()].filter(([, q]) => q > 1e-6).map(([account_id, quantity]) => ({ account_id, quantity: Math.round(quantity * 1e4) / 1e4 })),
      }
    })
    const total = rows.reduce((s, r) => s + r.market_value, 0)
    for (const r of rows) r.weight = total ? round2((r.market_value / total) * 100) : 0
    return rows.sort((a, b) => b.market_value - a.market_value)
  }

  function accountOut(a: RawAccount): T.Account {
    const balance = a.type === 'brokerage' ? 0 : cashBalance(a.id)
    const market_value = a.type === 'brokerage' ? round2(holdings(a.id).reduce((s, h) => s + h.market_value, 0)) : 0
    return { id: a.id, name: a.name, institution: a.institution, type: a.type, currency: a.currency, iban: a.iban, color: a.color, balance, market_value,
      total_value: round2(balance + market_value), settlement_account_id: a.settlement_account_id, is_active: a.is_active, interest_rate: a.interest_rate, sort_order: a.sort_order,
      opening_balance: a.opening_balance, opening_date: a.opening_date ?? null }
  }

  /** Replays the journal over ascending sample days and reports cash / invested / cost basis per day. */
  function walk(agoList: number[], accountId?: number) {
    const sorted = [...txs].sort((a, b) => a.booking_date.localeCompare(b.booking_date) || a.id - b.id)
    const cashAccs = accounts.filter((a) => a.type !== 'brokerage' && (!accountId || a.id === accountId))
    const includeSec = !accountId || isBrokerage(accountId)
    let cash = cashAccs.reduce((s, a) => s + a.opening_balance, 0)
    const qty = new Map<number, number>()
    const cost = new Map<number, number>()
    let netFlow = 0
    let k = 0
    const out: (T.HistoryPoint & { flow: number })[] = []
    for (const ago of agoList) {
      const iso = isoAgo(ago)
      for (; k < sorted.length && sorted[k].booking_date <= iso; k++) {
        const t = sorted[k]
        for (const a of cashAccs) cash += cashEffect(t, a.id)
        if (t.instrument_id != null && includeSec && (!accountId || t.account_id === accountId)) {
          const q = qty.get(t.instrument_id) ?? 0
          const c = cost.get(t.instrument_id) ?? 0
          if (t.type === 'buy') { qty.set(t.instrument_id, q + (t.quantity ?? 0)); cost.set(t.instrument_id, c - t.amount); netFlow += -t.amount }
          else if (t.type === 'sell') { const part = q > 0 ? (c / q) * (t.quantity ?? 0) : 0; qty.set(t.instrument_id, q - (t.quantity ?? 0)); cost.set(t.instrument_id, c - part); netFlow -= t.amount }
        }
      }
      let invested = 0
      let basis = 0
      for (const [id, q] of qty) { if (q > 1e-6) { invested += q * pxEur(id, ago); basis += cost.get(id) ?? 0 } }
      out.push({ date: iso, net_worth: round2(cash + invested), invested: round2(invested), cost_basis: round2(basis), cash: round2(cash), flow: netFlow })
    }
    return out
  }
  const RANGE_DAYS: Record<T.Range, number> = { '1M': 31, '3M': 92, YTD: Math.max(7, Math.round((TODAY.getTime() - new Date(TODAY.getFullYear(), 0, 1).getTime()) / 86400000)), '1Y': 365, '3Y': 1096, '5Y': 1826, MAX: HISTORY_DAYS }
  function sampleDays(range: T.Range, capToFirstTx = true): number[] {
    let n = RANGE_DAYS[range] ?? 365
    if (capToFirstTx) {
      const first = txs.reduce((m, t) => (t.booking_date < m ? t.booking_date : m), isoDate(TODAY))
      n = Math.min(n, Math.max(agoOf(first) + 3, 7))
    }
    const step = n <= 400 ? 1 : n <= 1200 ? 3 : 7
    const days: number[] = []
    for (let a = n; a > 0; a -= step) days.push(a)
    days.push(0)
    return days
  }

  function summary(): T.PortfolioSummary {
    const accs = accounts.map(accountOut)
    const h = holdings()
    const cash_total = round2(accs.reduce((s, a) => s + a.balance, 0))
    const invested_value = round2(h.reduce((s, x) => s + x.market_value, 0))
    const cost_basis = round2(h.reduce((s, x) => s + x.cost_basis, 0))
    const day_change = round2(h.reduce((s, x) => s + x.day_change, 0))
    const year = String(TODAY.getFullYear())
    const ytd = (type: T.TxType) => round2(txs.filter((t) => t.type === type && t.booking_date.startsWith(year)).reduce((s, t) => s + t.amount, 0))
    const realized = positions().flatMap((p) => p.realized).filter((r) => r.date.startsWith(year)).reduce((s, r) => s + r.gain, 0)
    const net = round2(cash_total + invested_value)
    return {
      net_worth: net, cash_total, invested_value, cost_basis, unrealized_gain: round2(invested_value - cost_basis),
      unrealized_gain_pct: cost_basis ? round2(((invested_value - cost_basis) / cost_basis) * 100) : 0,
      realized_gain_ytd: round2(realized), dividends_ytd: ytd('dividend'), interest_ytd: ytd('interest'), day_change,
      day_change_pct: net - day_change ? round2((day_change / (net - day_change)) * 100) : 0,
      by_account: accs.filter((a) => a.is_active).map((a) => ({ account_id: a.id, name: a.name, value: a.total_value })),
      updated_at: txs.length ? lastRefresh.toISOString() : null,
    }
  }

  function performance(range: T.Range): T.Performance {
    const pts = walk(sampleDays(range))
    const series: T.Performance['series'] = []
    let idx = 100
    let started = false
    for (let i = 0; i < pts.length; i++) {
      const p = pts[i]
      if (!started) { if (p.invested > 0) started = true; series.push({ date: p.date, twr_index: 100 }); continue }
      const prev = pts[i - 1]
      const flow = p.flow - prev.flow
      if (prev.invested > 0) idx *= (p.invested - flow) / prev.invested
      series.push({ date: p.date, twr_index: Math.round(idx * 100) / 100 })
    }
    if (!pts.length || !started) return { twr: null, xirr: null, absolute_gain: 0, series: [] }
    const first = pts.find((p) => p.invested > 0) ?? pts[0]
    const last = pts[pts.length - 1]
    const days = Math.max(1, agoOf(first.date))
    const twr = idx - 100
    const absolute = last.invested - first.invested - (last.flow - first.flow)
    const xirr = (Math.pow(idx / 100, 365 / days) - 1) * 100 * (days < 300 ? days / 365 : 0.94)
    return { twr: round2(twr), xirr: round2(days < 300 ? twr * 0.97 : xirr), absolute_gain: round2(absolute), series }
  }

  function etf(id: number): T.EtfHoldings {
    const data = etfOverrides.get(id) ?? (seedOf(id)?.type === 'etf' ? etfRows(id) : { source: null as string | null, rows: [] as T.EtfHoldingRow[] })
    return { as_of: data.rows.length ? isoAgo(id === 1 ? 2 : 9) : null, source: data.source, coverage_pct: round2(data.rows.reduce((s, r) => s + r.weight, 0)), holdings: data.rows }
  }

  function lookthrough(accountId?: number): T.Lookthrough {
    const h = holdings(accountId)
    const items = new Map<string, T.LookthroughItem & { sector: string | null; country: string | null }>()
    const total = h.reduce((s, x) => s + x.market_value, 0)
    let covered = 0
    const item = (key: string, name: string, isin: string | null, symbol: string | null, sector: string | null, country: string | null) => {
      let it = items.get(key)
      if (!it) { it = { key, name, isin, symbol, direct_value: 0, etf_value: 0, total_value: 0, weight: 0, sources: [], sector, country }; items.set(key, it) }
      return it
    }
    for (const x of h) {
      if (x.type === 'etf' || x.type === 'fund') {
        for (const row of etf(x.instrument_id).holdings) {
          const v = (x.market_value * row.weight) / 100
          const it = item(row.isin ?? row.name, row.name, row.isin, row.symbol, row.sector, row.country)
          it.etf_value += v
          it.sources.push({ instrument_id: x.instrument_id, name: x.name, value: round2(v), weight_in_etf: row.weight })
          covered += v
        }
      } else {
        const it = item(x.isin ?? x.name, x.name, x.isin, x.symbol, x.sector, x.country)
        it.direct_value += x.market_value
        it.sources.unshift({ instrument_id: x.instrument_id, name: 'Direktbestand', value: x.market_value, weight_in_etf: null })
        covered += x.market_value
      }
    }
    const list = [...items.values()]
    const group = (f: (i: (typeof list)[number]) => string | null) => {
      const m = new Map<string, number>()
      for (const it of list) m.set(f(it) ?? 'unknown', (m.get(f(it) ?? 'unknown') ?? 0) + it.direct_value + it.etf_value)
      if (total - covered > 1) m.set('unknown', (m.get('unknown') ?? 0) + total - covered)
      return [...m.entries()].map(([key, value]) => ({ key, value: round2(value), weight: total ? round2((value / total) * 100) : 0 })).sort((a, b) => b.value - a.value)
    }
    return {
      total_value: round2(total), coverage_pct: total ? round2((covered / total) * 100) : 0,
      items: list.map(({ sector: _s, country: _c, ...it }) => ({ ...it, direct_value: round2(it.direct_value), etf_value: round2(it.etf_value), total_value: round2(it.direct_value + it.etf_value), weight: total ? round2(((it.direct_value + it.etf_value) / total) * 100) : 0 }))
        .sort((a, b) => b.total_value - a.total_value),
      by_sector: group((i) => i.sector), by_country: group((i) => i.country),
    }
  }

  function allocation(by: T.AllocationBy, lt: boolean): T.AllocationSlice[] {
    const h = holdings()
    const m = new Map<string, number>()
    const add = (k: string | null, v: number) => m.set(k ?? 'unknown', (m.get(k ?? 'unknown') ?? 0) + v)
    if (lt && (by === 'sector' || by === 'country')) return by === 'sector' ? lookthrough().by_sector : lookthrough().by_country
    for (const x of h) {
      if (by === 'type') add(x.type, x.market_value)
      else if (by === 'sector') add(x.type === 'etf' ? 'ETF (nicht aufgeschlüsselt)' : x.sector, x.market_value)
      else if (by === 'country') add(x.type === 'etf' ? 'ETF (nicht aufgeschlüsselt)' : x.country, x.market_value)
      else if (by === 'currency') add(x.price_currency, x.market_value)
      else for (const a of x.accounts) add(acc(a.account_id)?.name ?? null, (x.market_value * a.quantity) / x.quantity)
    }
    const total = [...m.values()].reduce((s, v) => s + v, 0)
    return [...m.entries()].map(([key, value]) => ({ key, value: round2(value), weight: total ? round2((value / total) * 100) : 0 })).sort((a, b) => b.value - a.value)
  }

  function eventsOut(from?: string, to?: string, types?: string): T.MarketEvent[] {
    const pos = new Map(positions().map((p) => [p.instrument_id, p.quantity]))
    const typeSet = types ? new Set(types.split(',')) : null
    return events
      .filter((e) => (!from || e.date >= from) && (!to || e.date <= to) && (!typeSet || typeSet.has(e.type)))
      .filter((e) => { const i = instr(e.instrument_id); return i && ((pos.get(e.instrument_id) ?? 0) > 1e-6 || i.is_watchlist) })
      .map((e) => {
        const i = instr(e.instrument_id)!
        const q = pos.get(e.instrument_id) ?? 0
        const isDiv = e.type === 'ex_dividend' || e.type === 'dividend_payment'
        return { ...e, instrument_name: i.name, symbol: i.symbol, position_quantity: q > 1e-6 ? Math.round(q * 1e4) / 1e4 : null,
          expected_payment: isDiv && q > 1e-6 && e.amount ? round2((q * e.amount) / (FX[e.currency ?? 'EUR'] ?? 1)) : null }
      })
  }

  const monthKey = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
  function dividends(year?: number): T.DividendAnalytics {
    const y = year || TODAY.getFullYear()
    const divTx = txs.filter((t) => t.type === 'dividend')
    const received_by_month = Array.from({ length: 12 }, (_, m) => {
      const key = `${y}-${String(m + 1).padStart(2, '0')}`
      return { month: key, amount: round2(divTx.filter((t) => t.booking_date.startsWith(key)).reduce((s, t) => s + t.amount, 0)) }
    })
    const years = new Map<number, number>()
    for (const t of divTx) { const yy = Number(t.booking_date.slice(0, 4)); years.set(yy, (years.get(yy) ?? 0) + t.amount) }
    const tomorrow = isoAgo(-1)
    const future = eventsOut(tomorrow, isoAgo(-365), 'dividend_payment').filter((e) => e.expected_payment)
    const exDates = eventsOut(isoAgo(30), isoAgo(-365), 'ex_dividend')
    const forecast_12m = Array.from({ length: 12 }, (_, i) => {
      const d = new Date(TODAY.getFullYear(), TODAY.getMonth() + i, 1)
      const key = monthKey(d)
      const items = future.filter((e) => e.date.startsWith(key)).map((e) => ({
        instrument_id: e.instrument_id, name: e.instrument_name, amount: e.expected_payment ?? 0,
        ex_date: exDates.filter((x) => x.instrument_id === e.instrument_id && x.date <= e.date).pop()?.date ?? null, pay_date: e.date,
        is_estimate: e.is_estimate, source: 'events',
      }))
      return { month: key, amount: round2(items.reduce((s, x) => s + x.amount, 0)), items }
    })
    const total = forecast_12m.reduce((s, m) => s + m.amount, 0)
    const s = summary()
    return {
      received_by_month, received_by_year: [...years.entries()].sort((a, b) => a[0] - b[0]).map(([yy, amount]) => ({ year: yy, amount: round2(amount) })), forecast_12m, forecast_12m_total: round2(total),
      yield_on_cost: s.cost_basis ? round2((total / s.cost_basis) * 100) : null, portfolio_yield: s.invested_value ? round2((total / s.invested_value) * 100) : null,
    }
  }

  const isIncome = (t: RawTx) => (t.type === 'deposit' || t.type === 'interest' || t.type === 'dividend') && t.amount > 0
  const isExpense = (t: RawTx) => t.type === 'withdrawal' || t.type === 'fee' || (t.type === 'tax' && t.amount < 0)
  function cashflow(months: number): T.CashflowMonth[] {
    return Array.from({ length: months }, (_, i) => {
      const key = monthKey(new Date(TODAY.getFullYear(), TODAY.getMonth() - (months - 1 - i), 1))
      const m = txs.filter((t) => t.booking_date.startsWith(key))
      const income = round2(m.filter(isIncome).reduce((s, t) => s + t.amount, 0))
      const expenses = round2(-m.filter(isExpense).reduce((s, t) => s + t.amount, 0))
      return { month: key, income, expenses, savings: round2(income - expenses), savings_rate: income ? round2(((income - expenses) / income) * 100) : 0 }
    })
  }
  function expensesByCategory(from?: string, to?: string): T.ExpenseCategory[] {
    const m = new Map<number | null, number>()
    for (const t of txs) {
      if (!isExpense(t) || (from && t.booking_date < from) || (to && t.booking_date > to)) continue
      m.set(t.category_id, (m.get(t.category_id) ?? 0) - t.amount)
    }
    const total = [...m.values()].reduce((s, v) => s + v, 0)
    return [...m.entries()].map(([category_id, amount]) => ({ category_id, name: cat(category_id)?.name ?? 'Ohne Kategorie', amount: round2(amount), share: total ? round2((amount / total) * 100) : 0 }))
      .sort((a, b) => b.amount - a.amount)
  }
  function forecast(years: number, monthly: number, ret: number): T.WealthForecast {
    const s = summary()
    const start = s.net_worth
    const equityShare = start > 0 ? Math.max(0.3, s.invested_value / start) : 0.7
    const sigma = 0.15 * equityShare
    const mu = ret / 100
    const series = Array.from({ length: years + 1 }, (_, t) => {
      const val = (z: number) => {
        if (t === 0) return start
        const r = mu + (z * sigma) / Math.sqrt(t)
        const g = Math.pow(1 + r, t)
        const annuity = Math.abs(r) < 1e-9 ? t : (g - 1) / r
        return Math.max(0, start * g + monthly * 12 * annuity)
      }
      return { year: t, p10: round2(val(-1.2816)), p50: round2(val(0)), p90: round2(val(1.2816)), contributions: round2(start + monthly * 12 * t) }
    })
    return { series, start_value: start, monthly_savings: monthly, expected_return: ret, volatility: 15, years, start_year: TODAY.getFullYear() }
  }

  function fundamentals(id: number): T.Fundamentals {
    const s = seedOf(id)
    if (!s || s.type !== 'stock') throw new HttpError(404, 'Keine Fundamentaldaten für dieses Wertpapier vorhanden.')
    const r = mulberry32(500 + id)
    const y0 = TODAY.getFullYear()
    const years: T.FundamentalsYear[] = []
    for (let y = y0 - 10; y <= y0 + 2; y++) {
      const ago = (y0 - y) * 365
      const est = y >= y0
      const noise = est ? 1 : 1 + (r() - 0.5) * 0.16
      const eps = s.eps * Math.exp(-s.growth * (ago / 365)) * noise
      const dps = dpsAnnual(s, ago) * (est ? 1 : 1 + (r() - 0.5) * 0.03)
      const shares = s.sharesM * (1 + (y0 - y) * 0.008)
      const revenue = s.revenueBn * 1e9 * Math.exp(-(s.growth * 0.8) * (ago / 365)) * (est ? 1 : 1 + (r() - 0.5) * 0.06)
      years.push({ year: y, revenue: Math.round(revenue), net_income: Math.round(eps * shares * 1e6), eps: round2(eps), dps: round2(dps), fcf: Math.round(eps * shares * 1e6 * (0.85 + r() * 0.3)),
        payout_ratio: eps ? round2((dps / eps) * 100) : null, shares: Math.round(shares * 1e6), is_estimate: est })
    }
    const pe_history: T.Fundamentals['pe_history'] = []
    for (let ago = HISTORY_DAYS - 10; ago >= 0; ago -= 30) pe_history.push({ date: isoAgo(ago), pe: round2(priceNative(id, ago) / epsAt(s, ago)) })
    const pe_avg = round2(pe_history.reduce((a, p) => a + p.pe, 0) / pe_history.length)
    const eps_forward = years.find((y) => y.year === y0 + 1)!.eps!
    const price = priceNative(id, 0)
    const dpsNow = dpsAnnual(s)
    const dpsAgo = (n: number) => years.find((y) => y.year === y0 - 1 - n)?.dps ?? 0
    const last = years.find((y) => y.year === y0 - 1)!
    const cagr = (n: number) => (dpsAgo(n) > 0 && last.dps ? round2((Math.pow(last.dps / dpsAgo(n), 1 / n) - 1) * 100) : null)
    return {
      currency: s.currency, years, pe_history,
      fair_value: { method: 'Ø-KGV (10 J.) × erwarteter Gewinn je Aktie', value: round2(pe_avg * eps_forward), pe_avg, eps_forward },
      dividend_growth: { cagr_5y: cagr(5), cagr_10y: cagr(9), years_raised: s.payout > 0 ? 4 + (id % 9) : 0 },
      valuation: { pe: round2(price / s.eps), forward_pe: round2(price / eps_forward), pb: round2(1.4 + (PE_BASE[id] / 6) * (0.8 + r() * 0.4)), dividend_yield: round2((dpsNow / price) * 100), market_cap: Math.round(price * s.sharesM * 1e6) },
      earnings_quality: { fcf_conversion: round2(88 + r() * 20), eps_stability: round2(70 + r() * 25) },
    }
  }

  function quote(id: number): T.Quote | null {
    const i = instr(id)
    if (!i || !hasPrice(id)) return null
    const p = priceNative(id, 0)
    const prev = priceNative(id, 1)
    return { instrument_id: id, price: round2(p), previous_close: round2(prev), change: round2(p - prev), change_pct: round2(((p - prev) / prev) * 100), currency: i.currency,
      market_time: lastRefresh.toISOString(), updated_at: lastRefresh.toISOString() }
  }

  const txOut = (t: RawTx): T.Transaction => ({ ...t, account_name: acc(t.account_id)?.name ?? '?', instrument_name: instr(t.instrument_id)?.name ?? null, instrument_isin: instr(t.instrument_id)?.isin ?? null, category_name: cat(t.category_id)?.name ?? null })
  const docSummary = (d: T.DocumentDetail): T.DocumentSummary => {
    const { drafts, raw_text: _r, ...rest } = d
    return { ...rest, draft_count: drafts.length, pending_count: drafts.filter((x) => x.status === 'pending').length }
  }
  function settleDoc(d: T.DocumentDetail) {
    d.pending_count = d.drafts.filter((x) => x.status === 'pending').length
    d.draft_count = d.drafts.length
    if (d.status === 'review' && d.pending_count === 0) d.status = 'imported'
  }
  function findOrCreateInstrument(body: { isin?: string | null; symbol?: string | null; name?: string | null; type?: T.InstrumentType; currency?: string }): T.Instrument {
    const found = instruments.find((i) => (body.isin && i.isin === body.isin) || (body.symbol && i.symbol === body.symbol))
    if (found) return found
    const created: T.Instrument = { id: nextId++, isin: body.isin ?? null, wkn: null, symbol: body.symbol ?? null, name: body.name || body.symbol || body.isin || 'Unbekanntes Wertpapier', type: body.type ?? 'stock', currency: body.currency ?? 'EUR',
      exchange: null, sector: null, industry: null, country: null, fund_provider: null, ter: null, distribution: null, website: null, ir_url: null, is_watchlist: false }
    instruments.push(created)
    return created
  }
  function createTx(b: T.TransactionCreate, source_document_id: number | null = null): RawTx {
    const a = acc(Number(b.account_id))
    if (!a) throw new HttpError(422, 'account_id: Konto nicht gefunden')
    if (!b.booking_date) throw new HttpError(422, 'booking_date: Pflichtfeld')
    let instrument_id = b.instrument_id ?? null
    if (!instrument_id && b.isin) instrument_id = findOrCreateInstrument({ isin: b.isin, name: b.name }).id
    const fees = b.fees ?? 0
    const taxes = b.taxes ?? 0
    let amount = b.amount
    if (amount == null) {
      const gross = (b.quantity ?? 0) * (b.price ?? 0)
      amount = b.type === 'buy' ? -(gross + fees + taxes) : b.type === 'sell' || b.type === 'dividend' ? gross - fees - taxes : 0
    }
    const t: RawTx = {
      id: nextId++, account_id: a.id, cash_account_id: a.type === 'brokerage' ? a.settlement_account_id : null, instrument_id, type: b.type, booking_date: b.booking_date, value_date: b.value_date ?? b.booking_date,
      quantity: b.quantity ?? null, price: b.price ?? null, amount: round2(amount), fees, taxes, currency: b.currency ?? 'EUR', fx_rate: null, category_id: b.category_id ?? null,
      counterparty: b.counterparty ?? null, description: b.description ?? null, transfer_peer_id: null, source_document_id, notes: b.notes ?? null,
    }
    txs.push(t)
    return t
  }
  function acceptDraft(doc: T.DocumentDetail, dr: T.Draft) {
    if (dr.status !== 'pending') return
    const p = dr.payload
    if (dr.kind === 'balance') {
      if (p.account_id && p.date && p.balance != null) snapshots.push({ account_id: p.account_id, date: p.date, balance: p.balance })
    } else {
      if (!p.account_id) throw new HttpError(422, 'account_id: Bitte ein Konto wählen, bevor der Entwurf übernommen wird.')
      const hint = categories.find((c) => c.name === p.category_hint)
      const t = createTx({ account_id: p.account_id, type: p.type ?? 'withdrawal', booking_date: p.booking_date ?? isoDate(TODAY), value_date: p.value_date, isin: p.isin, name: p.name, quantity: p.quantity, price: p.price, amount: p.amount,
        fees: p.fees ?? 0, taxes: p.taxes ?? 0, currency: p.currency ?? 'EUR', counterparty: p.counterparty, description: p.description, category_id: hint?.id ?? null }, doc.id)
      dr.transaction_id = t.id
    }
    dr.status = 'accepted'
  }
  function scheduleProcessing(doc: T.DocumentDetail, text?: string) {
    setTimeout(() => { if (doc.status === 'uploaded') doc.status = 'processing' }, 900)
    setTimeout(() => {
      if (!documents.includes(doc) || doc.status === 'review') return
      if (offline && doc.source_kind !== 'pdf') { doc.status = 'failed'; doc.error = 'Ollama ist nicht erreichbar (http://localhost:11434). Ohne lokales Modell können nur bekannte PDF-Formate gelesen werden.'; return }
      doc.status = 'review'
      doc.parser = doc.source_kind === 'image' ? 'qwen2.5vl:7b' : doc.source_kind === 'pdf' ? 'generic_pdf' : 'qwen2.5:7b-instruct'
      doc.processing_ms = 3800
      doc.detected_doc_type = doc.source_kind === 'paste' ? 'statement' : 'trade_confirmation'
      doc.raw_text = text ?? `Mock-Extraktion für „${doc.filename}“\n\nKartenzahlung BAUHAUS GmbH  -84,20 EUR\nBuchungstag ${isoAgo(1).split('-').reverse().join('.')}`
      doc.drafts = [{ id: nextId++, kind: 'transaction', confidence: 0.78, status: 'pending', transaction_id: null,
        payload: { account_id: doc.account_id ?? null, type: 'withdrawal', booking_date: isoAgo(1), amount: -84.2, currency: 'EUR', counterparty: 'BAUHAUS GmbH', description: 'Kartenzahlung', category_hint: null } }]
      settleDoc(doc)
    }, 4200)
  }
  const first = documents.find((d) => d.status === 'processing')
  if (first) setTimeout(() => { first.status = 'uploaded'; scheduleProcessing(first) }, 6000)

  const SEARCH_EXTRA: T.SearchResult[] = [
    { symbol: 'NVDA', name: 'NVIDIA Corp.', isin: 'US67066G1040', exchange: 'NASDAQ', type: 'stock', currency: 'USD' },
    { symbol: 'DTE.DE', name: 'Deutsche Telekom AG', isin: 'DE0005557508', exchange: 'XETRA', type: 'stock', currency: 'EUR' },
    { symbol: 'NOVO-B.CO', name: 'Novo Nordisk A/S', isin: 'DK0062498333', exchange: 'CPH', type: 'stock', currency: 'DKK' },
    { symbol: 'XDWD.DE', name: 'Xtrackers MSCI World UCITS ETF 1C', isin: 'IE00BJ0KDQ92', exchange: 'XETRA', type: 'etf', currency: 'EUR' },
    { symbol: 'AMZN', name: 'Amazon.com Inc.', isin: 'US0231351067', exchange: 'NASDAQ', type: 'stock', currency: 'USD' },
  ]

  type Body = Record<string, unknown> | undefined
  type Handler = (ctx: { params: string[]; query: URLSearchParams; body: Body; form: FormData | null }) => unknown
  const routes: [string, RegExp, Handler][] = []
  const on = (method: string, pattern: string, h: Handler) => routes.push([method, new RegExp('^' + pattern.replace(/:id/g, '(\\d+)') + '$'), h])
  const num = (v: string | null) => (v ? Number(v) : undefined)
  const need = <X>(x: X | undefined, what = 'Eintrag'): X => { if (x === undefined) throw new HttpError(404, `${what} nicht gefunden.`); return x }

  // accounts
  on('GET', '/accounts', () => accounts.slice().sort((a, b) => a.sort_order - b.sort_order).map(accountOut))
  on('POST', '/accounts', ({ body }) => {
    const b = body as unknown as T.AccountInput
    if (!b?.name?.trim()) throw new HttpError(422, 'name: Pflichtfeld')
    const a: RawAccount = { id: nextId++, name: b.name, institution: b.institution || 'other', type: (b.type as RawAccount['type']) ?? 'checking', currency: b.currency ?? 'EUR', iban: b.iban ?? null, color: b.color ?? '#79838a',
      opening_balance: b.opening_balance ?? 0, settlement_account_id: b.settlement_account_id ?? null, is_active: b.is_active ?? true, interest_rate: b.interest_rate ?? null, sort_order: accounts.length + 1 }
    accounts.push(a)
    return accountOut(a)
  })
  on('GET', '/accounts/:id', ({ params }) => {
    const a = need(acc(Number(params[0])), 'Konto')
    const days: number[] = []
    for (let d = 730; d >= 0; d--) days.push(d)
    const history = txs.length ? walk(days, a.id).map((p) => ({ date: p.date, value: p.net_worth })) : []
    const snaps = snapshots.filter((s) => s.account_id === a.id)
    const base: T.BalanceSnapshot[] = a.id === 1 && !empty ? [{ date: isoAgo(72), balance: cashBalance(1, isoAgo(72)) }, { date: isoAgo(42), balance: round2(cashBalance(1, isoAgo(42)) + 129.95) }] : []
    return { ...accountOut(a), history, snapshots: [...base, ...snaps].map((s) => ({ ...s, computed_balance: cashBalance(a.id, s.date) })) } satisfies T.AccountDetail
  })
  on('PATCH', '/accounts/:id', ({ params, body }) => { const a = need(acc(Number(params[0])), 'Konto'); Object.assign(a, body); return accountOut(a) })
  on('DELETE', '/accounts/:id', ({ params }) => {
    const id = Number(params[0])
    const i = accounts.findIndex((a) => a.id === id)
    const count = txs.filter((t) => t.account_id === id || t.cash_account_id === id).length
    // like the backend: accounts with bookings are only deactivated
    if (count > 0) { if (i >= 0) accounts[i].is_active = false; return { deleted: false, deactivated: true, transaction_count: count } }
    if (i >= 0) accounts.splice(i, 1)
    return { deleted: true, deactivated: false, transaction_count: 0 }
  })

  // transactions
  on('GET', '/transactions', ({ query }) => {
    const f = { account_id: num(query.get('account_id')), instrument_id: num(query.get('instrument_id')), type: query.get('type'), category_id: num(query.get('category_id')), q: query.get('q')?.toLowerCase(), from: query.get('date_from'), to: query.get('date_to') }
    const all = txs.filter((t) =>
      (!f.account_id || t.account_id === f.account_id || t.cash_account_id === f.account_id) && (!f.instrument_id || t.instrument_id === f.instrument_id) && (!f.type || t.type === f.type) &&
      (!f.category_id || t.category_id === f.category_id) && (!f.from || t.booking_date >= f.from) && (!f.to || t.booking_date <= f.to) &&
      (!f.q || [t.counterparty, t.description, instr(t.instrument_id)?.name, instr(t.instrument_id)?.isin, acc(t.account_id)?.name].some((s) => s?.toLowerCase().includes(f.q!))))
      .sort((a, b) => b.booking_date.localeCompare(a.booking_date) || b.id - a.id)
    const offset = num(query.get('offset')) ?? 0
    const limit = num(query.get('limit')) ?? 50
    return { items: all.slice(offset, offset + limit).map(txOut), total: all.length } satisfies T.TransactionPage
  })
  on('POST', '/transactions', ({ body }) => txOut(createTx(body as unknown as T.TransactionCreate)))
  on('POST', '/transactions/transfer', ({ body }) => {
    const b = body as unknown as T.TransferInput
    if (b.from_account_id === b.to_account_id) throw new HttpError(422, 'to_account_id: Quell- und Zielkonto müssen verschieden sein')
    const out = createTx({ account_id: b.from_account_id, type: 'transfer_out', booking_date: b.date, amount: -Math.abs(b.amount), description: b.description, counterparty: acc(b.to_account_id)?.name, category_id: 13 })
    const inn = createTx({ account_id: b.to_account_id, type: 'transfer_in', booking_date: b.date, amount: Math.abs(b.amount), description: b.description, counterparty: acc(b.from_account_id)?.name, category_id: 13 })
    out.transfer_peer_id = inn.id; inn.transfer_peer_id = out.id
    return [txOut(out), txOut(inn)]
  })
  on('PATCH', '/transactions/:id', ({ params, body }) => { const t = need(txs.find((x) => x.id === Number(params[0])), 'Transaktion'); Object.assign(t, body); return txOut(t) })
  on('DELETE', '/transactions/:id', ({ params }) => { txs = txs.filter((t) => t.id !== Number(params[0])); return undefined })

  // categories
  on('GET', '/categories', () => categories)
  on('POST', '/categories', ({ body }) => { const c = { id: nextId++, parent_id: null, color: null, match_rules: [], ...(body as object) } as unknown as T.Category; if (!c.name?.trim()) throw new HttpError(422, 'name: Pflichtfeld'); categories.push(c); return c })
  on('POST', '/categories/auto-assign', () => {
    let assigned = 0
    for (const t of txs) {
      if (t.category_id != null || t.instrument_id != null) continue
      const hay = `${t.counterparty ?? ''} ${t.description ?? ''}`.toLowerCase()
      const c = categories.find((x) => x.match_rules?.some((r) => r && hay.includes(r.toLowerCase())))
      if (c) { t.category_id = c.id; assigned++ }
    }
    return { assigned }
  })
  on('PATCH', '/categories/:id', ({ params, body }) => { const c = need(cat(Number(params[0])), 'Kategorie'); Object.assign(c, body); return c })
  on('DELETE', '/categories/:id', ({ params }) => { const id = Number(params[0]); const i = categories.findIndex((c) => c.id === id); if (i >= 0) categories.splice(i, 1); for (const t of txs) if (t.category_id === id) t.category_id = null; return undefined })

  // instruments
  on('GET', '/instruments', ({ query }) => {
    const q = query.get('q')?.toLowerCase()
    const w = query.get('watchlist')
    return instruments.filter((i) => (!q || [i.name, i.isin, i.symbol, i.wkn].some((s) => s?.toLowerCase().includes(q))) && (w == null || String(i.is_watchlist) === w)).map((i) => ({ ...i, quote: quote(i.id) }))
  })
  on('POST', '/instruments', ({ body }) => { const b = body as Partial<T.Instrument>; if (!b.isin && !b.symbol) throw new HttpError(422, 'isin oder symbol erforderlich'); const i = findOrCreateInstrument(b); Object.assign(i, b); return i })
  on('GET', '/instruments/:id', ({ params }) => {
    const i = need(instr(Number(params[0])), 'Wertpapier')
    return { ...i, quote: quote(i.id), position: holdings().find((h) => h.instrument_id === i.id) ?? null, transactions: txs.filter((t) => t.instrument_id === i.id).sort((a, b) => b.booking_date.localeCompare(a.booking_date)).map(txOut) } satisfies T.InstrumentDetail
  })
  on('PATCH', '/instruments/:id', ({ params, body }) => { const i = need(instr(Number(params[0])), 'Wertpapier'); Object.assign(i, body); return i })

  // portfolio
  on('GET', '/portfolio/summary', summary)
  on('GET', '/portfolio/holdings', ({ query }) => holdings(num(query.get('account_id'))))
  on('GET', '/portfolio/history', ({ query }) => (txs.length ? walk(sampleDays((query.get('range') as T.Range) || '1Y'), num(query.get('account_id'))).map(({ flow: _f, ...p }) => p) : []))
  on('GET', '/portfolio/performance', ({ query }) => performance((query.get('range') as T.Range) || '1Y'))
  on('GET', '/portfolio/allocation', ({ query }) => allocation((query.get('by') as T.AllocationBy) || 'type', query.get('lookthrough') === 'true'))

  // analytics
  on('GET', '/analytics/dividends', ({ query }) => dividends(num(query.get('year'))))
  on('GET', '/analytics/cashflow', ({ query }) => cashflow(num(query.get('months')) ?? 12))
  on('GET', '/analytics/expenses-by-category', ({ query }) => expensesByCategory(query.get('date_from') ?? undefined, query.get('date_to') ?? undefined))
  on('GET', '/analytics/forecast', ({ query }) => forecast(num(query.get('years')) ?? 10, num(query.get('monthly_savings')) ?? 0, Number(query.get('expected_return') ?? 5)))
  on('GET', '/analytics/realized-gains', ({ query }) => { const y = query.get('year'); return positions().flatMap((p) => p.realized).filter((r) => !y || r.date.startsWith(y)) })

  // market
  // Background refresh like the backend: POST returns a running job, `/market/status` → `refresh` reports progress and result.
  let refreshJob: T.RefreshJob | null = null
  let lastJob: T.RefreshJob | null = null
  const stepsOf = (scope: string) => (scope === 'all' ? ['fx', 'quotes', 'profile', 'history', 'events', 'fundamentals', 'etf'] : [scope])
  function finishRefresh() {
    const job = refreshJob
    if (!job) return
    const steps = stepsOf(job.scope)
    const one = (sc: string): T.RefreshResult => (offline
      ? { scope: sc, status: 'error', errors: [{ instrument_id: null, symbol: null, error: 'ProviderError: Yahoo nicht erreichbar (Timeout)' }], duration_ms: 900 }
      : { scope: sc, status: 'ok', errors: [], duration_ms: 300 })
    const result: T.RefreshResult = job.scope === 'all' ? { scope: 'all', status: offline ? 'error' : 'ok', jobs: Object.fromEntries(steps.map((x) => [x, one(x)])) } : one(job.scope)
    if (!offline) lastRefresh = new Date()
    for (const sc of steps) {
      const i = jobs.findIndex((j) => j.job === sc)
      if (i >= 0) jobs.splice(i, 1)
      jobs.unshift({ job: sc, status: offline ? 'error' : 'ok', detail: offline ? 'processed=0; total=8; [-] ProviderError: Yahoo nicht erreichbar (Timeout)' : `processed=${instruments.length}; total=${instruments.length}`, created_at: new Date().toISOString() })
    }
    lastJob = { ...job, status: result.status, finished_at: new Date().toISOString(), progress: { done: steps.length, total: steps.length, current: null }, result }
    refreshJob = null
  }
  on('POST', '/market/refresh', ({ body }) => {
    const scope = String((body as { scope?: string })?.scope ?? 'quotes')
    if (refreshJob) return { ...refreshJob, already_running: true }
    refreshJob = { job_id: `mock${nextId++}`, scope, status: 'running', background: true, started_at: new Date().toISOString(), finished_at: null, progress: { done: 0, total: stepsOf(scope).length, current: stepsOf(scope)[0] }, result: null }
    setTimeout(finishRefresh, 2500)
    return refreshJob
  })
  on('GET', '/market/quotes', ({ query }) => (query.get('instrument_ids') ?? '').split(',').filter(Boolean).map((s) => quote(Number(s))).filter(Boolean))
  on('GET', '/market/history/:id', ({ params, query }) => {
    const id = Number(params[0])
    if (!hasPrice(id) || !instr(id)) return []
    return sampleDays((query.get('range') as T.Range) || '1Y', false).map((ago) => ({ date: isoAgo(ago), close: Math.round(priceNative(id, ago) * 100) / 100 }))
  })
  on('GET', '/market/events', ({ query }) => eventsOut(query.get('from') ?? undefined, query.get('to') ?? undefined, query.get('types') ?? undefined))
  on('POST', '/market/events', ({ body }) => {
    const b = body as unknown as T.MarketEventInput
    if (!instr(b.instrument_id)) throw new HttpError(422, 'instrument_id: Wertpapier nicht gefunden')
    if (!b.date) throw new HttpError(422, 'date: Pflichtfeld')
    const e: RawEvent = { id: nextId++, instrument_id: b.instrument_id, type: b.type, date: b.date, amount: b.amount ?? null, currency: b.currency ?? null, title: b.title ?? null, url: b.url ?? null, is_estimate: false }
    events = [...events, e].sort((a, c) => a.date.localeCompare(c.date))
    return eventsOut().find((x) => x.id === e.id) ?? e
  })
  on('GET', '/market/fundamentals/:id', ({ params }) => fundamentals(Number(params[0])))
  on('GET', '/market/etf/:id/holdings', ({ params }) => etf(Number(params[0])))
  on('POST', '/market/etf/:id/holdings/upload', ({ params }) => {
    const id = Number(params[0])
    const full = etfRows(1).rows.map((r) => ({ ...r, weight: Math.round(r.weight * 0.9 * 100) / 100 }))
    etfOverrides.set(id, { source: 'manual', rows: full })
    return etf(id)
  })
  on('GET', '/market/lookthrough', ({ query }) => lookthrough(num(query.get('account_id'))))
  on('GET', '/market/status', () => ({ source: 'yahoo', online: !offline, last_quote_update: txs.length || instruments.length ? lastRefresh.toISOString() : null, jobs: empty ? [] : jobs.slice(0, 8), errors: offline ? ['quotes: Yahoo Finance nicht erreichbar (Timeout nach 10 s)'] : [],
    refresh: { running: !!refreshJob, current: refreshJob, last: lastJob } }) satisfies T.MarketStatus)
  on('GET', '/market/search', ({ query }) => {
    const q = (query.get('q') ?? '').toLowerCase().trim()
    if (offline) throw new HttpError(503, 'Suche derzeit nicht möglich: Yahoo Finance ist nicht erreichbar. Das Wertpapier lässt sich mit ISIN oder Symbol trotzdem anlegen.')
    if (q.length < 2) return []
    const all: T.SearchResult[] = [...SEEDS.map((s) => ({ symbol: s.symbol, name: s.name, isin: s.isin, exchange: s.exchange, type: s.type, currency: s.currency })), ...SEARCH_EXTRA]
    return all.filter((r) => [r.name, r.symbol, r.isin].some((s) => s?.toLowerCase().includes(q))).slice(0, 8)
  })

  // documents
  on('GET', '/documents', ({ query }) => { const s = query.get('status'); return documents.filter((d) => !s || d.status === s).sort((a, b) => b.created_at.localeCompare(a.created_at)).map(docSummary) })
  on('POST', '/documents', ({ form }) => {
    const files = form ? [...form.getAll('files'), ...form.getAll('files[]')].filter((f): f is File => f instanceof File) : []
    if (!files.length) throw new HttpError(422, 'files: Keine Datei übermittelt')
    const account_id = form?.get('account_id') ? Number(form.get('account_id')) : null
    return files.map((f) => {
      const kind = f.type.startsWith('image/') ? 'image' : f.type === 'application/pdf' || f.name.endsWith('.pdf') ? 'pdf' : f.name.endsWith('.csv') ? 'csv' : 'text'
      const d: T.DocumentDetail = { id: nextId++, filename: f.name || 'Zwischenablage.png', source_kind: kind, status: 'uploaded', detected_institution: null, detected_doc_type: null, parser: null, draft_count: 0, pending_count: 0, error: null,
        created_at: new Date().toISOString(), processing_ms: null, account_id, drafts: [], raw_text: null, mime_type: f.type }
      uploads.set(d.id, f)
      documents.push(d); scheduleProcessing(d)
      return docSummary(d)
    })
  })
  on('POST', '/documents/paste', ({ body }) => {
    const b = body as { text?: string; account_id?: number }
    if (!b?.text?.trim()) throw new HttpError(422, 'text: Bitte Text einfügen')
    const d: T.DocumentDetail = { id: nextId++, filename: 'Eingefügter Text', source_kind: 'paste', status: 'uploaded', detected_institution: null, detected_doc_type: null, parser: null, draft_count: 0, pending_count: 0, error: null,
      created_at: new Date().toISOString(), processing_ms: null, account_id: b.account_id ?? null, drafts: [], raw_text: b.text }
    documents.push(d); scheduleProcessing(d, b.text)
    return docSummary(d)
  })
  on('GET', '/documents/llm/status', () => (offline
    ? { available: false, models: [], text_model: 'qwen2.5:7b-instruct', vision_model: 'qwen2.5vl:7b', text_model_available: false, vision_model_available: false, ocr_available: false, url: 'http://localhost:11434',
        error: 'Das lokale Sprachmodell (Ollama) ist unter http://localhost:11434 nicht erreichbar.', hint: 'Ollama starten: `ollama serve` (bzw. die Ollama-App öffnen).' }
    : { available: true, models: ['qwen2.5:7b-instruct', 'qwen2.5vl:7b', 'nomic-embed-text:latest'], text_model: 'qwen2.5:7b-instruct', vision_model: 'qwen2.5vl:7b', text_model_available: true, vision_model_available: true, ocr_available: false, url: 'http://localhost:11434', error: null, hint: null }) satisfies T.LlmStatus)
  const doc = (id: string) => need(documents.find((d) => d.id === Number(id)), 'Dokument')
  const draft = (id: string) => { for (const d of documents) { const x = d.drafts.find((y) => y.id === Number(id)); if (x) return [d, x] as const } throw new HttpError(404, 'Entwurf nicht gefunden.') }
  on('GET', '/documents/:id', ({ params }) => { const d = doc(params[0]); settleDoc(d); return d })
  on('GET', '/documents/:id/file', ({ params }) => {
    const d = doc(params[0])
    const up = uploads.get(d.id)
    if (up) return up
    if (d.source_kind === 'paste' || !d.raw_text) throw new HttpError(404, 'Für dieses Dokument ist keine Originaldatei gespeichert.')
    return new Blob([renderDocSvg(d.raw_text, d.source_kind === 'image')], { type: 'image/svg+xml' })
  })
  on('PATCH', '/documents/drafts/:id', ({ params, body }) => {
    const [d, x] = draft(params[0])
    const b = body as { payload?: T.DraftPayload; status?: string }
    if (x.status === 'accepted') throw new HttpError(409, 'Der Entwurf wurde bereits übernommen.')
    x.payload = { ...x.payload, ...(b?.payload ?? {}) }
    if (b?.status === 'pending') { x.status = 'pending'; d.status = 'review' }
    return x
  })
  on('POST', '/documents/drafts/:id/accept', ({ params }) => { const [d, x] = draft(params[0]); acceptDraft(d, x); settleDoc(d); return x })
  on('POST', '/documents/drafts/:id/reject', ({ params }) => { const [d, x] = draft(params[0]); if (x.status === 'pending') x.status = 'rejected'; settleDoc(d); return x })
  on('POST', '/documents/:id/accept-all', ({ params, query }) => {
    const d = doc(params[0])
    const flagged = query.get('include_flagged') === 'true'
    const res: T.AcceptAllResult = { accepted: 0, skipped: 0, duplicates: 0, errors: [] }
    for (const x of d.drafts) {
      if (x.status !== 'pending') continue
      if (x.payload.review_required && !flagged) { res.skipped++; continue }
      acceptDraft(d, x)
      if ((x.status as T.DraftStatus) === 'accepted') res.accepted++
    }
    settleDoc(d)
    return res
  })
  on('POST', '/documents/:id/reprocess', ({ params }) => { const d = doc(params[0]); d.status = 'uploaded'; d.error = null; d.drafts = []; settleDoc(d); scheduleProcessing(d, d.source_kind === 'paste' ? d.raw_text ?? undefined : undefined); return docSummary(d) })
  on('DELETE', '/documents/:id', ({ params }) => { documents = documents.filter((d) => d.id !== Number(params[0])); return undefined })

  const uploads = new Map<number, File>()

  async function handle(method: string, url: URL, body: Body, form: FormData | null): Promise<{ status: number; data: unknown }> {
    const path = url.pathname.replace(/^\/api/, '').replace(/\/$/, '') || '/'
    for (const [m, re, h] of routes) {
      if (m !== method) continue
      const match = re.exec(path)
      if (!match) continue
      try {
        return { status: 200, data: h({ params: match.slice(1), query: url.searchParams, body, form }) }
      } catch (e) {
        if (e instanceof HttpError) return { status: e.status, data: { detail: e.message } }
        throw e
      }
    }
    return { status: 404, data: { detail: `Mock: ${method} ${path} ist nicht implementiert.` } }
  }
  return { handle }
}

function renderDocSvg(text: string, phone: boolean): string {
  const lines = text.split('\n')
  const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  const w = phone ? 420 : 760
  const lh = phone ? 26 : 22
  const h = Math.max(phone ? 760 : 980, lines.length * lh + 120)
  const bg = phone ? '#0f1114' : '#ffffff'
  const fg = phone ? '#f2f3f5' : '#1b1b1b'
  const rows = lines.map((l, i) => `<text x="${phone ? 28 : 56}" y="${80 + i * lh}" font-size="${phone ? (i < 3 ? 20 : 15) : i === 2 ? 17 : 13}" font-weight="${(phone && i < 3) || (!phone && i === 2) ? 700 : 400}" xml:space="preserve">${esc(l)}</text>`).join('')
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><rect width="100%" height="100%" fill="${bg}"/><g fill="${fg}" font-family="${phone ? 'Helvetica, Arial, sans-serif' : 'Courier New, monospace'}">${rows}</g></svg>`
}
