import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from './AuthContext'
import { LoadingState } from '../components/States'

export function ProtectedRoute() {
  const { session, isAdmin, loading } = useAuth()
  const location = useLocation()

  if (loading) return <LoadingState message="Validando acesso administrativo…" fullPage />
  if (!session || !isAdmin) {
    return <Navigate to="/admin/login" replace state={{ from: location }} />
  }
  return <Outlet />
}
