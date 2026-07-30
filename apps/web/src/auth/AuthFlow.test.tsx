import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider, useAuth } from './AuthContext'
import { ProtectedRoute } from './ProtectedRoute'
import { ForgotPasswordPage } from '../pages/auth/ForgotPasswordPage'
import { LoginPage } from '../pages/auth/LoginPage'
import { ResetPasswordPage } from '../pages/auth/ResetPasswordPage'
import { getSupabaseAuthStorageKey } from './supabaseSessionStorage'

const mocks = vi.hoisted(() => ({
  getSession: vi.fn(),
  signInWithPassword: vi.fn(),
  adminSignOut: vi.fn(),
  refreshSession: vi.fn(),
  resetPasswordForEmail: vi.fn(),
  updateUser: vi.fn(),
  onAuthStateChange: vi.fn(),
  authStateCallback: null as (
    ((event: string, nextSession: unknown) => void) | null
  ),
  getAdminMe: vi.fn(),
  hasPasswordRecoveryContext: vi.fn(),
  clearPasswordRecoveryContext: vi.fn(),
}))

vi.mock('../lib/supabase', () => ({
  getSupabaseClient: () => ({
    auth: {
      ...mocks,
      admin: { signOut: mocks.adminSignOut },
    },
  }),
  hasPasswordRecoveryContext: mocks.hasPasswordRecoveryContext,
  clearPasswordRecoveryContext: mocks.clearPasswordRecoveryContext,
}))

vi.mock('../http/client', () => {
  class ApiError extends Error {
    constructor(public status: number, public code: string, message: string) {
      super(message)
    }
  }
  return { ApiError, apiClient: { getAdminMe: mocks.getAdminMe } }
})

const session = { access_token: 'test-access-token', user: { id: 'admin-id' } }

function PrivateContent() {
  const { signOut } = useAuth()
  return <><h1>Painel privado</h1><button onClick={() => void signOut()}>Sair agora</button></>
}

function renderAuth(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <AuthProvider>
        <Routes>
          <Route path="/admin/login" element={<LoginPage />} />
          <Route element={<ProtectedRoute />}>
            <Route path="/admin" element={<PrivateContent />} />
            <Route path="/admin/settings" element={<h1>Configurações privadas</h1>} />
          </Route>
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  localStorage.clear()
  mocks.getSession.mockResolvedValue({ data: { session: null }, error: null })
  mocks.signInWithPassword.mockResolvedValue({ data: { session }, error: null })
  mocks.adminSignOut.mockResolvedValue({ data: null, error: null })
  mocks.refreshSession.mockResolvedValue({ data: { session }, error: null })
  mocks.resetPasswordForEmail.mockResolvedValue({ error: null })
  mocks.updateUser.mockResolvedValue({ error: null })
  mocks.authStateCallback = null
  mocks.onAuthStateChange.mockImplementation(
    (callback: (event: string, nextSession: unknown) => void) => {
      mocks.authStateCallback = callback
      return { data: { subscription: { unsubscribe: vi.fn() } } }
    },
  )
  mocks.getAdminMe.mockResolvedValue({ user_id: 'admin-id', is_admin: true })
  mocks.hasPasswordRecoveryContext.mockReturnValue(false)
})

