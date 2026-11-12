import React from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import '@fontsource-variable/ibm-plex-sans'
import './styles/tokens.css'
import './styles/app.css'
import { App } from './App'
import { ApiError } from './api/client'
import { PrefsProvider } from './lib/prefs'
import { ToastProvider } from './components/Toast'

async function boot() {
  // Mock layer: statically replaced flag + dynamic import, so the mock code is not even emitted in a normal build.
  if (import.meta.env.VITE_MOCK === '1') {
    const { installMock } = await import('./mock')
    installMock()
  }
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        refetchOnWindowFocus: false,
        retry: (count, error) => !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 1,
      },
    },
  })
  createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <QueryClientProvider client={queryClient}>
        <PrefsProvider>
          <ToastProvider>
            <BrowserRouter>
              <App />
            </BrowserRouter>
          </ToastProvider>
        </PrefsProvider>
      </QueryClientProvider>
    </React.StrictMode>,
  )
}
void boot()
