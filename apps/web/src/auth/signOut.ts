import type { SupabaseClient } from '@supabase/supabase-js'
import {
  clearPersistedSupabaseSession,
  getPersistedSupabaseAccessToken,
} from './supabaseSessionStorage'

type SupabaseAuthClient = SupabaseClient['auth']
const REMOTE_SIGN_OUT_TIMEOUT_MS = 3_000

async function tryRevokeCurrentSession(
  auth: SupabaseAuthClient,
  accessToken: string,
): Promise<boolean> {
  try {
    const { error } = await auth.admin.signOut(accessToken, 'local')
    return error === null
  } catch {
    return false
  }
}

export function signOutLocally(): void {
  clearPersistedSupabaseSession()
}

export async function signOutWithLocalFallback(auth: SupabaseAuthClient): Promise<void> {
  let timeoutId: ReturnType<typeof setTimeout> | undefined
  const accessToken = getPersistedSupabaseAccessToken()
  const remoteSignOut = accessToken
    ? tryRevokeCurrentSession(auth, accessToken)
    : Promise.resolve(false)
  clearPersistedSupabaseSession()

  try {
    await Promise.race([
      remoteSignOut,
      new Promise<false>((resolve) => {
        timeoutId = setTimeout(
          () => resolve(false),
          REMOTE_SIGN_OUT_TIMEOUT_MS,
        )
      }),
    ])
  } finally {
    if (timeoutId !== undefined) clearTimeout(timeoutId)
  }
}
