import { expect, test } from './fixtures/test'
import { loginAsAdmin } from './support/actions'
import {
  ADMIN_EMAIL,
  E2E_PASSWORD,
  USER_EMAIL,
} from './support/constants'

test.describe('autenticação administrativa', () => {
  test('redireciona a rota privada e preserva um destino administrativo seguro', async ({
    page,
  }) => {
    await loginAsAdmin(page, '/admin/settings')

    await expect(
      page.getByRole('heading', { name: 'Configurações' }),
    ).toBeVisible()
  })

  test('login válido confirma o administrador com Bearer sem renderizar o token', async ({
    page,
    services,
  }) => {
    const consoleMessages: string[] = []
    page.on('console', (message) => consoleMessages.push(message.text()))

    await loginAsAdmin(page)
    await expect(
      page.getByRole('heading', { name: 'Visão geral' }),
    ).toBeVisible()

    const meRequests = services.requestsFor('GET', '/api/v1/admin/me')
    expect(meRequests.length).toBeGreaterThanOrEqual(1)
    expect(meRequests.at(-1)?.authorization).toBe(
      `Bearer ${services.lastIssuedAccessToken}`,
    )
    expect(services.lastIssuedAccessToken).not.toBeNull()
    await expect(page.locator('body')).not.toContainText(
      services.lastIssuedAccessToken as string,
    )
    expect(consoleMessages.join('\n')).not.toContain(
      services.lastIssuedAccessToken as string,
    )
  })

  test('login inválido mantém mensagem neutra', async ({ page, services }) => {
    await page.goto('/admin/login')
    await page.getByLabel('E-mail').fill('unknown@adt.test')
    await page.getByLabel('Senha').fill('senha-incorreta')
    await page.getByRole('button', { name: 'Entrar' }).click()

    await expect(page.getByRole('alert')).toContainText(
      'Não foi possível entrar. Verifique as credenciais',
    )
    await expect(page.getByRole('alert')).not.toContainText(
      'Invalid login credentials',
    )
    expect(
      services.requestsFor('POST', '/auth/v1/token'),
    ).toHaveLength(1)
  })

  test('usuário autenticado sem autorização é deslogado', async ({ page }) => {
    await page.goto('/admin/login')
    await page.getByLabel('E-mail').fill(USER_EMAIL)
    await page.getByLabel('Senha').fill(E2E_PASSWORD)
    await page.getByRole('button', { name: 'Entrar' }).click()

    await expect(page).toHaveURL(/\/admin\/login$/)
    await expect(page.getByRole('alert')).toContainText(
      'não possui acesso administrativo',
    )
    expect(
      await page.evaluate(() =>
        Object.keys(localStorage).filter(
          (key) => key.startsWith('sb-') && key.endsWith('-auth-token'),
        ),
      ),
    ).toEqual([])
  })

  test('restaura a sessão no reload e logout limpa o armazenamento local', async ({
    page,
    services,
  }) => {
    await loginAsAdmin(page)
    expect(
      services.requestsFor('POST', '/auth/v1/token'),
    ).toHaveLength(1)

    await page.reload()
    await expect(
      page.getByRole('heading', { name: 'Visão geral' }),
    ).toBeVisible()
    expect(
      services.requestsFor('POST', '/auth/v1/token'),
    ).toHaveLength(1)
    expect(
      services.requestsFor('GET', '/api/v1/admin/me').length,
    ).toBeGreaterThanOrEqual(2)

    await page.getByRole('button', { name: 'Sair' }).click()
    await expect(page).toHaveURL(/\/admin\/login$/)
    const storedAuthKeys = await page.evaluate(() =>
      Object.keys(localStorage).filter(
        (key) => key.startsWith('sb-') && key.endsWith('-auth-token'),
      ),
    )
    expect(storedAuthKeys).toEqual([])
    expect(
      services.requestsFor('POST', '/auth/v1/logout').some(
        (request) =>
          request.search === '?scope=local'
          && request.authorization
            === `Bearer ${services.lastIssuedAccessToken}`,
      ),
    ).toBe(true)
  })

  test('401 em GET renova a sessão uma vez antes de autorizar', async ({
    page,
    services,
  }) => {
    services.rejectNextAdminRequestsWith401(1)

    await loginAsAdmin(page)
    await expect(
      page.getByRole('heading', { name: 'Visão geral' }),
    ).toBeVisible()

    expect(
      services.requestsFor('POST', '/auth/v1/token').filter(
        (request) => request.search.includes('grant_type=refresh_token'),
      ),
    ).toHaveLength(1)
    const meRequests = services.requestsFor('GET', '/api/v1/admin/me')
    expect(meRequests).toHaveLength(2)
    expect(meRequests[0].authorization).not.toBe(
      meRequests[1].authorization,
    )
  })

  test('401 persistente invalida a sessão e não libera o painel', async ({
    page,
    services,
  }) => {
    services.rejectNextAdminRequestsWith401(2)
    await page.goto('/admin/login')
    await page.getByLabel('E-mail').fill(ADMIN_EMAIL)
    await page.getByLabel('Senha').fill(E2E_PASSWORD)
    await page.getByRole('button', { name: 'Entrar' }).click()

    await expect(page).toHaveURL(/\/admin\/login$/)
    await expect(page.getByRole('alert')).toContainText(
      'não possui acesso administrativo',
    )
    expect(
      services.requestsFor('GET', '/api/v1/admin/me'),
    ).toHaveLength(2)
    expect(
      await page.evaluate(() =>
        Object.keys(localStorage).filter(
          (key) => key.startsWith('sb-') && key.endsWith('-auth-token'),
        ),
      ),
    ).toEqual([])
  })
})

test.describe('recuperação de senha', () => {
  test('solicita recuperação com redirect local e resposta antienumeração', async ({
    page,
    services,
  }) => {
    await page.goto('/admin/forgot-password')
    await page.getByLabel('E-mail').fill(ADMIN_EMAIL)
    await page
      .getByRole('button', { name: 'Enviar instruções' })
      .click()

    await expect(page.getByRole('status')).toContainText(
      'Se a conta estiver cadastrada',
    )
    expect(services.recoveredEmail).toBe(ADMIN_EMAIL)
    expect(services.recoveryRedirectTo).toBe(
      'http://127.0.0.1:4173/admin/reset-password',
    )
  })

  test('link válido redefine a senha pelo SDK e remove tokens da URL', async ({
    page,
    services,
  }) => {
    await page.goto(services.recoveryCallbackUrl())
    await expect(
      page.getByLabel('Nova senha', { exact: true }),
    ).toBeVisible()
    await expect(page).not.toHaveURL(/access_token=/)

    await page
      .getByLabel('Nova senha', { exact: true })
      .fill('nova-senha-e2e-123')
    await page
      .getByLabel('Confirmar nova senha')
      .fill('nova-senha-e2e-123')
    await page.getByRole('button', { name: 'Atualizar senha' }).click()

    await expect(page).toHaveURL(/\/admin\/login$/)
    await expect(page.getByRole('status')).toContainText(
      'Senha atualizada',
    )
    expect(services.updatedPassword).toBe('nova-senha-e2e-123')
  })

  test('link expirado não cria sessão de recuperação', async ({
    page,
    services,
  }) => {
    await page.goto(services.recoveryCallbackUrl(true))

    await expect(page.getByRole('alert')).toContainText(
      'link de recuperação é inválido ou expirou',
    )
    await expect(
      page.getByRole('link', { name: 'Solicitar novo link' }),
    ).toBeVisible()
    expect(services.updatedPassword).toBeNull()
  })
})
