import type { EChartsOption } from 'echarts'
import type { ChartTheme } from '../components/Chart'
import { fmtCompact, fmtDate, fmtMoney, fmtMonth, fmtNumber, fmtPct } from './format'

export type Unit = 'money' | 'pct' | 'index' | 'number' | { currency: string }
const HIDDEN = '•••'

export function fmtUnit(v: number | null | undefined, unit: Unit, t: ChartTheme, compact = false): string {
  if (v == null) return '–'
  if (unit === 'pct') return fmtPct(v)
  if (unit === 'index' || unit === 'number') return fmtNumber(v, 2)
  if (t.privacy) return HIDDEN
  const currency = unit === 'money' ? 'EUR' : unit.currency
  return compact ? fmtCompact(v, currency) : fmtMoney(v, { currency })
}
const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;')

export interface TipRow { label: string; value: string; color?: string; dashed?: boolean }
export function tipHtml(title: string, rows: TipRow[], t: ChartTheme): string {
  const body = rows.map((r) => {
    const sw = r.color ? `<span style="display:inline-block;width:10px;height:${r.dashed ? 0 : 3}px;${r.dashed ? `border-top:2px dashed ${r.color};` : `background:${r.color};`}border-radius:1px;margin-right:6px;vertical-align:middle"></span>` : ''
    return `<div style="display:flex;justify-content:space-between;gap:18px"><span style="color:${t.text2}">${sw}${esc(r.label)}</span><span style="font-weight:600;font-variant-numeric:tabular-nums">${esc(r.value)}</span></div>`
  }).join('')
  return `<div style="font-size:12px;line-height:1.55;min-width:150px"><div style="color:${t.text3};margin-bottom:2px">${esc(title)}</div>${body}</div>`
}

function base(t: ChartTheme): EChartsOption {
  return {
    textStyle: { fontFamily: t.font, color: t.text2 },
    grid: { left: 4, right: 10, top: 12, bottom: 4, containLabel: true },
    tooltip: {
      trigger: 'axis', confine: true, backgroundColor: t.surface, borderColor: t.border, borderWidth: 1, padding: [8, 10],
      textStyle: { color: t.text, fontFamily: t.font, fontSize: 12 }, extraCssText: 'box-shadow:0 6px 18px -6px rgba(0,0,0,.35);border-radius:6px;',
      axisPointer: { type: 'line', lineStyle: { color: t.text3, width: 1, type: 'solid' } },
    },
  }
}
const yAxis = (t: ChartTheme, unit: Unit, extra: Record<string, unknown> = {}) => ({
  type: 'value' as const, scale: true, splitNumber: 4,
  axisLabel: { color: t.text3, fontSize: 12, fontFamily: t.font, formatter: (v: number) => fmtUnit(v, unit, t, true) },
  splitLine: { lineStyle: { color: t.border } }, axisLine: { show: false }, axisTick: { show: false }, ...extra,
})

export interface LineSeries { name: string; data: (number | null)[]; color: string; dashed?: boolean; area?: boolean; width?: number; step?: boolean }
export interface Band { name: string; lower: (number | null)[]; upper: (number | null)[]; color: string }

/** Time-series lines with optional band (drawn as stacked transparent base + filled delta). */
export function timeOption(t: ChartTheme, cfg: { dates: string[]; series: LineSeries[]; band?: Band; unit?: Unit; zeroBased?: boolean; markLine?: { value: number; label: string }; extraRows?: (i: number) => TipRow[] }): EChartsOption {
  const unit = cfg.unit ?? 'money'
  const { dates } = cfg
  const span = dates.length > 1 ? (Date.parse(dates[dates.length - 1]) - Date.parse(dates[0])) / 86400000 : 0
  const labelFmt = (v: number) => {
    const d = new Date(v)
    if (span > 800) return String(d.getFullYear())
    if (span > 100) return d.toLocaleDateString('de-DE', { month: 'short', year: '2-digit' })
    return d.toLocaleDateString('de-DE', { day: 'numeric', month: 'short' })
  }
  const pts = (arr: (number | null)[]) => arr.map((v, i) => [dates[i], v])
  const series: Record<string, unknown>[] = []
  if (cfg.band) {
    const b = cfg.band
    series.push({ type: 'line', name: '_lower', data: pts(b.lower), stack: 'band', symbol: 'none', lineStyle: { opacity: 0 }, silent: true, z: 1 })
    series.push({ type: 'line', name: '_band', data: pts(b.upper.map((u, i) => (u == null || b.lower[i] == null ? null : u - (b.lower[i] as number)))), stack: 'band', symbol: 'none', lineStyle: { opacity: 0 }, areaStyle: { color: b.color, opacity: t.dark ? 0.22 : 0.16 }, silent: true, z: 1 })
  }
  cfg.series.forEach((s, i) => series.push({
    type: 'line', name: s.name, data: pts(s.data), symbol: 'none', showSymbol: false, step: s.step ? 'end' : undefined, connectNulls: false,
    lineStyle: { color: s.color, width: s.width ?? 2, type: s.dashed ? [5, 4] : 'solid' }, itemStyle: { color: s.color },
    areaStyle: s.area ? { color: s.color, opacity: t.dark ? 0.14 : 0.1 } : undefined, emphasis: { disabled: true }, z: 3 + i,
    markLine: i === 0 && cfg.markLine ? { silent: true, symbol: 'none', lineStyle: { color: t.text3, type: [3, 3], width: 1 }, label: { formatter: cfg.markLine.label, color: t.text2, fontSize: 11, position: 'insideEndTop', fontFamily: t.font }, data: [{ yAxis: cfg.markLine.value }] } : undefined,
  }))
  return {
    ...base(t),
    xAxis: { type: 'time', boundaryGap: ['0%', '0%'], axisLabel: { color: t.text3, fontSize: 12, formatter: labelFmt, hideOverlap: true, fontFamily: t.font }, axisLine: { lineStyle: { color: t.border } }, axisTick: { show: false }, splitLine: { show: false } },
    yAxis: yAxis(t, unit, { scale: !cfg.zeroBased, min: cfg.zeroBased ? 0 : undefined }),
    tooltip: {
      ...(base(t).tooltip as object),
      formatter: (params: unknown) => {
        const list = params as { dataIndex: number }[]
        const i = list[0]?.dataIndex ?? 0
        const rows: TipRow[] = cfg.series.filter((s) => s.data[i] != null).map((s) => ({ label: s.name, value: fmtUnit(s.data[i], unit, t), color: s.color, dashed: s.dashed }))
        if (cfg.band && cfg.band.lower[i] != null) rows.push({ label: cfg.band.name, value: `${fmtUnit(cfg.band.lower[i], unit, t, true)} – ${fmtUnit(cfg.band.upper[i], unit, t, true)}`, color: cfg.band.color })
        if (cfg.extraRows) rows.push(...cfg.extraRows(i))
        return tipHtml(fmtDate(dates[i]), rows, t)
      },
    },
    series,
  } as EChartsOption
}

