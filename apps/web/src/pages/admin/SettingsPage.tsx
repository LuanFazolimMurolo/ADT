import { useEffect, useState } from 'react'
import { apiClient } from '../../http/client'
import type { JsonValue, Setting } from '../../types/api'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { EmptyState, InlineError, LoadingState, SuccessMessage } from '../../components/States'
import { formatDate, getErrorMessage } from '../../utils/format'

function serializeValue(value: JsonValue): string {
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
}

function parseValue(value: string, original: JsonValue): JsonValue {
  if (typeof original === 'string') return value
  return JSON.parse(value) as JsonValue
}

export function SettingsPage() {
  const [settings, setSettings] = useState<Setting[]>([])
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [pendingKey, setPendingKey] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await apiClient.listSettings()
      setSettings(response.items)
      setDrafts(Object.fromEntries(response.items.map((setting) => [setting.key, serializeValue(setting.value)])))
    } catch (nextError) {
      setError(getErrorMessage(nextError, 'Não foi possível carregar as configurações.'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load() }, [])

  const requestSave = (setting: Setting) => {
    const draft = drafts[setting.key] ?? ''
    try {
      parseValue(draft, setting.value)
      setError(null)
      setPendingKey(setting.key)
    } catch {
      setError(`O valor de “${setting.key}” deve ser um JSON válido.`)
    }
  }

  const save = async () => {
    const setting = settings.find((item) => item.key === pendingKey)
    if (!setting || busy) return
    setBusy(true)
    setError(null)
    try {
      const value = parseValue(drafts[setting.key] ?? '', setting.value)
      await apiClient.updateSetting(setting.key, value)
      setPendingKey(null)
      setSuccess(`Configuração “${setting.key}” atualizada.`)
      await load()
    } catch (nextError) {
      setPendingKey(null)
      setError(getErrorMessage(nextError, 'Não foi possível atualizar a configuração.'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <div className="page-heading">
        <div><p className="eyebrow">Parâmetros do sistema</p><h1>Configurações</h1><p>Somente valores não secretos expostos pelo backend.</p></div>
      </div>
      <div className="security-note"><span aria-hidden="true">⌾</span><p><strong>Limite de segurança</strong>Chaves, descrições e classificação são somente leitura. Segredos nunca são retornados por esta API.</p></div>
      {success && <SuccessMessage message={success} />}
      {error && <InlineError message={error} />}
      {loading ? <LoadingState message="Carregando configurações…" /> : settings.length === 0 ? (
        <EmptyState title="Nenhuma configuração disponível" description="O backend não retornou parâmetros administrativos." />
      ) : (
        <section className="settings-list">
          {settings.map((setting) => {
            const isComplex = typeof setting.value !== 'string'
            const changed = drafts[setting.key] !== serializeValue(setting.value)
            return (
              <article className="setting-card" key={setting.key}>
                <div className="setting-card__heading">
                  <div><code>{setting.key}</code><span className={setting.is_public ? 'visibility visibility--public' : 'visibility'}>{setting.is_public ? 'Pública' : 'Privada'}</span></div>
                  <small>Atualizada em {formatDate(setting.updated_at)}</small>
                </div>
                <p>{setting.description}</p>
                <label>
                  Valor
                  {isComplex ? (
                    <textarea rows={4} value={drafts[setting.key] ?? ''} onChange={(event) => setDrafts({ ...drafts, [setting.key]: event.target.value })} />
                  ) : (
                    <input value={drafts[setting.key] ?? ''} onChange={(event) => setDrafts({ ...drafts, [setting.key]: event.target.value })} />
                  )}
                </label>
                <div className="form-actions">
                  <button className="button button--ghost button--compact" type="button" disabled={!changed || busy} onClick={() => setDrafts({ ...drafts, [setting.key]: serializeValue(setting.value) })}>Descartar</button>
                  <button className="button button--compact" type="button" disabled={!changed || busy} onClick={() => requestSave(setting)}>Salvar alteração</button>
                </div>
              </article>
            )
          })}
        </section>
      )}
      <ConfirmDialog
        open={pendingKey !== null}
        title="Salvar configuração?"
        description={`O backend registrará a alteração do valor de “${pendingKey ?? ''}” em nome do administrador autenticado.`}
        confirmLabel="Salvar valor"
        busy={busy}
        onCancel={() => setPendingKey(null)}
        onConfirm={() => void save()}
      />
    </div>
  )
}
