import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import type { Session } from '@supabase/supabase-js'
import { apiClient, ApiError } from '../http/client'
import { getSupabaseClient } from '../lib/supabase'

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

  const signOut = useCallback(async () => {
    await getSupabaseClient().auth.signOut()
    setSession(null)
    setIsAdmin(false)
  }, [])

  const verifyAdministrator = useCallback(async (nextSession: Session) => {
    setSession(nextSession)
    try {
      const identity = await apiClient.getAdminMe()
      if (!identity.is_admin) throw new ApiError(403, 'forbidden', 'Acesso administrativo negado.')
      setIsAdmin(true)
    } catch (error) {
      setIsAdmin(false)
      await getSupabaseClient().auth.signOut()
      setSession(null)
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        throw new ApiError(403, 'forbidden', 'Esta conta não possui acesso administrativo.')
      }
      throw error
    }
  }, [])

  const signIn = useCallback(async (email: string, password: string) => {
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

    const restore = async () => {
      try {
        const { data, error } = await supabase.auth.getSession()
        if (error) throw error
        if (active && data.session) await verifyAdministrator(data.session)
      } catch {
        if (active) {
          setSession(null)
          setIsAdmin(false)
        }
      } finally {
        if (active) setLoading(false)
      }
    }

    void restore()
    const { data: subscription } = supabase.auth.onAuthStateChange((event, nextSession) => {
      if (!active) return
      if (event === 'SIGNED_OUT' || !nextSession) {
        setSession(null)
        setIsAdmin(false)
        setLoading(false)
      } else if (event === 'TOKEN_REFRESHED') {
        setSession(nextSession)
      }
    })

    return () => {
      active = false
      subscription.subscription.unsubscribe()
    }
  }, [verifyAdministrator])

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
