import { createClient, type SupabaseClient } from '@supabase/supabase-js'
import { getPublicConfig } from '../config/env'

let client: SupabaseClient | undefined

export function getSupabaseClient(): SupabaseClient {
  if (!client) {
    const config = getPublicConfig()
    client = createClient(config.supabaseUrl, config.supabasePublishableKey, {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: true,
      },
    })
  }
  return client
}
