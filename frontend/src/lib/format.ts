// de-DE formatting helpers. All output uses a real minus sign (U+2212) and non-breaking spaces from Intl.
const LOCALE = 'de-DE'
const MINUS = '−'
const DASH = '–'

const nf = (opts: Intl.NumberFormatOptions) => new Intl.NumberFormat(LOCALE, opts)
const cache = new Map<string, Intl.NumberFormat>()
function fmt(key: string, opts: Intl.NumberFormatOptions): Intl.NumberFormat {
  let f = cache.get(key)
  if (!f) { f = nf(opts); cache.set(key, f) }
  return f
}
const fixMinus = (s: string) => s.replace(/^-/, MINUS)
const isNum = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n)

/** Tolerates numeric strings (e.g. Decimal serialised as string). */
export function toNum(v: unknown): number | null {
  if (isNum(v)) return v
  if (typeof v === 'string' && v.trim() !== '' && Number.isFinite(Number(v))) return Number(v)
  return null
}

export interface MoneyOpts { signed?: boolean; decimals?: number; currency?: string; compact?: boolean }

export function fmtMoney(value: number | null | undefined, opts: MoneyOpts = {}): string {
  const n = toNum(value)
  if (n === null) return DASH
  const currency = opts.currency || 'EUR'
  if (opts.compact) return fmtCompact(n, currency)
  const decimals = opts.decimals ?? 2
  const s = fmt(`m${currency}${decimals}`, { style: 'currency', currency, minimumFractionDigits: decimals, maximumFractionDigits: decimals }).format(Math.abs(n))
  const zero = Math.abs(n) < 0.5 * 10 ** -decimals
  if (zero) return s
  if (n < 0) return MINUS + s
  return opts.signed ? '+' + s : s
}

/** 1,2 Mio. € / 345 Tsd. € — for axes and tight spots. */
export function fmtCompact(value: number | null | undefined, currency = 'EUR'): string {
  const n = toNum(value)
  if (n === null) return DASH
  const sym = currency === 'EUR' ? '€' : currency
  const a = Math.abs(n)
  const sign = n < 0 ? MINUS : ''
  if (a >= 1e9) return `${sign}${fmt('c2', { maximumFractionDigits: 2 }).format(a / 1e9)} Mrd. ${sym}`
  if (a >= 1e6) return `${sign}${fmt('c2', { maximumFractionDigits: 2 }).format(a / 1e6)} Mio. ${sym}`
  if (a >= 1e4) return `${sign}${fmt('c0', { maximumFractionDigits: 0 }).format(a / 1e3)} Tsd. ${sym}`
  if (a >= 1e3) return `${sign}${fmt('c1', { maximumFractionDigits: 1 }).format(a / 1e3)} Tsd. ${sym}`
  return `${sign}${fmt('c0', { maximumFractionDigits: 0 }).format(a)} ${sym}`
}

export function fmtNumber(value: number | null | undefined, maxDecimals = 2, minDecimals = 0): string {
  const n = toNum(value)
  if (n === null) return DASH
  return fixMinus(fmt(`n${maxDecimals}-${minDecimals}`, { maximumFractionDigits: maxDecimals, minimumFractionDigits: Math.min(minDecimals, maxDecimals) }).format(n))
}

/** `value` is a percent value (12.3 → "12,3 %"). */
export function fmtPct(value: number | null | undefined, opts: { signed?: boolean; decimals?: number } = {}): string {
  const n = toNum(value)
  if (n === null) return DASH
  const d = opts.decimals ?? 1
  const s = fmt(`p${d}`, { minimumFractionDigits: d, maximumFractionDigits: d }).format(Math.abs(n)) + ' %'
  const zero = Math.abs(n) < 0.5 * 10 ** -d
  if (zero) return s
  if (n < 0) return MINUS + s
  return opts.signed ? '+' + s : s
}

/** Share counts: up to 4 decimals, no trailing zeros. */
export function fmtQty(value: number | null | undefined): string {
  return fmtNumber(value, 4)
}

function parseDate(iso: string | null | undefined): Date | null {
  if (!iso) return null
  // Date-only strings are parsed as local dates to avoid timezone shifts.
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso)
  const d = m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date(iso)
  return Number.isNaN(d.getTime()) ? null : d
}
const df = (opts: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat(LOCALE, opts)
const D_FULL = df({ day: '2-digit', month: '2-digit', year: 'numeric' })
const D_SHORT = df({ day: 'numeric', month: 'short' })
const D_TIME = df({ day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
const D_MONTH = df({ month: 'short', year: 'numeric' })
const D_MONTH_LONG = df({ month: 'long', year: 'numeric' })
const D_MON = df({ month: 'short' })
const D_WEEKDAY = df({ weekday: 'short' })

export const fmtDate = (iso: string | null | undefined) => { const d = parseDate(iso); return d ? D_FULL.format(d) : DASH }
export const fmtDateShort = (iso: string | null | undefined) => { const d = parseDate(iso); return d ? D_SHORT.format(d) : DASH }
export const fmtDateTime = (iso: string | null | undefined) => { const d = parseDate(iso); return d ? D_TIME.format(d).replace(',', '') : DASH }
export const fmtWeekday = (iso: string) => { const d = parseDate(iso); return d ? D_WEEKDAY.format(d) : '' }
export const fmtMonShort = (iso: string) => { const d = parseDate(iso.length === 7 ? iso + '-01' : iso); return d ? D_MON.format(d).replace('.', '') : '' }
export const dayOfMonth = (iso: string) => parseDate(iso)?.getDate() ?? 0

/** "2026-11" → "Nov. 2026" (or "November 2026" with long). */
export function fmtMonth(month: string | null | undefined, long = false): string {
  const d = parseDate(month && month.length === 7 ? month + '-01' : month)
  return d ? (long ? D_MONTH_LONG : D_MONTH).format(d) : DASH
}

export function fmtRelative(iso: string | null | undefined, now: Date = new Date()): string {
  const d = parseDate(iso)
  if (!d) return DASH
  const min = Math.round((now.getTime() - d.getTime()) / 60000)
  if (min < 1) return 'gerade eben'
  if (min < 60) return `vor ${min} Min.`
  const h = Math.round(min / 60)
  if (h < 24) return `vor ${h} Std.`
  const days = Math.round(h / 24)
  if (days < 31) return `vor ${days} ${days === 1 ? 'Tag' : 'Tagen'}`
  return D_FULL.format(d)
}

export function isoDate(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}
export const todayIso = () => isoDate(new Date())
export function addDays(iso: string, days: number): string {
  const d = parseDate(iso) ?? new Date()
  d.setDate(d.getDate() + days)
  return isoDate(d)
}
export function daysUntil(iso: string, now: Date = new Date()): number {
  const d = parseDate(iso)
  if (!d) return 0
  const a = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  return Math.round((d.getTime() - a) / 86400000)
}

/** Parses German user input ("1.234,56", "1234.56", "-12,5") into a number. */
export function parseDecimal(input: string): number | null {
  let s = input.trim().replace(/\s|€|%/g, '').replace(MINUS, '-')
  if (!s) return null
  if (s.includes(',')) s = s.replace(/\./g, '').replace(',', '.')
  else if (/^-?\d{1,3}(\.\d{3})+$/.test(s)) s = s.replace(/\./g, '')
  const n = Number(s)
  return Number.isFinite(n) ? n : null
}

export function formatIban(iban: string | null | undefined): string {
  if (!iban) return DASH
  return iban.replace(/\s/g, '').replace(/(.{4})/g, '$1 ').trim()
}
