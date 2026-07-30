import { beforeEach, describe, expect, it } from 'vitest'
import {
  clearPersistedSupabaseSession,
  getSupabaseAuthStorageKey,
  reconcileCrossTabSessionInvalidation,
  rememberInvalidatedSerializedSession,
  supabaseAuthStorage,
} from './supabaseSessionStorage'

function jwtForSession(sessionId: string, marker: string): string {
  const header = globalThis.btoa(JSON.stringify({ alg: 'none', typ: 'JWT' }))
  const payload = globalThis.btoa(JSON.stringify({
    session_id: sessionId,
    marker,
  }))
  return `${header}.${payload}.test-signature`
}

describe('armazenamento da sessão Supabase', () => {
  beforeEach(() => localStorage.clear())

  it('não aceita que um refresh tardio restaure uma sessão invalidada', () => {
    const storageKey = getSupabaseAuthStorageKey('https://example.supabase.co')
    localStorage.setItem(storageKey, JSON.stringify({
      access_token: jwtForSession('invalidated-session', 'old'),
      refresh_token: 'old-refresh-token',
    }))

    clearPersistedSupabaseSession()
    supabaseAuthStorage.setItem(storageKey, JSON.stringify({
      access_token: jwtForSession('invalidated-session', 'late-refresh'),
      refresh_token: 'rotated-refresh-token',
    }))

    expect(localStorage.getItem(storageKey)).toBeNull()

    const newSession = JSON.stringify({
      access_token: jwtForSession('new-session', 'new-login'),
      refresh_token: 'new-refresh-token',
    })
    supabaseAuthStorage.setItem(storageKey, newSession)
    expect(localStorage.getItem(storageKey)).toBe(newSession)
  })

  it('propaga a invalidação recebida de outra aba', () => {
    const storageKey = getSupabaseAuthStorageKey('https://example.supabase.co')
    const previousSession = JSON.stringify({
      access_token: jwtForSession('cross-tab-session', 'old'),
      refresh_token: 'old-cross-tab-refresh-token',
    })

    rememberInvalidatedSerializedSession(previousSession)
    supabaseAuthStorage.setItem(storageKey, JSON.stringify({
      access_token: jwtForSession('cross-tab-session', 'late-refresh'),
      refresh_token: 'rotated-cross-tab-refresh-token',
    }))

    expect(localStorage.getItem(storageKey)).toBeNull()
  })

  it('reconcilia a ordem entre remoção cross-tab e refresh tardio', () => {
    const storageKey = getSupabaseAuthStorageKey('https://example.supabase.co')
    const previousSession = JSON.stringify({
      access_token: jwtForSession('cross-tab-race', 'old'),
      refresh_token: 'old-race-refresh-token',
    })
    localStorage.setItem(storageKey, JSON.stringify({
      access_token: jwtForSession('cross-tab-race', 'late-refresh'),
      refresh_token: 'rotated-race-refresh-token',
    }))

    expect(reconcileCrossTabSessionInvalidation(previousSession)).toBe(true)
    expect(localStorage.getItem(storageKey)).toBeNull()

    const newSession = JSON.stringify({
      access_token: jwtForSession('cross-tab-new-login', 'new-login'),
      refresh_token: 'new-login-refresh-token',
    })
    localStorage.setItem(storageKey, newSession)
    expect(reconcileCrossTabSessionInvalidation(previousSession)).toBe(false)
    expect(localStorage.getItem(storageKey)).toBe(newSession)
  })
})
