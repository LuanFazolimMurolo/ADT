import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

vi.stubEnv('VITE_ADT_API_URL', 'http://localhost:8000')
vi.stubEnv('VITE_SUPABASE_URL', 'https://example.supabase.co')
vi.stubEnv('VITE_SUPABASE_PUBLISHABLE_KEY', 'sb_publishable_test_only')

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})
