import { CircleAlert, RefreshCw } from "lucide-react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

interface QueryErrorStateProps {
    onRetry?: () => void
    className?: string
    compact?: boolean
}

export function QueryErrorState({ onRetry, className, compact = false }: QueryErrorStateProps) {
    const { t } = useTranslation()

    return (
        <div
            role="alert"
            className={cn(
                "flex items-start gap-3 rounded-lg border border-destructive/25 bg-destructive/5 text-foreground",
                compact ? "px-3 py-2.5" : "min-h-32 items-center justify-center p-6",
                className,
            )}
        >
            <CircleAlert className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden="true" />
            <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold">{t("common.loadFailedTitle")}</p>
                <p className="mt-0.5 text-xs text-muted-foreground">{t("common.loadFailedDescription")}</p>
            </div>
            {onRetry ? (
                <Button variant="outline" size="sm" className="shrink-0" onClick={onRetry}>
                    <RefreshCw className="size-3.5" aria-hidden="true" />
                    {t("common.retry")}
                </Button>
            ) : null}
        </div>
    )
}
