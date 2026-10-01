import { afterEach, beforeEach, describe, expect, it } from "vitest"
import type { AxiosAdapter, AxiosResponse, InternalAxiosRequestConfig } from "axios"
import { AxiosError } from "axios"
import { api, apiClient } from "@/api/client"

const REFRESH_ENDPOINT = "/api/auth/browser/refresh"

type MockState = {
  accessTokenValid: boolean
  refreshTokenValid: boolean
  protectedRequestStillUnauthorizedAfterRefresh: boolean
  calls: Record<string, number>
  nextAccessToken: string
  nextRefreshToken: string
  refreshRequestConfig: InternalAxiosRequestConfig | null
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
      state.refreshRequestConfig = config
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
    document.cookie = "releasetracker-csrf=; max-age=0; path=/"
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
      refreshRequestConfig: null,
    }
    apiClient.defaults.adapter = createMockAdapter(state)
  })

  afterEach(() => {
    apiClient.defaults.adapter = originalAdapter
    localStorage.clear()
    document.cookie = "releasetracker-csrf=; max-age=0; path=/"
    delete (window as { location?: Location }).location
    ;(window as { location: Location }).location = originalLocation
  })

  it("replays the original request after a single refresh", async () => {
    document.cookie = "releasetracker-csrf=csrf-old; path=/"

    const response = await apiClient.get("/api/protected")

    expect(response.data).toEqual({ ok: true })
    expect(state.calls[REFRESH_ENDPOINT]).toBe(1)
    expect(state.calls["/api/protected"]).toBe(2)
    expect(state.refreshRequestConfig?.url).toBe(REFRESH_ENDPOINT)
    expect(state.refreshRequestConfig?.params).toBeUndefined()
    expect(state.refreshRequestConfig?.data).toBeUndefined()
    expect(state.refreshRequestConfig?.headers["X-CSRF-Token"]).toBe("csrf-old")
    expect(state.refreshRequestConfig?.headers.Authorization).toBeUndefined()
    expect(state.refreshRequestConfig?.withCredentials).toBe(true)
    expect(localStorage.getItem("token")).toBeNull()
    expect(localStorage.getItem("refresh_token")).toBeNull()
  })

  it("clears auth state when refresh fails", async () => {
    state.refreshTokenValid = false
    document.cookie = "releasetracker-csrf=csrf-old; path=/"
    localStorage.setItem("user", "{}")

    await expect(apiClient.get("/api/protected")).rejects.toBeInstanceOf(AxiosError)

    expect(state.calls[REFRESH_ENDPOINT]).toBe(1)
    expect(state.calls["/api/protected"]).toBe(1)
    expect(localStorage.getItem("token")).toBeNull()
    expect(localStorage.getItem("refresh_token")).toBeNull()
    expect(localStorage.getItem("user")).toBeNull()
  })

  it("redirects to login when a protected request cannot refresh", async () => {
    state.refreshTokenValid = false
    document.cookie = "releasetracker-csrf=csrf-old; path=/"
    window.location.pathname = "/trackers"

    await expect(apiClient.get("/api/protected")).rejects.toBeInstanceOf(AxiosError)

    expect(window.location.pathname).toBe("/login")
  })

  it("clears auth state and redirects when the replayed protected request still returns 401", async () => {
    state.protectedRequestStillUnauthorizedAfterRefresh = true
    document.cookie = "releasetracker-csrf=csrf-old; path=/"
    localStorage.setItem("user", "{}")
    window.location.pathname = "/trackers"

    await expect(apiClient.get("/api/protected")).rejects.toBeInstanceOf(AxiosError)

    expect(state.calls[REFRESH_ENDPOINT]).toBe(1)
    expect(state.calls["/api/protected"]).toBe(2)
    expect(localStorage.getItem("token")).toBeNull()
    expect(localStorage.getItem("refresh_token")).toBeNull()
    expect(localStorage.getItem("user")).toBeNull()
    expect(window.location.pathname).toBe("/login")
  })

  it("single-flights concurrent 401 refresh attempts", async () => {
    document.cookie = "releasetracker-csrf=csrf-old; path=/"

    const responses = await Promise.all([
      apiClient.get("/api/protected"),
      apiClient.get("/api/protected"),
      apiClient.get("/api/protected"),
    ])

    responses.forEach((response) => {
      expect(response.data).toEqual({ ok: true })
    })
    expect(state.calls[REFRESH_ENDPOINT]).toBe(1)
    expect(state.calls["/api/protected"]).toBe(6)
  })

  it("recovers /api/auth/me via refresh before logout", async () => {
    document.cookie = "releasetracker-csrf=csrf-old; path=/"

    const user = await api.getCurrentUser({ suppressAuthRedirect: true })

    expect(user).toMatchObject({ username: "test" })
    expect(state.calls[REFRESH_ENDPOINT]).toBe(1)
    expect(state.calls["/api/auth/me"]).toBe(2)
    expect(localStorage.getItem("token")).toBeNull()
  })
})
