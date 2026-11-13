import '@fontsource-variable/inter/wght.css'
import { CssBaseline, ThemeProvider } from '@mui/material'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'

import { App } from './App'
import { ApiError } from './api/client'
import './index.css'
import { theme } from './theme'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Retry server/network errors once, never a 4xx (it will not change).
      retry: (failureCount, error) =>
        failureCount < 1 && !(error instanceof ApiError && error.status < 500),
      refetchOnWindowFocus: false,
    },
  },
})

const root = document.getElementById('root')
if (!root) {
  throw new Error('missing #root element')
}

createRoot(root).render(
  <StrictMode>
    <ThemeProvider theme={theme} defaultMode="system">
      <CssBaseline />
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
)
