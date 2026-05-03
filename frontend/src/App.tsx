import { Suspense, lazy } from "react"
import { BrowserRouter, Routes, Route, useLocation } from "react-router-dom"
import { ThemeProvider } from "@/providers/theme-provider"
import { Toaster } from "@/components/ui/sonner"
import AppLayout from "@/components/layout/AppLayout"
import { Navigate, Outlet } from "react-router-dom"
import { useAuth } from "@/context/auth-context"
import { Spinner } from "@/components/ui/spinner"

// Lazy load pages
const DashboardPage = lazy(() => import("@/pages/Dashboard"))
const TrackersPage = lazy(() => import("@/pages/Trackers"))
const ExecutorsPage = lazy(() => import("@/pages/Executors"))
const RuntimeConnectionsPage = lazy(() => import("@/pages/RuntimeConnections"))
const HistoryPage = lazy(() => import("@/pages/History"))
const CredentialsPage = lazy(() => import("@/pages/Credentials"))
const NotificationsPage = lazy(() => import("@/pages/Notifications"))
const SystemSettingsPage = lazy(() => import("@/pages/SystemSettings").then(m => ({ default: m.SystemSettingsPage })))
const LoginPage = lazy(() => import("@/pages/Login").then(module => ({ default: module.LoginPage })))


function RequireAuth() {
  const { isAuthenticated, isLoading } = useAuth()
  const location = useLocation()

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Spinner className="size-10 text-primary" />
      </div>
    )
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location }} />
  }

  return (
    <Suspense fallback={
      <div className="flex h-screen w-full items-center justify-center">
        <Spinner className="size-10 text-primary" />
      </div>
    }>
      <Outlet />
    </Suspense>
  )

}

function App() {
  return (
    <ThemeProvider defaultTheme="system" storageKey="vite-ui-theme">
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />

          <Route element={<RequireAuth />}>
            <Route element={<AppLayout />}>
              <Route path="/" element={<DashboardPage />} />
              <Route path="/trackers" element={<TrackersPage />} />
              <Route path="/executors" element={<ExecutorsPage />} />
              <Route path="/runtime-connections" element={<RuntimeConnectionsPage />} />
              <Route path="/history" element={<HistoryPage />} />
              <Route path="/credentials" element={<CredentialsPage />} />
              <Route path="/notifications" element={<NotificationsPage />} />
              <Route path="/settings" element={<SystemSettingsPage />} />
            </Route>
          </Route>
        </Routes>
      </BrowserRouter>
      <Toaster />
    </ThemeProvider>
  )
}

export default App
