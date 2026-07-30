export function formatMoney(value: string, currency = 'USD'): string {
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value)
  const normalizedCurrency = currency.trim().toUpperCase() || 'USD'
  if (!match) return `${normalizedCurrency} ${value}`

  const [, sign, rawInteger, rawFraction = ''] = match
  const integer = rawInteger.replace(/^0+(?=\d)/, '')
  const groupedInteger = integer.replace(/\B(?=(\d{3})+(?!\d))/g, '.')
  const fraction = rawFraction.replace(/0+$/, '').padEnd(2, '0')
  return `${normalizedCurrency} ${sign}${groupedInteger},${fraction}`
}

export function formatDate(value: string | null): string {
  if (!value) return '—'
  return new Intl.DateTimeFormat('pt-BR', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}

export function getErrorMessage(error: unknown, fallback: string): string {
  if (!(error instanceof Error)) return fallback
  const requestId = 'requestId' in error ? error.requestId : undefined
  return typeof requestId === 'string' && /^[0-9a-f-]{36}$/i.test(requestId)
    ? `${error.message} Referência: ${requestId}.`
    : error.message
}
