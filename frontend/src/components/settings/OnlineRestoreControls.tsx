import { useState } from "react"
import { useRestoreReceipt, saveReceipt, restoreError } from "@/hooks/restore-receipt"
import { useMutation, useQuery } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import { backupApi } from "@/api/backups"
import { appPath } from "@/lib/base-path"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Checkbox } from "@/components/ui/checkbox"
import { AlertDialog, AlertDialogContent, AlertDialogHeader, AlertDialogTitle, AlertDialogDescription, AlertDialogFooter, AlertDialogCancel, AlertDialogAction } from "@/components/ui/alert-dialog"

import i18nInstance from "@/i18n/config"
import backupEnglish from "@/i18n/locales/backups.en.json"
import backupChinese from "@/i18n/locales/backups.zh.json"
i18nInstance.addResourceBundle("en", "backups", backupEnglish)
i18nInstance.addResourceBundle("zh", "backups", backupChinese)


export function BackupRestoreDialog({ name, onClose }: { name: string; onClose: () => void }) {
    const { t, i18n } = useTranslation("backups")
    const [confirmation, setConfirmation] = useState("")
    const [acknowledged, setAcknowledged] = useState(false)
    const preview = useQuery({ queryKey: ["instance-restore-plan", name], queryFn: () => backupApi.restorePlan(name), retry: false, refetchOnWindowFocus: false })
    const restore = useMutation({ mutationFn: () => backupApi.restore(name, preview.data!), onSuccess: receipt => { saveReceipt(receipt); onClose() } })
    const cancel = () => { void backupApi.cancelPlan(name).catch(() => {}); onClose() }
    return <AlertDialog open onOpenChange={open => { if (!open && !restore.isPending) cancel() }}>
        <AlertDialogContent className="max-h-[90dvh] overflow-y-auto [&>*]:min-w-0">
            <AlertDialogHeader>
                <AlertDialogTitle>{t("onlineRestoreTitle")}</AlertDialogTitle>
                <AlertDialogDescription>{t("onlineRestoreWarning")}</AlertDialogDescription>
            </AlertDialogHeader>
            <p className="break-all font-mono text-xs">{name}</p>
            {preview.isFetching ? <p role="status">{t("restorePreflight")}</p> : preview.isError ? <p role="alert" className="text-sm text-destructive">{t(restoreError(preview.error), { defaultValue: t("restoreErrors.restore_failed") })}</p> : preview.data ? <div className="flex flex-col gap-2 text-sm">
                <p>{t("restorePoint", { time: typeof preview.data.created_at === "number" ? new Date(preview.data.created_at * 1000).toLocaleString(i18n.language) : preview.data.created_at ?? "—", version: preview.data.app_version ?? "—" })}</p>
                <p className="break-all font-mono text-xs">{t("restoreFingerprint", { fingerprint: preview.data.fingerprint })}</p>
                <p>{t("restoreSafetyExplanation")}</p>
                <Label htmlFor="restore-name-confirmation">{t("restoreNameConfirmation")}</Label>
                <Input id="restore-name-confirmation" value={confirmation} onChange={event => setConfirmation(event.target.value)} autoComplete="off" disabled={restore.isPending} />
                <div className="flex items-start gap-2">
                    <Checkbox id="restore-loss-acknowledgement" checked={acknowledged} onCheckedChange={checked => setAcknowledged(checked === true)} disabled={restore.isPending} />
                    <Label htmlFor="restore-loss-acknowledgement" className="leading-relaxed">{t("restoreLossAcknowledgement")}</Label>
                </div>
            </div> : null}
            {restore.isError && <p role="alert" className="text-sm text-destructive">{t(restoreError(restore.error), { defaultValue: t("restoreErrors.restore_failed") })}</p>}
            <AlertDialogFooter className="flex-wrap">
                <AlertDialogCancel disabled={restore.isPending} onClick={event => { event.preventDefault(); cancel() }}>{t("common.cancel", { ns: "translation" })}</AlertDialogCancel>
                <Button className="h-auto min-h-11 whitespace-normal" variant="outline" disabled={preview.isFetching || restore.isPending} onClick={() => { setAcknowledged(false); void preview.refetch() }}>{t("restoreRefreshPlan")}</Button>
                <AlertDialogAction className="h-auto min-h-11 whitespace-normal" disabled={!preview.data || preview.isFetching || preview.isError || restore.isPending || confirmation !== name || !acknowledged} onClick={event => { event.preventDefault(); restore.mutate() }}>{restore.isPending ? t("restoreSubmitting") : t("restoreConfirm")}</AlertDialogAction>
            </AlertDialogFooter>
        </AlertDialogContent>
    </AlertDialog>
}

export function OnlineRestoreProgress() {
    const receipt = useRestoreReceipt()
    const { t } = useTranslation("backups")
    const query = useQuery({ queryKey: ["instance-restore-status", receipt?.id], queryFn: ({ signal }) => backupApi.restoreStatus(receipt!, signal), enabled: !!receipt, retry: false, refetchInterval: query => query.state.data && query.state.data.state !== "running" ? false : 1000 })
    if (!receipt) return null
    const status = query.data
    const login = () => { saveReceipt(null); window.location.assign(appPath("/login")) }
    return <section aria-label={t("restoreProgressTitle")} className="flex flex-col gap-3 rounded-md border p-3 text-sm">
        <h3 className="font-medium">{t("restoreProgressTitle")}</h3>
        {query.isError ? <p role="alert">{t("restoreStatusUnavailable")}</p> : status?.state === "succeeded" ? <p role="status">{t("restoreSucceeded")}</p> : status?.state === "failed" || status?.state === "blocked" ? <p role="alert">{t(`restoreErrors.${status.error_code}`, { defaultValue: t("restoreErrors.restore_failed") })} {status.rolled_back ? t("restoreRolledBack") : status.state === "blocked" ? t("restoreBlocked") : t("restoreNotApplied")}</p> : <p role="status">{t(`restorePhases.${status?.phase ?? "draining"}`, { defaultValue: t("restoreInProgress") })}</p>}
        <div className="flex flex-wrap gap-2">
            {query.isError && <Button variant="outline" disabled={query.isFetching} onClick={() => void query.refetch()}>{t("restoreRefreshResult")}</Button>}
            {(status?.state === "succeeded" || query.isError) && <Button onClick={login}>{t("restoreLogin")}</Button>}
            {status?.state === "failed" && <Button variant="outline" onClick={() => saveReceipt(null)}>{t("common.close", { ns: "translation", defaultValue: "关闭" })}</Button>}
        </div>
    </section>
}
