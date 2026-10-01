import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import { Download, Loader2 } from "lucide-react"
import { toast } from "sonner"
import i18nInstance from "@/i18n/config"
import backupEnglish from "@/i18n/locales/backups.en.json"
import backupChinese from "@/i18n/locales/backups.zh.json"
import { backupApi } from "@/api/backups"
import { Button } from "@/components/ui/button"
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card"
import { AlertDialog, AlertDialogTrigger, AlertDialogContent, AlertDialogHeader, AlertDialogTitle, AlertDialogDescription, AlertDialogFooter, AlertDialogAction, AlertDialogCancel } from "@/components/ui/alert-dialog"

// This settings module is route-split: detailed operational copy does not need
// to inflate every page's initial translation payload.
i18nInstance.addResourceBundle("en", "backups", backupEnglish)
i18nInstance.addResourceBundle("zh", "backups", backupChinese)

export function BackupSettings() {
    const { t, i18n } = useTranslation("backups")
    const client = useQueryClient()
    const [confirming, setConfirming] = useState(false)
    const query = useQuery({ queryKey: ["instance-backups"], queryFn: backupApi.list, refetchInterval: 30000 })
    const create = useMutation({
        mutationFn: backupApi.create,
        onSuccess: () => {
            setConfirming(false)
            toast.success(t("created"))
            void client.invalidateQueries({ queryKey: ["instance-backups"] })
        },
        onError: () => toast.error(t("createFailed")),
    })
    const download = useMutation({
        mutationFn: async (name: string) => {
            const blob = await backupApi.download(name)
            const url = URL.createObjectURL(blob)
            const link = document.createElement("a")
            link.href = url
            link.download = name
            document.body.appendChild(link)
            link.click()
            link.remove()
            setTimeout(() => URL.revokeObjectURL(url), 1000)
        },
        onError: () => toast.error(t("downloadFailed")),
    })
    return (
        <Card>
            <CardHeader>
                <CardTitle>{t("title")}</CardTitle>
                <CardDescription>{t("description")}</CardDescription>
            </CardHeader>
            <CardContent className="flex min-w-0 flex-col gap-6">
                <p className="text-sm text-warning">{t("sensitive")}</p>
                {query.data && (query.data.consecutive_failures || query.data.overdue) ? (
                    <div role="alert" className="flex flex-col gap-1 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">
                        {query.data.consecutive_failures ? (
                            <p className="font-medium text-destructive">
                                {t("failing", {
                                    count: query.data.consecutive_failures,
                                    reason: t(`errors.${query.data.last_error_code ?? "failed"}`, { defaultValue: t("errors.failed") }),
                                })}
                            </p>
                        ) : null}
                        {query.data.overdue ? <p className="text-destructive">{t("overdue")}</p> : null}
                        {query.data.last_success_at ? (
                            <p className="text-muted-foreground tabular-nums">{t("lastSuccess", { time: new Date(query.data.last_success_at * 1000).toLocaleString(i18n.language) })}</p>
                        ) : null}
                    </div>
                ) : null}
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex flex-col gap-1 text-sm text-muted-foreground">
                        {query.data ? <>
                            <p>{query.data.interval_hours ? t("schedule", { hours: query.data.interval_hours }) : t("manualOnly")}</p>
                            <p>{t("retention", { count: query.data.retention })}</p>
                        </> : null}
                    </div>
                    <AlertDialog open={confirming} onOpenChange={open => { if (!create.isPending) setConfirming(open) }}>
                        <AlertDialogTrigger asChild>
                            <Button disabled={create.isPending || query.isPending || query.isError || query.data?.running}>{t("create")}</Button>
                        </AlertDialogTrigger>
                        <AlertDialogContent>
                            <AlertDialogHeader>
                                <AlertDialogTitle>{t("confirmTitle")}</AlertDialogTitle>
                                <AlertDialogDescription>{t("sensitive")}</AlertDialogDescription>
                            </AlertDialogHeader>
                            <AlertDialogFooter>
                                <AlertDialogCancel disabled={create.isPending}>{t("common.cancel", { ns: "translation" })}</AlertDialogCancel>
                                <AlertDialogAction disabled={create.isPending} onClick={event => { event.preventDefault(); create.mutate() }}>
                                    {create.isPending ? <Loader2 className="size-4 motion-safe:animate-spin" aria-hidden="true" /> : null}
                                    {create.isPending ? t("creating") : t("confirm")}
                                </AlertDialogAction>
                            </AlertDialogFooter>
                        </AlertDialogContent>
                    </AlertDialog>
                </div>
                {query.isPending ? <p role="status">{t("common.loading", { ns: "translation" })}</p> : query.isError ? (
                    <div className="flex flex-wrap items-center gap-3">
                        <p role="alert" className="text-sm text-destructive">{t("loadFailed")}</p>
                        <Button variant="outline" disabled={query.isFetching} onClick={() => void query.refetch()}>{t("common.refresh", { ns: "translation" })}</Button>
                    </div>
                ) : !query.data?.items.length ? <p className="text-sm text-muted-foreground">{t("empty")}</p> : (
                    <ul className="flex min-w-0 flex-col divide-y">
                        {query.data.items.map(item => (
                            <li key={item.name} className="flex min-w-0 flex-wrap items-center justify-between gap-3 py-3">
                                <div className="flex min-w-0 flex-col gap-1">
                                    <p className="break-all font-mono text-xs">{item.name}</p>
                                    <p className="text-sm text-muted-foreground tabular-nums">{new Date(item.created_at * 1000).toLocaleString(i18n.language)} · {(item.size / 1024 / 1024).toFixed(1)} MB</p>
                                </div>
                                <Button variant="outline" size="sm" disabled={download.isPending} aria-label={`${t("download")} ${item.name}`} onClick={() => download.mutate(item.name)}>
                                    {download.isPending && download.variables === item.name ? <Loader2 className="size-4 motion-safe:animate-spin" aria-hidden="true" /> : <Download className="size-4" aria-hidden="true" />}
                                    {t("download")}
                                </Button>
                            </li>
                        ))}
                    </ul>
                )}
                <section aria-labelledby="backup-restore-title" className="flex flex-col gap-2">
                    <h3 id="backup-restore-title" className="font-medium">{t("restoreTitle")}</h3>
                    <p className="text-sm text-muted-foreground">{t("restoreDescription")}</p>
                    <code className="break-all rounded-md bg-muted p-3 text-xs">python -m releasetracker.cli restore-backup BACKUP.zip --destination /restore/new-data --confirm-stopped</code>
                    <p className="text-sm text-muted-foreground">{t("configuration")}</p>
                </section>
            </CardContent>
        </Card>
    )
}
