import { describe, expect, it } from "vitest"

describe("test runner", () => {
  it("runs in jsdom", () => {
    const element = document.createElement("div")
    element.textContent = "ok"
    expect(element.textContent).toBe("ok")
  })
})
