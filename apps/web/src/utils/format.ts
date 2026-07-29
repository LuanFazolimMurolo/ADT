export function formatMoney(value: string, currency = 'USD'): string {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return `${currency} ${value}`
  try {
    return new Intl.NumberFormat('pt-BR', {
      style: 'currency',
      currency: currency.toUpperCase(),
      minimumFractionDigits: 2,
      maximumFractionDigits: 8,
    }).format(numeric)
  } catch {
    return `${currency.toUpperCase()} ${numeric.toLocaleString('pt-BR')}`
  }
}

export function formatDate(value: string | null): string {
  if (!value) return '—'
  return new Intl.DateTimeFormat('pt-BR', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}

export function getErrorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}
