import { describe, expect, it } from 'vitest'
import {
  isNegativeFinancialDecimal,
  validateFinancialDecimal,
} from './decimal'

describe('validateFinancialDecimal', () => {
  it.each([
    '0.00000001',
    '1',
    '001.25',
    '999999999999.99999999',
  ])('aceita decimal positivo exato dentro de numeric(20, 8): %s', (value) => {
    expect(validateFinancialDecimal(value)).toBeNull()
  })

  it.each([
    ['', 'format'],
    ['+1', 'format'],
    ['1e2', 'format'],
    ['1.', 'format'],
    ['1.123456789', 'format'],
    ['-1', 'negative'],
    ['0', 'zero'],
    ['-0.00000000', 'negative'],
    ['1000000000000', 'range'],
    ['9999999999999.1', 'range'],
  ] as const)('rejeita %s com erro %s', (value, expectedError) => {
    expect(validateFinancialDecimal(value)).toBe(expectedError)
  })

  it('aceita ajustes positivos e negativos, mas não zero', () => {
    const options = { allowNegative: true }
    expect(validateFinancialDecimal('-25.125', options)).toBeNull()
    expect(validateFinancialDecimal('25.125', options)).toBeNull()
    expect(validateFinancialDecimal('-0.00000000', options)).toBe('zero')
  })
})

describe('isNegativeFinancialDecimal', () => {
  it('detecta o sinal sem converter o decimal para Number', () => {
    expect(isNegativeFinancialDecimal('-0.00000001')).toBe(true)
    expect(isNegativeFinancialDecimal('0.00000001')).toBe(false)
    expect(isNegativeFinancialDecimal('-0.00000000')).toBe(false)
  })
})
