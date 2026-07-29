import { useEffect, useState } from 'react'
import { apiClient } from './http/client'
import { SystemStatus } from './types/system'
import './App.css'

function App() {
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const data = await apiClient.getSystemStatus()
        setStatus(data)
        setError(null)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to connect to API')
        setStatus(null)
      } finally {
        setLoading(false)
      }
    }

    fetchStatus()
  }, [])

  return (
    <div className="app">
      <header className="header">
        <h1 className="title">ADT</h1>
        <p className="subtitle">Automatic Dry Trade</p>
      </header>

      <main className="main-content">
        <section className="status-section">
          <h2>System Status</h2>
          {loading ? (
            <p className="status-text">Loading...</p>
          ) : error ? (
            <div className="status-error">
              <p className="status-text">⚠️ API Unavailable</p>
              <p className="error-message">{error}</p>
              <p className="error-hint">Ensure the backend is running on {import.meta.env.VITE_ADT_API_URL}</p>
            </div>
          ) : (
            <div className="status-ok">
              <p className="status-text">✓ System Ready</p>
              {status && (
                <div className="status-details">
                  <p>Environment: {status.environment}</p>
                  <p>Version: {status.version}</p>
                </div>
              )}
            </div>
          )}
        </section>

        <section className="components-grid">
          <div className="component-card">
            <h3>API</h3>
            <p className="phase">Phase 0</p>
            <p className="description">Backend service with FastAPI</p>
            <p className={`status ${error ? 'inactive' : 'active'}`}>
              {error ? 'Offline' : 'Online'}
            </p>
          </div>

          <div className="component-card">
            <h3>Supabase</h3>
            <p className="phase">Phase 1</p>
            <p className="description">PostgreSQL database & auth</p>
            <p className="status inactive">Pending</p>
          </div>

          <div className="component-card">
            <h3>Market Data</h3>
            <p className="phase">Phase 2</p>
            <p className="description">Historical candle collection</p>
            <p className="status inactive">Pending</p>
          </div>

          <div className="component-card">
            <h3>Workers</h3>
            <p className="phase">Phase 3+</p>
            <p className="description">Strategy engine & backtesting</p>
            <p className="status inactive">Pending</p>
          </div>
        </section>

        <section className="info-section">
          <p className="info-text">🔄 System under preparation</p>
          <p className="info-text">📖 Read the documentation for development details</p>
        </section>
      </main>
    </div>
  )
}

export default App
