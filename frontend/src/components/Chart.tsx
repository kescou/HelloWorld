import { useEffect, useMemo, useRef } from 'react'
import * as echarts from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, MarkLineComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsOption } from 'echarts'
import { usePrefs } from '../lib/prefs'

echarts.use([LineChart, BarChart, GridComponent, TooltipComponent, MarkLineComponent, CanvasRenderer])

export interface ChartTheme {
  text: string; text2: string; text3: string; border: string; surface: string; surface2: string
  accent: string; gain: string; loss: string; warn: string; c: string[]; privacy: boolean; font: string; dark: boolean
}
const VARS = ['--text', '--text-2', '--text-3', '--border', '--surface', '--surface-2', '--accent', '--gain', '--loss', '--warn', '--c1', '--c2', '--c3', '--c4', '--c5', '--c6', '--c7', '--c8'] as const

/** Chart colours are read from the CSS tokens so charts follow the theme (single source of truth). */
export function useChartTheme(): ChartTheme {
  const { resolvedTheme, privacy } = usePrefs()
  return useMemo(() => {
    const cs = getComputedStyle(document.documentElement)
    const v = Object.fromEntries(VARS.map((k) => [k, cs.getPropertyValue(k).trim() || '#888'])) as Record<(typeof VARS)[number], string>
    return {
      text: v['--text'], text2: v['--text-2'], text3: v['--text-3'], border: v['--border'], surface: v['--surface'], surface2: v['--surface-2'],
      accent: v['--accent'], gain: v['--gain'], loss: v['--loss'], warn: v['--warn'],
      c: [v['--c1'], v['--c2'], v['--c3'], v['--c4'], v['--c5'], v['--c6'], v['--c7'], v['--c8']],
      privacy, font: cs.getPropertyValue('--font').trim() || 'sans-serif', dark: resolvedTheme === 'dark',
    }
    // resolvedTheme drives the CSS variables; re-read when it changes
  }, [resolvedTheme, privacy])
}

export function Chart({ option, height = 280, label }: { option: EChartsOption; height?: number; label: string }) {
  const el = useRef<HTMLDivElement>(null)
  const inst = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (!el.current) return
    const chart = echarts.init(el.current, undefined, { renderer: 'canvas' })
    inst.current = chart
    const ro = new ResizeObserver(() => chart.resize())
    ro.observe(el.current)
    return () => { ro.disconnect(); chart.dispose(); inst.current = null }
  }, [])
  useEffect(() => {
    const reduce = typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches
    inst.current?.setOption({ animation: !reduce, animationDuration: 250, animationDurationUpdate: 200, ...option }, { notMerge: true })
  }, [option])

  return <div ref={el} className="chart" style={{ height }} role="img" aria-label={label} />
}
