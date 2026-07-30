import type { Page } from '@playwright/test'
import { expect } from '../fixtures/test'
import {
  ADMIN_EMAIL,
  E2E_PASSWORD,
} from './constants'

export async function loginAsAdmin(
  page: Page,
  destination = '/admin',
): Promise<void> {
  await page.goto(destination)
  await expect(page).toHaveURL(/\/admin\/login$/)
  await page.getByLabel('E-mail').fill(ADMIN_EMAIL)
  await page.getByLabel('Senha').fill(E2E_PASSWORD)
  await page.getByRole('button', { name: 'Entrar' }).click()
  await expect(page).toHaveURL(new RegExp(`${destination.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`))
}
