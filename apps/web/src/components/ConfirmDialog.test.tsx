import { useState } from 'react'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { ConfirmDialog } from './ConfirmDialog'

function DialogHarness({ busy = false }: { busy?: boolean }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>Abrir confirmação</button>
      <ConfirmDialog
        open={open}
        title="Confirmar operação?"
        description="Esta ação será permanente."
        confirmLabel="Confirmar agora"
        busy={busy}
        onCancel={() => setOpen(false)}
        onConfirm={() => setOpen(false)}
      />
    </>
  )
}

describe('ConfirmDialog', () => {
  it('leva o foco ao diálogo e o contém entre as ações', async () => {
    const user = userEvent.setup()
    render(<DialogHarness />)
    await act(async () => {
      await user.click(screen.getByRole('button', { name: 'Abrir confirmação' }))
    })

    const cancel = screen.getByRole('button', { name: 'Voltar' })
    const confirm = screen.getByRole('button', { name: 'Confirmar agora' })
    expect(document.activeElement).toBe(cancel)

    await user.tab()
    expect(document.activeElement).toBe(confirm)
    await user.tab()
    expect(document.activeElement).toBe(cancel)
    await user.tab({ shift: true })
    expect(document.activeElement).toBe(confirm)
  })

  it('fecha com Escape e restaura o foco no acionador', async () => {
    const user = userEvent.setup()
    render(<DialogHarness />)
    const trigger = screen.getByRole('button', { name: 'Abrir confirmação' })
    await act(async () => {
      await user.click(trigger)
    })

    await act(async () => {
      await user.keyboard('{Escape}')
    })

    expect(screen.queryByRole('alertdialog')).toBeNull()
    await waitFor(() => expect(document.activeElement).toBe(trigger))
  })

  it('não permite fechar com Escape enquanto uma operação está em andamento', async () => {
    const user = userEvent.setup()
    render(<DialogHarness busy />)
    await act(async () => {
      await user.click(screen.getByRole('button', { name: 'Abrir confirmação' }))
    })

    await user.keyboard('{Escape}')

    const dialog = screen.getByRole('alertdialog')
    expect(dialog).toBeDefined()
    expect(document.activeElement).toBe(dialog)
  })
})
