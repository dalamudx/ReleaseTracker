import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './i18n/config'
import App from './App.tsx'

import { AuthProvider } from '@/providers/AuthProvider'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

// Create the global QueryClient instance and configure default behavior
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Retry failed requests at most once to avoid extra backend load
      retry: 1,
      // When components remount, refetch only if data exceeds staleTime
      refetchOnMount: true,
      // When the window regains focus, refresh in the background only if data exceeds staleTime
      refetchOnWindowFocus: true,
      // Automatically refetch when the network reconnects
      refetchOnReconnect: true,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <App />
      </AuthProvider>
    </QueryClientProvider>
  </StrictMode>,
)
