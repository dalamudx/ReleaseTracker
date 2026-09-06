import { afterEach, describe, expect, it } from "vitest"
import { appBasePath, appPath, assetPath } from "@/lib/base-path"

afterEach(() => {
  document.querySelector("base")?.remove()
})

describe("application base path", () => {
  it("keeps root deployments unchanged", () => {
    expect(appBasePath()).toBe("/")
    expect(appPath("/api/releases")).toBe("/api/releases")
  })

  it("uses the runtime base tag for routes, APIs, and assets", () => {
    document.head.insertAdjacentHTML("afterbegin", '<base href="/releasetracker/">')

    expect(appBasePath()).toBe("/releasetracker")
    expect(appPath("/login")).toBe("/releasetracker/login")
    expect(assetPath("logo.svg")).toBe("/releasetracker/logo.svg")
  })
})
