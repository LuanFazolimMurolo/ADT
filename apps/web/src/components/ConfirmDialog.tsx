import { useEffect, useRef } from 'react'

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = 'Confirmar',
  danger = false,
  busy = false,
  onConfirm,
  onCancel,
}: {
  open: boolean
  title: string
  description: string
  confirmLabel?: string
  danger?: boolean
  busy?: boolean
  onConfirm(): void
  onCancel(): void
}) {
  const cancelRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (open) cancelRef.current?.focus()
  }, [open])

  if (!open) return null
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={onCancel}>
      <section
        className="dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby="confirm-description"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <p className="eyebrow">Confirmação necessária</p>
        <h2 id="confirm-title">{title}</h2>
        <p id="confirm-description">{description}</p>
        <div className="dialog__actions">
          <button ref={cancelRef} className="button button--ghost" type="button" onClick={onCancel} disabled={busy}>
            Voltar
          </button>
          <button className={danger ? 'button button--danger' : 'button'} type="button" onClick={onConfirm} disabled={busy}>
            {busy ? 'Processando…' : confirmLabel}
          </button>
        </div>
      </section>
    </div>
  )
}
