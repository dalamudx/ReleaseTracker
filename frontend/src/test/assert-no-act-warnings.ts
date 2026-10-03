import { cleanup } from "@testing-library/react"
import { afterEach, beforeEach, expect, vi, type MockInstance } from "vitest"

// Spy with the default passthrough: warnings remain visible, never suppressed.
export function assertNoActWarnings() {
  let errors: MockInstance<typeof console.error>
  beforeEach(() => {
    errors = vi.spyOn(console, "error")
  })
  afterEach(() => {
    cleanup()
    try {
      const actWarnings = errors.mock.calls.filter(([message]) =>
        String(message).includes("not wrapped in act"),
      )
      expect(actWarnings).toEqual([])
    } finally {
      errors.mockRestore()
    }
  })
}
