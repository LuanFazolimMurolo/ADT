import { useEffect, useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../../auth/AuthContext'
import { InlineError, SuccessMessage } from '../../components/States'
import { getErrorMessage } from '../../utils/format'

interface LoginLocationState {
  from?: { pathname: string; search?: string }
  passwordReset?: boolean
}

function safeAdminDestination(state: LoginLocationState | null): string {
  const pathname = state?.from?.pathname
  if (
    typeof pathname !== 'string' ||
    !pathname.startsWith('/admin') ||
    pathname.startsWith('//') ||
    pathname.includes('\\')
  ) {
    return '/admin'
  }
  const search = state?.from?.search
  return `${pathname}${typeof search === 'string' && !search.includes('#') ? search : ''}`
}

export function LoginPage() {
  const { signIn, session, isAdmin } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const state = location.state as LoginLocationState | null
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const destination = safeAdminDestination(state)

  useEffect(() => {
    if (session && isAdmin) navigate(destination, { replace: true })
  }, [session, isAdmin, navigate, destination])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      await signIn(email.trim(), password)
      navigate(destination, { replace: true })
    } catch (nextError) {
      setError(getErrorMessage(nextError, 'Não foi possível entrar. Tente novamente.'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-panel">
        <Link className="auth-brand" to="/" aria-label="Voltar ao site público">
          <span className="brand-mark" aria-hidden="true">A</span>
          <span>ADT</span>
        </Link>
        <div className="auth-panel__intro">
          <p className="eyebrow">Acesso restrito</p>
          <h1>Administração</h1>
          <p>Use a conta administrativa cadastrada. O ADT não oferece cadastro público.</p>
        </div>
        <form onSubmit={submit} className="form-stack">
          <label>
            E-mail
            <input
              type="email"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
              autoFocus
            />
          </label>
          <label>
            Senha
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </label>
          {state?.passwordReset && <SuccessMessage message="Senha atualizada. Entre com a nova senha." />}
          {error && <InlineError message={error} />}
          <button className="button button--wide" type="submit" disabled={busy}>
            {busy ? 'Validando acesso…' : 'Entrar'}
          </button>
          <Link className="text-link" to="/admin/forgot-password">Esqueci minha senha</Link>
        </form>
      </section>
      <aside className="auth-aside" aria-hidden="true">
        <div>
          <p className="metric-label">PRINCÍPIO OPERACIONAL</p>
          <blockquote>“Sem impulso. Sem pânico. Apenas regras verificáveis.”</blockquote>
        </div>
      </aside>
    </main>
  )
}