export interface BarSeries { name: string; data: (number | null)[]; color: string; stack?: string; hatchedFrom?: number; perItemColor?: (v: number, i: number) => string }
/** Category bars (months / years). `hatchedFrom` renders later items as estimates (decal). */
export function barOption(t: ChartTheme, cfg: { categories: string[]; labels?: string[]; series: BarSeries[]; line?: LineSeries & { unit?: Unit }; unit?: Unit; tipTitle?: (i: number) => string; extraRows?: (i: number) => TipRow[]; maxBarWidth?: number }): EChartsOption {
  const unit = cfg.unit ?? 'money'
  const labels = cfg.labels ?? cfg.categories
  const hasLineAxis = !!cfg.line && (cfg.line.unit ?? unit) !== unit
  const series: Record<string, unknown>[] = cfg.series.map((s) => ({
    type: 'bar', name: s.name, stack: s.stack, barMaxWidth: cfg.maxBarWidth ?? 26, barGap: '15%',
    data: s.data.map((v, i) => ({
      value: v,
      itemStyle: {
        color: s.perItemColor && v != null ? s.perItemColor(v, i) : s.color, borderRadius: s.stack ? 0 : [2, 2, 0, 0],
        ...(s.hatchedFrom != null && i >= s.hatchedFrom ? { opacity: 0.55, borderColor: s.color, borderWidth: 1, borderType: 'dashed' } : {}),
      },
    })),
    emphasis: { disabled: true },
  }))
  if (cfg.line) series.push({ type: 'line', name: cfg.line.name, data: cfg.line.data, yAxisIndex: hasLineAxis ? 1 : 0, symbol: 'circle', symbolSize: 5, lineStyle: { color: cfg.line.color, width: 2, type: cfg.line.dashed ? [5, 4] : 'solid' }, itemStyle: { color: cfg.line.color }, emphasis: { disabled: true }, z: 5 })
  const b = base(t)
  return {
    ...b,
    tooltip: {
      ...(b.tooltip as object), axisPointer: { type: 'shadow', shadowStyle: { color: t.text3, opacity: 0.1 } },
      formatter: (params: unknown) => {
        const i = (params as { dataIndex: number }[])[0]?.dataIndex ?? 0
        const rows: TipRow[] = cfg.series.filter((s) => s.data[i] != null).map((s) => ({ label: s.name, value: fmtUnit(s.data[i], unit, t), color: s.perItemColor ? s.perItemColor(s.data[i] as number, i) : s.color }))
        if (cfg.line && cfg.line.data[i] != null) rows.push({ label: cfg.line.name, value: fmtUnit(cfg.line.data[i], cfg.line.unit ?? unit, t), color: cfg.line.color, dashed: cfg.line.dashed })
        if (cfg.extraRows) rows.push(...cfg.extraRows(i))
        return tipHtml(cfg.tipTitle ? cfg.tipTitle(i) : labels[i], rows, t)
      },
    },
    xAxis: { type: 'category', data: labels, axisLabel: { color: t.text3, fontSize: 12, hideOverlap: true, fontFamily: t.font }, axisLine: { lineStyle: { color: t.border } }, axisTick: { show: false } },
    yAxis: hasLineAxis
      ? [yAxis(t, unit, { scale: false }), yAxis(t, cfg.line!.unit ?? unit, { scale: false, splitLine: { show: false }, position: 'right' })]
      : yAxis(t, unit, { scale: false }),
    series,
  } as EChartsOption
}

export const monthLabels = (months: string[]) => months.map((m) => fmtMonth(m).replace(/\.? (\d{2})(\d{2})$/, ' $2'))
