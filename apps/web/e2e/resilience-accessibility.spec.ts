import { expect, test } from './fixtures/test'
import { loginAsAdmin } from './support/actions'
import { SIMULATION_ID } from './support/constants'

test.describe('resiliência', () => {
  test('dashboard diferencia banco indisponível do processo vivo', async ({
    page,
    services,
  }) => {
    await loginAsAdmin(page, '/admin/settings')
    await expect(
      page.getByRole('heading', { name: 'Configurações' }),
    ).toBeVisible()

    services.setDatabaseAvailable(false)
    await page.getByRole('link', { name: 'Visão geral' }).click()

    const health = page.getByRole('region', {
      name: 'Estado dos serviços',
    })
    await expect(health).toContainText('BACKENDOperacional')
    await expect(health).toContainText('BANCO DE DADOSIndisponível')
    await expect(page.getByRole('alert')).toContainText(
      'O banco de dados está temporariamente indisponível',
    )
    await expect(page.getByRole('alert')).toContainText(
      'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    )
  })

  test('dashboard trata 503 do backend sem vazar detalhes internos', async ({
    page,
    services,
  }) => {
    await loginAsAdmin(page, '/admin/settings')
    services.setBackendMode('service-unavailable')
    await page.getByRole('link', { name: 'Visão geral' }).click()

    await expect(page.getByRole('alert')).toContainText(
      'O serviço está temporariamente indisponível',
    )
    await expect(page.locator('body')).not.toContainText('Traceback')
    await expect(page.locator('body')).not.toContainText('postgresql://')
  })
})

test.describe('acessibilidade e responsividade', () => {
  test('confirmação recebe foco e pode ser concluída apenas com teclado', async ({
    page,
    services,
  }) => {
    await loginAsAdmin(page, '/admin/simulations?create=true')
    await page.getByLabel('Nome').fill('Ciclo por teclado')
    await page.getByLabel('Capital inicial').fill('250.00')
    await page.getByRole('button', { name: 'Revisar e criar' }).click()

    const dialog = page.getByRole('alertdialog')
    await expect(
      dialog.getByRole('button', { name: 'Voltar' }),
    ).toBeFocused()
    await page.keyboard.press('Tab')
    await expect(
      dialog.getByRole('button', { name: 'Criar simulação' }),
    ).toBeFocused()
    await page.keyboard.press('Enter')

    await expect(
      page.getByText('Simulação criada com capital inicial registrado pelo backend.'),
    ).toBeVisible()
    expect(
      services.requestsFor('POST', '/api/v1/admin/simulations'),
    ).toHaveLength(1)
  })

  test('menu móvel expõe navegação e fecha após selecionar uma rota', async ({
    page,
    services,
  }) => {
    services.seedActiveSimulation({ id: SIMULATION_ID })
    await page.setViewportSize({ width: 390, height: 844 })
    await loginAsAdmin(page)

    const menu = page.getByRole('button', { name: 'Alternar navegação' })
    await expect(menu).toBeVisible()
    await menu.click()
    await expect(menu).toHaveAttribute('aria-expanded', 'true')
    await page
      .getByRole('link', { name: 'Configurações', exact: true })
      .click()

    await expect(page).toHaveURL(/\/admin\/settings$/)
    await expect(menu).toHaveAttribute('aria-expanded', 'false')
    await expect(
      page.getByRole('heading', { name: 'Configurações' }),
    ).toBeVisible()
  })
})
