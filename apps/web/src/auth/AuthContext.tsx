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
import type { AppMe } from '../types/api'
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
  identity: AppMe | null
  isAdmin: boolean
  loading: boolean
  signIn(email: string, password: string): Promise<AppMe>
  signOut(): Promise<void>
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null)
  const [identity, setIdentity] = useState<AppMe | null>(null)
  const [loading, setLoading] = useState(true)
  const pendingSignOut = useRef<Promise<void> | null>(null)
  const identityValidation = useRef(0)

  const clearAuthenticationState = useCallback(() => {
    identityValidation.current += 1
    setSession(null)
    setIdentity(null)
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

  const verifyIdentity = useCallback(
    async (nextSession: Session) => {
      const validation = identityValidation.current + 1
      identityValidation.current = validation
      setSession(nextSession)
      setIdentity(null)
      try {
        const nextIdentity = await apiClient.getAppMe()
        if (identityValidation.current !== validation) {
          throw new Error('A validação da sessão foi substituída.')
        }
        if (nextIdentity.user_id !== nextSession.user.id) {
          await clearLocalAccess()
          throw new ApiError(
            401,
            'identity_mismatch',
            'Não foi possível validar sua sessão. Entre novamente.',
          )
        }
        setIdentity(nextIdentity)
        return nextIdentity
      } catch (error) {
        if (identityValidation.current === validation) {
          setIdentity(null)
          if (error instanceof ApiError && error.status === 401) {
            await clearLocalAccess()
          }
        }
        throw error
      }
    },
    [clearLocalAccess],
  )

  const signIn = useCallback(
    async (email: string, password: string) => {
      await pendingSignOut.current
      const { data, error } = await getSupabaseClient().auth.signInWithPassword(
        {
          email,
          password,
        },
      )
      if (error || !data.session) {
        throw new Error(
          'Não foi possível entrar. Verifique as credenciais e tente novamente.',
        )
      }
      return verifyIdentity(data.session)
    },
    [verifyIdentity],
  )

  useEffect(() => {
    let active = true
    const supabase = getSupabaseClient()
    const authStorageKey = getSupabaseAuthStorageKey(
      getPublicConfig().supabaseUrl,
    )
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
      let restoredSession: Session | null = null
      try {
        const { data, error } = await supabase.auth.getSession()
        if (error) throw error
        restoredSession = data.session
        if (active && restoredSession) await verifyIdentity(restoredSession)
      } catch {
        if (active && !restoredSession) {
          clearAuthenticationState()
        }
      } finally {
        if (active) setLoading(false)
      }
    }

    void restore()
    const { data: subscription } = supabase.auth.onAuthStateChange(
      (event, nextSession) => {
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
      },
    )

    return () => {
      active = false
      unsubscribeInvalidation()
      globalThis.removeEventListener('storage', handleCrossTabSignOut)
      subscription.subscription.unsubscribe()
    }
  }, [clearAuthenticationState, verifyIdentity])

  const isAdmin = identity?.is_admin === true

  const value = useMemo<AuthContextValue>(
    () => ({
      session,
      identity,
      isAdmin,
      loading,
      signIn,
      signOut,
    }),
    [session, identity, isAdmin, loading, signIn, signOut],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

// The hook intentionally shares the provider module to keep the auth contract local.
// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context)
    throw new Error('useAuth precisa ser usado dentro de AuthProvider.')
  return context
}
