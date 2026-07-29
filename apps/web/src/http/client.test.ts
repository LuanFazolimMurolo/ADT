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
    expect(onFailure).toHaveBeenCalledOnce()
  })

  it('não repete POST após 401', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(401, { error: { code: 'expired', message: 'Expirado.' } }))
    const client = new ApiClient({
      baseUrl: 'http://api.test',
      getAccessToken: async () => 'token',
      refreshAccessToken: vi.fn(),
      fetchImplementation: fetchMock as typeof fetch,
    })
    await expect(client.createSimulation({ name: 'Teste', initial_capital: '100', currency: 'USD' })).rejects.toBeInstanceOf(ApiError)
    expect(fetchMock).toHaveBeenCalledOnce()
  })
})
