export const FINANCIAL_DECIMAL_MAX_EXCLUSIVE = '1000000000000'

export type FinancialDecimalValidationError =
  | 'format'
  | 'negative'
  | 'range'
  | 'zero'

interface FinancialDecimalOptions {
  allowNegative?: boolean
  allowZero?: boolean
}

const FINANCIAL_DECIMAL_PATTERN = /^(-?)(\d+)(?:\.(\d{1,8}))?$/

export function validateFinancialDecimal(
  value: string,
  {
    allowNegative = false,
    allowZero = false,
  }: FinancialDecimalOptions = {},
): FinancialDecimalValidationError | null {
  const match = FINANCIAL_DECIMAL_PATTERN.exec(value)
  if (!match) return 'format'

  const [, sign, rawInteger, fraction = ''] = match
  if (sign === '-' && !allowNegative) return 'negative'

  const integer = rawInteger.replace(/^0+/, '') || '0'
  if (integer.length > 12) return 'range'

  const isZero = integer === '0' && (fraction === '' || /^0+$/.test(fraction))
  if (isZero && !allowZero) return 'zero'

  return null
}

export function isNegativeFinancialDecimal(value: string): boolean {
  return value.startsWith('-') &&
    validateFinancialDecimal(value, { allowNegative: true }) === null
}
