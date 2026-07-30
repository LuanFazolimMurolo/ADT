import type { Page } from '@playwright/test'
import { expect, test } from './fixtures/test'
import { loginAsAdmin } from './support/actions'
import { SIMULATION_ID } from './support/constants'

async function openMovementPage(page: Page): Promise<void> {
  await loginAsAdmin(page, `/admin/simulations/${SIMULATION_ID}`)
  await expect(
    page.getByRole('heading', { name: 'Simulação principal' }),
  ).toBeVisible()
}

async function fillMovement(
  page: Page,
  type: 'DEPOSIT' | 'WITHDRAWAL' | 'ADJUSTMENT',
  amount: string,
  reason: string,
): Promise<void> {
  await page.getByLabel('Tipo').selectOption(type)
  await page
    .getByLabel(type === 'ADJUSTMENT' ? 'Valor assinado' : 'Valor absoluto')
    .fill(amount)
  await page.getByLabel('Motivo').fill(reason)
  await page.getByRole('button', { name: 'Registrar movimento' }).click()
}

test.describe('dashboard e simulações', () => {
  test('dashboard mostra estado vazio sem simulação ativa', async ({
    page,
  }) => {
    await loginAsAdmin(page)

    await expect(
      page.getByRole('heading', { name: 'Nenhuma simulação ativa' }),
    ).toBeVisible()
    await expect(
      page.getByRole('link', { name: 'Criar primeira simulação' }),
    ).toBeVisible()
  })

  test('cria uma simulação uma única vez após confirmação', async ({
    page,
    services,
  }) => {
    await loginAsAdmin(page, '/admin/simulations?create=true')
    await page.getByLabel('Nome').fill('Ciclo E2E')
    await page.getByLabel('Capital inicial').fill('5000.25')
    await page.getByLabel('Moeda').fill('brl')
    await page.getByRole('button', { name: 'Revisar e criar' }).click()

    const dialog = page.getByRole('alertdialog')
    await expect(dialog).toBeVisible()
    await dialog.getByRole('button', { name: 'Criar simulação' }).dblclick()

    await expect(
      page.getByText('Simulação criada com capital inicial registrado pelo backend.'),
    ).toBeVisible()
    const requests = services.requestsFor(
      'POST',
      '/api/v1/admin/simulations',
    )
    expect(requests).toHaveLength(1)
    expect(requests[0].body).toEqual({
      name: 'Ciclo E2E',
      initial_capital: '5000.25',
      currency: 'BRL',
    })
    await expect(page.getByText('Ciclo E2E')).toBeVisible()
  })

  test('segunda simulação ativa retorna conflito seguro', async ({
    page,
    services,
  }) => {
    services.seedActiveSimulation()
    await loginAsAdmin(page, '/admin/simulations')
    await page.getByRole('button', { name: '+ Nova simulação' }).click()
    await page.getByLabel('Nome').fill('Ciclo duplicado')
    await page.getByLabel('Capital inicial').fill('100.00')
    await page.getByRole('button', { name: 'Revisar e criar' }).click()
    await page
      .getByRole('alertdialog')
      .getByRole('button', { name: 'Criar simulação' })
      .click()

    await expect(page.getByRole('alert')).toContainText(
      'Já existe uma simulação ativa',
    )
    expect(
      services.requestsFor('POST', '/api/v1/admin/simulations'),
    ).toHaveLength(1)
  })
})

