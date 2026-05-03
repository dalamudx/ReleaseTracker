import * as React from "react"

import { cn } from "@/lib/utils"

function Textarea({ className, onInput, ...props }: React.ComponentProps<"textarea">) {
  const textareaRef = React.useRef<HTMLTextAreaElement | null>(null)

  const resizeToContent = React.useCallback((textarea: HTMLTextAreaElement) => {
    textarea.style.height = "auto"
    textarea.style.height = `${textarea.scrollHeight}px`
  }, [])

  React.useLayoutEffect(() => {
    if (textareaRef.current) {
      resizeToContent(textareaRef.current)
    }
  }, [props.value, props.defaultValue, resizeToContent])

  return (
    <textarea
      data-slot="textarea"
      ref={textareaRef}
      className={cn(
        "flex min-h-16 w-full min-w-0 max-w-full resize-y overflow-hidden rounded-md border border-input bg-transparent px-3 py-2 text-base shadow-xs transition-[color,box-shadow] outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 md:text-sm dark:bg-input/30 dark:aria-invalid:ring-destructive/40",
        className
      )}
      onInput={(event) => {
        resizeToContent(event.currentTarget)
        onInput?.(event)
      }}
      {...props}
    />
  )
}

export { Textarea }
