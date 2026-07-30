import { expect, test } from './fixtures/test'
import { loginAsAdmin } from './support/actions'

test.describe('configurações administrativas', () => {
  test('atualiza somente value após confirmação e recarrega a configuração', async ({
    page,
    services,
  }) => {
    await loginAsAdmin(page, '/admin/settings')
    const card = page
      .locator('.setting-card')
      .filter({ hasText: 'display_currency' })
    await card.getByLabel('Valor').fill('BRL')
    await card.getByRole('button', { name: 'Salvar alteração' }).click()
    await page
      .getByRole('alertdialog')
      .getByRole('button', { name: 'Salvar valor' })
      .click()

    await expect(
      page.getByText('Configuração “display_currency” atualizada.'),
    ).toBeVisible()
    const requests = services.requestsFor(
      'PATCH',
      '/api/v1/admin/settings/display_currency',
    )
    expect(requests).toHaveLength(1)
    expect(requests[0].body).toEqual({ value: 'BRL' })
    await expect(card.getByLabel('Valor')).toHaveValue('BRL')
  })

  test('JSON inválido é rejeitado sem PATCH', async ({ page, services }) => {
    await loginAsAdmin(page, '/admin/settings')
    const card = page.locator('.setting-card').filter({ hasText: 'risk_limits' })
    await card.getByLabel('Valor').fill('{invalido')
    await card.getByRole('button', { name: 'Salvar alteração' }).click()

    await expect(page.getByRole('alert')).toContainText(
      'deve ser um JSON válido',
    )
    expect(
      services.requestsFor(
        'PATCH',
        '/api/v1/admin/settings/risk_limits',
      ),
    ).toHaveLength(0)
  })
})
