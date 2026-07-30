import type { SupportedStorage } from '@supabase/supabase-js'
import { getPublicConfig } from '../config/env'

const sessionInvalidationListeners = new Set<() => void>()
const invalidatedSessionIds = new Set<string>()
const MAX_INVALIDATED_SESSION_IDS = 32

function getLocalStorage(): Storage | null {
  try {
    return globalThis.localStorage ?? null
  } catch {
    return null
  }
}

function accessTokenFromSerializedSession(value: string): string | null {
  try {
    const session = JSON.parse(value) as unknown
    if (
      typeof session === 'object'
      && session !== null
      && 'access_token' in session
      && typeof session.access_token === 'string'
      && session.access_token.length > 0
    ) {
      return session.access_token
    }
  } catch {
    // Malformed session data is treated as unauthenticated.
  }
  return null
}

function sessionIdFromAccessToken(accessToken: string): string | null {
  const payload = accessToken.split('.')[1]
  if (!payload) return null

  try {
    const base64 = payload.replace(/-/g, '+').replace(/_/g, '/')
    const padding = '='.repeat((4 - (base64.length % 4)) % 4)
    const claims = JSON.parse(globalThis.atob(`${base64}${padding}`)) as unknown
    if (
      typeof claims === 'object'
      && claims !== null
      && 'session_id' in claims
      && typeof claims.session_id === 'string'
      && claims.session_id.length > 0
    ) {
      return claims.session_id
    }
  } catch {
    // Opaque or malformed tokens cannot provide a session identifier.
  }
  return null
}

function rememberInvalidatedSession(accessToken: string | null): void {
  if (!accessToken) return
  const sessionId = sessionIdFromAccessToken(accessToken)
  if (!sessionId) return

  invalidatedSessionIds.add(sessionId)
  while (invalidatedSessionIds.size > MAX_INVALIDATED_SESSION_IDS) {
    const oldestSessionId = invalidatedSessionIds.values().next().value
    if (typeof oldestSessionId !== 'string') break
    invalidatedSessionIds.delete(oldestSessionId)
  }
}

function belongsToInvalidatedSession(value: string): boolean {
  const accessToken = accessTokenFromSerializedSession(value)
  const sessionId = accessToken ? sessionIdFromAccessToken(accessToken) : null
  return sessionId !== null && invalidatedSessionIds.has(sessionId)
}

export function rememberInvalidatedSerializedSession(
  serializedSession: string | null,
): void {
  rememberInvalidatedSession(
    serializedSession
      ? accessTokenFromSerializedSession(serializedSession)
      : null,
  )
}

export function getSupabaseAuthStorageKey(supabaseUrl: string): string {
  const projectNamespace = new URL(supabaseUrl).hostname.split('.')[0]
  return `sb-${projectNamespace}-auth-token`
}

export const supabaseAuthStorage: SupportedStorage = {
  getItem(key) {
    return getLocalStorage()?.getItem(key) ?? null
  },
  setItem(key, value) {
    const authStorageKey = getSupabaseAuthStorageKey(getPublicConfig().supabaseUrl)
    if (key === authStorageKey && belongsToInvalidatedSession(value)) return
    getLocalStorage()?.setItem(key, value)
  },
  removeItem(key) {
    getLocalStorage()?.removeItem(key)
  },
}

export function getPersistedSupabaseAccessToken(): string | null {
  const storage = getLocalStorage()
  if (!storage) return null

  const storageKey = getSupabaseAuthStorageKey(getPublicConfig().supabaseUrl)
  const serializedSession = storage.getItem(storageKey)
  if (!serializedSession) return null

  return accessTokenFromSerializedSession(serializedSession)
}

export function subscribeToSessionInvalidation(listener: () => void): () => void {
  sessionInvalidationListeners.add(listener)
  return () => sessionInvalidationListeners.delete(listener)
}

export function reconcileCrossTabSessionInvalidation(
  previousSerializedSession: string | null,
): boolean {
  rememberInvalidatedSerializedSession(previousSerializedSession)

  const storage = getLocalStorage()
  if (!storage) return true

  const storageKey = getSupabaseAuthStorageKey(getPublicConfig().supabaseUrl)
  const currentSerializedSession = storage.getItem(storageKey)
  if (!currentSerializedSession) return true

  const currentAccessToken = accessTokenFromSerializedSession(
    currentSerializedSession,
  )
  const currentSessionId = currentAccessToken
    ? sessionIdFromAccessToken(currentAccessToken)
    : null
  if (
    currentSessionId !== null
    && !invalidatedSessionIds.has(currentSessionId)
  ) {
    return false
  }

  clearPersistedSupabaseSession()
  return true
}

export function clearPersistedSupabaseSession(): void {
  const storage = getLocalStorage()
  if (storage) {
    const storageKey = getSupabaseAuthStorageKey(getPublicConfig().supabaseUrl)
    rememberInvalidatedSerializedSession(storage.getItem(storageKey))
    storage.removeItem(storageKey)
    storage.removeItem(`${storageKey}-code-verifier`)
    storage.removeItem(`${storageKey}-user`)
  }

  for (const listener of sessionInvalidationListeners) {
    try {
      listener()
    } catch {
      // Session removal must not depend on a UI listener completing.
    }
  }
}
