import { useEffect, type ReactNode } from 'react'
import { useTheme } from 'next-themes'
import { useApp } from '../store/app-store'

export type ColorTheme = 'dark' | 'light'

const THEME_KEY_PREFIX = 'flowhub_theme_'

function userThemeKey(user: { id?: string; account?: string } | null) {
  const identity = user?.id || user?.account
  return identity ? `${THEME_KEY_PREFIX}${identity}` : null
}

export function getUserTheme(user: { id?: string; account?: string } | null): ColorTheme {
  if (user && "theme" in user && user.theme === 'light') return 'light'
  const key = userThemeKey(user)
  return key && localStorage.getItem(key) === 'light' ? 'light' : 'dark'
}

export function saveUserTheme(user: { id?: string; account?: string } | null, theme: ColorTheme) {
  const key = userThemeKey(user)
  if (key) localStorage.setItem(key, theme)
}

/** Keeps next-themes' document class aligned with the signed-in user's setting. */
export function UserThemeSync({ children }: { children: ReactNode }) {
  const { currentUser } = useApp()
  const { setTheme } = useTheme()

  useEffect(() => {
    setTheme(getUserTheme(currentUser))
  }, [currentUser?.id, currentUser?.account, setTheme])

  return <>{children}</>
}
