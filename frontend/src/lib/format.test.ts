import { describe, expect, it } from 'vitest'
import { addDays, daysUntil, fmtCompact, fmtDate, fmtMoney, fmtMonth, fmtNumber, fmtPct, fmtQty, formatIban, parseDecimal, toNum } from './format'

const norm = (s: string) => s.replace(/ | /g, ' ')

describe('fmtMoney', () => {
  it('formats EUR the German way', () => {
    expect(norm(fmtMoney(1234.5))).toBe('1.234,50 €')
    expect(norm(fmtMoney(0))).toBe('0,00 €')
  })
  it('uses a real minus and optional plus sign', () => {
    expect(norm(fmtMoney(-12.3))).toBe('−12,30 €')
    expect(norm(fmtMoney(12.3, { signed: true }))).toBe('+12,30 €')
    expect(norm(fmtMoney(0.001, { signed: true }))).toBe('0,00 €')
  })
  it('handles other currencies, decimals and missing values', () => {
    expect(norm(fmtMoney(228.5, { currency: 'USD' }))).toBe('228,50 $')
    expect(norm(fmtMoney(1999.6, { decimals: 0 }))).toBe('2.000 €')
    expect(fmtMoney(null)).toBe('–')
    expect(fmtMoney(undefined)).toBe('–')
    expect(norm(fmtMoney('12.5' as unknown as number))).toBe('12,50 €')
  })
})

describe('fmtCompact', () => {
  it('abbreviates large amounts', () => {
    expect(norm(fmtCompact(1_250_000))).toBe('1,25 Mio. €')
    expect(norm(fmtCompact(184_300))).toBe('184 Tsd. €')
    expect(norm(fmtCompact(-2_500))).toBe('−2,5 Tsd. €')
    expect(norm(fmtCompact(420))).toBe('420 €')
  })
})

describe('fmtPct / fmtNumber / fmtQty', () => {
  it('formats percent values', () => {
    expect(norm(fmtPct(12.345))).toBe('12,3 %')
    expect(norm(fmtPct(-0.52, { signed: true, decimals: 2 }))).toBe('−0,52 %')
    expect(norm(fmtPct(3, { signed: true }))).toBe('+3,0 %')
    expect(fmtPct(null)).toBe('–')
  })
  it('formats numbers and quantities', () => {
    expect(fmtNumber(1234.567, 2)).toBe('1.234,57')
    expect(fmtNumber(-3)).toBe('−3')
    expect(fmtQty(12.5)).toBe('12,5')
    expect(fmtQty(0.123456)).toBe('0,1235')
  })
})

describe('dates', () => {
  it('formats ISO dates without timezone drift', () => {
    expect(fmtDate('2026-11-02')).toBe('02.11.2026')
    expect(fmtDate(null)).toBe('–')
    expect(fmtDate('kaputt')).toBe('–')
  })
  it('formats months', () => {
    expect(fmtMonth('2026-03')).toMatch(/Mär.* 2026/)
    expect(fmtMonth('2026-03', true)).toBe('März 2026')
  })
  it('does date arithmetic', () => {
    expect(addDays('2026-12-30', 3)).toBe('2027-01-02')
    expect(daysUntil('2026-11-15', new Date(2026, 10, 12, 15, 0))).toBe(3)
  })
})

describe('parseDecimal / toNum / formatIban', () => {
  it('parses German and plain decimals', () => {
    expect(parseDecimal('1.234,56')).toBe(1234.56)
    expect(parseDecimal('1234.56')).toBe(1234.56)
    expect(parseDecimal('1.234')).toBe(1234)
    expect(parseDecimal('−12,5 €')).toBe(-12.5)
    expect(parseDecimal('')).toBeNull()
    expect(parseDecimal('abc')).toBeNull()
  })
  it('coerces numeric strings', () => {
    expect(toNum('3.5')).toBe(3.5)
    expect(toNum('')).toBeNull()
    expect(toNum(NaN)).toBeNull()
  })
  it('groups IBANs', () => {
    expect(formatIban('DE02100100100006820101')).toBe('DE02 1001 0010 0006 8201 01')
  })
})
