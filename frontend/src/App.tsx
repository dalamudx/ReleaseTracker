import { Suspense, lazy } from "react"
import { BrowserRouter, Routes, Route } from "react-router-dom"
import { ThemeProvider } from "@/providers/theme-provider"
import { Toaster } from "@/components/ui/sonner"
import AppLayout from "@/components/layout/AppLayout"
import { Navigate, Outlet } from "react-router-dom"
import { useAuth } from "@/context/auth-context"
import { Spinner } from "@/components/ui/spinner"

// Lazy load pages
const DashboardPage = lazy(() => import("@/pages/Dashboard"))
const TrackersPage = lazy(() => import("@/pages/Trackers"))
const HistoryPage = lazy(() => import("@/pages/History"))
const CredentialsPage = lazy(() => import("@/pages/Credentials"))
const NotificationsPage = lazy(() => import("@/pages/Notifications"))
const LoginPage = lazy(() => import("@/pages/Login").then(module => ({ default: module.LoginPage })))


function RequireAuth() {
  const { isAuthenticated, isLoading } = useAuth()

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Spinner className="size-10 text-primary" />
      </div>
    )
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
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
              <Route path="/history" element={<HistoryPage />} />
              <Route path="/credentials" element={<CredentialsPage />} />
              <Route path="/notifications" element={<NotificationsPage />} />
            </Route>
          </Route>
        </Routes>
      </BrowserRouter>
      <Toaster />
    </ThemeProvider>
  )
}

export default App
