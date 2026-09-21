
import { describe, it, expect } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { CopyableCode } from "@/components/common/CopyableCode"
import { ActiveRowMarker } from "@/components/common/ActiveRowMarker"

describe("Extracted Common Design Components", () => {
  describe("CopyableCode", () => {
    it("renders code capsule and triggers clipboard copy", async () => {
      let copiedText = ""
      Object.assign(navigator, {
        clipboard: {
          writeText: async (text: string) => {
            copiedText = text
          },
        },
      })

      render(
        <CopyableCode
          value="sha256:1234567890abcdef"
          displayValue="1234567"
        />
      )

      expect(screen.getByText("1234567")).toBeInTheDocument()
      const copyBtn = screen.getByRole("button", { name: /copy|复制/i })
      fireEvent.click(copyBtn)

      expect(copiedText).toBe("sha256:1234567890abcdef")
    })
  })

  describe("ActiveRowMarker", () => {
    it("renders marker when active is true", () => {
      const { container } = render(<ActiveRowMarker active={true} testId="test-marker" />)
      expect(screen.getByTestId("test-marker")).toBeInTheDocument()
      expect(container.firstChild).toHaveClass("bg-primary")
    })

    it("does not render when active is false", () => {
      const { container } = render(<ActiveRowMarker active={false} testId="test-marker" />)
      expect(screen.queryByTestId("test-marker")).not.toBeInTheDocument()
      expect(container.firstChild).toBeNull()
    })
  })
})
