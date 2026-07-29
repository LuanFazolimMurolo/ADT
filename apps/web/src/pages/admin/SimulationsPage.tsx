import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { apiClient, ApiError } from '../../http/client'
import type { PageMeta, SimulationCreateRequest, SimulationDetail } from '../../types/api'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { EmptyState, InlineError, LoadingState, SuccessMessage } from '../../components/States'
import { Pagination } from '../../components/Pagination'
import { StatusBadge } from '../../components/StatusBadge'
import { formatDate, formatMoney, getErrorMessage } from '../../utils/format'

const EMPTY_FORM: SimulationCreateRequest = { name: '', initial_capital: '', currency: 'USD' }

export function SimulationsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [items, setItems] = useState<SimulationDetail[]>([])
  const [pagination, setPagination] = useState<PageMeta>({ page: 1, page_size: 10, total: 0, total_pages: 0 })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [formOpen, setFormOpen] = useState(searchParams.get('create') === 'true')
  const [form, setForm] = useState<SimulationCreateRequest>(EMPTY_FORM)
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async (page: number) => {
    setLoading(true)
    setError(null)
    try {
      const response = await apiClient.listSimulations(page, 10)
      const details = await Promise.all(response.items.map((item) => apiClient.getSimulation(item.id)))
      setItems(details)
      setPagination(response.pagination)
    } catch (nextError) {
      setError(getErrorMessage(nextError, 'Não foi possível carregar as simulações.'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load(1) }, [load])

  const requestConfirmation = (event: FormEvent) => {
    event.preventDefault()
    const capital = Number(form.initial_capital)
    if (!form.name.trim() || !Number.isFinite(capital) || capital <= 0 || !form.currency.trim()) {
      setError('Informe nome, moeda e capital inicial maior que zero.')
      return
    }
    if (!/^\d+(\.\d{1,8})?$/.test(form.initial_capital)) {
      setError('Use capital positivo com ponto decimal e até 8 casas.')
      return
    }
    setError(null)
    setConfirming(true)
  }

  const create = async () => {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      await apiClient.createSimulation({
        name: form.name.trim(),
        initial_capital: form.initial_capital,
        currency: form.currency.trim().toUpperCase(),
      })
      setForm(EMPTY_FORM)
      setFormOpen(false)
      setConfirming(false)
      setSearchParams({})
      setSuccess('Simulação criada com capital inicial registrado pelo backend.')
      await load(1)
    } catch (nextError) {
      setConfirming(false)
      if (nextError instanceof ApiError && nextError.code === 'active_simulation_exists') {
        setError('Já existe uma simulação ativa. Encerre-a antes de criar outra.')
      } else {
        setError(getErrorMessage(nextError, 'Não foi possível criar a simulação.'))
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <div className="page-heading">
        <div><p className="eyebrow">Capital simulado</p><h1>Simulações</h1><p>Histórico imutável de ciclos de paper trading.</p></div>
        <button className="button" type="button" onClick={() => { setFormOpen((value) => !value); setSuccess(null) }}>
          {formOpen ? 'Fechar formulário' : '+ Nova simulação'}
        </button>
      </div>

      {formOpen && (
        <section className="panel form-panel">
          <div className="section-heading"><div><p className="eyebrow">Novo ciclo</p><h2>Criar simulação</h2></div></div>
          <form className="form-grid" onSubmit={requestConfirmation}>
            <label className="form-grid__wide">Nome<input value={form.name} maxLength={120} required autoFocus onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="Ex.: Validação principal" /></label>
            <label>Capital inicial<input inputMode="decimal" value={form.initial_capital} required onChange={(event) => setForm({ ...form, initial_capital: event.target.value })} placeholder="100000.00" /></label>
            <label>Moeda<input value={form.currency} required maxLength={12} onChange={(event) => setForm({ ...form, currency: event.target.value })} placeholder="USD" /></label>
            <p className="form-hint form-grid__wide">O capital inicial é enviado como string decimal e não poderá ser editado.</p>
            <div className="form-actions form-grid__wide"><button className="button button--ghost" type="button" onClick={() => setFormOpen(false)}>Cancelar</button><button className="button" type="submit">Revisar e criar</button></div>
          </form>
        </section>
      )}

      {success && <SuccessMessage message={success} />}
      {error && <InlineError message={error} />}
      {loading ? <LoadingState message="Carregando simulações…" /> : items.length === 0 ? (
        <EmptyState title="Nenhuma simulação criada" description="Abra o primeiro ciclo de paper trading para iniciar o ledger." action={<button className="button" type="button" onClick={() => setFormOpen(true)}>Criar simulação</button>} />
      ) : (
        <>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Simulação</th><th>Status</th><th>Capital inicial</th><th>Saldo</th><th>P/L</th><th>Início</th><th><span className="sr-only">Ações</span></th></tr></thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id}>
                    <td><strong>{item.name}</strong><small>{item.currency} · {item.id.slice(0, 8)}</small></td>
                    <td><StatusBadge status={item.status} /></td>
                    <td>{formatMoney(item.initial_capital, item.currency)}</td>
                    <td>{formatMoney(item.current_balance, item.currency)}</td>
                    <td className={Number(item.total_profit_loss) < 0 ? 'value-negative' : 'value-positive'}>{formatMoney(item.total_profit_loss, item.currency)}</td>
                    <td>{formatDate(item.started_at)}</td>
                    <td><Link className="table-link" to={`/admin/simulations/${item.id}`}>Detalhes →</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination pagination={pagination} onChange={(page) => void load(page)} />
        </>
      )}

      <ConfirmDialog
        open={confirming}
        title="Criar esta simulação?"
        description={`O capital inicial de ${form.currency.toUpperCase()} ${form.initial_capital} será permanente no histórico.`}
        confirmLabel="Criar simulação"
        busy={busy}
        onCancel={() => setConfirming(false)}
        onConfirm={() => void create()}
      />
    </div>
  )
}
