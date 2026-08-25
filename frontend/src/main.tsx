import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ThemeProvider } from 'next-themes'
import { Toaster } from './components/ui/sonner'
import './index.css'
import App from './App.tsx'
import { AppProvider } from './store/app-store'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider attribute="class" defaultTheme="light" enableSystem={false} disableTransitionOnChange>
      <AppProvider>
        <App />
        <Toaster position="bottom-right" richColors closeButton />
      </AppProvider>
    </ThemeProvider>
  </StrictMode>,
)
