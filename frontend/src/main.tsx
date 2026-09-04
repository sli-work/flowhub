import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ThemeProvider } from 'next-themes'
import { UserThemeSync } from './components/user-theme-sync'
import { Toaster } from './components/ui/sonner'
import './index.css'
import App from './App.tsx'
import { AppProvider } from './store/app-store'
import { ExpertOsProvider } from './store/expert-os-store'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false} disableTransitionOnChange>
      <AppProvider>
        <ExpertOsProvider>
          <UserThemeSync>
            <App />
          </UserThemeSync>
          <Toaster position="bottom-right" richColors closeButton />
        </ExpertOsProvider>
      </AppProvider>
    </ThemeProvider>
  </StrictMode>,
)
