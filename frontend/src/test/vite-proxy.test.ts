import { describe, expect, it } from "vitest"
import viteConfig from "../../vite.config"

describe("development API proxy", () => {
  it("preserves the browser-facing Host for backend same-origin checks", async () => {
    const config = await (typeof viteConfig === "function"
      ? viteConfig({ command: "serve", mode: "development" })
      : viteConfig)
    const proxy = config.server?.proxy?.["/api"]
    expect(proxy).toBeDefined()
    if (!proxy || typeof proxy === "string") throw new Error("API proxy options missing")
    expect(proxy.target).toBe("http://localhost:8000")
    expect(proxy.changeOrigin).toBe(false)
    expect(proxy.rewrite).toBeUndefined()
  })
})
