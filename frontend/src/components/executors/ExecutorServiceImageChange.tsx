import type { ReactNode } from "react"

/** Shared by grouped-target review (including Kubernetes) and execution history. */
export function ExecutorServiceImageChange({
    service,
    sourceImage,
    targetImage,
    sourceLabel,
    targetLabel,
    sourceTestId,
    targetTestId,
    children,
}: {
    service: string
    sourceImage: string
    targetImage: string
    sourceLabel?: string
    targetLabel?: string
    sourceTestId?: string
    targetTestId?: string
    children?: ReactNode
}) {
    return (
        <div role="group" aria-label={service} className="min-w-0 rounded-lg border border-border/60 bg-background px-3 py-2">
            <div className="break-all text-sm font-medium text-foreground">{service}</div>
            <div className="mt-2 grid min-w-0 gap-2 text-xs md:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] md:items-center">
                <div className="min-w-0 space-y-1">
                    {sourceLabel ? <div className="text-muted-foreground">{sourceLabel}</div> : null}
                    <div data-testid={sourceTestId} className="break-all rounded-md bg-muted px-2 py-1 font-mono text-muted-foreground">{sourceImage}</div>
                </div>
                <div aria-hidden className="text-center text-muted-foreground">→</div>
                <div className="min-w-0 space-y-1">
                    {targetLabel ? <div className="text-muted-foreground">{targetLabel}</div> : null}
                    <div data-testid={targetTestId} className="break-all rounded-md bg-primary/10 px-2 py-1 font-mono text-primary">{targetImage}</div>
                </div>
            </div>
            {children}
        </div>
    )
}
