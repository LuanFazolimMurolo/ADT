import { expect, test } from './fixtures/test'
import { API_ORIGIN } from './support/constants'

test.describe('área pública', () => {
  test('permanece acessível sem expor login ou cadastro', async ({
    page,
    services,
  }) => {
    await page.goto('/')

    await expect(
      page.getByRole('heading', { name: /Decisões frias/ }),
    ).toBeVisible()
    await expect(page.getByText('Sistema operacional · test')).toBeVisible()
    await expect(page.getByRole('link', { name: /entrar/i })).toHaveCount(0)
    await expect(page.getByRole('link', { name: /cadastro/i })).toHaveCount(0)

    const statusRequests = services.requestsFor(
      'GET',
      '/api/v1/system/status',
    )
    expect(statusRequests.length).toBeGreaterThanOrEqual(1)
    expect(statusRequests[0].origin).toBe(API_ORIGIN)
    expect(
      statusRequests.every(
        (request) => request.authorization === undefined,
      ),
    ).toBe(true)
  })

  test('mostra estado seguro quando o backend está indisponível', async ({
    page,
    services,
  }) => {
    services.setBackendMode('service-unavailable')

    await page.goto('/')

    await expect(
      page.getByText('API temporariamente indisponível'),
    ).toBeVisible()
    await expect(page.locator('body')).not.toContainText('Traceback')
    await expect(page.locator('body')).not.toContainText('43102')
  })
})
