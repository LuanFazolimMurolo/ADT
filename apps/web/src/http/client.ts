import { getPublicConfig } from '../config/env'
import { getSupabaseClient } from '../lib/supabase'
import type {
  AdminMe,
  ApiErrorEnvelope,
  CapitalMovement,
  HealthResponse,
  MovementCreateRequest,
  MovementListResponse,
  Setting,
  SettingsListResponse,
  SimulationCreateRequest,
  SimulationDetail,
  SimulationListResponse,
} from '../types/api'
import type { SystemStatus } from '../types/system'

const STATUS_MESSAGES: Record<number, string> = {
  401: 'Sua sessão expirou. Entre novamente.',
  403: 'Esta conta não possui acesso administrativo.',
  404: 'O recurso solicitado não foi encontrado.',
  409: 'A operação conflita com o estado atual dos dados.',
  422: 'Revise os dados informados.',
  503: 'O serviço está temporariamente indisponível.',
}

const browserFetch: typeof fetch = (input, init) =>
  globalThis.fetch(input, init)

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details?: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export interface ApiClientOptions {
  baseUrl?: string
  getAccessToken?: () => Promise<string | null>
  refreshAccessToken?: () => Promise<string | null>
  onAuthenticationFailure?: () => Promise<void>
  fetchImplementation?: typeof fetch
}

export class ApiClient {
  private readonly fetchImplementation: typeof fetch

  constructor(private readonly options: ApiClientOptions) {
    this.fetchImplementation = options.fetchImplementation ?? browserFetch
  }

  private async parse<T>(response: Response): Promise<T> {
    if (response.status === 204) return undefined as T
    const text = await response.text()
    if (!text) return undefined as T
    try {
      return JSON.parse(text) as T
    } catch {
      throw new ApiError(response.status, 'invalid_response', 'A API retornou uma resposta inválida.')
    }
  }

  private async performRequest<T>(
    path: string,
    options: RequestInit,
    token: string | null,
  ): Promise<T> {
    const headers = new Headers(options.headers)
    headers.set('Accept', 'application/json')
    if (options.body) headers.set('Content-Type', 'application/json')
    if (token) headers.set('Authorization', `Bearer ${token}`)

    const baseUrl = this.options.baseUrl ?? getPublicConfig().apiUrl
    const response = await this.fetchImplementation(`${baseUrl}${path}`, {
      ...options,
      headers,
    })

    if (response.ok) return this.parse<T>(response)

    let envelope: ApiErrorEnvelope | undefined
    try {
      envelope = await this.parse<ApiErrorEnvelope>(response)
    } catch {
      envelope = undefined
    }
    throw new ApiError(
      response.status,
      envelope?.error?.code ?? `http_${response.status}`,
      envelope?.error?.message ?? STATUS_MESSAGES[response.status] ?? 'Não foi possível concluir a solicitação.',
      envelope?.error?.details,
    )
  }

  async request<T>(
    path: string,
    options: RequestInit = {},
    authenticated = true,
  ): Promise<T> {
    const method = (options.method ?? 'GET').toUpperCase()
    const token = authenticated && this.options.getAccessToken
      ? await this.options.getAccessToken()
      : null

    try {
      return await this.performRequest<T>(path, options, token)
    } catch (error) {
      if (
        error instanceof ApiError &&
        error.status === 401 &&
        method === 'GET' &&
        authenticated &&
        this.options.refreshAccessToken
      ) {
        let refreshedToken: string | null = null
        try {
          refreshedToken = await this.options.refreshAccessToken()
        } catch {
          await this.options.onAuthenticationFailure?.()
          throw error
        }
        if (refreshedToken) {
          try {
            return await this.performRequest<T>(path, options, refreshedToken)
          } catch (retryError) {
            if (retryError instanceof ApiError && retryError.status === 401) {
              await this.options.onAuthenticationFailure?.()
            }
            throw retryError
          }
        }
        await this.options.onAuthenticationFailure?.()
      } else if (error instanceof ApiError && error.status === 403) {
        await this.options.onAuthenticationFailure?.()
      }
      throw error
    }
  }

  getSystemStatus(): Promise<SystemStatus> {
    return this.request('/api/v1/system/status', {}, false)
  }

  getHealth(): Promise<HealthResponse> {
    return this.request('/health', {}, false)
  }

  getDatabaseHealth(): Promise<HealthResponse> {
    return this.request('/health/database', {}, false)
  }

  getAdminMe(): Promise<AdminMe> {
    return this.request('/api/v1/admin/me')
  }

  listSimulations(page = 1, pageSize = 20): Promise<SimulationListResponse> {
    return this.request(`/api/v1/admin/simulations?page=${page}&page_size=${pageSize}`)
  }

  createSimulation(payload: SimulationCreateRequest): Promise<SimulationDetail> {
    return this.request('/api/v1/admin/simulations', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  getSimulation(id: string): Promise<SimulationDetail> {
    return this.request(`/api/v1/admin/simulations/${encodeURIComponent(id)}`)
  }

  completeSimulation(id: string): Promise<SimulationDetail> {
    return this.request(`/api/v1/admin/simulations/${encodeURIComponent(id)}/complete`, {
      method: 'POST',
    })
  }

  cancelSimulation(id: string): Promise<SimulationDetail> {
    return this.request(`/api/v1/admin/simulations/${encodeURIComponent(id)}/cancel`, {
      method: 'POST',
    })
  }

  listMovements(id: string, page = 1, pageSize = 20): Promise<MovementListResponse> {
    return this.request(
      `/api/v1/admin/simulations/${encodeURIComponent(id)}/movements?page=${page}&page_size=${pageSize}`,
    )
  }

  createMovement(id: string, payload: MovementCreateRequest): Promise<CapitalMovement> {
    return this.request(`/api/v1/admin/simulations/${encodeURIComponent(id)}/movements`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  listSettings(): Promise<SettingsListResponse> {
    return this.request('/api/v1/admin/settings')
  }

  updateSetting(key: string, value: Setting['value']): Promise<Setting> {
    return this.request(`/api/v1/admin/settings/${encodeURIComponent(key)}`, {
      method: 'PATCH',
      body: JSON.stringify({ value }),
    })
  }
}

export const apiClient = new ApiClient({
  getAccessToken: async () => {
    const { data } = await getSupabaseClient().auth.getSession()
    return data.session?.access_token ?? null
  },
  refreshAccessToken: async () => {
    const { data } = await getSupabaseClient().auth.refreshSession()
    return data.session?.access_token ?? null
  },
  onAuthenticationFailure: async () => {
    await getSupabaseClient().auth.signOut()
  },
})
