import type { Session, User } from '@supabase/supabase-js'
import type {
  CapitalMovement,
  Setting,
  SimulationDetail,
} from '../../src/types/api'
import {
  ADMIN_EMAIL,
  ADMIN_ID,
  SIMULATION_ID,
  USER_EMAIL,
  USER_ID,
} from './constants'

export type MockRole = 'admin' | 'user'

const FIXED_NOW = '2026-07-29T15:00:00.000Z'

export function createUser(role: MockRole): User {
  const isAdmin = role === 'admin'
  return {
    id: isAdmin ? ADMIN_ID : USER_ID,
    aud: 'authenticated',
    role: 'authenticated',
    email: isAdmin ? ADMIN_EMAIL : USER_EMAIL,
    email_confirmed_at: FIXED_NOW,
    phone: '',
    confirmed_at: FIXED_NOW,
    last_sign_in_at: FIXED_NOW,
    app_metadata: { provider: 'email', providers: ['email'] },
    user_metadata: {},
    identities: [],
    created_at: FIXED_NOW,
    updated_at: FIXED_NOW,
    is_anonymous: false,
  }
}

export function createSession(
  role: MockRole,
  accessToken: string,
  refreshToken: string,
): Session {
  return {
    access_token: accessToken,
    token_type: 'bearer',
    expires_in: 3600,
    expires_at: Math.floor(Date.now() / 1000) + 3600,
    refresh_token: refreshToken,
    user: createUser(role),
  }
}

export function createSimulation(
  overrides: Partial<SimulationDetail> = {},
): SimulationDetail {
  return {
    id: SIMULATION_ID,
    name: 'Simulação principal',
    status: 'ACTIVE',
    currency: 'USD',
    initial_capital: '1000.00000000',
    current_balance: '1000.00000000',
    total_profit_loss: '0.00000000',
    started_at: FIXED_NOW,
    ended_at: null,
    created_at: FIXED_NOW,
    updated_at: FIXED_NOW,
    created_by: ADMIN_ID,
    ...overrides,
  }
}

export function createInitialMovement(
  simulationId = SIMULATION_ID,
  amount = '1000.00000000',
): CapitalMovement {
  return {
    id: '44444444-4444-4444-8444-444444444444',
    simulation_id: simulationId,
    type: 'INITIAL_CAPITAL',
    amount,
    reason: 'Initial simulated capital',
    reference_id: null,
    created_by: ADMIN_ID,
    created_at: FIXED_NOW,
    metadata: null,
  }
}

export function createSettings(): Setting[] {
  return [
    {
      key: 'display_currency',
      value: 'USD',
      description: 'Moeda exibida por padrão.',
      is_public: true,
      updated_by: null,
      created_at: FIXED_NOW,
      updated_at: FIXED_NOW,
    },
    {
      key: 'risk_limits',
      value: { daily: 2, enabled: true },
      description: 'Limites administrativos para paper trading.',
      is_public: false,
      updated_by: ADMIN_ID,
      created_at: FIXED_NOW,
      updated_at: FIXED_NOW,
    },
  ]
}
