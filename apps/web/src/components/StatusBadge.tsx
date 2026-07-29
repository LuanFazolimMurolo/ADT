import type { SimulationStatus } from '../types/api'

export function StatusBadge({ status }: { status: SimulationStatus }) {
  const labels: Record<SimulationStatus, string> = {
    ACTIVE: 'Ativa',
    COMPLETED: 'Concluída',
    CANCELLED: 'Cancelada',
  }
  return <span className={`badge badge--${status.toLowerCase()}`}>{labels[status]}</span>
}