test.describe('movimentos do ledger', () => {
  test.beforeEach(async ({ services }) => {
    services.seedActiveSimulation()
  })

  test('depósito envia valor positivo e atualiza o saldo retornado', async ({
    page,
    services,
  }) => {
    await openMovementPage(page)
    await fillMovement(page, 'DEPOSIT', '25.50', 'Depósito E2E')

    await expect(page.getByRole('status')).toContainText(
      'Movimento registrado',
    )
    const requests = services.requestsFor(
      'POST',
      `/api/v1/admin/simulations/${SIMULATION_ID}/movements`,
    )
    expect(requests).toHaveLength(1)
    expect(requests[0].body).toEqual({
      type: 'DEPOSIT',
      amount: '25.50',
      reason: 'Depósito E2E',
      metadata: null,
    })
    await expect(page.getByText('ADMIN_DEPOSIT')).toBeVisible()
    await expect(page.getByText('USD 1.025,50')).toBeVisible()
  })

  test('retirada exige confirmação e envia sinal negativo', async ({
    page,
    services,
  }) => {
    await openMovementPage(page)
    await fillMovement(page, 'WITHDRAWAL', '100.00', 'Retirada E2E')
    expect(
      services.requestsFor(
        'POST',
        `/api/v1/admin/simulations/${SIMULATION_ID}/movements`,
      ),
    ).toHaveLength(0)

    const dialog = page.getByRole('alertdialog')
    await expect(
      dialog.getByRole('button', { name: 'Voltar' }),
    ).toBeFocused()
    await dialog
      .getByRole('button', { name: 'Registrar movimento' })
      .click()

    await expect(
      page.getByText('Movimento registrado no ledger imutável.'),
    ).toBeVisible()
    const requests = services.requestsFor(
      'POST',
      `/api/v1/admin/simulations/${SIMULATION_ID}/movements`,
    )
    expect(requests).toHaveLength(1)
    expect(requests[0].body).toEqual({
      type: 'WITHDRAWAL',
      amount: '-100.00',
      reason: 'Retirada E2E',
      metadata: null,
    })
    await expect(page.getByText('ADMIN_WITHDRAWAL')).toBeVisible()
  })

  test('retirada sem saldo mostra conflito sem inserir movimento', async ({
    page,
    services,
  }) => {
    await openMovementPage(page)
    await fillMovement(
      page,
      'WITHDRAWAL',
      '5000.00',
      'Retirada impossível',
    )
    await page
      .getByRole('alertdialog')
      .getByRole('button', { name: 'Registrar movimento' })
      .click()

    await expect(page.getByRole('alert')).toContainText(
      'Saldo insuficiente',
    )
    await expect(page.getByText('ADMIN_WITHDRAWAL')).toHaveCount(0)
    expect(
      services.requestsFor(
        'POST',
        `/api/v1/admin/simulations/${SIMULATION_ID}/movements`,
      ),
    ).toHaveLength(1)
  })

  test('ajuste preserva sinal e altera saldo sem misturar o P/L', async ({
    page,
    services,
  }) => {
    await openMovementPage(page)
    await fillMovement(page, 'ADJUSTMENT', '-10.25', 'Ajuste E2E')
    await page
      .getByRole('alertdialog')
      .getByRole('button', { name: 'Registrar movimento' })
      .click()

    await expect(
      page.getByText('Movimento registrado no ledger imutável.'),
    ).toBeVisible()
    const requests = services.requestsFor(
      'POST',
      `/api/v1/admin/simulations/${SIMULATION_ID}/movements`,
    )
    expect(requests[0].body).toEqual({
      type: 'ADJUSTMENT',
      amount: '-10.25',
      reason: 'Ajuste E2E',
      metadata: null,
    })
    await expect(page.getByText('ADJUSTMENT')).toBeVisible()
    await expect(
      page.locator('.metric-card').filter({ hasText: 'SALDO ATUAL' }),
    ).toContainText('USD 989,75')
    await expect(
      page.locator('.metric-card').filter({ hasText: 'P/L TOTAL' }),
    ).toContainText('USD 0,00')
  })
})

test.describe('encerramento da simulação', () => {
  test.beforeEach(async ({ services }) => {
    services.seedActiveSimulation()
  })

  test('conclui permanentemente após confirmação', async ({
    page,
    services,
  }) => {
    await openMovementPage(page)
    await page
      .getByRole('button', { name: 'Marcar como concluída' })
      .click()
    await page
      .getByRole('alertdialog')
      .getByRole('button', { name: 'Concluir simulação' })
      .click()

    await expect(page.getByText('Concluída', { exact: true })).toBeVisible()
    await expect(page.getByText('Novo movimento')).toHaveCount(0)
    expect(
      services.requestsFor(
        'POST',
        `/api/v1/admin/simulations/${SIMULATION_ID}/complete`,
      ),
    ).toHaveLength(1)
  })

  test('cancela preservando o histórico', async ({ page, services }) => {
    await openMovementPage(page)
    await page.getByRole('button', { name: 'Cancelar simulação' }).click()
    await page
      .getByRole('alertdialog')
      .getByRole('button', { name: 'Cancelar simulação' })
      .click()

    await expect(page.getByText('Cancelada', { exact: true })).toBeVisible()
    await expect(page.getByText('INITIAL_CAPITAL')).toBeVisible()
    expect(
      services.requestsFor(
        'POST',
        `/api/v1/admin/simulations/${SIMULATION_ID}/cancel`,
      ),
    ).toHaveLength(1)
  })
})
