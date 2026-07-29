export type JsonValue =
  | null
  | boolean
  | number
  | string
  | JsonValue[]
  | { [key: string]: JsonValue }

export interface AdminMe {
  user_id: string
  is_admin: boolean
}

export interface HealthResponse {
  status: 'healthy' | 'ready'
}

export interface PageMeta {
  page: number
  page_size: number
  total: number
  total_pages: number
}

export type SimulationStatus = 'ACTIVE' | 'COMPLETED' | 'CANCELLED'

export interface SimulationListItem {
  id: string
  name: string
  status: SimulationStatus
  currency: string
  initial_capital: string
  started_at: string
  ended_at: string | null
  created_at: string
  updated_at: string
}

export interface SimulationDetail extends SimulationListItem {
  created_by: string
  current_balance: string
  total_profit_loss: string
}

export interface SimulationListResponse {
  items: SimulationListItem[]
  pagination: PageMeta
}

export interface SimulationCreateRequest {
  name: string
  initial_capital: string
  currency: string
}

export type MovementCreateType = 'DEPOSIT' | 'WITHDRAWAL' | 'ADJUSTMENT'
export type CapitalMovementType =
  | 'INITIAL_CAPITAL'
  | 'ADMIN_DEPOSIT'
  | 'ADMIN_WITHDRAWAL'
  | 'TRADE_PROFIT'
  | 'TRADE_LOSS'
  | 'FEE'
  | 'ADJUSTMENT'

export interface MovementCreateRequest {
  type: MovementCreateType
  amount: string
  reason: string
  metadata?: Record<string, JsonValue> | null
}

export interface CapitalMovement {
  id: string
  simulation_id: string
  type: CapitalMovementType
  amount: string
  reason: string
  reference_id: string | null
  created_by: string | null
  created_at: string
  metadata: Record<string, JsonValue> | null
}

export interface MovementListResponse {
  items: CapitalMovement[]
  pagination: PageMeta
}

export interface Setting {
  key: string
  value: JsonValue
  description: string
  is_public: boolean
  updated_by: string | null
  created_at: string
  updated_at: string
}

export interface SettingsListResponse {
  items: Setting[]
}

export interface ApiErrorEnvelope {
  error?: {
    code?: string
    message?: string
    details?: JsonValue
  }
}
