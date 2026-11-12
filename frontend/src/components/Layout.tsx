import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { NavLink, useLocation } from 'react-router-dom'
import {
  ArrowLeftRight, Building2, CalendarDays, Coins, Eye, EyeOff, FileInput, Landmark, Layers, LayoutDashboard, Menu, Monitor, Moon,
  PieChart, RefreshCw, Settings as SettingsIcon, Star, Sun, TrendingUp, Wallet, X, type LucideIcon,
} from 'lucide-react'
import { summarizeRefresh, useDocuments, useLlmStatus, useMarketRefresh, useMarketStatus } from '../api/hooks'
import { fmtRelative } from '../lib/format'
import { MARKET_JOB } from '../lib/labels'
import { usePrefs, type ThemeChoice } from '../lib/prefs'
import { useToast } from './Toast'
import { cx } from './ui'

interface NavItem { to: string; label: string; icon: LucideIcon; end?: boolean }
const GROUPS: { label: string; items: NavItem[] }[] = [
  { label: 'Vermögen', items: [
    { to: '/', label: 'Übersicht', icon: LayoutDashboard, end: true },
    { to: '/konten', label: 'Konten', icon: Building2 },
    { to: '/depot', label: 'Depot', icon: PieChart },
    { to: '/durchschau', label: 'Durchschau', icon: Layers },
    { to: '/dividenden', label: 'Dividenden', icon: Coins },
    { to: '/steuern', label: 'Steuern', icon: Landmark },
    { to: '/kalender', label: 'Kalender', icon: CalendarDays },
    { to: '/watchlist', label: 'Watchlist', icon: Star },
  ] },
  { label: 'Buchhaltung', items: [
    { to: '/transaktionen', label: 'Transaktionen', icon: ArrowLeftRight },
    { to: '/haushaltsbuch', label: 'Haushaltsbuch', icon: Wallet },
    { to: '/import', label: 'Import', icon: FileInput },
  ] },
  { label: 'Planung', items: [
    { to: '/prognose', label: 'Prognose', icon: TrendingUp },
    { to: '/einstellungen', label: 'Einstellungen', icon: SettingsIcon },
  ] },
]
const ALL = GROUPS.flatMap((g) => g.items)
const BOTTOM = ['/', '/depot', '/transaktionen', '/import']
const TITLES: [RegExp, string][] = [[/^\/wertpapier\//, 'Wertpapier'], [/^\/konten\/\d+/, 'Konto'], [/^\/import\/\d+/, 'Beleg prüfen']]

function Logo() {
  return (
    <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="7" fill="var(--rail-active)" />
      <path d="M7 21.5 12.5 14l4.5 4 8-10" fill="none" stroke="#e2b354" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M7 25.5h18" stroke="#a3b9bf" strokeWidth="2" strokeLinecap="round" />
    </svg>
  )
}

const THEME_NEXT: Record<ThemeChoice, ThemeChoice> = { system: 'light', light: 'dark', dark: 'system' }
const THEME_LABEL: Record<ThemeChoice, string> = { system: 'Systemeinstellung', light: 'Hell', dark: 'Dunkel' }

export function Layout({ children }: { children: ReactNode }) {
  const loc = useLocation()
  const { theme, setTheme, privacy, togglePrivacy } = usePrefs()
  const [sheet, setSheet] = useState(false)
  const docs = useDocuments()
  const market = useMarketStatus()
  const llm = useLlmStatus()
  const toast = useToast()
  const refresh = useMarketRefresh()
  const qc = useQueryClient()
  // A background refresh (started here, in the settings or before a reload) has finished: reload what depends on
  // market data and say honestly how it went.
  const watched = useRef<string | null>(null)
  useEffect(() => {
    const r = market.data?.refresh
    if (!r) return
    if (r.running && r.current) { watched.current = r.current.job_id; return }
    if (watched.current && r.last?.job_id === watched.current) {
      watched.current = null
      for (const key of [['portfolio'], ['accounts'], ['instruments'], ['analytics'], ['market', 'quotes'], ['market', 'events'], ['market', 'history'], ['market', 'lookthrough'], ['market', 'etf'], ['market', 'fundamentals']]) void qc.invalidateQueries({ queryKey: key })
      const sum = summarizeRefresh(r.last, (sc) => MARKET_JOB[sc] ?? sc)
      if (sum.ok) toast.ok(sum.text); else toast.error(sum.text)
    }
  }, [market.data, qc, toast])
  const reviewCount = docs.data?.filter((d) => d.status === 'review').length ?? 0

  useEffect(() => { setSheet(false); window.scrollTo(0, 0) }, [loc.pathname])
  useEffect(() => {
    if (!sheet) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setSheet(false) }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [sheet])

  const title = TITLES.find(([re]) => re.test(loc.pathname))?.[1] ?? ALL.find((i) => (i.end ? loc.pathname === i.to : loc.pathname.startsWith(i.to)))?.label ?? 'Vermögen'
  useEffect(() => { document.title = `${title} · Vermögen` }, [title])
  const marketOk = market.data ? market.data.online !== false : !market.isError
  const marketNever = market.data?.online == null && !market.data?.last_quote_update
  const ThemeIcon = theme === 'system' ? Monitor : theme === 'light' ? Sun : Moon

  const link = (i: NavItem) => (
    <NavLink key={i.to} to={i.to} end={i.end} className={({ isActive }) => cx('nav-link', isActive && 'active')} title={i.label}>
      <i.icon size={18} strokeWidth={1.75} aria-hidden />
      <span>{i.label}</span>
      {i.to === '/import' && reviewCount > 0 && <span className="count" aria-label={`${reviewCount} Belege zu prüfen`}>{reviewCount}</span>}
    </NavLink>
  )

  return (
    <div className="app">
      <a href="#inhalt" className="skip-link">Zum Inhalt springen</a>
      <aside className="rail">
        <NavLink to="/" className="rail__brand" aria-label="Vermögen – zur Übersicht"><Logo /><span>Vermögen</span></NavLink>
        <nav aria-label="Hauptnavigation">
          {GROUPS.map((g) => (
            <div className="rail__group" key={g.label}>
              <div className="rail__label">{g.label}</div>
              {g.items.map(link)}
            </div>
          ))}
        </nav>
        <div className="rail__foot">
          <div className="rail__status" title={marketOk ? 'Marktdaten erreichbar' : 'Marktdaten nicht erreichbar'}>
            <i className={cx('dot', marketOk && !marketNever ? 'dot--ok' : 'dot--off')} /><span>{refresh.running ? 'Marktdaten werden geladen …' : marketNever ? 'Noch keine Marktdaten' : `Marktdaten ${marketOk ? 'erreichbar' : 'offline'}`}</span>
          </div>
          <div className="rail__status" title={llm.data?.available ? 'Ollama erreichbar' : 'Ollama nicht erreichbar'}>
            <i className={cx('dot', llm.data?.available ? 'dot--ok' : 'dot--off')} /><span>Ollama {llm.data?.available ? 'bereit' : 'offline'}</span>
          </div>
        </div>
      </aside>

      <div className="shell-main">
        <header className="topbar">
          <span className="topbar__title">{title}</span>
          <span className="topbar__meta">
            {market.data?.last_quote_update ? `Kurse ${fmtRelative(market.data.last_quote_update)}` : market.isError ? 'Marktdaten-Status unbekannt' : market.data ? 'Noch keine Kurse abgerufen' : ''}
            {refresh.running ? ' · Aktualisierung läuft …' : market.data && !marketOk ? ' · Yahoo nicht erreichbar' : ''}
          </span>
          <div className="topbar__actions">
            <button className="btn btn--ghost btn--icon" aria-label={refresh.running ? 'Marktdaten werden aktualisiert' : 'Kurse aktualisieren'} title={refresh.running ? 'Aktualisierung läuft …' : 'Kurse aktualisieren'} disabled={refresh.running}
              onClick={() => refresh.start('quotes', { onError: (e) => toast.error(e, 'Aktualisierung konnte nicht gestartet werden:') })}>
              <RefreshCw size={17} aria-hidden className={refresh.running ? 'spin' : undefined} />
            </button>
            <button className="btn btn--ghost btn--icon" aria-pressed={privacy} onClick={togglePrivacy} aria-label="Beträge verbergen" title={privacy ? 'Beträge anzeigen' : 'Beträge verbergen'}>
              {privacy ? <EyeOff size={17} aria-hidden /> : <Eye size={17} aria-hidden />}
            </button>
            <button className="btn btn--ghost btn--icon" onClick={() => setTheme(THEME_NEXT[theme])} aria-label={`Farbschema: ${THEME_LABEL[theme]}. Wechseln`} title={`Farbschema: ${THEME_LABEL[theme]}`}>
              <ThemeIcon size={17} aria-hidden />
            </button>
          </div>
        </header>
        <main id="inhalt" tabIndex={-1} style={{ outline: 'none' }}>{children}</main>
      </div>

      <nav className="bottom-nav" aria-label="Schnellnavigation">
        {ALL.filter((i) => BOTTOM.includes(i.to)).map((i) => (
          <NavLink key={i.to} to={i.to} end={i.end} className={({ isActive }) => (isActive ? 'active' : undefined)}>
            <i.icon size={20} strokeWidth={1.75} aria-hidden />
            {i.label}
          </NavLink>
        ))}
        <button type="button" onClick={() => setSheet(true)} aria-haspopup="dialog" aria-expanded={sheet} className={!BOTTOM.some((b) => (b === '/' ? loc.pathname === '/' : loc.pathname.startsWith(b))) ? 'active' : undefined}>
          <Menu size={20} strokeWidth={1.75} aria-hidden />Mehr
        </button>
      </nav>
      {sheet && (
        <>
          <div className="sheet-scrim" onClick={() => setSheet(false)} />
          <div className="sheet" role="dialog" aria-modal="true" aria-label="Alle Seiten">
            <div className="sheet__head">
              <h2>Alle Seiten</h2>
              <button className="btn btn--ghost btn--icon" onClick={() => setSheet(false)} aria-label="Schließen" autoFocus><X size={18} aria-hidden /></button>
            </div>
            <div className="sheet__grid">
              {ALL.map((i) => (
                <NavLink key={i.to} to={i.to} end={i.end} className={({ isActive }) => (isActive ? 'active' : undefined)}>
                  <i.icon size={18} strokeWidth={1.75} aria-hidden />{i.label}
                </NavLink>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
