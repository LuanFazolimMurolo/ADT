import { expect, test } from './fixtures/test'
import {
  ADMIN_EMAIL,
  E2E_PASSWORD,
  USER_EMAIL,
} from './support/constants'

const CATALOG_PATH = '/api/v1/app/paper-trading/sessions'

async function signInForRequestedSessionsPage(
  page: import('@playwright/test').Page,
  email: string,
) {
  await page.goto('/app/sessions')
  await expect(page).toHaveURL(/\/login$/)
  await page.getByLabel('E-mail').fill(email)
  await page.getByLabel('Senha').fill(E2E_PASSWORD)
  await page.getByRole('button', { name: 'Entrar' }).click()
  await expect(page).toHaveURL(/\/app\/sessions$/)
}

async function signInToApp(page: import('@playwright/test').Page) {
  await page.goto('/login')
  await page.getByLabel('E-mail').fill(USER_EMAIL)
  await page.getByLabel('Senha').fill(E2E_PASSWORD)
  await page.getByRole('button', { name: 'Entrar' }).click()
  await expect(page).toHaveURL(/\/app$/)
}

test('non-admin recebe catálogo vazio neutro sem usar API administrativa', async ({
  page,
  services,
}) => {
  await signInToApp(page)
  await page.getByRole('link', { name: 'Sessões' }).click()
  await expect(page).toHaveURL(/\/app\/sessions$/)

  await expect(
    page.getByText(
      'Nenhuma sessão de paper trading autorizada para esta conta.',
    ),
  ).toBeVisible()
  await expect(page.getByLabel(/sessão/i)).toHaveCount(0)

  const requests = services.requestsFor('GET', CATALOG_PATH)
  expect(requests.length).toBeGreaterThanOrEqual(1)
  expect(requests.at(-1)?.search).toBe('?page=1&page_size=20')
  expect(requests.at(-1)?.authorization).toBe(
    `Bearer ${services.lastIssuedAccessToken}`,
  )
  expect(
    services.requests.filter((request) =>
      request.pathname.startsWith('/api/v1/admin/'),
    ),
  ).toEqual([])
})

test('project owner vê somente a projeção mínima pelo endpoint app', async ({
  page,
  services,
}) => {
  await signInForRequestedSessionsPage(page, ADMIN_EMAIL)

  await expect(
    page.getByRole('heading', { name: 'Sessões autorizadas' }),
  ).toBeVisible()
  await expect(page.getByRole('heading', { name: 'BTC/USDT' })).toBeVisible()
  await expect(page.getByText('paper-buy-test')).toBeVisible()
  await expect(page.getByText('ID cccccccccccc…')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Abrir sessão' })).toHaveAttribute(
    'href',
    `/app/sessions/${'c'.repeat(64)}`,
  )
  await expect(page.getByLabel(/sessão/i)).toHaveCount(0)

  expect(services.requestsFor('GET', CATALOG_PATH).length).toBeGreaterThanOrEqual(
    1,
  )
  expect(
    services.requests.filter((request) =>
      request.pathname.startsWith('/api/v1/admin/'),
    ),
  ).toEqual([])
})
