import { BrowserRouter, Routes, Route } from "react-router-dom"
import { ThemeProvider } from "@/providers/theme-provider"
import { Toaster } from "@/components/ui/sonner"
import AppLayout from "@/components/layout/AppLayout"
import DashboardPage from "@/pages/Dashboard"
import TrackersPage from "@/pages/Trackers"
import HistoryPage from "@/pages/History"
import CredentialsPage from "@/pages/Credentials"
import NotificationsPage from "@/pages/Notifications"
import { Navigate, Outlet } from "react-router-dom"
import { useAuth } from "@/providers/AuthProvider"
import { LoginPage } from "@/pages/Login"

function RequireAuth() {
  const { isAuthenticated, isLoading } = useAuth()

  if (isLoading) {
    return <div className="flex h-screen items-center justify-center">Loading...</div>
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }

  return <Outlet />
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
