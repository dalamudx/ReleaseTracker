import { Suspense, lazy } from "react"
import { BrowserRouter, Routes, Route, useLocation } from "react-router"
import { ThemeProvider } from "@/providers/theme-provider"
import { Toaster } from "@/components/ui/sonner"
import AppLayout from "@/components/layout/AppLayout"
import { Navigate, Outlet } from "react-router"
import { useAuth } from "@/context/auth-context"
import { Spinner } from "@/components/ui/spinner"
import { appBasePath } from "@/lib/base-path"

// Lazy load pages
const DashboardPage = lazy(() => import("@/pages/Dashboard"))
const TrackersPage = lazy(() => import("@/pages/Trackers"))
const TasksPage = lazy(() => import("@/pages/Tasks"))
const ExecutorsPage = lazy(() => import("@/pages/Executors"))
const RuntimeConnectionsPage = lazy(() => import("@/pages/RuntimeConnections"))
const HistoryPage = lazy(() => import("@/pages/History"))
const CredentialsPage = lazy(() => import("@/pages/Credentials"))
const WebhooksPage = lazy(() => import("@/pages/Webhooks"))
const SystemSettingsPage = lazy(() => import("@/pages/SystemSettings").then(m => ({ default: m.SystemSettingsPage })))
const LoginPage = lazy(() => import("@/pages/Login").then(module => ({ default: module.LoginPage })))
const NotFoundPage = lazy(() => import("@/pages/NotFound"))


function RequireAuth() {
  const { isAuthenticated, isLoading } = useAuth()
  const location = useLocation()

  if (isLoading) {
    return (
      <div className="flex min-h-dvh items-center justify-center" role="status" aria-live="polite">
        <Spinner className="size-10 text-primary" />
      </div>
    )
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location }} />
  }

  return <Outlet />

}

function App() {
  return (
    <ThemeProvider defaultTheme="system" storageKey="vite-ui-theme">
      <BrowserRouter basename={appBasePath()}>
        <Suspense fallback={
          <div className="flex min-h-dvh w-full items-center justify-center" role="status" aria-live="polite">
            <Spinner className="size-10 text-primary" />
          </div>
        }>
        <Routes>
          <Route path="/login" element={<LoginPage />} />

          <Route element={<RequireAuth />}>
            <Route element={<AppLayout />}>
              <Route path="/" element={<DashboardPage />} />
              <Route path="/trackers" element={<TrackersPage />} />
              <Route path="/tasks" element={<TasksPage />} />
              <Route path="/executors" element={<ExecutorsPage />} />
              <Route path="/runtime-connections" element={<RuntimeConnectionsPage />} />
              <Route path="/history" element={<HistoryPage />} />
              <Route path="/credentials" element={<CredentialsPage />} />
              <Route path="/webhooks" element={<WebhooksPage />} />
              <Route path="/notifications" element={<Navigate to="/webhooks" replace />} />
              <Route path="/settings" element={<SystemSettingsPage />} />
              <Route path="*" element={<NotFoundPage />} />
            </Route>
          </Route>
        </Routes>
        </Suspense>
      </BrowserRouter>
      <Toaster />
    </ThemeProvider>
  )
}

export default App
