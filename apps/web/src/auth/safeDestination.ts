interface DestinationLocationState {
  from?: { pathname?: unknown; search?: unknown }
}

function hasUnsafeRedirectSyntax(value: string): boolean {
  let decoded: string
  try {
    decoded = decodeURIComponent(value)
  } catch {
    return true
  }
  return [value, decoded].some(
    (candidate) =>
      candidate.includes('//') ||
      candidate.includes('\\') ||
      candidate.includes('#'),
  )
}

export function safeNamespacedDestination(
  state: DestinationLocationState | null,
  namespace: '/app' | '/admin',
): string {
  const pathname = state?.from?.pathname
  const search = state?.from?.search
  if (
    typeof pathname !== 'string' ||
    (pathname !== namespace && !pathname.startsWith(`${namespace}/`)) ||
    hasUnsafeRedirectSyntax(pathname)
  ) {
    return namespace
  }
  if (search === undefined || search === '') return pathname
  if (
    typeof search !== 'string' ||
    !search.startsWith('?') ||
    hasUnsafeRedirectSyntax(search)
  ) {
    return namespace
  }
  return `${pathname}${search}`
}
