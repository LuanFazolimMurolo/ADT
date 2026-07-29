import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { getSupabaseClient } from '../../lib/supabase'
import { InlineError, SuccessMessage } from '../../components/States'

export function ForgotPasswordPage() {
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [sent, setSent] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setError(null)
    const redirectTo = `${window.location.origin}/admin/reset-password`
    const { error: resetError } = await getSupabaseClient().auth.resetPasswordForEmail(
      email.trim(),
      { redirectTo },
    )
    setBusy(false)
    if (resetError) {
      setError('Não foi possível processar a solicitação agora. Tente novamente.')
      return
    }
    setSent(true)
  }

  return (
    <main className="auth-page auth-page--single">
      <section className="auth-panel">
        <Link className="auth-brand" to="/"><span className="brand-mark" aria-hidden="true">A</span><span>ADT</span></Link>
        <div className="auth-panel__intro">
          <p className="eyebrow">Recuperação segura</p>
          <h1>Redefinir acesso</h1>
          <p>Informe o e-mail da conta administrativa.</p>
        </div>
        {sent ? (
          <>
            <SuccessMessage message="Se a conta estiver cadastrada, você receberá instruções para redefinir a senha." />
            <Link className="button button--ghost button--wide" to="/admin/login">Voltar ao login</Link>
          </>
        ) : (
          <form className="form-stack" onSubmit={submit}>
            <label>
              E-mail
              <input type="email" autoComplete="email" required autoFocus value={email} onChange={(event) => setEmail(event.target.value)} />
            </label>
            {error && <InlineError message={error} />}
            <button className="button button--wide" type="submit" disabled={busy}>
              {busy ? 'Enviando…' : 'Enviar instruções'}
            </button>
            <Link className="text-link" to="/admin/login">Voltar ao login</Link>
          </form>
        )}
      </section>
    </main>
  )
}
