import { useEffect, useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../../auth/AuthContext'
import { safeNamespacedDestination } from '../../auth/safeDestination'
import { InlineError, SuccessMessage } from '../../components/States'
import { getErrorMessage } from '../../utils/format'

interface LoginLocationState {
  from?: { pathname: string; search?: string }
  passwordReset?: boolean
}

interface LoginPageProps {
  mode: 'app' | 'admin'
}

export function LoginPage({ mode }: LoginPageProps) {
  const { signIn, session, identity } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const state = location.state as LoginLocationState | null
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const namespace = mode === 'admin' ? '/admin' : '/app'
  const destination = safeNamespacedDestination(state, namespace)
  const isAdministrativeLogin = mode === 'admin'

  useEffect(() => {
    if (!session || !identity) return
    navigate(
      isAdministrativeLogin && !identity.is_admin ? '/app' : destination,
      { replace: true },
    )
  }, [session, identity, navigate, destination, isAdministrativeLogin])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      const nextIdentity = await signIn(email.trim(), password)
      navigate(
        isAdministrativeLogin && !nextIdentity.is_admin ? '/app' : destination,
        { replace: true },
      )
    } catch (nextError) {
      setError(
        getErrorMessage(nextError, 'Não foi possível entrar. Tente novamente.'),
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-panel">
        <Link className="auth-brand" to="/" aria-label="Voltar ao site público">
          <span className="brand-mark" aria-hidden="true">
            A
          </span>
          <span>ADT</span>
        </Link>
        <div className="auth-panel__intro">
          <p className="eyebrow">
            {isAdministrativeLogin ? 'Acesso restrito' : 'Área autenticada'}
          </p>
          <h1>{isAdministrativeLogin ? 'Administração' : 'Entrar no ADT'}</h1>
          <p>
            {isAdministrativeLogin
              ? 'Use uma conta com acesso administrativo verificado pelo backend.'
              : 'Use uma conta existente para acessar a área read-only de paper trading.'}{' '}
            O ADT não oferece cadastro público.
          </p>
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
          {state?.passwordReset && (
            <SuccessMessage message="Senha atualizada. Entre com a nova senha." />
          )}
          {error && <InlineError message={error} />}
          <button className="button button--wide" type="submit" disabled={busy}>
            {busy ? 'Validando acesso…' : 'Entrar'}
          </button>
          <Link className="text-link" to="/admin/forgot-password">
            Esqueci minha senha
          </Link>
          {isAdministrativeLogin ? (
            <Link className="text-link" to="/login">
              Ir para a área autenticada
            </Link>
          ) : (
            <Link className="text-link" to="/admin/login">
              Acesso administrativo
            </Link>
          )}
        </form>
      </section>
      <aside className="auth-aside" aria-hidden="true">
        <div>
          <p className="metric-label">PRINCÍPIO OPERACIONAL</p>
          <blockquote>
            “Sem impulso. Sem pânico. Apenas regras verificáveis.”
          </blockquote>
        </div>
      </aside>
    </main>
  )
}
