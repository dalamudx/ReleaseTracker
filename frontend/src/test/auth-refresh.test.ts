import { afterEach, beforeEach, describe, expect, it } from "vitest"
import type { AxiosAdapter, AxiosResponse, InternalAxiosRequestConfig } from "axios"
import { AxiosError } from "axios"
import { api, apiClient } from "@/api/client"
import type { TokenPair } from "@/api/types"

const REFRESH_ENDPOINT = "/api/auth/refresh"

type MockState = {
  accessTokenValid: boolean
  refreshTokenValid: boolean
  protectedRequestStillUnauthorizedAfterRefresh: boolean
  calls: Record<string, number>
  nextAccessToken: string
  nextRefreshToken: string
}

function createMockLocation(url: string): Location {
  let currentUrl = new URL(url)

  return {
    get href() {
      return currentUrl.href
    },
    set href(value: string) {
      currentUrl = new URL(value, currentUrl.origin)
    },
    get pathname() {
      return currentUrl.pathname
    },
    set pathname(value: string) {
      currentUrl = new URL(value, currentUrl.origin)
    },
    get origin() {
      return currentUrl.origin
    },
  } as Location
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

    if (path === "/api/protected") {
      if (!state.accessTokenValid || state.protectedRequestStillUnauthorizedAfterRefresh) {
        throw createError(config, 401, { detail: "expired" })
      }
      return createResponse(config, 200, { ok: true })
    }

    return createResponse(config, 200, { ok: true })
  }
}

describe("auth refresh-on-401 contract", () => {
  const originalAdapter = apiClient.defaults.adapter
  let state: MockState
  let originalLocation: Location

  beforeEach(() => {
    localStorage.clear()
    originalLocation = window.location
    delete (window as { location?: Location }).location
    ;(window as { location: Location }).location = createMockLocation(originalLocation.href)
    state = {
      accessTokenValid: false,
      refreshTokenValid: true,
      protectedRequestStillUnauthorizedAfterRefresh: false,
      calls: {},
      nextAccessToken: "access-next",
      nextRefreshToken: "refresh-next",
    }
    apiClient.defaults.adapter = createMockAdapter(state)
  })

  afterEach(() => {
    apiClient.defaults.adapter = originalAdapter
    localStorage.clear()
    delete (window as { location?: Location }).location
    ;(window as { location: Location }).location = originalLocation
  })

  it("replays the original request after a single refresh", async () => {
    localStorage.setItem("token", "access-old")
    localStorage.setItem("refresh_token", "refresh-old")

    const response = await apiClient.get("/api/protected")

    expect(response.data).toEqual({ ok: true })
    expect(state.calls["/api/auth/refresh"]).toBe(1)
    expect(state.calls["/api/protected"]).toBe(2)
    expect(localStorage.getItem("token")).toBe("access-next")
    expect(localStorage.getItem("refresh_token")).toBe("refresh-next")
  })

  it("clears auth state when refresh fails", async () => {
    state.refreshTokenValid = false
    localStorage.setItem("token", "access-old")
    localStorage.setItem("refresh_token", "refresh-old")
    localStorage.setItem("user", "{}")

    await expect(apiClient.get("/api/protected")).rejects.toBeInstanceOf(AxiosError)

    expect(state.calls["/api/auth/refresh"]).toBe(1)
    expect(state.calls["/api/protected"]).toBe(1)
    expect(localStorage.getItem("token")).toBeNull()
    expect(localStorage.getItem("refresh_token")).toBeNull()
    expect(localStorage.getItem("user")).toBeNull()
  })

  it("redirects to login when a protected request cannot refresh", async () => {
    state.refreshTokenValid = false
    localStorage.setItem("token", "access-old")
    localStorage.setItem("refresh_token", "refresh-old")
    window.location.pathname = "/trackers"

    await expect(apiClient.get("/api/protected")).rejects.toBeInstanceOf(AxiosError)

    expect(window.location.pathname).toBe("/login")
  })

  it("clears auth state and redirects when the replayed protected request still returns 401", async () => {
    state.protectedRequestStillUnauthorizedAfterRefresh = true
    localStorage.setItem("token", "access-old")
    localStorage.setItem("refresh_token", "refresh-old")
    localStorage.setItem("user", "{}")
    window.location.pathname = "/trackers"

    await expect(apiClient.get("/api/protected")).rejects.toBeInstanceOf(AxiosError)

    expect(state.calls["/api/auth/refresh"]).toBe(1)
    expect(state.calls["/api/protected"]).toBe(2)
    expect(localStorage.getItem("token")).toBeNull()
    expect(localStorage.getItem("refresh_token")).toBeNull()
    expect(localStorage.getItem("user")).toBeNull()
    expect(window.location.pathname).toBe("/login")
  })

  it("single-flights concurrent 401 refresh attempts", async () => {
    localStorage.setItem("token", "access-old")
    localStorage.setItem("refresh_token", "refresh-old")

    const responses = await Promise.all([
      apiClient.get("/api/protected"),
      apiClient.get("/api/protected"),
      apiClient.get("/api/protected"),
    ])

    responses.forEach((response) => {
      expect(response.data).toEqual({ ok: true })
    })
    expect(state.calls["/api/auth/refresh"]).toBe(1)
    expect(state.calls["/api/protected"]).toBe(6)
  })

  it("recovers /api/auth/me via refresh before logout", async () => {
    localStorage.setItem("token", "access-old")
    localStorage.setItem("refresh_token", "refresh-old")

    const user = await api.getCurrentUser({ suppressAuthRedirect: true })

    expect(user).toMatchObject({ username: "test" })
    expect(state.calls["/api/auth/refresh"]).toBe(1)
    expect(state.calls["/api/auth/me"]).toBe(2)
    expect(localStorage.getItem("token")).toBe("access-next")
  })
})
