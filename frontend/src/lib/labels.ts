import type { AccountType, AllocationBy, CategoryKind, DocumentStatus, DraftStatus, EventType, InstrumentType, TxType } from '../api/types'

export const TX_TYPE: Record<TxType, string> = {
  buy: 'Kauf', sell: 'Verkauf', dividend: 'Dividende', interest: 'Zinsen', deposit: 'Einzahlung', withdrawal: 'Auszahlung',
  transfer_in: 'Umbuchung (Eingang)', transfer_out: 'Umbuchung (Ausgang)', fee: 'Gebühr', tax: 'Steuer', split: 'Split',
  delivery_in: 'Einlieferung', delivery_out: 'Auslieferung',
}
export const TX_TYPES = Object.keys(TX_TYPE) as TxType[]
export const SECURITY_TX: TxType[] = ['buy', 'sell', 'dividend', 'split', 'delivery_in', 'delivery_out']
export const ACCOUNT_TYPE: Record<AccountType, string> = { checking: 'Girokonto', savings: 'Tagesgeld', brokerage: 'Depot', credit_card: 'Kreditkarte', other: 'Sonstiges' }
export const INSTRUMENT_TYPE: Record<InstrumentType, string> = { stock: 'Aktie', etf: 'ETF', fund: 'Fonds', bond: 'Anleihe', crypto: 'Krypto', commodity: 'Rohstoff', other: 'Sonstiges' }
export const CATEGORY_KIND: Record<CategoryKind, string> = { income: 'Einnahme', expense: 'Ausgabe', transfer: 'Umbuchung', investment: 'Investition' }
export const EVENT_TYPE: Record<EventType, string> = { ex_dividend: 'Ex-Tag', dividend_payment: 'Zahltag', earnings: 'Quartalszahlen', agm: 'Hauptversammlung', investor_day: 'IR-Termin', split: 'Split', other: 'Termin' }
export const DOC_STATUS: Record<DocumentStatus, string> = { uploaded: 'Hochgeladen', processing: 'Wird verarbeitet', review: 'Zu prüfen', imported: 'Übernommen', failed: 'Fehlgeschlagen' }
export const DRAFT_STATUS: Record<DraftStatus, string> = { pending: 'Offen', accepted: 'Übernommen', rejected: 'Verworfen', duplicate: 'Duplikat' }
export const ALLOCATION_BY: Record<AllocationBy, string> = { type: 'Typ', sector: 'Sektor', country: 'Land', account: 'Konto', currency: 'Währung' }
export const INSTITUTION: Record<string, string> = { postbank: 'Postbank', ing: 'ING', scalable: 'Scalable Capital', trade_republic: 'Trade Republic' }
export const DOC_TYPE: Record<string, string> = { trade_confirmation: 'Wertpapierabrechnung', dividend: 'Dividendenabrechnung', statement: 'Kontoauszug', tax_report: 'Steuerbescheinigung', savings_plan: 'Sparplanausführung', screenshot: 'Screenshot',
  trade_buy: 'Kaufabrechnung', trade_sell: 'Verkaufsabrechnung', account_statement: 'Kontoauszug', interest: 'Zinsabrechnung', csv_export: 'Umsatz-Export (CSV)', csv: 'Umsatz-Export (CSV)',
  tax_statement: 'Steuerbescheinigung', depot_statement: 'Depotauszug', paste: 'Eingefügter Text', unknown: 'Unbekannter Belegtyp' }
export const MARKET_JOB: Record<string, string> = { fx: 'Wechselkurse', quotes: 'Kurse', profile: 'Stammdaten', history: 'Kurshistorie', events: 'Termine', fundamentals: 'Fundamentaldaten', etf: 'ETF-Zusammensetzung', all: 'Alle Marktdaten' }
export const SOURCE: Record<string, string> = { ishares: 'iShares (Emittent)', xtrackers: 'Xtrackers (Emittent)', vanguard: 'Vanguard (Emittent)', yahoo_top10: 'Yahoo Top 10', manual: 'manueller Upload', yahoo: 'Yahoo Finance', demo: 'Demo-Daten', amundi: 'Amundi (Emittent)', spdr: 'SPDR (Emittent)', invesco: 'Invesco (Emittent)' }

const KEY_LABELS: Record<string, string> = {
  stock: 'Aktien', etf: 'ETFs', fund: 'Fonds', bond: 'Anleihen', crypto: 'Krypto', commodity: 'Rohstoffe', other: 'Sonstige', cash: 'Liquidität',
  unknown: 'Nicht zugeordnet', null: 'Nicht zugeordnet', '': 'Nicht zugeordnet', Unbekannt: 'Nicht zugeordnet', ETF: 'ETFs (nicht durchschaut)',
  // account types (allocation by account type) and common country names from Yahoo / issuers
  checking: 'Girokonto', savings: 'Tagesgeld', brokerage: 'Depot',
  'United States': 'USA', Germany: 'Deutschland', Switzerland: 'Schweiz', 'United Kingdom': 'Großbritannien', France: 'Frankreich', Netherlands: 'Niederlande',
  Japan: 'Japan', China: 'China', Taiwan: 'Taiwan', Canada: 'Kanada', Ireland: 'Irland', Denmark: 'Dänemark', Sweden: 'Schweden', Spain: 'Spanien', Italy: 'Italien',
  Australia: 'Australien', 'South Korea': 'Südkorea', India: 'Indien',
  // GICS / Yahoo sector names
  Technology: 'Technologie', 'Information Technology': 'Informationstechnologie', 'Financial Services': 'Finanzen', Financials: 'Finanzen', Healthcare: 'Gesundheit', 'Health Care': 'Gesundheit',
  'Consumer Cyclical': 'Zyklischer Konsum', 'Consumer Discretionary': 'Zyklischer Konsum', 'Consumer Defensive': 'Basiskonsum', 'Consumer Staples': 'Basiskonsum',
  'Communication Services': 'Kommunikation', Industrials: 'Industrie', Energy: 'Energie', Utilities: 'Versorger', 'Real Estate': 'Immobilien', 'Basic Materials': 'Grundstoffe', Materials: 'Grundstoffe',
}
/** Allocation keys come as raw values (enum slugs, sector names). Map the known slugs, keep the rest. */
export const allocationLabel = (key: string | null | undefined) => KEY_LABELS[String(key ?? '')] ?? String(key)
export const institutionLabel = (slug: string | null | undefined) => (slug ? INSTITUTION[slug] ?? slug : '–')
export const docTypeLabel = (slug: string | null | undefined) => (slug ? DOC_TYPE[slug] ?? slug : 'Unbekannter Belegtyp')
