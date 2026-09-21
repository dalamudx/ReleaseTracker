import { render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import { ReadinessSummary } from "@/components/executors/ReadinessSummary"

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key: string, options?: { defaultValue?: string; count?: number }) => options?.defaultValue ?? (key === "readiness.elapsed" ? `${options?.count}s elapsed` : key) }) }))

describe("readiness diagnostic compatibility", () => {
  it.each([undefined, null, false, "healthy", {}, { services: [] }])("does not invent readiness for legacy or missing data: %j", result => {
    const { container } = render(<ReadinessSummary result={result} />)
    expect(container).toBeEmptyDOMElement()
  })

  it("keeps per-service runtime evidence separate from aggregate health", () => {
    render(<ReadinessSummary result={{ outcome: "healthy", elapsed_seconds: 12.4, services: [
      { service: "service-a", status: "healthy", method: "runtime_state", message: "Running; no healthcheck configured" },
      { service: "service-b", status: "healthy", method: "runtime_native", message: "Native healthcheck passed" },
    ] }} />)
    expect(screen.getAllByRole("listitem")).toHaveLength(2)
    expect(screen.getByText(/service-a:/)).toHaveTextContent("runtime_state — Running; no healthcheck configured")
    expect(screen.getByText(/service-b:/)).toHaveTextContent("runtime_native — Native healthcheck passed")
    expect(screen.getByText(/12s elapsed/)).toBeInTheDocument()
    expect(screen.queryByText(/business.*healthy|application.*healthy/i)).not.toBeInTheDocument()
    expect(screen.queryByRole("status")).not.toBeInTheDocument()
  })

  it("announces pending without a success claim and tolerates incomplete observations", () => {
    render(<ReadinessSummary result={{ outcome: "pending", elapsed_seconds: Infinity, services: [null, false, "bad", {}] }} />)
    expect(screen.getByRole("status")).toHaveTextContent("pending")
    expect(screen.getByRole("status")).not.toHaveTextContent(/healthy|Infinity/)
    expect(screen.getAllByRole("listitem")).toHaveLength(1)
  })

  it("preserves readable unknown outcomes and error messages", () => {
    render(<ReadinessSummary result={{ outcome: "future_outcome", services: [{ service: "service-a", status: "unknown", message: "Runtime observation unavailable" }] }} />)
    expect(screen.getByText(/future_outcome/)).toBeInTheDocument()
    expect(screen.getByRole("listitem")).toHaveTextContent("unknown — Runtime observation unavailable")
  })
})
