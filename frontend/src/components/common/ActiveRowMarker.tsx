import { cn } from "@/lib/utils"

interface ActiveRowMarkerProps {
    className?: string
    active?: boolean
    testId?: string
}

/**
 * 统一条目选中态左侧主色指示条组件，与 Trackers / Executors / History 视觉规范统一
 */
export function ActiveRowMarker({
    className,
    active = true,
    testId,
}: ActiveRowMarkerProps) {
    if (!active) return null

    return (
        <span
            aria-hidden="true"
            data-testid={testId}
            className={cn(
                "pointer-events-none absolute left-0 top-1/2 h-7 w-1 -translate-y-1/2 rounded-r-full bg-primary shadow-xs transition-all",
                className
            )}
        />
    )
}
