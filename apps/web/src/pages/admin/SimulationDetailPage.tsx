import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { apiClient, ApiError } from '../../http/client'
import type {
  CapitalMovement,
  JsonValue,
  MovementCreateRequest,
  MovementCreateType,
  PageMeta,
  SimulationDetail,
} from '../../types/api'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { Pagination } from '../../components/Pagination'
import { EmptyState, InlineError, LoadingState, SuccessMessage } from '../../components/States'
import { StatusBadge } from '../../components/StatusBadge'
import { formatDate, formatMoney, getErrorMessage } from '../../utils/format'

interface MovementForm {
  type: MovementCreateType
  amount: string
  reason: string
  metadata: string
}

const EMPTY_MOVEMENT: MovementForm = { type: 'DEPOSIT', amount: '', reason: '', metadata: '' }

export function SimulationDetailPage() {
  const { simulationId = '' } = useParams()
  const navigate = useNavigate()
  const [simulation, setSimulation] = useState<SimulationDetail | null>(null)
  const [movements, setMovements] = useState<CapitalMovement[]>([])
  const [pagination, setPagination] = useState<PageMeta>({ page: 1, page_size: 10, total: 0, total_pages: 0 })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [form, setForm] = useState<MovementForm>(EMPTY_MOVEMENT)
  const [confirmAction, setConfirmAction] = useState<'movement' | 'complete' | 'cancel' | null>(null)
  const [busy, setBusy] = useState(false)

  const loadMovements = useCallback(async (page: number) => {
    const response = await apiClient.listMovements(simulationId, page, 10)
    setMovements(response.items)
    setPagination(response.pagination)
  }, [simulationId])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [detail] = await Promise.all([
        apiClient.getSimulation(simulationId),
        loadMovements(1),
      ])
      setSimulation(detail)
    } catch (nextError) {
      setError(getErrorMessage(nextError, 'Não foi possível carregar a simulação.'))
    } finally {
      setLoading(false)
    }
  }, [simulationId, loadMovements])

  useEffect(() => { void load() }, [load])

  const validateMovement = (event: FormEvent) => {
    event.preventDefault()
    const amount = Number(form.amount)
    const isAdjustment = form.type === 'ADJUSTMENT'
    if (!Number.isFinite(amount) || amount === 0 || (!isAdjustment && amount < 0)) {
      setError(isAdjustment
        ? 'O ajuste deve ser diferente de zero e pode ser positivo ou negativo.'
        : 'Informe um valor maior que zero. O sinal é aplicado conforme o tipo.')
      return
    }
    const decimalPattern = isAdjustment ? /^-?\d+(\.\d{1,8})?$/ : /^\d+(\.\d{1,8})?$/
    if (!decimalPattern.test(form.amount) || !form.reason.trim()) {
      setError('Informe motivo e valor com ponto decimal e até 8 casas.')
      return
    }
    if (form.metadata.trim()) {
      try {
        const parsed: unknown = JSON.parse(form.metadata)
        if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error()
      } catch {
        setError('Metadados devem ser um objeto JSON válido.')
        return
      }
    }
    setError(null)
    if (form.type === 'DEPOSIT') void createMovement()
    else setConfirmAction('movement')
  }

  const createMovement = async () => {
    if (busy) return
    setBusy(true)
    setError(null)
    const signedAmount = form.type === 'WITHDRAWAL' ? `-${form.amount}` : form.amount
    let metadata: Record<string, JsonValue> | null = null
    if (form.metadata.trim()) metadata = JSON.parse(form.metadata) as Record<string, JsonValue>
    const payload: MovementCreateRequest = {
      type: form.type,
      amount: signedAmount,
      reason: form.reason.trim(),
      metadata,
    }
    try {
      await apiClient.createMovement(simulationId, payload)
      setForm(EMPTY_MOVEMENT)
      setConfirmAction(null)
      setSuccess('Movimento registrado no ledger imutável.')
      const [detail] = await Promise.all([apiClient.getSimulation(simulationId), loadMovements(1)])
      setSimulation(detail)
    } catch (nextError) {
      setConfirmAction(null)
      if (nextError instanceof ApiError && nextError.code === 'insufficient_balance') {
        setError('Saldo insuficiente para realizar esta retirada.')
      } else {
        setError(getErrorMessage(nextError, 'Não foi possível criar o movimento.'))
      }
    } finally {
      setBusy(false)
    }
  }

  const transition = async (action: 'complete' | 'cancel') => {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      const updated = action === 'complete'
        ? await apiClient.completeSimulation(simulationId)
        : await apiClient.cancelSimulation(simulationId)
      setSimulation(updated)
      setConfirmAction(null)
      setSuccess(action === 'complete' ? 'Simulação encerrada como concluída.' : 'Simulação cancelada.')
    } catch (nextError) {
      setConfirmAction(null)
      setError(getErrorMessage(nextError, 'Não foi possível encerrar a simulação.'))
    } finally {
      setBusy(false)
    }
  }

  if (loading) return <LoadingState message="Carregando detalhes e movimentos…" />
  if (!simulation) return <><InlineError message={error ?? 'Simulação não encontrada.'} /><button className="button button--ghost" onClick={() => navigate('/admin/simulations')}>Voltar</button></>

  return (
    <div>
      <Link className="back-link" to="/admin/simulations">← Todas as simulações</Link>
      <div className="page-heading">
        <div><p className="eyebrow">Detalhes da simulação</p><h1>{simulation.name}</h1><p>ID {simulation.id}</p></div>
        <StatusBadge status={simulation.status} />
      </div>

      <section className="metrics-grid metrics-grid--three">
        <article className="metric-card"><span className="metric-label">CAPITAL INICIAL</span><strong>{formatMoney(simulation.initial_capital, simulation.currency)}</strong><small>Imutável</small></article>
        <article className="metric-card metric-card--primary"><span className="metric-label">SALDO ATUAL</span><strong>{formatMoney(simulation.current_balance, simulation.currency)}</strong><small>Autoridade: backend</small></article>
        <article className={`metric-card ${Number(simulation.total_profit_loss) < 0 ? 'metric-card--negative' : ''}`}><span className="metric-label">P/L TOTAL</span><strong>{formatMoney(simulation.total_profit_loss, simulation.currency)}</strong><small>Desde {formatDate(simulation.started_at)}</small></article>
      </section>

      {success && <SuccessMessage message={success} />}
      {error && <InlineError message={error} />}

      {simulation.status === 'ACTIVE' && (
        <section className="panel">
          <div className="section-heading"><div><p className="eyebrow">Ledger administrativo</p><h2>Novo movimento</h2></div></div>
          <form className="form-grid" onSubmit={validateMovement}>
            <label>Tipo<select value={form.type} onChange={(event) => setForm({ ...form, type: event.target.value as MovementCreateType })}><option value="DEPOSIT">Depósito</option><option value="WITHDRAWAL">Retirada</option><option value="ADJUSTMENT">Ajuste</option></select></label>
            <label>{form.type === 'ADJUSTMENT' ? 'Valor assinado' : 'Valor absoluto'}<input inputMode="decimal" value={form.amount} onChange={(event) => setForm({ ...form, amount: event.target.value })} placeholder={form.type === 'ADJUSTMENT' ? '-100.00 ou 100.00' : '1000.00'} required /></label>
            <label className="form-grid__wide">Motivo<input value={form.reason} onChange={(event) => setForm({ ...form, reason: event.target.value })} placeholder="Justificativa auditável" required /></label>
            <label className="form-grid__wide">Metadados opcionais (JSON)<textarea value={form.metadata} onChange={(event) => setForm({ ...form, metadata: event.target.value })} placeholder={'{"origem":"administrativa"}'} rows={3} /></label>
            <p className="form-hint form-grid__wide">Retiradas recebem sinal negativo antes do envio; ajustes preservam o sinal informado. INITIAL_CAPITAL não está disponível para criação manual.</p>
            <div className="form-actions form-grid__wide"><button className="button" type="submit" disabled={busy}>Registrar movimento</button></div>
          </form>
        </section>
      )}

      <section className="panel">
        <div className="section-heading"><div><p className="eyebrow">Auditoria financeira</p><h2>Histórico de movimentos</h2></div><span>{pagination.total} registro(s)</span></div>
        {movements.length === 0 ? <EmptyState title="Sem movimentos" description="Nenhum lançamento foi encontrado nesta página." /> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Data</th><th>Tipo</th><th>Motivo</th><th>Valor</th><th>Metadados</th></tr></thead>
              <tbody>{movements.map((movement) => <tr key={movement.id}><td>{formatDate(movement.created_at)}</td><td><code>{movement.type}</code></td><td>{movement.reason}</td><td className={Number(movement.amount) < 0 ? 'value-negative' : 'value-positive'}>{formatMoney(movement.amount, simulation.currency)}</td><td><small>{movement.metadata ? JSON.stringify(movement.metadata) : '—'}</small></td></tr>)}</tbody>
            </table>
          </div>
        )}
        <Pagination pagination={pagination} onChange={(page) => void loadMovements(page)} />
      </section>

      {simulation.status === 'ACTIVE' && (
        <section className="danger-zone">
          <div><p className="eyebrow">Encerramento</p><h2>Finalizar ciclo</h2><p>Esta transição é permanente e impede novos movimentos administrativos.</p></div>
          <div><button className="button button--ghost" type="button" onClick={() => setConfirmAction('complete')}>Marcar como concluída</button><button className="button button--danger" type="button" onClick={() => setConfirmAction('cancel')}>Cancelar simulação</button></div>
        </section>
      )}

      <ConfirmDialog
        open={confirmAction === 'movement'}
        title={form.type === 'WITHDRAWAL' ? 'Confirmar retirada?' : 'Confirmar ajuste?'}
        description={`${form.type === 'WITHDRAWAL' ? 'Será retirado' : 'Será ajustado'} ${simulation.currency} ${form.amount}. O movimento não poderá ser editado ou apagado.`}
        confirmLabel="Registrar movimento"
        danger={form.type === 'WITHDRAWAL'}
        busy={busy}
        onCancel={() => setConfirmAction(null)}
        onConfirm={() => void createMovement()}
      />
      <ConfirmDialog
        open={confirmAction === 'complete'}
        title="Concluir esta simulação?"
        description="O status mudará permanentemente para COMPLETED e novos movimentos serão bloqueados."
        confirmLabel="Concluir simulação"
        busy={busy}
        onCancel={() => setConfirmAction(null)}
        onConfirm={() => void transition('complete')}
      />
      <ConfirmDialog
        open={confirmAction === 'cancel'}
        title="Cancelar esta simulação?"
        description="O status mudará permanentemente para CANCELLED. O histórico será preservado."
        confirmLabel="Cancelar simulação"
        danger
        busy={busy}
        onCancel={() => setConfirmAction(null)}
        onConfirm={() => void transition('cancel')}
      />
    </div>
  )
}
