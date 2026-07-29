import type { ReactNode } from 'react'

export function LoadingState({
  message = 'Carregando…',
  fullPage = false,
}: {
  message?: string
  fullPage?: boolean
}) {
  return (
    <div className={fullPage ? 'state state--full' : 'state'} role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <p>{message}</p>
    </div>
  )
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string
  description: string
  action?: ReactNode
}) {
  return (
    <div className="empty-state">
      <span className="empty-state__mark" aria-hidden="true">◇</span>
      <h2>{title}</h2>
      <p>{description}</p>
      {action}
    </div>
  )
}

export function InlineError({ message }: { message: string }) {
  return <p className="form-message form-message--error" role="alert">{message}</p>
}

export function SuccessMessage({ message }: { message: string }) {
  return <p className="form-message form-message--success" role="status">{message}</p>
}
