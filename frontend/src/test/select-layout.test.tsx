import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"

describe("Select layout", () => {
  it("uses full-width triggers so dense form columns cannot overlap", () => {
    render(
      <Select>
        <SelectTrigger data-testid="select-trigger">
          <SelectValue placeholder="Very long placeholder text" />
        </SelectTrigger>
      </Select>,
    )

    const trigger = screen.getByTestId("select-trigger")
    expect(trigger).toHaveClass("w-full")
    expect(trigger).toHaveClass("min-w-0")
    expect(trigger).not.toHaveClass("w-fit")
  })

  it("uses popper-positioned content above sheet overlays", () => {
    render(
      <Select defaultOpen value="stable">
        <SelectTrigger>
          <SelectValue />
        </SelectTrigger>
        <SelectContent data-testid="select-content">
          <SelectItem value="stable">stable</SelectItem>
        </SelectContent>
      </Select>,
    )

    const content = screen.getByTestId("select-content")
    expect(content).toHaveClass("z-[70]")

    const viewport = content.querySelector("[data-radix-select-viewport]")
    expect(viewport).toHaveClass("min-w-[var(--radix-select-trigger-width)]")
  })
})
