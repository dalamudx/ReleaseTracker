import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import { Download, Loader2, Trash2 } from "lucide-react"
import { toast } from "sonner"
import i18nInstance from "@/i18n/config"
import backupEnglish from "@/i18n/locales/backups.en.json"
import backupChinese from "@/i18n/locales/backups.zh.json"
import { backupApi } from "@/api/backups"
import { BackupRestoreDialog, OnlineRestoreProgress } from "./OnlineRestoreControls"
import { useRestoreReceipt } from "@/hooks/restore-receipt"
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
    const [restoring, setRestoring] = useState<string | null>(null)
    const receipt = useRestoreReceipt()
    const [deleting, setDeleting] = useState<string | null>(null)
    const [deleteError, setDeleteError] = useState<string | null>(null)
    const query = useQuery({ queryKey: ["instance-backups"], queryFn: backupApi.list, refetchInterval: 30000, enabled: !receipt })
    const create = useMutation({
        mutationFn: backupApi.create,
        onSuccess: () => {
            setConfirming(false)
            toast.success(t("created"))
            void client.invalidateQueries({ queryKey: ["instance-backups"] })
        },
        onError: () => toast.error(t("createFailed")),
    })
    const remove = useMutation({
        mutationFn: backupApi.delete,
        onSuccess: () => {
            setDeleting(null)
            setDeleteError(null)
            toast.success(t("deleted"))
            void client.invalidateQueries({ queryKey: ["instance-backups"] })
        },
        onError: (error: unknown) => {
            const code = (error as {response?: {data?: {detail?: unknown}}})?.response?.data?.detail
            const key = typeof code === "string" && ["last_local_backup", "backup_busy", "backup_in_use", "backup_not_found"].includes(code) ? `deleteErrors.${code}` : "deleteFailed"
            setDeleteError(key)
            toast.error(t(key))
            void client.invalidateQueries({ queryKey: ["instance-backups"] })
        },
    })
    const review = useMutation({mutationFn: backupApi.reviewRestore, onSuccess: () => { toast.success(t("restoreReviewed")); void client.invalidateQueries({queryKey:["instance-backups"]}) }, onError: () => toast.error(t("restoreErrors.restore_failed"))})
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
    if (receipt) return <Card><CardHeader><CardTitle>{t("title")}</CardTitle></CardHeader><CardContent><OnlineRestoreProgress /></CardContent></Card>
    return (
        <Card>
            <CardHeader>
                <CardTitle>{t("title")}</CardTitle>
                <CardDescription>{t("description")}</CardDescription>
            </CardHeader>
            <CardContent className="flex min-w-0 flex-col gap-6">
                <OnlineRestoreProgress />
                <p className="text-sm text-warning">{t("sensitive")}</p>
                {query.data?.restore_review_required && <section className="flex flex-col gap-2 rounded-md border p-3 text-sm">
                    <p>{t("restoreReviewWarning")}</p>
                    <AlertDialog><AlertDialogTrigger asChild><Button variant="outline" disabled={review.isPending}>{t("restoreReview")}</Button></AlertDialogTrigger><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>{t("restoreReview")}</AlertDialogTitle><AlertDialogDescription>{t("restoreReviewWarning")}</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>{t("common.cancel",{ns:"translation"})}</AlertDialogCancel><AlertDialogAction className="h-auto min-h-11 whitespace-normal" disabled={review.isPending} onClick={() => review.mutate()}>{t("restoreReviewConfirm")}</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
                </section>}
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
                            {query.data.directory && <p className="break-all">{t("directory", { path: query.data.directory })}</p>}
                            <p>{query.data.interval_hours ? t("schedule", { hours: query.data.interval_hours }) : t("manualOnly")}</p>
                            <p>{t("retention", { count: query.data.retention })}</p>
                            {!!query.data.daily_retention && <p>{t("dailyRetention", { count: query.data.daily_retention })}</p>}
                            {!!query.data.weekly_retention && <p>{t("weeklyRetention", { count: query.data.weekly_retention })}</p>}
                            <p>{t("usage", { count: query.data.items.length, size: ((query.data.total_size ?? query.data.items.reduce((sum, item) => sum + item.size, 0)) / 1024 / 1024).toFixed(1) })}</p>
                            <p>{t("minimumLocal")}</p>
                        </> : null}
                    </div>
                    <AlertDialog open={confirming} onOpenChange={open => { if (!create.isPending) setConfirming(open) }}>
                        <AlertDialogTrigger asChild>
                            <Button disabled={!!receipt || create.isPending || remove.isPending || query.isPending || query.isError || query.data?.running}>{t("create")}</Button>
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
                                <div className="flex flex-wrap items-center gap-2">
                                <Button variant="outline" size="sm" disabled={!!receipt || download.isPending || query.data?.running || remove.isPending} aria-label={`${t("download")} ${item.name}`} onClick={() => download.mutate(item.name)}>
                                    {download.isPending && download.variables === item.name ? <Loader2 className="size-4 motion-safe:animate-spin" aria-hidden="true" /> : <Download className="size-4" aria-hidden="true" />}
                                    {t("download")}
                                </Button>
                                <Button variant="outline" size="sm" disabled={!!receipt || remove.isPending || create.isPending || query.data?.running || item.in_use || query.data.items.length <= (query.data.minimum_local_archives ?? 1) || (download.isPending && download.variables === item.name)} aria-label={`${t("delete")} ${item.name}`} onClick={() => { setDeleting(item.name); setDeleteError(null) }}>
                                    <Trash2 className="size-4" aria-hidden="true" />{t("delete")}
                                </Button>
                                {query.data?.online_restore_available && <Button variant="outline" size="sm" disabled={!!receipt || query.data.running || create.isPending || remove.isPending || download.isPending || item.in_use} aria-label={`${t("onlineRestore")} ${item.name}`} onClick={() => setRestoring(item.name)}>{t("onlineRestore")}</Button>}
                                </div>
                            </li>
                        ))}
                    </ul>
                )}
                {restoring && <BackupRestoreDialog key={restoring} name={restoring} onClose={() => setRestoring(null)} />}
                {query.data?.safety_backup && <section className="flex flex-col gap-2 rounded-md border p-3 text-sm"><h3 className="font-medium">{t("restoreSafetyTitle")}</h3><p>{t("restoreSafetyRetention")}</p><Button variant="outline" size="sm" disabled={!!receipt || download.isPending} onClick={async () => {
                    try { const blob = await backupApi.downloadSafety(); const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href=url; link.download="before-online-restore.zip"; link.click(); setTimeout(() => URL.revokeObjectURL(url),1000) } catch { toast.error(t("downloadFailed")) }
                }}>{t("restoreSafetyDownload")}</Button></section>}
                <AlertDialog open={deleting !== null} onOpenChange={open => { if (!open && !remove.isPending) { setDeleting(null); setDeleteError(null) } }}>
                    <AlertDialogContent>
                        <AlertDialogHeader>
                            <AlertDialogTitle>{t("deleteTitle")}</AlertDialogTitle>
                            <AlertDialogDescription>{t("deleteDescription", { name: deleting })}</AlertDialogDescription>
                        </AlertDialogHeader>
                        {deleteError && <p role="alert" className="text-sm text-destructive">{t(deleteError)}</p>}
                        <AlertDialogFooter>
                            <AlertDialogCancel disabled={remove.isPending}>{t("common.cancel", { ns: "translation" })}</AlertDialogCancel>
                            {deleteError && <Button type="button" variant="outline" disabled={query.isFetching || remove.isPending} onClick={async () => {
                                const result = await query.refetch()
                                if (!result.isError && result.data?.items.some(item => item.name === deleting && !item.in_use) && result.data.items.length > (result.data.minimum_local_archives ?? 1)) setDeleteError(null)
                            }}>{t("refreshList")}</Button>}
                            <AlertDialogAction disabled={remove.isPending || query.isFetching || query.data?.running || !query.data?.items.some(item => item.name === deleting && !item.in_use) || (query.data?.items.length ?? 0) <= (query.data?.minimum_local_archives ?? 1)} onClick={event => { event.preventDefault(); if (deleting) remove.mutate(deleting) }}>
                                {remove.isPending && <Loader2 className="size-4 motion-safe:animate-spin" aria-hidden="true" />}
                                {remove.isPending ? t("deleting") : t("confirmDelete")}
                            </AlertDialogAction>
                        </AlertDialogFooter>
                    </AlertDialogContent>
                </AlertDialog>
                <section aria-labelledby="backup-restore-title" className="flex flex-col gap-2">
                    <h3 id="backup-restore-title" className="font-medium">{t("restoreTitle")}</h3>
                    <p className="text-sm text-muted-foreground">{t("restoreDescription")}</p>
                    <p className="text-sm text-muted-foreground">{t("onlineRestoreExplanation")}</p>
                    <code className="break-all rounded-md bg-muted p-3 text-xs">python -m releasetracker.cli restore-backup BACKUP.zip --destination /restore/new-data --confirm-stopped</code>
                    <p className="text-sm text-muted-foreground">{t("configuration")}</p>
                </section>
            </CardContent>
        </Card>
    )
}