describe('autenticação administrativa', () => {
  it('faz login válido e confirma a autorização no backend', async () => {
    const user = userEvent.setup()
    renderAuth('/admin/login')
    await user.type(screen.getByLabelText('E-mail'), 'admin@example.com')
    await user.type(screen.getByLabelText('Senha'), 'senha-segura')
    await user.click(screen.getByRole('button', { name: 'Entrar' }))
    expect(await screen.findByText('Painel privado')).toBeDefined()
    expect(mocks.getAdminMe).toHaveBeenCalled()
  })

  it('mostra mensagem neutra em login inválido', async () => {
    mocks.signInWithPassword.mockResolvedValue({ data: { session: null }, error: new Error('user does not exist') })
    const user = userEvent.setup()
    renderAuth('/admin/login')
    await user.type(screen.getByLabelText('E-mail'), 'unknown@example.com')
    await user.type(screen.getByLabelText('Senha'), 'incorreta')
    await user.click(screen.getByRole('button', { name: 'Entrar' }))
    expect(await screen.findByText(/Não foi possível entrar/)).toBeDefined()
    expect(document.body.textContent).not.toContain('user does not exist')
  })

  it('restaura a sessão e valida novamente o administrador', async () => {
    mocks.getSession.mockResolvedValue({ data: { session }, error: null })
    renderAuth('/admin')
    expect(await screen.findByText('Painel privado')).toBeDefined()
    expect(mocks.getAdminMe).toHaveBeenCalled()
  })

  it('encerra a sessão de usuário autenticado sem permissão administrativa', async () => {
    const ApiError = (await import('../http/client')).ApiError
    mocks.getAdminMe.mockRejectedValue(new ApiError(403, 'forbidden', 'Negado.'))
    const user = userEvent.setup()
    renderAuth('/admin/login')
    await user.type(screen.getByLabelText('E-mail'), 'user@example.com')
    await user.type(screen.getByLabelText('Senha'), 'senha-valida')
    await user.click(screen.getByRole('button', { name: 'Entrar' }))
    expect(await screen.findByText(/não possui acesso administrativo/i)).toBeDefined()
  })

  it('faz logout e retorna ao login', async () => {
    const storageKey = getSupabaseAuthStorageKey('https://example.supabase.co')
    localStorage.setItem(storageKey, JSON.stringify(session))
    mocks.getSession.mockResolvedValue({ data: { session }, error: null })
    const user = userEvent.setup()
    renderAuth('/admin')
    await user.click(await screen.findByRole('button', { name: 'Sair agora' }))
    expect(await screen.findByRole('button', { name: 'Entrar' })).toBeDefined()
    expect(localStorage.getItem(storageKey)).toBeNull()
    expect(mocks.adminSignOut).toHaveBeenCalledWith('test-access-token', 'local')
  })

  it('remove o acesso local antes de aguardar o logout remoto', async () => {
    const storageKey = getSupabaseAuthStorageKey('https://example.supabase.co')
    localStorage.setItem(storageKey, JSON.stringify(session))
    mocks.getSession.mockImplementation(async () => ({
      data: { session: localStorage.getItem(storageKey) ? session : null },
      error: null,
    }))
    let finishRemoteSignOut: (() => void) | undefined
    mocks.adminSignOut.mockImplementation(() => {
      return new Promise((resolve) => {
        finishRemoteSignOut = () => resolve({ data: null, error: null })
      })
    })
    const user = userEvent.setup()
    const rendered = renderAuth('/admin')

    await user.click(await screen.findByRole('button', { name: 'Sair agora' }))

    expect(await screen.findByRole('button', { name: 'Entrar' })).toBeDefined()
    expect(localStorage.getItem(storageKey)).toBeNull()
    expect(mocks.adminSignOut).toHaveBeenCalledWith('test-access-token', 'local')

    await user.type(screen.getByLabelText('E-mail'), 'admin@example.com')
    await user.type(screen.getByLabelText('Senha'), 'senha-valida')
    await user.click(screen.getByRole('button', { name: 'Entrar' }))
    expect(mocks.signInWithPassword).not.toHaveBeenCalled()

    finishRemoteSignOut?.()
    await waitFor(() => expect(mocks.signInWithPassword).toHaveBeenCalled())
    expect(await screen.findByText('Painel privado')).toBeDefined()

    localStorage.setItem(storageKey, JSON.stringify({
      ...session,
      access_token: 'new-session-access-token',
    }))
    act(() => {
      mocks.authStateCallback?.('TOKEN_REFRESHED', session)
    })
    expect(screen.getByText('Painel privado')).toBeDefined()
    localStorage.removeItem(storageKey)

    rendered.unmount()
    renderAuth('/admin')
    expect(await screen.findByRole('button', { name: 'Entrar' })).toBeDefined()
  })

  it('redireciona rota privada sem sessão e preserva o destino', async () => {
    renderAuth('/admin/settings')
    expect(await screen.findByRole('button', { name: 'Entrar' })).toBeDefined()
  })
})

