import { expect, test } from './fixtures/test'
import { API_ORIGIN } from './support/constants'

test.describe('área pública', () => {
  test('permanece acessível e o CTA leva somente ao login geral', async ({
    page,
    services,
  }) => {
    await page.goto('/')

    await expect(
      page.getByRole('heading', { name: /Pesquisa disciplinada/ }),
    ).toBeVisible()
    await expect(page).toHaveURL(/\/$/)
    await expect(page.getByText('API operacional')).toBeVisible()
    await expect(page.getByRole('link', { name: 'Entrar' })).toHaveAttribute(
      'href',
      '/login',
    )
    await expect(
      page.getByRole('link', { name: /criar conta|registrar|sign up/i }),
    ).toHaveCount(0)

    const statusRequests = services.requestsFor('GET', '/api/v1/system/status')
    expect(statusRequests.length).toBeGreaterThanOrEqual(1)
    expect(statusRequests[0].origin).toBe(API_ORIGIN)
    expect(
      statusRequests.every((request) => request.authorization === undefined),
    ).toBe(true)
    const simulationRequests = services.requestsFor(
      'GET',
      '/api/v1/public/simulation',
    )
    expect(simulationRequests.length).toBeGreaterThanOrEqual(1)
    expect(
      simulationRequests.every(
        (request) => request.authorization === undefined,
      ),
    ).toBe(true)
    expect(services.requestsFor('GET', '/api/v1/app/me')).toHaveLength(0)
    expect(services.requestsFor('GET', '/api/v1/admin/me')).toHaveLength(0)

    await page.getByRole('link', { name: 'Entrar' }).click()
    await expect(page).toHaveURL(/\/login$/)
  })

  test('renderiza a projeção pública restrita de uma simulação ativa', async ({
    page,
    services,
  }) => {
    services.seedActiveSimulation({
      name: 'Paper público E2E',
      initial_capital: '10000.00000000',
      current_balance: '10250.50000000',
      total_profit_loss: '250.50000000',
    })

    await page.goto('/')

    await expect(
      page.getByRole('heading', { name: 'Paper público E2E' }),
    ).toBeVisible()
    await expect(page.getByText('Capital simulado')).toBeVisible()
    await expect(page.getByText('Não representa capital real')).toBeVisible()
    await expect(page.getByText('USD 10.000,00')).toBeVisible()
    await expect(page.getByText('USD 10.250,50')).toBeVisible()
    await expect(page.getByText('USD 250,50')).toBeVisible()
    await expect(page.locator('body')).not.toContainText(
      '11111111-1111-4111-8111-111111111111',
    )
    await expect(page.locator('body')).not.toContainText('admin@adt.test')
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
    await expect(
      page.getByRole('heading', {
        name: 'Simulação pública temporariamente indisponível.',
      }),
    ).toBeVisible()
    await expect(page.locator('body')).not.toContainText('Traceback')
    await expect(page.locator('body')).not.toContainText('43102')
  })
})
