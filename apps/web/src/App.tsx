import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { ProtectedRoute } from "./auth/ProtectedRoute";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { PublicConfigError, validatePublicConfig } from "./config/env";
import { AdminLayout } from "./layouts/AdminLayout";
import { PublicHome } from "./pages/PublicHome";
import { DashboardPage } from "./pages/admin/DashboardPage";
import { InstrumentChartPage } from "./pages/admin/InstrumentChartPage";
import { PaperPeriodMetricsPage } from "./pages/admin/PaperPeriodMetricsPage";
import { PaperTradeJournalPage } from "./pages/admin/PaperTradeJournalPage";
import { PaperTradingDashboardPage } from "./pages/admin/PaperTradingDashboardPage";
import { SettingsPage } from "./pages/admin/SettingsPage";
import { SimulationDetailPage } from "./pages/admin/SimulationDetailPage";
import { SimulationsPage } from "./pages/admin/SimulationsPage";
import { ForgotPasswordPage } from "./pages/auth/ForgotPasswordPage";
import { LoginPage } from "./pages/auth/LoginPage";
import { ResetPasswordPage } from "./pages/auth/ResetPasswordPage";
import "./App.css";

function ConfigurationFailure({ error }: { error: PublicConfigError }) {
  return (
    <main className="state state--full configuration-error" role="alert">
      <div className="brand-mark" aria-hidden="true">
        A
      </div>
      <p className="eyebrow">Configuração necessária</p>
      <h1>O frontend do ADT não pode iniciar</h1>
      <p>
        Defina as variáveis públicas obrigatórias e reinicie o servidor de
        desenvolvimento.
      </p>
      <code>{error.missingVariables.join(", ")}</code>
      <small>Nenhum valor configurado foi exibido.</small>
    </main>
  );
}

function App() {
  try {
    validatePublicConfig();
  } catch (error) {
    if (error instanceof PublicConfigError)
      return <ConfigurationFailure error={error} />;
    throw error;
  }

  return (
    <ErrorBoundary>
      <BrowserRouter>
        <AuthProvider>
          <Routes>
            <Route path="/" element={<PublicHome />} />
            <Route path="/admin/login" element={<LoginPage />} />
            <Route
              path="/admin/forgot-password"
              element={<ForgotPasswordPage />}
            />
            <Route
              path="/admin/reset-password"
              element={<ResetPasswordPage />}
            />
            <Route element={<ProtectedRoute />}>
              <Route path="/admin" element={<AdminLayout />}>
                <Route index element={<DashboardPage />} />
                <Route
                  path="paper-trading"
                  element={<PaperTradingDashboardPage />}
                />
                <Route
                  path="paper-trading/chart"
                  element={<InstrumentChartPage />}
                />
                <Route
                  path="paper-trading/journal"
                  element={<PaperTradeJournalPage />}
                />
                <Route
                  path="paper-trading/period-metrics"
                  element={<PaperPeriodMetricsPage />}
                />
                <Route path="simulations" element={<SimulationsPage />} />
                <Route
                  path="simulations/:simulationId"
                  element={<SimulationDetailPage />}
                />
                <Route path="settings" element={<SettingsPage />} />
              </Route>
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </AuthProvider>
      </BrowserRouter>
    </ErrorBoundary>
  );
}

export default App;
