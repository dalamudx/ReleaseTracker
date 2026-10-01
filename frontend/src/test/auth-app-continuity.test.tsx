import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { StrictMode } from "react"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import type { AxiosAdapter, AxiosResponse, InternalAxiosRequestConfig } from "axios"
import { AxiosError } from "axios"

vi.mock("sonner", () => ({
  Toaster: function MockToaster() {
    return null
  },
  toast: {
    info: vi.fn(),
    success: vi.fn(),
    error: vi.fn(),
  },
}))

vi.mock("@/components/layout/AppLayout", async () => {
  const { Outlet } = await import("react-router")
  return {
    default: function MockAppLayout() {
      return (
        <div data-testid="app-layout">
          <Outlet />
        </div>
      )
    },
  }
})

vi.mock("@/providers/theme-provider", () => ({
  ThemeProvider: function MockThemeProvider({ children }: { children: React.ReactNode }) {
    return <>{children}</>
  },
}))

vi.mock("@/pages/Login", () => ({
  LoginPage: function MockLoginPage() {
    return <h1>Login Page</h1>
  },
}))

vi.mock("@/pages/Trackers", () => ({
  default: function MockTrackersPage() {
    return <h1>Trackers Page</h1>
  },
}))

import App from "@/App"
import { apiClient } from "@/api/client"
import "@/i18n/config"
import { AuthProvider } from "@/providers/AuthProvider"

const REFRESH_ENDPOINT = "/api/auth/browser/refresh"

type MockState = {
  accessTokenValid: boolean
  refreshTokenValid: boolean
  calls: Record<string, number>
  nextAccessToken: string
  nextRefreshToken: string
}

function resolvePath(requestUrl?: string): string | null {
  if (!requestUrl) {
    return null
  }

  try {
    return new URL(requestUrl, window.location.origin).pathname
  } catch {
    return requestUrl.split("?")[0]
  }
}

function recordCall(state: MockState, path: string) {
  state.calls[path] = (state.calls[path] ?? 0) + 1
}

function createResponse<T>(
  config: InternalAxiosRequestConfig,
  status: number,
  data: T,
): AxiosResponse<T> {
  return {
    data,
    status,
    statusText: status === 200 ? "OK" : "Unauthorized",
    headers: {},
    config,
  }
}

function createError<T>(config: InternalAxiosRequestConfig, status: number, data: T) {
  return new AxiosError(
    status === 401 ? "Unauthorized" : "Request failed",
    `${status}`,
    config,
    undefined,
    createResponse(config, status, data),
  )
}

function createMockAdapter(state: MockState): AxiosAdapter {
  return async (config: InternalAxiosRequestConfig) => {
    const path = resolvePath(config.url) ?? ""
    recordCall(state, path)

    if (path === "/api/auth/browser/migrate") {
      state.accessTokenValid = true
      document.cookie = "releasetracker-csrf=csrf-migrated; path=/"
      return createResponse(config, 200, {user:{id:1, username:"test"}})
    }

    if (path === REFRESH_ENDPOINT) {
      if (!state.refreshTokenValid) {
        throw createError(config, 401, { detail: "refresh invalid" })
      }

      state.accessTokenValid = true
      document.cookie = "releasetracker-csrf=csrf-next; path=/"
      return createResponse(config, 200, {user:{id:1, username:"test"}})
    }

    if (path === "/api/auth/me") {
      if (!state.accessTokenValid) {
        throw createError(config, 401, { detail: "expired" })
      }

      return createResponse(config, 200, {
        id: 1,
        username: "test",
        email: "test@example.com",
      })
    }

    return createResponse(config, 200, { ok: true })
  }
}

function renderAppAt(pathname: string, hash?: string) {
  window.history.pushState({}, "", pathname)
  if (hash) {
    window.location.hash = hash
  }

  return render(
    <AuthProvider>
      <App />
    </AuthProvider>,
  )
}

