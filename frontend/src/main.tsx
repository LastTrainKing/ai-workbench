import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import './index.css'
import { setUnauthorizedHandler, toApiError } from '@/lib/api'
import { handleUnauthorized, useAuthStore } from '@/store/auth'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error) => {
        const apiError = toApiError(error)
        // Never retry an auth failure; do retry transient infrastructure errors.
        if (apiError.isAuthError || apiError.status === 404) return false
        return apiError.isRetryable && failureCount < 2
      },
      refetchOnWindowFocus: false,
      staleTime: 15_000,
    },
    mutations: { retry: false },
  },
})

// Any 401 clears the session; the route guard then redirects to /login.
setUnauthorizedHandler(() => handleUnauthorized())

// Revalidate the persisted token before the first protected render.
void useAuthStore.getState().hydrate()

const rootElement = document.getElementById('root')
if (!rootElement) throw new Error('#root element is missing from index.html')

ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
)
