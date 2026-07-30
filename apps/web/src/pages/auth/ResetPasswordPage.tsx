import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { signOutLocally } from '../../auth/signOut'
import {
  clearPasswordRecoveryContext,
  getSupabaseClient,
  hasPasswordRecoveryContext,
} from '../../lib/supabase'
import { InlineError, LoadingState } from '../../components/States'

export function ResetPasswordPage() {
  const navigate = useNavigate()
  const [checking, setChecking] = useState(true)
  const [validSession, setValidSession] = useState(false)
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let active = true
    const supabase = getSupabaseClient()
    const verify = async () => {
      try {
        const { data, error: sessionError } = await supabase.auth.getSession()
        if (sessionError) throw sessionError
        if (active) {
          setValidSession(Boolean(data.session) && hasPasswordRecoveryContext())
        }
      } catch {
        if (active) setValidSession(false)
      } finally {
        if (active) setChecking(false)
      }
    }
    void verify()
    const { data } = supabase.auth.onAuthStateChange((event, session) => {
      if (active && event === 'PASSWORD_RECOVERY') {
        setValidSession(Boolean(session))
        setChecking(false)
      } else if (active && (event === 'SIGNED_OUT' || !session)) {
        setValidSession(false)
        setChecking(false)
      }
    })
    return () => {
      active = false
      data.subscription.unsubscribe()
    }
  }, [])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    if (password.length < 8) {
      setError('A nova senha deve ter pelo menos 8 caracteres.')
      return
    }
    if (password !== confirmation) {
      setError('A confirmação não corresponde à nova senha.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const { error: updateError } = await getSupabaseClient().auth.updateUser({
        password,
      })
      if (updateError) throw updateError

      clearPasswordRecoveryContext()
      signOutLocally()
      navigate('/admin/login', { replace: true, state: { passwordReset: true } })
    } catch {
      setError('O link é inválido ou expirou. Solicite uma nova recuperação.')
    } finally {
      setBusy(false)
    }
  }

  if (checking) return <LoadingState message="Validando link de recuperação…" fullPage />

  return (
    <main className="auth-page auth-page--single">
      <section className="auth-panel">
        <Link className="auth-brand" to="/"><span className="brand-mark" aria-hidden="true">A</span><span>ADT</span></Link>
        <div className="auth-panel__intro">
          <p className="eyebrow">Nova credencial</p>
          <h1>Crie uma nova senha</h1>
          <p>Use no mínimo 8 caracteres e guarde-a em um gerenciador de senhas.</p>
        </div>
        {!validSession ? (
          <>
            <InlineError message="O link de recuperação é inválido ou expirou." />
            <Link className="button button--wide" to="/admin/forgot-password">Solicitar novo link</Link>
          </>
        ) : (
          <form className="form-stack" onSubmit={submit}>
            <label>
              Nova senha
              <input type="password" autoComplete="new-password" minLength={8} required autoFocus value={password} onChange={(event) => setPassword(event.target.value)} />
            </label>
            <label>
              Confirmar nova senha
              <input type="password" autoComplete="new-password" minLength={8} required value={confirmation} onChange={(event) => setConfirmation(event.target.value)} />
            </label>
            {error && <InlineError message={error} />}
            <button className="button button--wide" type="submit" disabled={busy}>
              {busy ? 'Atualizando…' : 'Atualizar senha'}
            </button>
          </form>
        )}
      </section>
    </main>
  )
}
