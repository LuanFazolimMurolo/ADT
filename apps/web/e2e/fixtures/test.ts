import {
  expect as baseExpect,
  test as base,
} from '@playwright/test'
import { MockServices } from '../support/mock-services'

interface Fixtures {
  services: MockServices
}

export const test = base.extend<Fixtures>({
  services: [
    async ({ context }, use) => {
      const services = new MockServices()
      await services.install(context)
      await use(services)
      baseExpect(
        services.unexpectedRequests,
        'A suíte tentou acessar uma origem ou endpoint não permitido.',
      ).toEqual([])
    },
    { auto: true },
  ],
})

export { baseExpect as expect }
