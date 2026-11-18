import { useId, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import { Database, Download, Loader2, Trash2, Upload } from "lucide-react"
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
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

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
    const uploadId = useId()
    const uploadInput = useRef<HTMLInputElement>(null)
    const [archive, setArchive] = useState<File | null>(null)
    const [uploadPercent, setUploadPercent] = useState(0)
    const [deleting, setDeleting] = useState<string | null>(null)
    const [deleteError, setDeleteError] = useState<string | null>(null)
    const query = useQuery({ queryKey: ["instance-backups"], queryFn: backupApi.list, refetchInterval: 30000, enabled: !receipt })
    const upload = useMutation({
        mutationFn: (file: File) => backupApi.import(file, setUploadPercent),
        onSuccess: () => {
            setArchive(null)
            if (uploadInput.current) uploadInput.current.value = ""
            toast.success(t("imported"))
            void client.invalidateQueries({ queryKey: ["instance-backups"] })
        },
        onError: () => toast.error(t("importFailed")),
    })
    const importCode = (upload.error as { response?: { status?: number; data?: { detail?: string } } } | null)?.response
    const importErrorKey = importCode?.status === 413 ? "importErrors.backup_import_too_large"
        : importCode?.data?.detail && ["backup_busy", "backup_import_invalid", "backup_import_timeout", "backup_import_storage_error"].includes(importCode.data.detail)
            ? `importErrors.${importCode.data.detail}` : "importFailed"
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
    if (receipt) return <Card className="border-border/60 bg-card/80 shadow-xs"><CardHeader className="border-b border-border/40 pb-4"><CardTitle className="flex items-center gap-2 text-base font-semibold"><Database className="h-4 w-4 text-primary" />{t("title")}</CardTitle></CardHeader><CardContent className="pt-6"><OnlineRestoreProgress /></CardContent></Card>
    return (
        <Card className="border-border/60 bg-card/80 shadow-xs">
            <CardHeader className="border-b border-border/40 pb-4">
                <CardTitle className="flex items-center gap-2 text-base font-semibold">
                    <Database className="h-4 w-4 text-primary" />
                    {t("title")}
                </CardTitle>
                <CardDescription className="text-xs">{t("description")}</CardDescription>
            </CardHeader>
            <CardContent className="flex min-w-0 flex-col gap-6 pt-6">
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
                    <div className="min-w-0 space-y-1 text-sm">
                        {query.data && <>
                            <p className="font-medium tabular-nums">{t("usage", { count: query.data.items.length, size: ((query.data.total_size ?? query.data.items.reduce((sum, item) => sum + item.size, 0)) / 1024 / 1024).toFixed(1) })}</p>
                            <p className="text-muted-foreground">{query.data.interval_hours ? t("schedule", { hours: query.data.interval_hours }) : t("manualOnly")}</p>
                        </>}
                    </div>
                    <AlertDialog open={confirming} onOpenChange={open => { if (!create.isPending) setConfirming(open) }}>
                        <AlertDialogTrigger asChild>
                            <Button disabled={upload.isPending || !!receipt || create.isPending || remove.isPending || query.isPending || query.isError || query.data?.running}>{t("create")}</Button>
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
                            <li key={item.name} className="grid min-w-0 gap-2 py-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-center">
                                <div className="min-w-0 space-y-1">
                                    <p className="text-sm font-medium tabular-nums">{new Date(item.created_at * 1000).toLocaleString(i18n.language)} · {(item.size / 1024 / 1024).toFixed(1)} MB</p>
                                    <p className="break-all font-mono text-xs text-muted-foreground">{item.name}</p>
                                </div>
                                <div className="flex flex-wrap items-center gap-1">
                                    <Button variant="outline" size="sm" disabled={upload.isPending || download.isPending || query.data?.running || remove.isPending} aria-label={`${t("download")} ${item.name}`} onClick={() => download.mutate(item.name)}>
                                        {download.isPending && download.variables === item.name ? <Loader2 className="size-4 motion-safe:animate-spin" aria-hidden="true" /> : <Download className="size-4" aria-hidden="true" />}
                                        {t("download")}
                                    </Button>
                                    {query.data?.online_restore_available && <Button variant="ghost" size="sm" disabled={upload.isPending || query.data.running || create.isPending || remove.isPending || download.isPending || item.in_use} aria-label={`${t("onlineRestore")} ${item.name}`} onClick={() => setRestoring(item.name)}>{t("onlineRestore")}</Button>}
                                    <Button variant="ghost" size="sm" className="text-muted-foreground hover:text-destructive" disabled={upload.isPending || remove.isPending || create.isPending || query.data?.running || item.in_use || query.data.items.length <= (query.data.minimum_local_archives ?? 1) || (download.isPending && download.variables === item.name)} aria-label={`${t("delete")} ${item.name}`} title={t("delete")} onClick={() => { setDeleting(item.name); setDeleteError(null) }}>
                                        <Trash2 className="size-4" aria-hidden="true" />
                                    </Button>
                                </div>
                            </li>
                        ))}
                    </ul>
                )}
                {restoring && <BackupRestoreDialog key={restoring} name={restoring} onClose={() => setRestoring(null)} />}
                <form className="flex min-w-0 flex-col gap-3 border-t pt-4" onSubmit={event => {
                    event.preventDefault()
                    if (archive) { setUploadPercent(0); upload.mutate(archive) }
                }}>
                    <h3 className="font-medium">{t("uploadRestoreTitle")}</h3>
                    <Label className="sr-only" htmlFor={uploadId}>{t("importFile")}</Label>
                    <p id={`${uploadId}-hint`} className="text-sm text-muted-foreground">{t("importDescription")}</p>
                    <div className="flex min-w-0 flex-col items-start gap-3">
                        <div className="relative max-w-full">
                            <Input ref={uploadInput} id={uploadId} type="file" accept=".zip,application/zip"
                                className="peer absolute inset-0 z-10 h-full w-full cursor-pointer opacity-0 file:cursor-pointer"
                                aria-describedby={`${uploadId}-hint ${uploadId}-name`}
                                disabled={upload.isPending || create.isPending || remove.isPending || query.data?.running || query.isPending || query.isError}
                                onChange={event => { setArchive(event.target.files?.[0] ?? null); upload.reset() }} />
                            <span aria-hidden="true" className="inline-flex min-h-9 items-center rounded-md border border-input px-3 text-sm font-medium peer-hover:bg-muted peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-disabled:opacity-50">{t("chooseZip")}</span>
                        </div>
                        <span id={`${uploadId}-name`} aria-live="polite" className="max-w-full break-all text-sm text-muted-foreground empty:sr-only">{archive?.name}</span>
                        <Button type="submit" variant="outline" disabled={!archive || upload.isPending || create.isPending || remove.isPending || query.data?.running || query.isPending || query.isError}>
                            {upload.isPending ? <Loader2 className="size-4 motion-safe:animate-spin" aria-hidden="true" /> : <Upload className="size-4" aria-hidden="true" />}
                            {upload.isPending ? t(uploadPercent < 100 ? "importUploading" : "importValidating", { percent: uploadPercent }) : t("import")}
                        </Button>
                    </div>
                    {upload.isPending && <p role="status" className="sr-only">{t(uploadPercent < 100 ? "importUploading" : "importValidating", { percent: uploadPercent })}</p>}
                    {upload.isError && <p role="alert" className="text-sm text-destructive">{t(importErrorKey)}</p>}
                </form>
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
                <details className="border-t pt-4 text-sm">
                    <summary className="cursor-pointer font-medium">{t("restoreTitle")}</summary>
                    <p className="my-2 text-muted-foreground">{t("restoreDescription")}</p>
                    <code className="block break-all rounded-md bg-muted p-3 text-xs">python -m releasetracker.cli restore-backup BACKUP.zip --destination /restore/new-data --confirm-stopped</code>
                </details>
            </CardContent>
        </Card>
    )
}
