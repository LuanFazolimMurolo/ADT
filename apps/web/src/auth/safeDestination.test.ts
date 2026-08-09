import { describe, expect, it } from 'vitest'
import { safeNamespacedDestination } from './safeDestination'

describe('destino seguro de login', () => {
  it('preserva somente destinos internos do namespace solicitado', () => {
    expect(
      safeNamespacedDestination(
        { from: { pathname: '/app/details', search: '?tab=summary' } },
        '/app',
      ),
    ).toBe('/app/details?tab=summary')
    expect(
      safeNamespacedDestination(
        { from: { pathname: '/admin/settings' } },
        '/admin',
      ),
    ).toBe('/admin/settings')
  })

  it.each([
    'https://evil.example',
    '//evil.example',
    '/admin//evil.example',
    '/admin\\evil.example',
    '/admin/%2F%2Fevil.example',
    '/app/details',
  ])('bloqueia destino administrativo inseguro %s', (pathname) => {
    expect(safeNamespacedDestination({ from: { pathname } }, '/admin')).toBe(
      '/admin',
    )
  })

  it('bloqueia query com sintaxe de redirecionamento externo', () => {
    expect(
      safeNamespacedDestination(
        { from: { pathname: '/app', search: '?next=//evil.example' } },
        '/app',
      ),
    ).toBe('/app')
  })
})
