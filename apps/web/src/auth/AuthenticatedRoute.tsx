import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { LoadingState } from '../components/States'
import { useAuth } from './AuthContext'

export function AuthenticatedRoute() {
  const { session, identity, loading } = useAuth()
  const location = useLocation()

  if (loading) return <LoadingState message="Validando sessão…" fullPage />
  if (!session || !identity) {
    return <Navigate to="/login" replace state={{ from: location }} />
  }
  return <Outlet />
}
