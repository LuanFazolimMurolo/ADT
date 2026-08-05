import { describe, expect, it, vi } from 'vitest'
import { ApiClient, ApiError } from './client'

function jsonResponse(status: number, body?: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('ApiClient', () => {
  it('mantém o binding do fetch nativo quando não há implementação injetada', async () => {
    const nativeFetch = vi.fn(function (this: unknown) {
      expect(this).toBe(globalThis)
      return Promise.resolve(jsonResponse(200, { status: 'healthy' }))
    })
    vi.stubGlobal('fetch', nativeFetch)

    try {
      const client = new ApiClient({ baseUrl: 'http://api.test' })
      await expect(client.getHealth()).resolves.toEqual({ status: 'healthy' })
      expect(nativeFetch).toHaveBeenCalledOnce()
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('consulta o dashboard de paper trading com paginação autenticada', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, {
      items: [],
      totals: {},
      page: 3,
      page_size: 20,
      total: 0,
      total_pages: 0,
      runner: null,
    }))
    const client = new ApiClient({
      baseUrl: 'http://api.test',
      getAccessToken: async () => 'token',
      fetchImplementation: fetchMock as typeof fetch,
    })

    await client.getPaperTradingDashboard(3, 20)

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      'http://api.test/api/v1/admin/paper-trading/dashboard?page=3&page_size=20',
    )
    const headers = fetchMock.mock.calls[0]?.[1]?.headers as Headers
    expect(headers.get('Authorization')).toBe('Bearer token')
  })

  it('envia o token Bearer sem registrá-lo', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { user_id: 'id', is_admin: true }))
    const consoleSpy = vi.spyOn(console, 'log')
    const client = new ApiClient({
      baseUrl: 'http://api.test',
      getAccessToken: async () => 'token-ultrassecreto',
      fetchImplementation: fetchMock as typeof fetch,
    })
    await client.getAdminMe()
    const headers = (fetchMock.mock.calls[0]?.[1]?.headers as Headers)
    expect(headers.get('Authorization')).toBe('Bearer token-ultrassecreto')
    expect(consoleSpy).not.toHaveBeenCalled()
    expect(document.body.textContent).not.toContain('token-ultrassecreto')
  })

  it('normaliza falha nativa de fetch sem expor a mensagem do navegador', async () => {
    const fetchMock = vi.fn().mockRejectedValue(
      new TypeError('Failed to fetch https://internal.example/private'),
    )
    const client = new ApiClient({
      baseUrl: 'http://api.test',
      fetchImplementation: fetchMock as typeof fetch,
    })

    try {
      await client.getHealth()
      throw new Error('A chamada deveria ter falhado.')
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError)
      expect(error).toMatchObject({
        status: 0,
        code: 'network_error',
        message: 'Não foi possível conectar à API. Tente novamente em instantes.',
      })
      expect((error as Error).message).not.toContain('internal.example')
      expect((error as Error).message).not.toContain('Failed to fetch')
    }
  })

  it.each([200, 204])('rejeita resposta %i sem corpo como contrato inválido', async (status) => {
    const client = new ApiClient({
      baseUrl: 'http://api.test',
      fetchImplementation: vi.fn().mockResolvedValue(new Response(null, { status })) as typeof fetch,
    })

    await expect(client.getHealth()).rejects.toMatchObject({
      status,
      code: 'invalid_response',
    })
  })

  it('preserva JSON null como resposta pública válida', async () => {
    const client = new ApiClient({
      baseUrl: 'http://api.test',
      fetchImplementation: vi.fn().mockResolvedValue(jsonResponse(200, null)) as typeof fetch,
    })

    await expect(client.getPublicSimulation()).resolves.toBeNull()
  })

  it('não invalida a sessão administrativa por erro de endpoint público', async () => {
    const onFailure = vi.fn()
    const client = new ApiClient({
      baseUrl: 'http://api.test',
      onAuthenticationFailure: onFailure,
      fetchImplementation: vi.fn().mockResolvedValue(
        jsonResponse(403, {
          error: { code: 'forbidden', message: 'Mensagem segura.' },
        }),
      ) as typeof fetch,
    })

    await expect(client.getPublicSimulation()).rejects.toMatchObject({
      status: 403,
      code: 'forbidden',
    })
    expect(onFailure).not.toHaveBeenCalled()
  })

  it.each([
    [403, 'forbidden'],
    [409, 'active_simulation_exists'],
    [422, 'validation_error'],
    [503, 'service_unavailable'],
  ])('preserva status %i e código seguro', async (status, code) => {
    const client = new ApiClient({
      baseUrl: 'http://api.test',
      getAccessToken: async () => 'token',
      fetchImplementation: vi.fn().mockResolvedValue(jsonResponse(status, { error: { code, message: 'Mensagem segura.' } })),
    })
    await expect(client.getAdminMe()).rejects.toMatchObject({ status, code })
  })

  it('renova uma vez em GET e encerra a sessão após 401 persistente', async () => {
    const onFailure = vi.fn()
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(401, { error: { code: 'expired', message: 'Expirado.' } }))
      .mockResolvedValueOnce(jsonResponse(401, { error: { code: 'expired', message: 'Expirado.' } }))
    const client = new ApiClient({
      baseUrl: 'http://api.test',
      getAccessToken: async () => 'old-token',
      refreshAccessToken: async () => 'new-token',
      onAuthenticationFailure: onFailure,
      fetchImplementation: fetchMock as typeof fetch,
    })
    await expect(client.getAdminMe()).rejects.toBeInstanceOf(ApiError)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(onFailure).toHaveBeenCalledWith('new-token')
  })

  it('não repete POST após 401', async () => {
    const onFailure = vi.fn()
    const refreshAccessToken = vi.fn()
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(401, { error: { code: 'expired', message: 'Expirado.' } }))
    const client = new ApiClient({
      baseUrl: 'http://api.test',
      getAccessToken: async () => 'token',
      refreshAccessToken,
      onAuthenticationFailure: onFailure,
      fetchImplementation: fetchMock as typeof fetch,
    })
    await expect(client.createSimulation({ name: 'Teste', initial_capital: '100', currency: 'USD' })).rejects.toBeInstanceOf(ApiError)
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(refreshAccessToken).not.toHaveBeenCalled()
    expect(onFailure).toHaveBeenCalledWith('token')
  })

  it('encerra a sessão se o GET renovado for proibido', async () => {
    const onFailure = vi.fn()
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(401, { error: { code: 'expired', message: 'Expirado.' } }))
      .mockResolvedValueOnce(jsonResponse(403, { error: { code: 'forbidden', message: 'Negado.' } }))
    const client = new ApiClient({
      baseUrl: 'http://api.test',
      getAccessToken: async () => 'old-token',
      refreshAccessToken: async () => 'new-token',
      onAuthenticationFailure: onFailure,
      fetchImplementation: fetchMock as typeof fetch,
    })

    await expect(client.getAdminMe()).rejects.toMatchObject({
      status: 403,
      code: 'forbidden',
    })
    expect(onFailure).toHaveBeenCalledWith('new-token')
  })

  it('normaliza falha ao obter a sessão e invalida sem vazar detalhes', async () => {
    const onFailure = vi.fn()
    const client = new ApiClient({
      baseUrl: 'http://api.test',
      getAccessToken: async () => {
        throw new Error('storage interno indisponível em /segredo')
      },
      onAuthenticationFailure: onFailure,
      fetchImplementation: vi.fn() as typeof fetch,
    })

    await expect(client.getAdminMe()).rejects.toMatchObject({
      status: 0,
      code: 'session_unavailable',
      message: 'Não foi possível validar sua sessão. Entre novamente.',
    })
    expect(onFailure).toHaveBeenCalledWith(null)
  })
})
