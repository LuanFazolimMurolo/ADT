import type { PageMeta } from '../types/api'

export function Pagination({
  pagination,
  onChange,
}: {
  pagination: PageMeta
  onChange(page: number): void
}) {
  if (pagination.total_pages <= 1) return null
  return (
    <nav className="pagination" aria-label="Paginação">
      <button className="button button--ghost button--compact" type="button" disabled={pagination.page <= 1} onClick={() => onChange(pagination.page - 1)}>
        Anterior
      </button>
      <span>Página {pagination.page} de {pagination.total_pages}</span>
      <button className="button button--ghost button--compact" type="button" disabled={pagination.page >= pagination.total_pages} onClick={() => onChange(pagination.page + 1)}>
        Próxima
      </button>
    </nav>
  )
}
