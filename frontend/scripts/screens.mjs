// Screenshots every page of the mock-mode app at desktop + phone size in light + dark.
//   node scripts/screens.mjs                 all routes
//   node scripts/screens.mjs depot kalender  only routes whose name contains one of the words
// Output: frontend/.screens/<name>-<viewport>-<theme>.png. Console errors are reported and fail the run.
// Playwright + Chromium are expected to be installed globally (not a project dependency).
import { spawn } from 'node:child_process'
import { mkdirSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const require = createRequire(import.meta.url)
let chromium
try { ({ chromium } = require('playwright')) } catch {
  const { execSync } = await import('node:child_process')
  ;({ chromium } = require(join(execSync('npm root -g').toString().trim(), 'playwright')))
}

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const out = join(root, '.screens')
const PORT = 5199
const BASE = `http://localhost:${PORT}`
mkdirSync(out, { recursive: true })

const ROUTES = [
  ['uebersicht', '/'], ['konten', '/konten'], ['konto-giro', '/konten/1'], ['konto-depot', '/konten/4'], ['depot', '/depot'], ['durchschau', '/durchschau'],
  ['wertpapier-aktie', '/wertpapier/6'], ['wertpapier-etf', '/wertpapier/2'], ['dividenden', '/dividenden'], ['kalender', '/kalender'],
  ['transaktionen', '/transaktionen'], ['haushaltsbuch', '/haushaltsbuch'], ['import', '/import'], ['import-pruefen', '/import/4'],
  ['prognose', '/prognose'], ['watchlist', '/watchlist'], ['einstellungen', '/einstellungen'],
]
// Fresh install and offline scenarios (desktop light + phone dark only).
const SCENARIOS = [
  ['leer-uebersicht', '/?mock=empty'], ['leer-depot', '/depot?mock=empty'], ['leer-transaktionen', '/transaktionen?mock=empty'], ['leer-durchschau', '/durchschau?mock=empty'],
  ['leer-dividenden', '/dividenden?mock=empty'], ['leer-haushaltsbuch', '/haushaltsbuch?mock=empty'], ['leer-import', '/import?mock=empty'], ['leer-watchlist', '/watchlist?mock=empty'],
  ['leer-kalender', '/kalender?mock=empty'], ['leer-prognose', '/prognose?mock=empty'], ['leer-konto', '/konten/1?mock=empty'],
  ['offline-import', '/import?mock=offline'], ['offline-einstellungen', '/einstellungen?mock=offline'], ['offline-watchlist', '/watchlist?mock=offline'],
]
const VIEWPORTS = { desktop: { width: 1440, height: 900 }, phone: { width: 390, height: 844 } }
const only = process.argv.slice(2)
const pick = (list) => (only.length ? list.filter(([name]) => only.some((o) => name.includes(o))) : list)

const server = spawn('npx', ['vite', '--port', String(PORT), '--strictPort'], { cwd: root, env: { ...process.env, VITE_MOCK: '1' }, stdio: 'pipe' })
async function waitForServer() {
  for (let i = 0; i < 80; i++) {
    try { if ((await fetch(BASE)).ok) return } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 250))
  }
  throw new Error('Vite dev server did not start')
}

const problems = []
try {
  await waitForServer()
  const browser = await chromium.launch()
  async function shoot(name, path, vp, theme) {
    const ctx = await browser.newContext({ viewport: VIEWPORTS[vp], deviceScaleFactor: 1, colorScheme: theme, locale: 'de-DE', reducedMotion: 'reduce', isMobile: vp === 'phone', hasTouch: vp === 'phone' })
    const page = await ctx.newPage()
    page.on('console', (m) => { if (m.type() === 'error') problems.push(`${name} [${vp}/${theme}] console: ${m.text()}`) })
    page.on('pageerror', (e) => problems.push(`${name} [${vp}/${theme}] exception: ${e.message}`))
    await page.goto(BASE + path, { waitUntil: 'networkidle' })
    await page.waitForTimeout(900)
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    if (overflow > 1) problems.push(`${name} [${vp}/${theme}] horizontal overflow: ${overflow}px`)
    await page.screenshot({ path: join(out, `${name}-${vp}-${theme}.png`), fullPage: true })
    await ctx.close()
  }
  for (const [name, path] of pick(ROUTES)) for (const vp of Object.keys(VIEWPORTS)) for (const theme of ['light', 'dark']) await shoot(name, path, vp, theme)
  for (const [name, path] of pick(SCENARIOS)) { await shoot(name, path, 'desktop', 'light'); await shoot(name, path, 'phone', 'dark') }
  await browser.close()
} finally {
  server.kill('SIGTERM')
}
if (problems.length) { console.error(`${problems.length} problem(s):\n` + problems.join('\n')); process.exit(1) }
console.log(`OK – screenshots in ${out}`)
