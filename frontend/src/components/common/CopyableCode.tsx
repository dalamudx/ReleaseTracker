import { useState } from "react"
import { Check, Copy } from "lucide-react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
    Tooltip,
    TooltipContent,
    TooltipTrigger,
} from "@/components/ui/tooltip"

interface CopyableCodeProps {
    value: string
    displayValue?: string
    className?: string
    codeClassName?: string
    truncate?: boolean
    title?: string
    showCopyButton?: boolean
}

/**
 * 结构化等宽代码胶囊，支持一键复制与复制成功状态反馈
 */
export function CopyableCode({
    value,
    displayValue,
    className,
    codeClassName,
    truncate = true,
    title,
    showCopyButton = true,
}: CopyableCodeProps) {
    const { t } = useTranslation()
    const [copied, setCopied] = useState(false)

    const handleCopy = async (e: React.MouseEvent) => {
        e.stopPropagation()
        try {
            await navigator.clipboard.writeText(value)
            setCopied(true)
            toast.success(t("common.copied", { defaultValue: "已复制" }))
            setTimeout(() => setCopied(false), 1500)
        } catch {
            // fallback
        }
    }

    const displayText = displayValue ?? value

    return (
        <span
            className={cn(
                "inline-flex items-center gap-1 max-w-full rounded border border-border/60 bg-muted/40 px-1.5 py-0.5 font-mono text-xs text-foreground/90 transition-colors",
                className
            )}
            title={title ?? value}
        >
            <code className={cn(truncate && "truncate", codeClassName)}>
                {displayText}
            </code>
            {showCopyButton ? (
                <Tooltip>
                    <TooltipTrigger asChild>
                        <Button
                            variant="ghost"
                            size="icon"
                            className="h-4 w-4 shrink-0 text-muted-foreground hover:text-foreground p-0"
                            onClick={handleCopy}
                            aria-label={t("common.copy", { defaultValue: "复制" })}
                        >
                            {copied ? (
                                <Check className="h-3 w-3 text-success animate-in zoom-in-50 duration-150" />
                            ) : (
                                <Copy className="h-3 w-3" />
                            )}
                        </Button>
                    </TooltipTrigger>
                    <TooltipContent side="top" className="text-xs">
                        {copied
                            ? t("common.copied", { defaultValue: "已复制" })
                            : t("common.copy", { defaultValue: "复制" })}
                    </TooltipContent>
                </Tooltip>
            ) : null}
        </span>
    )
}