describe('recuperação de senha', () => {
  it('envia recuperação com redirectTo correto e confirmação neutra', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><ForgotPasswordPage /></MemoryRouter>)
    await user.type(screen.getByLabelText('E-mail'), 'admin@example.com')
    await user.click(screen.getByRole('button', { name: 'Enviar instruções' }))
    expect(await screen.findByText(/Se a conta estiver cadastrada/)).toBeDefined()
    expect(mocks.resetPasswordForEmail).toHaveBeenCalledWith('admin@example.com', {
      redirectTo: 'http://localhost:3000/admin/reset-password',
    })
  })

  it('redefine a senha e encerra a sessão de recuperação', async () => {
    const storageKey = getSupabaseAuthStorageKey('https://example.supabase.co')
    localStorage.setItem(storageKey, JSON.stringify(session))
    mocks.getSession.mockResolvedValue({ data: { session }, error: null })
    mocks.hasPasswordRecoveryContext.mockReturnValue(true)
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/admin/reset-password']}>
        <Routes>
          <Route path="/admin/reset-password" element={<ResetPasswordPage />} />
          <Route path="/admin/login" element={<p>Login após redefinição</p>} />
        </Routes>
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByLabelText('Nova senha')).toBeDefined())
    await user.type(screen.getByLabelText('Nova senha'), 'nova-senha-123')
    await user.type(screen.getByLabelText('Confirmar nova senha'), 'nova-senha-123')
    await user.click(screen.getByRole('button', { name: 'Atualizar senha' }))
    expect(await screen.findByText('Login após redefinição')).toBeDefined()
    expect(mocks.updateUser).toHaveBeenCalledWith({ password: 'nova-senha-123' })
    expect(mocks.clearPasswordRecoveryContext).toHaveBeenCalled()
    expect(localStorage.getItem(storageKey)).toBeNull()
  })

  it('não aceita uma sessão administrativa comum como recuperação', async () => {
    mocks.getSession.mockResolvedValue({ data: { session }, error: null })
    mocks.hasPasswordRecoveryContext.mockReturnValue(false)

    render(
      <MemoryRouter>
        <ResetPasswordPage />
      </MemoryRouter>,
    )

    expect(
      await screen.findByText(/link de recuperação é inválido ou expirou/i),
    ).toBeDefined()
    expect(screen.queryByLabelText('Nova senha')).toBeNull()
    expect(mocks.updateUser).not.toHaveBeenCalled()
  })

  it('trata falha ao validar o link sem manter loading infinito', async () => {
    mocks.getSession.mockRejectedValue(new TypeError('storage indisponível'))
    mocks.hasPasswordRecoveryContext.mockReturnValue(true)

    render(
      <MemoryRouter>
        <ResetPasswordPage />
      </MemoryRouter>,
    )

    expect(
      await screen.findByText(/link de recuperação é inválido ou expirou/i),
    ).toBeDefined()
    expect(screen.queryByText(/Validando link/)).toBeNull()
  })

  it('libera nova tentativa quando a atualização de senha falha', async () => {
    mocks.getSession.mockResolvedValue({ data: { session }, error: null })
    mocks.hasPasswordRecoveryContext.mockReturnValue(true)
    mocks.updateUser.mockRejectedValue(new TypeError('rede indisponível'))
    const user = userEvent.setup()

    render(
      <MemoryRouter>
        <ResetPasswordPage />
      </MemoryRouter>,
    )
    await user.type(await screen.findByLabelText('Nova senha'), 'nova-senha-123')
    await user.type(screen.getByLabelText('Confirmar nova senha'), 'nova-senha-123')
    await user.click(screen.getByRole('button', { name: 'Atualizar senha' }))

    expect(
      await screen.findByText(/link é inválido ou expirou/i),
    ).toBeDefined()
    expect(
      (screen.getByRole('button', {
        name: 'Atualizar senha',
      }) as HTMLButtonElement).disabled,
    ).toBe(false)
  })
})
