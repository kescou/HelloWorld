// Deterministic fixture dataset for mock mode. Everything derives from the transaction journal and
// synthetic price paths, so balances, holdings, history and analytics stay internally consistent.
import type { Category, DocumentDetail, EtfHoldingRow, EventType, Instrument, InstrumentType, Transaction, TxType } from '../api/types'
import { isoDate } from '../lib/format'

export function mulberry32(seed: number) {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
const gauss = (r: () => number) => Math.sqrt(-2 * Math.log(1 - r() + 1e-12)) * Math.cos(2 * Math.PI * r())
export const round2 = (n: number) => Math.round(n * 100) / 100
const round4 = (n: number) => Math.round(n * 10000) / 10000

export const TODAY = (() => { const d = new Date(); return new Date(d.getFullYear(), d.getMonth(), d.getDate()) })()
export const HISTORY_DAYS = 3660
export const daysAgo = (n: number) => { const d = new Date(TODAY); d.setDate(d.getDate() - n); return d }
export const isoAgo = (n: number) => isoDate(daysAgo(n))
export const agoOf = (iso: string) => Math.round((TODAY.getTime() - new Date(iso + 'T00:00:00').getTime()) / 86400000)
export const FX: Record<string, number> = { EUR: 1, USD: 1.08, CHF: 0.94 }

export interface RawAccount {
  id: number; name: string; institution: string; type: 'checking' | 'savings' | 'brokerage'; currency: string
  iban: string | null; color: string; opening_balance: number; opening_date?: string | null; settlement_account_id: number | null
  is_active: boolean; interest_rate: number | null; sort_order: number
}
export const ACCOUNTS: RawAccount[] = [
  { id: 1, name: 'Postbank Giro', institution: 'postbank', type: 'checking', currency: 'EUR', iban: 'DE02100100100006820101', color: '#bd861b', opening_balance: 3200, settlement_account_id: null, is_active: true, interest_rate: null, sort_order: 1 },
  { id: 2, name: 'ING Girokonto', institution: 'ing', type: 'checking', currency: 'EUR', iban: 'DE12500105170648489890', color: '#b4553d', opening_balance: 6500, settlement_account_id: null, is_active: true, interest_rate: null, sort_order: 2 },
  { id: 3, name: 'ING Depot', institution: 'ing', type: 'brokerage', currency: 'EUR', iban: null, color: '#93536a', opening_balance: 0, settlement_account_id: 2, is_active: true, interest_rate: null, sort_order: 3 },
  { id: 4, name: 'Scalable Depot', institution: 'scalable', type: 'brokerage', currency: 'EUR', iban: null, color: '#14607a', opening_balance: 0, settlement_account_id: 5, is_active: true, interest_rate: null, sort_order: 4 },
  { id: 5, name: 'Scalable Tagesgeld', institution: 'scalable', type: 'savings', currency: 'EUR', iban: 'DE75120300001082345671', color: '#6c8db0', opening_balance: 9000, settlement_account_id: null, is_active: true, interest_rate: 2.0, sort_order: 5 },
  { id: 6, name: 'Trade Republic Depot', institution: 'trade_republic', type: 'brokerage', currency: 'EUR', iban: null, color: '#5b8a58', opening_balance: 0, settlement_account_id: 7, is_active: true, interest_rate: null, sort_order: 6 },
  { id: 7, name: 'Trade Republic Tagesgeld', institution: 'trade_republic', type: 'savings', currency: 'EUR', iban: 'DE89100123450123456789', color: '#7b783a', opening_balance: 12000, settlement_account_id: null, is_active: true, interest_rate: 2.25, sort_order: 7 },
]

interface InstrumentSeed {
  id: number; isin: string; wkn: string; symbol: string; name: string; type: InstrumentType; currency: string
  price: number; sector: string | null; industry: string | null; country: string | null; exchange: string
  eps: number; growth: number; payout: number; revenueBn: number; sharesM: number
  divMonths: number[]; watch?: boolean; fund_provider?: string; ter?: number; distribution?: string
  website?: string; ir_url?: string
}
const S: InstrumentSeed[] = [
  { id: 1, isin: 'IE00B4L5Y983', wkn: 'A0RPWH', symbol: 'EUNL.DE', name: 'iShares Core MSCI World UCITS ETF (Acc)', type: 'etf', currency: 'EUR', price: 104.32, sector: null, industry: null, country: null, exchange: 'XETRA', eps: 4.6, growth: 0.085, payout: 0, revenueBn: 0, sharesM: 0, divMonths: [], fund_provider: 'ishares', ter: 0.2, distribution: 'accumulating', website: 'https://www.ishares.com/de' },
  { id: 2, isin: 'IE00B3RBWM25', wkn: 'A1JX52', symbol: 'VGWL.DE', name: 'Vanguard FTSE All-World UCITS ETF (Dist)', type: 'etf', currency: 'EUR', price: 132.1, sector: null, industry: null, country: null, exchange: 'XETRA', eps: 6.3, growth: 0.075, payout: 0.36, revenueBn: 0, sharesM: 0, divMonths: [2, 5, 8, 11], fund_provider: 'vanguard', ter: 0.22, distribution: 'distributing', website: 'https://www.de.vanguard' },
  { id: 3, isin: 'US0378331005', wkn: '865985', symbol: 'AAPL', name: 'Apple Inc.', type: 'stock', currency: 'USD', price: 228.5, sector: 'Technologie', industry: 'Unterhaltungselektronik', country: 'USA', exchange: 'NASDAQ', eps: 7.1, growth: 0.11, payout: 0.15, revenueBn: 405, sharesM: 15100, divMonths: [1, 4, 7, 10], website: 'https://www.apple.com', ir_url: 'https://investor.apple.com' },
  { id: 4, isin: 'US5949181045', wkn: '870747', symbol: 'MSFT', name: 'Microsoft Corp.', type: 'stock', currency: 'USD', price: 431.2, sector: 'Technologie', industry: 'Software', country: 'USA', exchange: 'NASDAQ', eps: 13.2, growth: 0.14, payout: 0.25, revenueBn: 270, sharesM: 7430, divMonths: [2, 5, 8, 11], website: 'https://www.microsoft.com', ir_url: 'https://www.microsoft.com/investor' },
  { id: 5, isin: 'DE0007164600', wkn: '716460', symbol: 'SAP.DE', name: 'SAP SE', type: 'stock', currency: 'EUR', price: 221.4, sector: 'Technologie', industry: 'Software', country: 'Deutschland', exchange: 'XETRA', eps: 5.9, growth: 0.1, payout: 0.4, revenueBn: 36.8, sharesM: 1167, divMonths: [4], website: 'https://www.sap.com', ir_url: 'https://www.sap.com/investors' },
  { id: 6, isin: 'DE0008404005', wkn: '840400', symbol: 'ALV.DE', name: 'Allianz SE', type: 'stock', currency: 'EUR', price: 302.6, sector: 'Finanzen', industry: 'Versicherung', country: 'Deutschland', exchange: 'XETRA', eps: 26.4, growth: 0.065, payout: 0.6, revenueBn: 179, sharesM: 386, divMonths: [4], website: 'https://www.allianz.com', ir_url: 'https://www.allianz.com/de/investor_relations.html' },
  { id: 7, isin: 'DE0007236101', wkn: '723610', symbol: 'SIE.DE', name: 'Siemens AG', type: 'stock', currency: 'EUR', price: 186.2, sector: 'Industrie', industry: 'Mischkonzern', country: 'Deutschland', exchange: 'XETRA', eps: 10.6, growth: 0.07, payout: 0.49, revenueBn: 78.9, sharesM: 785, divMonths: [1], website: 'https://www.siemens.com', ir_url: 'https://www.siemens.com/investor' },
  { id: 8, isin: 'US4781601046', wkn: '853260', symbol: 'JNJ', name: 'Johnson & Johnson', type: 'stock', currency: 'USD', price: 156.4, sector: 'Gesundheit', industry: 'Pharma', country: 'USA', exchange: 'NYSE', eps: 10.1, growth: 0.045, payout: 0.5, revenueBn: 89, sharesM: 2410, divMonths: [2, 5, 8, 11], website: 'https://www.jnj.com', ir_url: 'https://www.investor.jnj.com' },
  { id: 9, isin: 'DE0008430026', wkn: '843002', symbol: 'MUV2.DE', name: 'Münchener Rück AG', type: 'stock', currency: 'EUR', price: 478.3, sector: 'Finanzen', industry: 'Rückversicherung', country: 'Deutschland', exchange: 'XETRA', eps: 44.5, growth: 0.08, payout: 0.45, revenueBn: 61, sharesM: 131, divMonths: [4], website: 'https://www.munichre.com', ir_url: 'https://www.munichre.com/de/unternehmen/investoren.html' },
  { id: 10, isin: 'NL0010273215', wkn: 'A1J4U4', symbol: 'ASML.AS', name: 'ASML Holding N.V.', type: 'stock', currency: 'EUR', price: 655.8, sector: 'Technologie', industry: 'Halbleiter', country: 'Niederlande', exchange: 'AMS', eps: 22.8, growth: 0.16, payout: 0.3, revenueBn: 31.2, sharesM: 393, divMonths: [1, 4, 7, 10], website: 'https://www.asml.com', ir_url: 'https://www.asml.com/en/investors' },
  { id: 11, isin: 'US02079K3059', wkn: 'A14Y6F', symbol: 'GOOGL', name: 'Alphabet Inc. (A)', type: 'stock', currency: 'USD', price: 172.3, sector: 'Kommunikation', industry: 'Internetdienste', country: 'USA', exchange: 'NASDAQ', eps: 8.6, growth: 0.15, payout: 0.1, revenueBn: 372, sharesM: 12200, divMonths: [2, 5, 8, 11], watch: true, website: 'https://abc.xyz', ir_url: 'https://abc.xyz/investor' },
  { id: 12, isin: 'CH0038863350', wkn: 'A0Q4DC', symbol: 'NESN.SW', name: 'Nestlé S.A.', type: 'stock', currency: 'CHF', price: 78.4, sector: 'Basiskonsum', industry: 'Nahrungsmittel', country: 'Schweiz', exchange: 'SIX', eps: 4.5, growth: 0.03, payout: 0.68, revenueBn: 91, sharesM: 2600, divMonths: [3], watch: true, website: 'https://www.nestle.com', ir_url: 'https://www.nestle.com/investors' },
  { id: 13, isin: 'DE000BASF111', wkn: 'BASF11', symbol: 'BAS.DE', name: 'BASF SE', type: 'stock', currency: 'EUR', price: 44.1, sector: 'Grundstoffe', industry: 'Chemie', country: 'Deutschland', exchange: 'XETRA', eps: 3.2, growth: 0.005, payout: 0.72, revenueBn: 65, sharesM: 892, divMonths: [4], watch: true, website: 'https://www.basf.com', ir_url: 'https://www.basf.com/investor' },
]
export const SEEDS = S
export const seedOf = (id: number) => S.find((s) => s.id === id)

export function buildInstruments(): Instrument[] {
  return S.map((s) => ({
    id: s.id, isin: s.isin, wkn: s.wkn, symbol: s.symbol, name: s.name, type: s.type, currency: s.currency, exchange: s.exchange,
    sector: s.sector, industry: s.industry, country: s.country, fund_provider: s.fund_provider ?? null, ter: s.ter ?? null,
    distribution: s.distribution ?? null, website: s.website ?? null, ir_url: s.ir_url ?? null, is_watchlist: !!s.watch,
  }))
}

/** Native-currency close per instrument, index = days ago (0 = today). Price mean-reverts around EPS × base P/E. */
export const PRICES: Record<number, Float64Array> = {}
export const PE_BASE: Record<number, number> = {}
for (const s of S) {
  const r = mulberry32(1000 + s.id * 77)
  const dev = new Float64Array(HISTORY_DAYS + 1)
  const sigma = s.type === 'etf' ? 0.007 : 0.013
  dev[0] = (r() - 0.45) * 0.16
  for (let i = 1; i <= HISTORY_DAYS; i++) dev[i] = dev[i - 1] * 0.994 + sigma * gauss(r)
  const p = new Float64Array(HISTORY_DAYS + 1)
  for (let i = 0; i <= HISTORY_DAYS; i++) p[i] = s.price * Math.exp(-s.growth * (i / 365)) * Math.exp(dev[i] - dev[0])
  PRICES[s.id] = p
  PE_BASE[s.id] = (s.price / s.eps) * Math.exp(-dev[0])
}
export const priceNative = (id: number, ago: number) => PRICES[id][Math.max(0, Math.min(HISTORY_DAYS, Math.round(ago)))]
export const priceEur = (id: number, ago: number) => priceNative(id, ago) / FX[seedOf(id)!.currency]
export const epsAt = (s: InstrumentSeed, ago: number) => s.eps * Math.exp(-s.growth * (ago / 365))
export const dpsAnnual = (s: InstrumentSeed, ago = 0) => epsAt(s, ago) * s.payout

export const CATEGORIES: Category[] = [
  { id: 1, name: 'Gehalt', kind: 'income', parent_id: null, color: '#5b8a58', match_rules: ['gehalt', 'lohn'] },
  { id: 2, name: 'Zinsen & Dividenden', kind: 'income', parent_id: null, color: '#14607a', match_rules: ['zinsen', 'dividende'] },
  { id: 3, name: 'Sonstige Einnahmen', kind: 'income', parent_id: null, color: '#79838a', match_rules: ['erstattung'] },
  { id: 4, name: 'Wohnen', kind: 'expense', parent_id: null, color: '#14607a', match_rules: ['miete', 'stadtwerke', 'hausverwaltung'] },
  { id: 5, name: 'Lebensmittel', kind: 'expense', parent_id: null, color: '#bd861b', match_rules: ['rewe', 'edeka', 'aldi', 'lidl', 'dm-drogerie'] },
  { id: 6, name: 'Mobilität', kind: 'expense', parent_id: null, color: '#6c8db0', match_rules: ['deutschlandticket', 'db vertrieb', 'aral', 'shell'] },
  { id: 7, name: 'Versicherungen', kind: 'expense', parent_id: null, color: '#93536a', match_rules: ['huk', 'versicherung'] },
  { id: 8, name: 'Freizeit & Restaurant', kind: 'expense', parent_id: null, color: '#b4553d', match_rules: ['restaurant', 'lieferando', 'kino', 'fitness'] },
  { id: 9, name: 'Abos & Medien', kind: 'expense', parent_id: null, color: '#7b783a', match_rules: ['netflix', 'spotify', 'telekom', 'rundfunk'] },
  { id: 10, name: 'Gesundheit', kind: 'expense', parent_id: null, color: '#5b8a58', match_rules: ['apotheke', 'praxis'] },
  { id: 11, name: 'Shopping', kind: 'expense', parent_id: null, color: '#79838a', match_rules: ['amazon', 'zalando', 'ikea'] },
  { id: 12, name: 'Reisen', kind: 'expense', parent_id: null, color: '#b4553d', match_rules: ['booking.com', 'lufthansa', 'airbnb'] },
  { id: 13, name: 'Umbuchung', kind: 'transfer', parent_id: null, color: null, match_rules: [] },
  { id: 14, name: 'Wertpapiersparen', kind: 'investment', parent_id: null, color: null, match_rules: ['sparplan'] },
]

type TxSeed = Omit<Transaction, 'id' | 'account_name' | 'instrument_name' | 'instrument_isin' | 'category_name'>
const MONTHS = 36

export function buildTransactions(): TxSeed[] {
  const out: TxSeed[] = []
  const r = mulberry32(42)
  const base = (account_id: number, type: TxType, date: Date, amount: number, extra: Partial<TxSeed> = {}): TxSeed => ({
    account_id, cash_account_id: null, instrument_id: null, type, booking_date: isoDate(date), value_date: isoDate(date),
    quantity: null, price: null, amount: round2(amount), fees: 0, taxes: 0, currency: 'EUR', fx_rate: null, category_id: null,
    counterparty: null, description: null, transfer_peer_id: null, source_document_id: null, notes: null, ...extra,
  })
  const push = (t: TxSeed) => { if (new Date(t.booking_date + 'T00:00:00') <= TODAY) out.push(t) }
  const cash = (acc: number, date: Date, amount: number, counterparty: string, description: string, category_id: number | null) =>
    push(base(acc, amount >= 0 ? 'deposit' : 'withdrawal', date, amount, { counterparty, description, category_id }))
  const transfer = (from: number, to: number, date: Date, amount: number, description: string) => {
    push(base(from, 'transfer_out', date, -amount, { description, category_id: 13, counterparty: ACCOUNTS[to - 1].name }))
    push(base(to, 'transfer_in', date, amount, { description, category_id: 13, counterparty: ACCOUNTS[from - 1].name }))
  }
  const settle = (depot: number) => ACCOUNTS[depot - 1].settlement_account_id!
  const buy = (depot: number, instr: number, date: Date, opts: { qty?: number; amount?: number; fee?: number; plan?: boolean }) => {
    const ago = Math.round((TODAY.getTime() - date.getTime()) / 86400000)
    if (ago < 0) return
    const price = round4(priceEur(instr, ago))
    const fee = opts.fee ?? 0
    const qty = opts.qty ?? round4((opts.amount! - fee) / price)
    push(base(depot, 'buy', date, -(qty * price + fee), {
      cash_account_id: settle(depot), instrument_id: instr, quantity: qty, price, fees: fee, category_id: 14,
      description: opts.plan ? 'Sparplanausführung' : 'Kauf', counterparty: ACCOUNTS[depot - 1].institution === 'ing' ? 'ING-DiBa AG' : null,
    }))
  }
  const sell = (depot: number, instr: number, date: Date, qty: number, fee: number) => {
    const ago = Math.round((TODAY.getTime() - date.getTime()) / 86400000)
    const price = round4(priceEur(instr, ago))
    push(base(depot, 'sell', date, qty * price - fee, { cash_account_id: settle(depot), instrument_id: instr, quantity: qty, price, fees: fee, description: 'Verkauf' }))
  }
  const at = (monthsBack: number, day: number) => new Date(TODAY.getFullYear(), TODAY.getMonth() - monthsBack, day)

  const grocers = ['REWE Markt GmbH', 'EDEKA Center', 'ALDI SÜD', 'Lidl Dienstleistung', 'dm-drogerie markt']
  const eatOut = ['Restaurant Da Marco', 'Lieferando.de', 'Café Extrablatt', 'Kino Cinestar']
  for (let m = MONTHS; m >= 0; m--) {
    const raise = m < 10 ? 4350 : m < 22 ? 4150 : 3980
    cash(1, at(m, 27), raise, 'Nordwind Software GmbH', 'Gehalt', 1)
    cash(1, at(m, 1), -1180, 'Hausverwaltung Lindenhof', 'Miete inkl. Nebenkosten', 4)
    cash(1, at(m, 3), -(72 + (m < 12 ? 9 : 0)), 'Stadtwerke Strom & Gas', 'Abschlag Strom', 4)
    cash(1, at(m, 5), -39.95, 'Telekom Deutschland GmbH', 'Festnetz & Internet', 9)
    cash(1, at(m, 2), -(m < 20 ? 58 : 49), 'DB Vertrieb GmbH', 'Deutschlandticket', 6)
    cash(1, at(m, 12), -13.99, 'Netflix International', 'Abo', 9)
    cash(1, at(m, 14), -10.99, 'Spotify AB', 'Premium', 9)
    cash(1, at(m, 15), -29.9, 'Fitness First', 'Mitgliedsbeitrag Fitness', 8)
    if (m % 3 === 0) cash(1, at(m, 15), -55.08, 'Rundfunk ARD ZDF DRadio', 'Rundfunkbeitrag', 9)
    if (m % 12 === 2) cash(1, at(m, 4), -486.3, 'HUK-COBURG', 'Kfz- und Haftpflichtversicherung', 7)
    cash(1, at(m, 6), -64.5, 'Allianz Versicherungs-AG', 'Berufsunfähigkeitsversicherung', 7)
    const nGroc = 5 + Math.floor(r() * 3)
    for (let i = 0; i < nGroc; i++) cash(1, at(m, 2 + Math.floor(r() * 26)), -(18 + r() * 85), grocers[Math.floor(r() * grocers.length)], 'Kartenzahlung', 5)
    const nEat = 1 + Math.floor(r() * 3)
    for (let i = 0; i < nEat; i++) cash(1, at(m, 4 + Math.floor(r() * 24)), -(14 + r() * 62), eatOut[Math.floor(r() * eatOut.length)], 'Kartenzahlung', 8)
    if (r() < 0.55) cash(1, at(m, 8 + Math.floor(r() * 18)), -(35 + r() * 40), r() < 0.5 ? 'ARAL Tankstelle' : 'Shell Deutschland', 'Tanken', 6)
    if (r() < 0.6) cash(1, at(m, 5 + Math.floor(r() * 20)), -(19 + r() * 140), 'AMAZON PAYMENTS EUROPE', 'Bestellung', m < 2 ? null : 11)
    if (r() < 0.25) cash(1, at(m, 9 + Math.floor(r() * 15)), -(12 + r() * 48), 'Apotheke am Markt', 'Kartenzahlung', m < 1 ? null : 10)
    if (m % 12 === 4) cash(1, at(m, 18), -(1250 + r() * 500), 'Booking.com B.V.', 'Sommerurlaub', 12)
    if (m % 12 === 10) cash(1, at(m, 11), -(380 + r() * 260), 'Lufthansa AG', 'Flug', 12)
    if (m === 7) cash(1, at(m, 20), -749, 'IKEA Deutschland', 'Möbel', 11)
    if (m % 12 === 6) cash(1, at(m, 21), 212.4, 'Finanzamt', 'Erstattung Einkommensteuer', 3)
    // savings transfers
    transfer(1, 2, at(m, 28), 400, 'Dauerauftrag ING')
    transfer(1, 5, at(m, 28), 500, 'Sparrate Scalable')
    transfer(1, 7, at(m, 28), 400, 'Sparrate Trade Republic')
    // savings plans
    buy(4, 1, at(m, 4), { amount: 500, plan: true })
    buy(6, 2, at(m, 2), { amount: 300, plan: true })
    // interest on savings accounts (approximation on a typical balance)
    push(base(5, 'interest', at(m, 1), 3.2 + r() * 2.4, { counterparty: 'Baader Bank AG', description: 'Zinsen Tagesgeld', category_id: 2, taxes: 0 }))
    push(base(7, 'interest', at(m, 1), 17 + r() * 5, { counterparty: 'Trade Republic Bank GmbH', description: 'Zinsen Tagesgeld', category_id: 2 }))
  }
  // single stock purchases
  buy(3, 6, at(34, 16), { qty: 12, fee: 9.9 })
  buy(3, 7, at(31, 9), { qty: 20, fee: 8.65 })
  buy(3, 13, at(30, 22), { qty: 40, fee: 6.9 })
  buy(3, 9, at(26, 11), { qty: 6, fee: 10.4 })
  buy(3, 5, at(21, 5), { qty: 14, fee: 9.9 })
  buy(3, 7, at(13, 17), { qty: 10, fee: 7.4 })
  buy(3, 5, at(5, 19), { qty: 6, fee: 8.2 })
  sell(3, 13, at(8, 13), 40, 6.9)
  buy(4, 10, at(28, 8), { qty: 3, fee: 0.99 })
  buy(4, 4, at(24, 20), { qty: 8, fee: 0.99 })
  buy(4, 3, at(18, 12), { qty: 10, fee: 0.99 })
  buy(4, 10, at(9, 6), { qty: 1, fee: 0.99 })
  buy(6, 3, at(29, 14), { qty: 15, fee: 1 })
  buy(6, 8, at(23, 7), { qty: 20, fee: 1 })
  buy(6, 4, at(3, 10), { qty: 2, fee: 1 })

  // dividends: for each instrument/pay month in the past, pay on holdings at that date
  out.sort((a, b) => a.booking_date.localeCompare(b.booking_date))
  const divs: TxSeed[] = []
  for (const s of S) {
    if (!s.divMonths.length || s.payout === 0) continue
    for (let m = MONTHS; m >= 0; m--) {
      const date = at(m, 12 + (s.id % 9))
      if (date > TODAY || !s.divMonths.includes(date.getMonth())) continue
      const iso = isoDate(date)
      const ago = Math.round((TODAY.getTime() - date.getTime()) / 86400000)
      const perShareEur = dpsAnnual(s, ago) / s.divMonths.length / FX[s.currency]
      for (const depot of [3, 4, 6]) {
        let qty = 0
        for (const t of out) {
          if (t.booking_date >= iso) break
          if (t.instrument_id !== s.id || t.account_id !== depot) continue
          if (t.type === 'buy') qty += t.quantity ?? 0
          if (t.type === 'sell') qty -= t.quantity ?? 0
        }
        if (qty <= 0.0001) continue
        const gross = round2(qty * perShareEur)
        const tax = round2(gross * 0.26375)
        divs.push(base(depot, 'dividend', date, gross - tax, {
          cash_account_id: ACCOUNTS[depot - 1].settlement_account_id, instrument_id: s.id, quantity: round4(qty), price: round4(perShareEur),
          taxes: tax, category_id: 2, description: 'Dividende', counterparty: s.name,
        }))
      }
    }
  }
  out.push(...divs)
  out.sort((a, b) => a.booking_date.localeCompare(b.booking_date))
  return out
}

// ---- ETF constituents ----
interface Co { name: string; isin: string; symbol: string; sector: string; country: string; w: number; em?: boolean }
export const COMPANIES: Co[] = [
  { name: 'NVIDIA Corp.', isin: 'US67066G1040', symbol: 'NVDA', sector: 'Technologie', country: 'USA', w: 5.12 },
  { name: 'Apple Inc.', isin: 'US0378331005', symbol: 'AAPL', sector: 'Technologie', country: 'USA', w: 4.86 },
  { name: 'Microsoft Corp.', isin: 'US5949181045', symbol: 'MSFT', sector: 'Technologie', country: 'USA', w: 4.31 },
  { name: 'Amazon.com Inc.', isin: 'US0231351067', symbol: 'AMZN', sector: 'Zyklischer Konsum', country: 'USA', w: 2.74 },
  { name: 'Meta Platforms Inc.', isin: 'US30303M1027', symbol: 'META', sector: 'Kommunikation', country: 'USA', w: 1.92 },
  { name: 'Alphabet Inc. (A)', isin: 'US02079K3059', symbol: 'GOOGL', sector: 'Kommunikation', country: 'USA', w: 1.48 },
  { name: 'Broadcom Inc.', isin: 'US11135F1012', symbol: 'AVGO', sector: 'Technologie', country: 'USA', w: 1.44 },
  { name: 'Tesla Inc.', isin: 'US88160R1014', symbol: 'TSLA', sector: 'Zyklischer Konsum', country: 'USA', w: 1.36 },
  { name: 'Alphabet Inc. (C)', isin: 'US02079K1079', symbol: 'GOOG', sector: 'Kommunikation', country: 'USA', w: 1.27 },
  { name: 'JPMorgan Chase & Co.', isin: 'US46625H1005', symbol: 'JPM', sector: 'Finanzen', country: 'USA', w: 1.04 },
  { name: 'Taiwan Semiconductor', isin: 'TW0002330008', symbol: '2330.TW', sector: 'Technologie', country: 'Taiwan', w: 0.98, em: true },
  { name: 'Eli Lilly and Co.', isin: 'US5324571083', symbol: 'LLY', sector: 'Gesundheit', country: 'USA', w: 0.93 },
  { name: 'Berkshire Hathaway (B)', isin: 'US0846707026', symbol: 'BRK-B', sector: 'Finanzen', country: 'USA', w: 0.88 },
  { name: 'Visa Inc.', isin: 'US92826C8394', symbol: 'V', sector: 'Finanzen', country: 'USA', w: 0.79 },
  { name: 'Exxon Mobil Corp.', isin: 'US30231G1022', symbol: 'XOM', sector: 'Energie', country: 'USA', w: 0.68 },
  { name: 'UnitedHealth Group', isin: 'US91324P1021', symbol: 'UNH', sector: 'Gesundheit', country: 'USA', w: 0.62 },
  { name: 'Mastercard Inc.', isin: 'US57636Q1040', symbol: 'MA', sector: 'Finanzen', country: 'USA', w: 0.6 },
  { name: 'Johnson & Johnson', isin: 'US4781601046', symbol: 'JNJ', sector: 'Gesundheit', country: 'USA', w: 0.55 },
  { name: 'Procter & Gamble Co.', isin: 'US7427181091', symbol: 'PG', sector: 'Basiskonsum', country: 'USA', w: 0.52 },
  { name: 'Costco Wholesale', isin: 'US22160K1051', symbol: 'COST', sector: 'Basiskonsum', country: 'USA', w: 0.51 },
  { name: 'Tencent Holdings', isin: 'KYG875721634', symbol: '0700.HK', sector: 'Kommunikation', country: 'China', w: 0.46, em: true },
  { name: 'ASML Holding N.V.', isin: 'NL0010273215', symbol: 'ASML.AS', sector: 'Technologie', country: 'Niederlande', w: 0.45 },
  { name: 'SAP SE', isin: 'DE0007164600', symbol: 'SAP.DE', sector: 'Technologie', country: 'Deutschland', w: 0.41 },
  { name: 'Novo Nordisk A/S', isin: 'DK0062498333', symbol: 'NOVO-B.CO', sector: 'Gesundheit', country: 'Dänemark', w: 0.39 },
  { name: 'Nestlé S.A.', isin: 'CH0038863350', symbol: 'NESN.SW', sector: 'Basiskonsum', country: 'Schweiz', w: 0.35 },
  { name: 'Toyota Motor Corp.', isin: 'JP3633400001', symbol: '7203.T', sector: 'Zyklischer Konsum', country: 'Japan', w: 0.31 },
  { name: 'Roche Holding AG', isin: 'CH0012032048', symbol: 'ROG.SW', sector: 'Gesundheit', country: 'Schweiz', w: 0.3 },
  { name: 'Samsung Electronics', isin: 'KR7005930003', symbol: '005930.KS', sector: 'Technologie', country: 'Südkorea', w: 0.29, em: true },
  { name: 'AstraZeneca PLC', isin: 'GB0009895292', symbol: 'AZN.L', sector: 'Gesundheit', country: 'Großbritannien', w: 0.28 },
  { name: 'Shell PLC', isin: 'GB00BP6MXD84', symbol: 'SHEL.L', sector: 'Energie', country: 'Großbritannien', w: 0.27 },
  { name: 'LVMH Moët Hennessy', isin: 'FR0000121014', symbol: 'MC.PA', sector: 'Zyklischer Konsum', country: 'Frankreich', w: 0.25 },
  { name: 'Siemens AG', isin: 'DE0007236101', symbol: 'SIE.DE', sector: 'Industrie', country: 'Deutschland', w: 0.23 },
  { name: 'Allianz SE', isin: 'DE0008404005', symbol: 'ALV.DE', sector: 'Finanzen', country: 'Deutschland', w: 0.18 },
  { name: 'Sony Group Corp.', isin: 'JP3435000009', symbol: '6758.T', sector: 'Zyklischer Konsum', country: 'Japan', w: 0.17 },
  { name: 'Deutsche Telekom AG', isin: 'DE0005557508', symbol: 'DTE.DE', sector: 'Kommunikation', country: 'Deutschland', w: 0.16 },
  { name: 'Münchener Rück AG', isin: 'DE0008430026', symbol: 'MUV2.DE', sector: 'Finanzen', country: 'Deutschland', w: 0.1 },
]
export function etfRows(etfId: number): { source: string; rows: EtfHoldingRow[] } {
  if (etfId === 1) {
    return { source: 'ishares', rows: COMPANIES.filter((c) => !c.em).map((c) => ({ name: c.name, isin: c.isin, symbol: c.symbol, weight: c.w, sector: c.sector, country: c.country })) }
  }
  const rows = COMPANIES.map((c) => ({ name: c.name, isin: c.isin, symbol: c.symbol, weight: Math.round(c.w * (c.em ? 1 : 0.9) * 100) / 100, sector: c.sector, country: c.country }))
    .sort((a, b) => b.weight - a.weight).slice(0, 10)
  return { source: 'yahoo_top10', rows }
}

// ---- Events ----
export interface RawEvent { id: number; instrument_id: number; type: EventType; date: string; amount: number | null; currency: string | null; title: string | null; url: string | null; is_estimate: boolean }
export function buildEvents(): RawEvent[] {
  const ev: RawEvent[] = []
  let id = 1
  const add = (instrument_id: number, type: EventType, date: Date, extra: Partial<RawEvent> = {}) =>
    ev.push({ id: id++, instrument_id, type, date: isoDate(date), amount: null, currency: null, title: null, url: null, is_estimate: false, ...extra })
  for (const s of S) {
    for (let m = -3; m <= 13; m++) {
      const mon = new Date(TODAY.getFullYear(), TODAY.getMonth() + m, 1)
      const estimate = m > 3
      if (s.divMonths.includes(mon.getMonth()) && s.payout > 0) {
        const per = Math.round((dpsAnnual(s) / s.divMonths.length) * 100) / 100
        const pay = new Date(mon.getFullYear(), mon.getMonth(), 12 + (s.id % 9))
        const ex = new Date(pay); ex.setDate(pay.getDate() - (s.currency === 'EUR' ? 3 : 14))
        add(s.id, 'ex_dividend', ex, { amount: per, currency: s.currency, is_estimate: estimate })
        add(s.id, 'dividend_payment', pay, { amount: per, currency: s.currency, is_estimate: estimate })
      }
      if (s.type === 'stock' && (mon.getMonth() + s.id) % 3 === 0) {
        const q = Math.floor(((mon.getMonth() + 11) % 12) / 3) + 1
        add(s.id, 'earnings', new Date(mon.getFullYear(), mon.getMonth(), 5 + ((s.id * 5) % 22)), { title: `Zahlen Q${q}`, is_estimate: m > 2, url: s.ir_url ?? null })
      }
      if (s.type === 'stock' && s.country === 'Deutschland' && mon.getMonth() === (s.id === 7 ? 1 : 4)) {
        add(s.id, 'agm', new Date(mon.getFullYear(), mon.getMonth(), 6 + (s.id % 5)), { title: 'Ordentliche Hauptversammlung', url: s.ir_url ?? null })
      }
    }
  }
  add(5, 'investor_day', new Date(TODAY.getFullYear(), TODAY.getMonth() + 1, 9), { title: 'Capital Markets Day', url: 'https://www.sap.com/investors' })
  add(10, 'investor_day', new Date(TODAY.getFullYear(), TODAY.getMonth() + 2, 14), { title: 'Investor Day Veldhoven', url: 'https://www.asml.com/en/investors' })
  return ev.sort((a, b) => a.date.localeCompare(b.date))
}

// ---- Documents ----
const RAW_ING = `ING-DiBa AG · 60628 Frankfurt am Main

Wertpapierabrechnung Kauf
Depotnummer 5412 0098 31            Datum ${fmtDe(2)}

Wertpapierbezeichnung   Siemens AG Namens-Aktien o.N.
ISIN (WKN)              DE0007236101 (723610)
Nominale                Stück 5
Kurs                    EUR 185,90
Handelsplatz            XETRA
Ausführungstag          ${fmtDe(2)}   Valuta ${fmtDe(0)}

Kurswert                EUR   929,50
Provision               EUR     7,22
Handelsplatzgebühr      EUR     1,90
Endbetrag zu Ihren Lasten   EUR   938,62

Die Wertpapiere schreiben wir Ihrem Depotkonto gut.
Abrechnungskonto: DE12 5001 0517 0648 4898 90`
const RAW_PB = `Postbank – eine Niederlassung der Deutsche Bank AG
Kontoauszug Nr. 10/${TODAY.getFullYear()}       IBAN DE02 1001 0010 0006 8201 01

Buchung  Valuta  Vorgang                                   Betrag EUR
01.10.   01.10.  Dauerauftrag Hausverwaltung Lindenhof      -1.180,00
                 Miete inkl. Nebenkosten
02.10.   02.10.  Lastschrift DB Vertrieb GmbH                  -58,00
                 Deutschlandticket
07.10.   07.10.  Kartenzahlung REWE Markt GmbH                 -64,37
11.10.   11.10.  Kartenzahlung HORNBACH Baumarkt              -129,95
19.10.   19.10.  Gutschrift Jonas Weber                        +45,00
                 Anteil Geburtstagsgeschenk
27.10.   27.10.  Gehalt Nordwind Software GmbH              +4.350,00

Alter Kontostand vom 30.09.                              EUR  27.412,18
Neuer Kontostand vom 31.10.                              EUR  30.374,86`
const RAW_TR = `Trade Republic
Dividende
Johnson & Johnson
US4781601046

20 Stück · 1,24 USD je Aktie
Brutto            24,80 USD
Wechselkurs       1,0812
Quellensteuer     −3,44 €
KapESt + Soli     −2,61 €
Gutschrift        16,89 €
Valuta ${fmtDe(6)}`
const RAW_SC = `Scalable Capital – Sparplanausführung
iShares Core MSCI World UCITS ETF USD (Acc) · IE00B4L5Y983
Ausführung ${fmtDe(38)} · gettex
4,8102 Stück zu 103,946 EUR
Betrag 500,00 EUR · Gebühren 0,00 EUR
Belastung Verrechnungskonto 500,00 EUR`
function fmtDe(ago: number) { const d = daysAgo(ago); return `${String(d.getDate()).padStart(2, '0')}.${String(d.getMonth() + 1).padStart(2, '0')}.${d.getFullYear()}` }
const stamp = (ago: number, h = 10) => { const d = daysAgo(ago); d.setHours(h, 24); return d.toISOString() }
const octDate = (day: number) => { const y = TODAY.getMonth() >= 10 ? TODAY.getFullYear() : TODAY.getFullYear() - 1; return `${y}-10-${String(day).padStart(2, '0')}` }

export function buildDocuments(): DocumentDetail[] {
  let did = 100
  const d = (o: Partial<DocumentDetail> & Pick<DocumentDetail, 'id' | 'filename' | 'source_kind' | 'status'>): DocumentDetail => {
    const drafts = o.drafts ?? []
    return { detected_institution: null, detected_doc_type: null, parser: null, error: null, created_at: stamp(1), processing_ms: null, raw_text: null, account_id: null,
      ...o, drafts, draft_count: drafts.length, pending_count: drafts.filter((x) => x.status === 'pending').length }
  }
  return [
    d({ id: 6, filename: 'Depotauszug_Q3.pdf', source_kind: 'pdf', status: 'processing', created_at: stamp(0, 9), mime_type: 'application/pdf' }),
    d({ id: 5, filename: 'ING_Wertpapierabrechnung_Kauf_Siemens.pdf', source_kind: 'pdf', status: 'review', detected_institution: 'ing', detected_doc_type: 'trade_confirmation', parser: 'ing_pdf', created_at: stamp(0, 8), processing_ms: 412, raw_text: RAW_ING, mime_type: 'application/pdf', account_id: 3,
      drafts: [{ id: did++, kind: 'transaction', confidence: 0.97, status: 'pending', transaction_id: null, payload: { account_id: 3, type: 'buy', booking_date: isoAgo(2), value_date: isoAgo(0), isin: 'DE0007236101', wkn: '723610', name: 'Siemens AG', symbol: 'SIE.DE', quantity: 5, price: 185.9, amount: -938.62, fees: 9.12, taxes: 0, currency: 'EUR', fx_rate: null, counterparty: 'ING-DiBa AG', description: 'Kauf Siemens AG', category_hint: null } }] }),
    d({ id: 4, filename: `Postbank_Kontoauszug_10_${TODAY.getFullYear()}.pdf`, source_kind: 'pdf', status: 'review', detected_institution: 'postbank', detected_doc_type: 'statement', parser: 'postbank_pdf', created_at: stamp(1, 19), processing_ms: 1260, raw_text: RAW_PB, mime_type: 'application/pdf', account_id: 1,
      drafts: [
        { id: did++, kind: 'transaction', confidence: 0.99, status: 'duplicate', transaction_id: null, payload: { account_id: 1, type: 'withdrawal', booking_date: octDate(1), amount: -1180, currency: 'EUR', counterparty: 'Hausverwaltung Lindenhof', description: 'Miete inkl. Nebenkosten', category_hint: 'Wohnen' } },
        { id: did++, kind: 'transaction', confidence: 0.99, status: 'duplicate', transaction_id: null, payload: { account_id: 1, type: 'withdrawal', booking_date: octDate(2), amount: -58, currency: 'EUR', counterparty: 'DB Vertrieb GmbH', description: 'Deutschlandticket', category_hint: 'Mobilität' } },
        { id: did++, kind: 'transaction', confidence: 0.98, status: 'accepted', transaction_id: null, payload: { account_id: 1, type: 'withdrawal', booking_date: octDate(7), amount: -64.37, currency: 'EUR', counterparty: 'REWE Markt GmbH', description: 'Kartenzahlung', category_hint: 'Lebensmittel' } },
        { id: did++, kind: 'transaction', confidence: 0.93, status: 'pending', transaction_id: null, payload: { account_id: 1, type: 'withdrawal', booking_date: octDate(11), amount: -129.95, currency: 'EUR', counterparty: 'HORNBACH Baumarkt', description: 'Kartenzahlung', category_hint: null } },
        { id: did++, kind: 'transaction', confidence: 0.64, status: 'pending', transaction_id: null, payload: { account_id: 1, type: 'deposit', booking_date: octDate(19), amount: 45, currency: 'EUR', counterparty: 'Jonas Weber', description: 'Anteil Geburtstagsgeschenk', category_hint: 'Sonstige Einnahmen' } },
        { id: did++, kind: 'balance', confidence: 0.99, status: 'pending', transaction_id: null, payload: { account_id: 1, date: octDate(31), balance: 30374.86 } },
      ] }),
    d({ id: 3, filename: 'Screenshot Trade Republic Dividende.png', source_kind: 'image', status: 'review', detected_institution: 'trade_republic', detected_doc_type: 'dividend', parser: 'qwen2.5vl:7b', created_at: stamp(2, 21), processing_ms: 18400, raw_text: RAW_TR, mime_type: 'image/png', account_id: 6,
      drafts: [{ id: did++, kind: 'transaction', confidence: 0.71, status: 'pending', transaction_id: null, payload: { account_id: 6, type: 'dividend', booking_date: isoAgo(6), value_date: isoAgo(6), isin: 'US4781601046', name: 'Johnson & Johnson', quantity: 20, price: 1.24, amount: 16.89, fees: 0, taxes: 6.05, currency: 'EUR', fx_rate: 1.0812, counterparty: 'Johnson & Johnson', description: 'Dividende', category_hint: 'Zinsen & Dividenden' } }] }),
    d({ id: 2, filename: 'Eingefügter Text', source_kind: 'paste', status: 'imported', detected_institution: 'scalable', detected_doc_type: 'savings_plan', parser: 'qwen2.5:7b-instruct', created_at: stamp(38, 7), processing_ms: 6300, raw_text: RAW_SC, account_id: 4,
      drafts: [{ id: did++, kind: 'transaction', confidence: 0.9, status: 'accepted', transaction_id: null, payload: { account_id: 4, type: 'buy', booking_date: isoAgo(38), isin: 'IE00B4L5Y983', name: 'iShares Core MSCI World UCITS ETF (Acc)', quantity: 4.8102, price: 103.946, amount: -500, fees: 0, taxes: 0, currency: 'EUR', description: 'Sparplanausführung' } }] }),
    d({ id: 1, filename: 'scan_0231.jpg', source_kind: 'image', status: 'failed', created_at: stamp(44, 18), processing_ms: 30010, mime_type: 'image/jpeg', error: 'Kein Text erkannt: Vision-Modell qwen2.5vl:7b hat nach 30 s nicht geantwortet.' }),
  ]
}
