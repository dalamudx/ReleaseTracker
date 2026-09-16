import { useMemo, useState } from "react"
import { Copy, Edit, History, Plus, Trash2 } from "lucide-react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import type { RepositoryWebhook, RepositoryWebhookInput, RepositoryWebhookProvider } from "@/api/types"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import {
    useCreateRepositoryWebhook,
    useDeleteRepositoryWebhook,
    useRepositoryWebhookDeliveries,
    useRepositoryWebhooks,
    useTrackers,
    useUpdateRepositoryWebhook,
} from "@/hooks/queries"

const initialForm: RepositoryWebhookInput = {
    tracker_source_id: 0,
    provider: "github",
    auth_mode: "hmac",
    enabled: true,
    release_published: true,
    workflow_success: true,
    linked_source_ids: [],
    branches: [],
    workflows: [],
    secret: "",
}

function splitPatterns(value: string) {
    return value.split(",").map((item) => item.trim()).filter(Boolean)
}

function statusVariant(state: string) {
    return state === "failed" ? "destructive" : state === "completed" ? "default" : "secondary"
}

export function RepositoryWebhookSettings() {
    const { t } = useTranslation()
    const { data: hooks = [], isLoading } = useRepositoryWebhooks()
    const { data: trackerPage } = useTrackers({ limit: 1000 })
    const createHook = useCreateRepositoryWebhook()
    const updateHook = useUpdateRepositoryWebhook()
    const deleteHook = useDeleteRepositoryWebhook()
    const [dialogOpen, setDialogOpen] = useState(false)
    const [editing, setEditing] = useState<RepositoryWebhook | null>(null)
    const [form, setForm] = useState<RepositoryWebhookInput>(initialForm)
    const [branchesText, setBranchesText] = useState("")
    const [workflowsText, setWorkflowsText] = useState("")
    const [deliveryHook, setDeliveryHook] = useState<RepositoryWebhook | null>(null)
    const { data: deliveries = [] } = useRepositoryWebhookDeliveries(deliveryHook?.id ?? null)

    const repositorySources = useMemo(() => (trackerPage?.items ?? []).flatMap((tracker) => tracker.sources
        .filter((source) => source.id && ["github", "gitlab", "gitea"].includes(source.source_type ?? source.channel_type))
        .map((source) => ({ tracker, source }))), [trackerPage?.items])
    const selected = repositorySources.find(({ source }) => source.id === form.tracker_source_id)
    const linkedSources = selected?.tracker.sources.filter((source) => source.id && ["container", "helm"].includes(source.source_type ?? source.channel_type)) ?? []
    const selectedType = selected?.source.source_type ?? selected?.source.channel_type
    const providers: RepositoryWebhookProvider[] = selectedType === "gitea" ? ["gitea", "forgejo"] : selectedType ? [selectedType as RepositoryWebhookProvider] : ["github"]

    const openCreate = () => {
        const first = repositorySources.find(({ source }) => !hooks.some((hook) => hook.tracker_source_id === source.id))
        const type = first?.source.source_type ?? first?.source.channel_type
        setEditing(null)
        setForm({ ...initialForm, tracker_source_id: first?.source.id ?? 0, provider: (type as RepositoryWebhookProvider) || "github", auth_mode: type === "gitlab" ? "gitlab_signing" : "hmac" })
        setBranchesText("")
        setWorkflowsText("")
        setDialogOpen(true)
    }

    const openEdit = (hook: RepositoryWebhook) => {
        setEditing(hook)
        setForm({
            tracker_source_id: hook.tracker_source_id,
            provider: hook.provider,
            auth_mode: hook.auth_mode,
            enabled: hook.enabled,
            release_published: hook.release_published,
            workflow_success: hook.workflow_success,
            linked_source_ids: hook.linked_source_ids,
            branches: hook.branches,
            workflows: hook.workflows,
            secret: "",
        })
        setBranchesText(hook.branches.join(", "))
        setWorkflowsText(hook.workflows.join(", "))
        setDialogOpen(true)
    }

    const submit = async () => {
        if (!form.tracker_source_id || (!editing && !form.secret)) return
        try {
            const payload = {
                ...form,
                branches: splitPatterns(branchesText),
                workflows: splitPatterns(workflowsText),
                secret: form.secret || undefined,
            }
            if (editing) await updateHook.mutateAsync({ id: editing.id, data: payload })
            else await createHook.mutateAsync(payload)
            toast.success(t(editing ? "webhooks.repository.updated" : "webhooks.repository.created"))
            setDialogOpen(false)
        } catch (error: unknown) {
            const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
            toast.error(detail || t("webhooks.repository.saveFailed"))
        }
    }

    return (
        <div className="flex min-h-0 flex-1 flex-col gap-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h2 className="text-base font-semibold">{t("webhooks.repository.title")}</h2>
                    <p className="mt-1 text-sm text-muted-foreground">{t("webhooks.repository.description")}</p>
                </div>
                <Button onClick={openCreate} disabled={!repositorySources.some(({ source }) => !hooks.some((hook) => hook.tracker_source_id === source.id))}>
                    <Plus className="mr-2 size-4" />{t("webhooks.repository.add")}
                </Button>
            </div>

            <div className="min-h-0 flex-1 overflow-hidden rounded-md border">
                <Table containerClassName="overflow-hidden">
                    <TableHeader><TableRow>
                        <TableHead>{t("webhooks.repository.source")}</TableHead>
                        <TableHead>{t("webhooks.repository.events")}</TableHead>
                        <TableHead className="hidden lg:table-cell">{t("webhooks.repository.endpoint")}</TableHead>
                        <TableHead>{t("webhooks.repository.status")}</TableHead>
                        <TableHead className="w-[1%] text-right">{t("common.actions")}</TableHead>
                    </TableRow></TableHeader>
                    <TableBody>
                        {isLoading ? <TableRow><TableCell colSpan={5} className="h-24 text-center">{t("common.loading")}</TableCell></TableRow> : hooks.length === 0 ?
                            <TableRow><TableCell colSpan={5} className="h-24 text-center text-muted-foreground">{t("webhooks.repository.empty")}</TableCell></TableRow> : hooks.map((hook) => (
                                <TableRow key={hook.id}>
                                    <TableCell className="align-middle"><div className="font-medium">{hook.tracker_name}</div><div className="text-xs text-muted-foreground">{hook.source_key} · {hook.provider}</div><Button variant="link" size="sm" className="h-auto px-0 text-xs lg:hidden" onClick={() => { navigator.clipboard.writeText(hook.endpoint_url); toast.success(t("common.copied")) }}><Copy className="mr-1 size-3" />{t("webhooks.repository.copyEndpoint")}</Button></TableCell>
                                    <TableCell className="align-middle"><div className="flex flex-wrap gap-1">
                                        {hook.release_published && <Badge variant="outline">{t("webhooks.repository.release")}</Badge>}
                                        {hook.workflow_success && <Badge variant="outline">{t("webhooks.repository.workflow")}</Badge>}
                                    </div></TableCell>
                                    <TableCell className="hidden align-middle lg:table-cell"><div className="flex max-w-md items-center gap-1"><code className="truncate text-xs">{hook.endpoint_url}</code><Button variant="ghost" size="icon" aria-label={t("common.copy")} onClick={() => { navigator.clipboard.writeText(hook.endpoint_url); toast.success(t("common.copied")) }}><Copy className="size-4" /></Button></div></TableCell>
                                    <TableCell className="align-middle"><Badge variant={hook.enabled ? "default" : "secondary"}>{t(hook.enabled ? "common.enabled" : "common.disabled")}</Badge></TableCell>
                                    <TableCell className="align-middle"><div className="flex justify-end gap-1">
                                        <Button variant="ghost" size="icon" aria-label={t("webhooks.repository.deliveries")} onClick={() => setDeliveryHook(hook)}><History className="size-4" /></Button>
                                        <Button variant="ghost" size="icon" aria-label={t("common.edit")} onClick={() => openEdit(hook)}><Edit className="size-4" /></Button>
                                        <Button variant="ghost" size="icon" aria-label={t("common.delete")} onClick={async () => { if (window.confirm(t("webhooks.repository.deleteConfirm"))) { await deleteHook.mutateAsync(hook.id); toast.success(t("common.deleted")) } }}><Trash2 className="size-4" /></Button>
                                    </div></TableCell>
                                </TableRow>
                            ))}
                    </TableBody>
                </Table>
            </div>

            <Dialog open={dialogOpen} onOpenChange={setDialogOpen}><DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
                <DialogHeader><DialogTitle>{t(editing ? "webhooks.repository.edit" : "webhooks.repository.add")}</DialogTitle><DialogDescription>{t("webhooks.repository.formDescription")}</DialogDescription></DialogHeader>
                <div className="grid gap-5 py-2">
                    <div className="grid gap-2"><Label htmlFor="hook-source">{t("webhooks.repository.source")}</Label><Select disabled={Boolean(editing)} value={form.tracker_source_id ? String(form.tracker_source_id) : ""} onValueChange={(value) => { const item = repositorySources.find(({ source }) => String(source.id) === value); const type = item?.source.source_type ?? item?.source.channel_type; setForm({ ...form, tracker_source_id: Number(value), provider: type as RepositoryWebhookProvider, auth_mode: type === "gitlab" ? "gitlab_signing" : "hmac", linked_source_ids: [] }) }}><SelectTrigger id="hook-source"><SelectValue placeholder={t("webhooks.repository.selectSource")} /></SelectTrigger><SelectContent>{repositorySources.filter(({ source }) => editing || !hooks.some((hook) => hook.tracker_source_id === source.id)).map(({ tracker, source }) => <SelectItem key={source.id} value={String(source.id)}>{tracker.name} / {source.source_key}</SelectItem>)}</SelectContent></Select></div>
                    {providers.length > 1 && <div className="grid gap-2"><Label>{t("webhooks.repository.provider")}</Label><Select value={form.provider} onValueChange={(value) => setForm({ ...form, provider: value as RepositoryWebhookProvider })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{providers.map((provider) => <SelectItem value={provider} key={provider}>{provider}</SelectItem>)}</SelectContent></Select></div>}
                    {form.provider === "gitlab" && <div className="grid gap-2"><Label>{t("webhooks.repository.authMode")}</Label><Select value={form.auth_mode} onValueChange={(value) => setForm({ ...form, auth_mode: value as RepositoryWebhookInput["auth_mode"] })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="gitlab_signing">{t("webhooks.repository.signingToken")}</SelectItem><SelectItem value="gitlab_token">{t("webhooks.repository.secretToken")}</SelectItem></SelectContent></Select></div>}
                    <div className="grid gap-2"><Label htmlFor="hook-secret">{t("webhooks.repository.secret")}</Label><Input id="hook-secret" type="password" autoComplete="new-password" value={form.secret ?? ""} onChange={(event) => setForm({ ...form, secret: event.target.value })} placeholder={editing ? t("webhooks.repository.secretUnchanged") : t("webhooks.repository.secretPlaceholder")} /><p className="text-xs text-muted-foreground">{form.auth_mode === "gitlab_signing" ? t("webhooks.repository.gitlabSigningHelp") : form.provider === "gitlab" ? t("webhooks.repository.gitlabSecretHelp") : t("webhooks.repository.hmacSecretHelp")}</p></div>
                    <div className="grid gap-3 rounded-md border p-3"><div className="flex items-center justify-between gap-3"><Label htmlFor="release-event">{t("webhooks.repository.release")}</Label><Switch id="release-event" checked={form.release_published} onCheckedChange={(checked) => setForm({ ...form, release_published: checked })} /></div><div className="flex items-center justify-between gap-3"><Label htmlFor="workflow-event">{t("webhooks.repository.workflow")}</Label><Switch id="workflow-event" checked={form.workflow_success} onCheckedChange={(checked) => setForm({ ...form, workflow_success: checked })} /></div></div>
                    <div className="grid gap-2"><Label htmlFor="hook-branches">{t("webhooks.repository.branches")}</Label><Input id="hook-branches" value={branchesText} onChange={(event) => setBranchesText(event.target.value)} placeholder="main, release/*" /><p className="text-xs text-muted-foreground">{t("webhooks.repository.branchesHelp")}</p></div>
                    <div className="grid gap-2"><Label htmlFor="hook-workflows">{t("webhooks.repository.workflows")}</Label><Input id="hook-workflows" value={workflowsText} onChange={(event) => setWorkflowsText(event.target.value)} placeholder="publish.yml" /><p className="text-xs text-muted-foreground">{t("webhooks.repository.workflowsHelp")}</p></div>
                    {linkedSources.length > 0 && <fieldset className="grid gap-2"><legend className="text-sm font-medium">{t("webhooks.repository.linkedSources")}</legend><p className="text-xs text-muted-foreground">{t("webhooks.repository.linkedSourcesHelp")}</p>{linkedSources.map((source) => <label key={source.id} className="flex min-h-11 items-center gap-3 rounded-md border px-3"><Checkbox checked={form.linked_source_ids.includes(source.id!)} onCheckedChange={(checked) => setForm({ ...form, linked_source_ids: checked ? [...form.linked_source_ids, source.id!] : form.linked_source_ids.filter((id) => id !== source.id) })} /><span className="text-sm">{source.source_key} · {source.source_type ?? source.channel_type}</span></label>)}</fieldset>}
                    <div className="flex min-h-11 items-center justify-between gap-3 rounded-md border px-3"><Label htmlFor="hook-enabled">{t("common.enabled")}</Label><Switch id="hook-enabled" checked={form.enabled} onCheckedChange={(checked) => setForm({ ...form, enabled: checked })} /></div>
                </div>
                <DialogFooter><Button variant="outline" onClick={() => setDialogOpen(false)}>{t("common.cancel")}</Button><Button onClick={submit} disabled={!form.tracker_source_id || (!form.release_published && !form.workflow_success) || (!editing && !form.secret) || createHook.isPending || updateHook.isPending}>{t("common.save")}</Button></DialogFooter>
            </DialogContent></Dialog>

            <Dialog open={Boolean(deliveryHook)} onOpenChange={(open) => !open && setDeliveryHook(null)}><DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-3xl"><DialogHeader><DialogTitle>{t("webhooks.repository.deliveries")}</DialogTitle><DialogDescription>{deliveryHook?.tracker_name} / {deliveryHook?.source_key}</DialogDescription></DialogHeader><div className="space-y-2">{deliveries.length === 0 ? <p className="py-8 text-center text-sm text-muted-foreground">{t("webhooks.repository.noDeliveries")}</p> : deliveries.map((delivery) => <div key={delivery.id} className="rounded-md border p-3"><div className="flex flex-wrap items-center justify-between gap-2"><div className="flex items-center gap-2"><Badge variant={statusVariant(delivery.state)}>{t(`webhooks.states.${delivery.state}`, { defaultValue: delivery.state })}</Badge><span className="text-sm font-medium">{t(`webhooks.repository.eventKinds.${delivery.summary.kind}`, { defaultValue: delivery.summary.kind })}</span></div><time className="text-xs text-muted-foreground">{new Date(delivery.received_at * 1000).toLocaleString()}</time></div><div className="mt-2 break-words text-xs text-muted-foreground">{delivery.summary.ref || delivery.summary.workflow || delivery.reason || "—"}{delivery.duplicates ? ` · ${t("webhooks.repository.duplicates", { count: delivery.duplicates })}` : ""}</div>{delivery.requests.length > 0 && <div className="mt-3 grid gap-2">{delivery.requests.map((refresh) => <div key={refresh.tracker_source_id} className="flex flex-wrap items-center justify-between gap-2 rounded bg-muted/50 px-2 py-1.5 text-xs"><div className="flex items-center gap-2"><span className="font-medium">{refresh.source_key}</span><Badge variant={statusVariant(refresh.state)}>{t(`webhooks.states.${refresh.state}`, { defaultValue: refresh.state })}</Badge></div><span className="text-muted-foreground">{refresh.reason || (refresh.source_fetch_run_id ? `${t("webhooks.repository.fetchRun")} #${refresh.source_fetch_run_id}` : refresh.attempts ? t("webhooks.repository.attempts", { count: refresh.attempts }) : "")}</span></div>)}</div>}</div>)}</div></DialogContent></Dialog>
        </div>
    )
}
