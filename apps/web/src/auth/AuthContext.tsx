import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import type { Session } from '@supabase/supabase-js'
import { apiClient, ApiError } from '../http/client'
import { getPublicConfig } from '../config/env'
import {
  clearPasswordRecoveryContext,
  getSupabaseClient,
} from '../lib/supabase'
import { signOutLocally, signOutWithLocalFallback } from './signOut'
import {
  getPersistedSupabaseAccessToken,
  getSupabaseAuthStorageKey,
  reconcileCrossTabSessionInvalidation,
  subscribeToSessionInvalidation,
} from './supabaseSessionStorage'

interface AuthContextValue {
  session: Session | null
  isAdmin: boolean
  loading: boolean
  signIn(email: string, password: string): Promise<void>
  signOut(): Promise<void>
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null)
  const [isAdmin, setIsAdmin] = useState(false)
  const [loading, setLoading] = useState(true)
  const pendingSignOut = useRef<Promise<void> | null>(null)

  const clearAuthenticationState = useCallback(() => {
    setSession(null)
    setIsAdmin(false)
  }, [])

  const clearLocalAccess = useCallback(async () => {
    try {
      signOutLocally()
    } finally {
      clearAuthenticationState()
    }
  }, [clearAuthenticationState])

  const signOut = useCallback(async () => {
    clearAuthenticationState()
    const operation = signOutWithLocalFallback(getSupabaseClient().auth)
    pendingSignOut.current = operation
    try {
      await operation
    } finally {
      if (pendingSignOut.current === operation) pendingSignOut.current = null
    }
  }, [clearAuthenticationState])

  const verifyAdministrator = useCallback(async (nextSession: Session) => {
    setSession(nextSession)
    try {
      const identity = await apiClient.getAdminMe()
      if (!identity.is_admin) throw new ApiError(403, 'forbidden', 'Acesso administrativo negado.')
      setIsAdmin(true)
    } catch (error) {
      setIsAdmin(false)
      await clearLocalAccess()
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        throw new ApiError(403, 'forbidden', 'Esta conta não possui acesso administrativo.')
      }
      throw error
    }
  }, [clearLocalAccess])

  const signIn = useCallback(async (email: string, password: string) => {
    await pendingSignOut.current
    const { data, error } = await getSupabaseClient().auth.signInWithPassword({
      email,
      password,
    })
    if (error || !data.session) {
      throw new Error('Não foi possível entrar. Verifique as credenciais e tente novamente.')
    }
    await verifyAdministrator(data.session)
  }, [verifyAdministrator])

  useEffect(() => {
    let active = true
    const supabase = getSupabaseClient()
    const authStorageKey = getSupabaseAuthStorageKey(getPublicConfig().supabaseUrl)
    const unsubscribeInvalidation = subscribeToSessionInvalidation(
      clearAuthenticationState,
    )
    const handleCrossTabSignOut = (event: StorageEvent) => {
      if (event.key === authStorageKey && event.newValue === null) {
        if (reconcileCrossTabSessionInvalidation(event.oldValue)) {
          clearPasswordRecoveryContext()
          clearAuthenticationState()
        }
      }
    }
    globalThis.addEventListener('storage', handleCrossTabSignOut)

    const restore = async () => {
      try {
        const { data, error } = await supabase.auth.getSession()
        if (error) throw error
        if (active && data.session) await verifyAdministrator(data.session)
      } catch {
        if (active) {
          clearAuthenticationState()
        }
      } finally {
        if (active) setLoading(false)
      }
    }

    void restore()
    const { data: subscription } = supabase.auth.onAuthStateChange((event, nextSession) => {
      if (!active) return
      if (event === 'SIGNED_OUT' || !nextSession) {
        clearAuthenticationState()
        setLoading(false)
      } else if (event === 'TOKEN_REFRESHED') {
        const persistedAccessToken = getPersistedSupabaseAccessToken()
        if (persistedAccessToken === nextSession.access_token) {
          setSession(nextSession)
        } else if (persistedAccessToken === null) {
          clearAuthenticationState()
        }
      }
    })

    return () => {
      active = false
      unsubscribeInvalidation()
      globalThis.removeEventListener('storage', handleCrossTabSignOut)
      subscription.subscription.unsubscribe()
    }
  }, [clearAuthenticationState, verifyAdministrator])

  const value = useMemo<AuthContextValue>(() => ({
    session,
    isAdmin,
    loading,
    signIn,
    signOut,
  }), [session, isAdmin, loading, signIn, signOut])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

// The hook intentionally shares the provider module to keep the auth contract local.
// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth precisa ser usado dentro de AuthProvider.')
  return context
}
