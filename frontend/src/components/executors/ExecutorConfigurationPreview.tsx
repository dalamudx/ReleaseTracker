import { useEffect, useState } from "react"
import { useTranslation } from "react-i18next"
import { RefreshCw } from "lucide-react"
import { api } from "@/api/client"
import type { ExecutorConfigurationPreview as Preview } from "@/api/types"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export function ExecutorConfigurationPreview({ executorId, payloadJson }: { executorId: number | null; payloadJson: string }) {
    const { t } = useTranslation()
    const [revision, setRevision] = useState(0)
    const requestKey = JSON.stringify([executorId, payloadJson, revision])
    const [result, setResult] = useState<{ key: string; data: Preview | null; error: boolean } | null>(null)

    useEffect(() => {
        const controller = new AbortController()
        let active = true
        async function inspect() {
            try {
                const data = await api.previewExecutorConfiguration(JSON.parse(payloadJson), executorId, controller.signal)
                if (active) setResult({ key: requestKey, data, error: false })
            } catch {
                if (active) setResult({ key: requestKey, data: null, error: true })
            }
        }
        void inspect()
        return () => { active = false; controller.abort() }
    }, [executorId, payloadJson, requestKey])

    const current = result?.key === requestKey ? result : null
    const loading = !current
    const data = current?.data
    const diff = data?.configuration_diff
    const valid = data?.mutation_performed === false && !data.comparison_error && !!diff

    return (
        <section aria-label={t("executors.review.liveDiffTitle")} className="flex min-w-0 flex-col gap-3" data-testid="executor-draft-configuration-preview">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-sm font-medium">{t("executors.review.liveDiffTitle")}</h3>
                <Button type="button" size="sm" variant="outline" disabled={loading} onClick={() => setRevision(value => value + 1)}>
                    <RefreshCw className={cn("size-3.5", loading && "animate-spin")} aria-hidden="true" />
                    {t("executors.review.refreshDiff")}
                </Button>
            </div>
            <p className="text-xs text-muted-foreground">{t("executors.review.liveDiffPolicy")}</p>
            <div aria-live="polite" className="flex min-w-0 flex-col gap-2 text-xs">
                {loading ? <p>{t("executors.review.loadingDiff")}</p> : !valid ? (
                    <p role="alert" className="text-destructive">{t(data?.comparison_error === "no_deployable_version" ? "executors.review.noVersionDiff" : "executors.review.failedDiff")}</p>
                ) : diff && (
                    <>
                        <p className="text-muted-foreground">{t("tasks.diffLegend")}</p>
                        <p>{t(`executors.rollback.dialog.scopes.${diff.scope}`, { defaultValue: t("tasks.diffScopeLimited") })}</p>
                        {diff.lines.length ? (
                            <ol className="max-h-80 overflow-y-auto rounded-md border border-border font-mono" data-testid="executor-draft-configuration-diff">
                                {diff.lines.map((line, index) => (
                                    <li key={`${line.path}:${line.operation}:${index}`} className={cn("flex gap-2 px-3 py-1.5", line.operation === "-" ? "bg-destructive/10 text-destructive" : "bg-success/10 text-success")}>
                                        <span className="shrink-0" aria-label={t(line.operation === "-" ? "executors.rollback.dialog.removed" : "executors.rollback.dialog.added")}>{line.operation}</span>
                                        <span className="min-w-0 whitespace-pre-wrap break-all">{line.path}: {line.redacted ? t("executors.rollback.dialog.hiddenValue") : line.value}</span>
                                    </li>
                                ))}
                            </ol>
                        ) : <p>{t("tasks.diffNoChanges")}</p>}
                        {diff.lines.some(line => line.redacted) && <p className="text-muted-foreground">{t("executors.rollback.dialog.secretNote")}</p>}
                        {diff.truncated && <p role="alert" className="text-warning">{t("executors.review.truncatedDiff")}</p>}
                        <p className="text-muted-foreground">{t("executors.review.checkedDiff", { time: new Date(data.checked_at).toLocaleString() })}</p>
                    </>
                )}
            </div>
        </section>
    )
}