describe("protected app auth continuity", () => {
  const originalAdapter = apiClient.defaults.adapter
  let consoleErrorSpy: ReturnType<typeof vi.spyOn>
  let state: MockState

  beforeEach(() => {
    cleanup()
    consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {})
    localStorage.clear()
    document.cookie = "releasetracker-csrf=; max-age=0; path=/"
    localStorage.setItem("language", "en")
    state = {
      accessTokenValid: false,
      refreshTokenValid: true,
      calls: {},
      nextAccessToken: "access-next",
      nextRefreshToken: "refresh-next",
    }
    apiClient.defaults.adapter = createMockAdapter(state)
  })

  afterEach(() => {
    cleanup()
    consoleErrorSpy.mockRestore()
    apiClient.defaults.adapter = originalAdapter
    localStorage.clear()
    document.cookie = "releasetracker-csrf=; max-age=0; path=/"
    window.history.pushState({}, "", "/")
  })

  it("keeps the requested protected route accessible after lazy refresh succeeds", async () => {
    document.cookie = "releasetracker-csrf=csrf-old; path=/"

    renderAppAt("/trackers")

    expect(await screen.findByText("Trackers Page")).toBeInTheDocument()
    await waitFor(() => {
      expect(window.location.pathname).toBe("/trackers")
    })

    expect(state.calls[REFRESH_ENDPOINT]).toBe(1)
    expect(state.calls["/api/auth/me"]).toBe(2)
    expect(localStorage.getItem("token")).toBeNull()
    expect(localStorage.getItem("refresh_token")).toBeNull()
  })

  it("loads the HttpOnly OIDC session without accepting URL tokens", async () => {
    state.accessTokenValid = true

    document.cookie = "releasetracker-csrf=csrf-oidc; path=/"
    renderAppAt("/trackers", "#oidc=success")

    expect(await screen.findByText("Trackers Page")).toBeInTheDocument()
    await waitFor(() => {
      expect(window.location.hash).toBe("")
    })

    expect(localStorage.getItem("token")).toBeNull()
    expect(localStorage.getItem("refresh_token")).toBeNull()
    expect(state.calls["/api/auth/me"]).toBe(1)
  })

  it("does not authenticate using a forged legacy token fragment", async () => {
    renderAppAt("/trackers", "#token=attacker&refresh_token=attacker-refresh")
    expect(await screen.findByText("Login Page")).toBeInTheDocument()
    expect(window.location.hash).toBe("")
    expect(localStorage.getItem("token")).toBeNull()
    expect(state.calls["/api/auth/me"]).toBeUndefined()
  })

  it("migrates an old session once even in StrictMode and erases local JWTs", async () => {
    localStorage.setItem("token", "legacy-access")
    localStorage.setItem("refresh_token", "legacy-refresh")
    window.history.pushState({}, "", "/trackers")
    render(<StrictMode><AuthProvider><App /></AuthProvider></StrictMode>)
    expect(await screen.findByText("Trackers Page")).toBeInTheDocument()
    expect(state.calls["/api/auth/browser/migrate"]).toBe(1)
    expect(localStorage.getItem("token")).toBeNull()
    expect(localStorage.getItem("refresh_token")).toBeNull()
  })

  it("redirects to login after refresh failure leaves the session unauthenticated", async () => {
    state.refreshTokenValid = false
    document.cookie = "releasetracker-csrf=csrf-old; path=/"
    localStorage.setItem("user", JSON.stringify({ username: "stale" }))

    renderAppAt("/trackers")

    expect(await screen.findByText("Login Page")).toBeInTheDocument()
    await waitFor(() => {
      expect(window.location.pathname).toBe("/login")
    })

    expect(state.calls[REFRESH_ENDPOINT]).toBe(1)
    expect(state.calls["/api/auth/me"]).toBe(1)
    expect(localStorage.getItem("token")).toBeNull()
    expect(localStorage.getItem("refresh_token")).toBeNull()
    expect(localStorage.getItem("user")).toBeNull()
  })
})
