import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
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
  const { Outlet } = await import("react-router-dom")
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
import type { TokenPair } from "@/api/types"
import "@/i18n/config"
import { AuthProvider } from "@/providers/AuthProvider"

const REFRESH_ENDPOINT = "/api/auth/refresh"

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

    if (path === REFRESH_ENDPOINT) {
      if (!state.refreshTokenValid) {
        throw createError(config, 401, { detail: "refresh invalid" })
      }

      state.accessTokenValid = true
      const tokenPair: TokenPair = {
        access_token: state.nextAccessToken,
        refresh_token: state.nextRefreshToken,
        token_type: "bearer",
        expires_in: 1800,
      }
      return createResponse(config, 200, tokenPair)
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
    window.history.pushState({}, "", "/")
  })

  it("keeps the requested protected route accessible after lazy refresh succeeds", async () => {
    localStorage.setItem("token", "access-old")
    localStorage.setItem("refresh_token", "refresh-old")

    renderAppAt("/trackers")

    expect(await screen.findByText("Trackers Page")).toBeInTheDocument()
    await waitFor(() => {
      expect(window.location.pathname).toBe("/trackers")
    })

    expect(state.calls["/api/auth/refresh"]).toBe(1)
    expect(state.calls["/api/auth/me"]).toBe(2)
    expect(localStorage.getItem("token")).toBe("access-next")
    expect(localStorage.getItem("refresh_token")).toBe("refresh-next")
  })

  it("stores refresh token from OIDC callback hash", async () => {
    state.accessTokenValid = true

    renderAppAt("/trackers", "#token=access-oidc&refresh_token=refresh-oidc")

    expect(await screen.findByText("Trackers Page")).toBeInTheDocument()
    await waitFor(() => {
      expect(window.location.hash).toBe("")
    })

    expect(localStorage.getItem("token")).toBe("access-oidc")
    expect(localStorage.getItem("refresh_token")).toBe("refresh-oidc")
    expect(state.calls["/api/auth/me"]).toBe(1)
  })

  it("redirects to login after refresh failure leaves the session unauthenticated", async () => {
    state.refreshTokenValid = false
    localStorage.setItem("token", "access-old")
    localStorage.setItem("refresh_token", "refresh-old")
    localStorage.setItem("user", JSON.stringify({ username: "stale" }))

    renderAppAt("/trackers")

    expect(await screen.findByText("Login Page")).toBeInTheDocument()
    await waitFor(() => {
      expect(window.location.pathname).toBe("/login")
    })

    expect(state.calls["/api/auth/refresh"]).toBe(1)
    expect(state.calls["/api/auth/me"]).toBe(1)
    expect(localStorage.getItem("token")).toBeNull()
    expect(localStorage.getItem("refresh_token")).toBeNull()
    expect(localStorage.getItem("user")).toBeNull()
  })
})
