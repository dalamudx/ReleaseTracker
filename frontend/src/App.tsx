import { BrowserRouter, Routes, Route } from "react-router-dom"
import { ThemeProvider } from "@/providers/theme-provider"
import { Toaster } from "@/components/ui/sonner"
import AppLayout from "@/components/layout/AppLayout"
import DashboardPage from "@/pages/Dashboard"
import TrackersPage from "@/pages/Trackers"
import HistoryPage from "@/pages/History"
import CredentialsPage from "@/pages/Credentials"

function App() {
  return (
    <ThemeProvider defaultTheme="system" storageKey="vite-ui-theme">
      <BrowserRouter>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/trackers" element={<TrackersPage />} />
            <Route path="/history" element={<HistoryPage />} />
            <Route path="/credentials" element={<CredentialsPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
      <Toaster />
    </ThemeProvider>
  )
}

export default App
