import { createClient, type SupabaseClient } from '@supabase/supabase-js'
import {
  getSupabaseAuthStorageKey,
  subscribeToSessionInvalidation,
  supabaseAuthStorage,
} from '../auth/supabaseSessionStorage'
import { getPublicConfig } from '../config/env'

let client: SupabaseClient | undefined
let passwordRecoveryContext = false

subscribeToSessionInvalidation(() => {
  passwordRecoveryContext = false
})

export function getSupabaseClient(): SupabaseClient {
  if (!client) {
    const config = getPublicConfig()
    client = createClient(config.supabaseUrl, config.supabasePublishableKey, {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: true,
        storage: supabaseAuthStorage,
        storageKey: getSupabaseAuthStorageKey(config.supabaseUrl),
      },
    })
    client.auth.onAuthStateChange((event) => {
      if (event === 'PASSWORD_RECOVERY') passwordRecoveryContext = true
      if (event === 'SIGNED_OUT') passwordRecoveryContext = false
    })
  }
  return client
}

export function hasPasswordRecoveryContext(): boolean {
  return passwordRecoveryContext
}

export function clearPasswordRecoveryContext(): void {
  passwordRecoveryContext = false
}
