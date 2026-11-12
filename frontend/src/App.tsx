import { lazy, Suspense } from 'react'
import { Link, Route, Routes } from 'react-router-dom'
import { Compass } from 'lucide-react'
import { Layout } from './components/Layout'
import { EmptyState, SkeletonRows } from './components/ui'
import { Overview } from './pages/Overview'

const Accounts = lazy(() => import('./pages/Accounts').then((m) => ({ default: m.Accounts })))
const AccountDetail = lazy(() => import('./pages/Accounts').then((m) => ({ default: m.AccountDetail })))
const Portfolio = lazy(() => import('./pages/Portfolio').then((m) => ({ default: m.Portfolio })))
const Lookthrough = lazy(() => import('./pages/Lookthrough').then((m) => ({ default: m.Lookthrough })))
const InstrumentDetail = lazy(() => import('./pages/InstrumentDetail').then((m) => ({ default: m.InstrumentDetail })))
const Dividends = lazy(() => import('./pages/Dividends').then((m) => ({ default: m.Dividends })))
const Taxes = lazy(() => import('./pages/Taxes').then((m) => ({ default: m.Taxes })))
const Calendar = lazy(() => import('./pages/Calendar').then((m) => ({ default: m.Calendar })))
const Transactions = lazy(() => import('./pages/Transactions').then((m) => ({ default: m.Transactions })))
const Budget = lazy(() => import('./pages/Budget').then((m) => ({ default: m.Budget })))
const Import = lazy(() => import('./pages/Import').then((m) => ({ default: m.Import })))
const ImportReview = lazy(() => import('./pages/ImportReview').then((m) => ({ default: m.ImportReview })))
const Forecast = lazy(() => import('./pages/Forecast').then((m) => ({ default: m.Forecast })))
const Watchlist = lazy(() => import('./pages/Watchlist').then((m) => ({ default: m.Watchlist })))
const Settings = lazy(() => import('./pages/Settings').then((m) => ({ default: m.Settings })))

function NotFound() {
  return (
    <div className="page">
      <EmptyState icon={<Compass size={24} aria-hidden />} title="Seite nicht gefunden" text="Diese Adresse gibt es in der App nicht.">
        <Link className="btn btn--primary" to="/">Zur Übersicht</Link>
      </EmptyState>
    </div>
  )
}

export function App() {
  return (
    <Layout>
      <Suspense fallback={<div className="page"><SkeletonRows rows={8} pad={false} /></div>}>
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/konten" element={<Accounts />} />
          <Route path="/konten/:id" element={<AccountDetail />} />
          <Route path="/depot" element={<Portfolio />} />
          <Route path="/durchschau" element={<Lookthrough />} />
          <Route path="/wertpapier/:id" element={<InstrumentDetail />} />
          <Route path="/dividenden" element={<Dividends />} />
          <Route path="/steuern" element={<Taxes />} />
          <Route path="/kalender" element={<Calendar />} />
          <Route path="/transaktionen" element={<Transactions />} />
          <Route path="/haushaltsbuch" element={<Budget />} />
          <Route path="/import" element={<Import />} />
          <Route path="/import/:id" element={<ImportReview />} />
          <Route path="/prognose" element={<Forecast />} />
          <Route path="/watchlist" element={<Watchlist />} />
          <Route path="/einstellungen" element={<Settings />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </Suspense>
    </Layout>
  )
}
