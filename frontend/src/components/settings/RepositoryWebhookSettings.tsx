import { useMemo, useState } from "react"
import {
    Activity,
    AlertCircle,
    CheckCircle2,
    Clock,
    Copy,
    Edit,
    Eye,
    EyeOff,
    GitBranch,
    History,
    Key,
    Layers,
    Plus,
    RefreshCw,
    Search,
    Shield,
    Trash2,
    Webhook as WebhookIcon,
    Workflow,
    X,
} from "lucide-react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import type { RepositoryWebhook, RepositoryWebhookInput, RepositoryWebhookProvider } from "@/api/types"
import { CopyableCode } from "@/components/common/CopyableCode"
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
    InputGroup,
    InputGroupAddon,
    InputGroupInput,
    InputGroupText,
} from "@/components/ui/input-group"
import { Label } from "@/components/ui/label"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table"
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

function getDeliveryStateBadge(state: string, t: (k: string, opt?: Record<string, unknown>) => string) {
    switch (state) {
        case "completed":
            return <Badge variant="outline" className="border-success/30 bg-success/10 text-success text-[10px] font-medium"><CheckCircle2 className="mr-1 size-3" />{t(`webhooks.states.${state}`, { defaultValue: state })}</Badge>
        case "failed":
            return <Badge variant="outline" className="border-destructive/30 bg-destructive/10 text-destructive text-[10px] font-medium"><AlertCircle className="mr-1 size-3" />{t(`webhooks.states.${state}`, { defaultValue: state })}</Badge>
        case "running":
            return <Badge variant="outline" className="border-primary/30 bg-primary/10 text-primary text-[10px] font-medium"><RefreshCw className="mr-1 size-3 animate-spin" />{t(`webhooks.states.${state}`, { defaultValue: state })}</Badge>
        case "deferred":
        case "waiting":
            return <Badge variant="outline" className="border-warning/30 bg-warning/10 text-warning text-[10px] font-medium"><Clock className="mr-1 size-3" />{t(`webhooks.states.${state}`, { defaultValue: state })}</Badge>
        default:
            return <Badge variant="secondary" className="text-[10px] font-medium">{t(`webhooks.states.${state}`, { defaultValue: state })}</Badge>
    }
}

export function RepositoryWebhookSettings() {
    const { t } = useTranslation()
    const { data: hooks = [], isLoading, refetch, isFetching } = useRepositoryWebhooks()
    const { data: trackerPage } = useTrackers({ limit: 1000 })
    const createHook = useCreateRepositoryWebhook()
    const updateHook = useUpdateRepositoryWebhook()
    const deleteHook = useDeleteRepositoryWebhook()

    const [search, setSearch] = useState("")
    const [dialogOpen, setDialogOpen] = useState(false)
    const [editing, setEditing] = useState<RepositoryWebhook | null>(null)
    const [form, setForm] = useState<RepositoryWebhookInput>(initialForm)
    const [showSecret, setShowSecret] = useState(false)
    const [branchesText, setBranchesText] = useState("")
    const [workflowsText, setWorkflowsText] = useState("")
    const [deliveryHook, setDeliveryHook] = useState<RepositoryWebhook | null>(null)
    const [pendingDeleteHook, setPendingDeleteHook] = useState<RepositoryWebhook | null>(null)

    const { data: deliveries = [], isLoading: loadingDeliveries } = useRepositoryWebhookDeliveries(deliveryHook?.id ?? null)

    const repositorySources = useMemo(() => (trackerPage?.items ?? []).flatMap((tracker) => tracker.sources
        .filter((source) => source.id && ["github", "gitlab", "gitea"].includes(source.source_type ?? source.channel_type))
        .map((source) => ({ tracker, source }))), [trackerPage?.items])

    const selected = repositorySources.find(({ source }) => source.id === form.tracker_source_id)
    const linkedSources = selected?.tracker.sources.filter((source) => source.id && ["container", "helm"].includes(source.source_type ?? source.channel_type)) ?? []
    const selectedType = selected?.source.source_type ?? selected?.source.channel_type
    const providers: RepositoryWebhookProvider[] = selectedType === "gitea" ? ["gitea", "forgejo"] : selectedType ? [selectedType as RepositoryWebhookProvider] : ["github"]

    const filteredHooks = useMemo(() => {
        if (!search.trim()) return hooks
        const q = search.trim().toLowerCase()
        return hooks.filter((h) =>
            h.tracker_name.toLowerCase().includes(q) ||
            h.source_key.toLowerCase().includes(q) ||
            h.provider.toLowerCase().includes(q) ||
            h.endpoint_url.toLowerCase().includes(q)
        )
    }, [hooks, search])

    const openCreate = () => {
        const first = repositorySources.find(({ source }) => !hooks.some((hook) => hook.tracker_source_id === source.id))
        const type = first?.source.source_type ?? first?.source.channel_type
        setEditing(null)
        setForm({
            ...initialForm,
            tracker_source_id: first?.source.id ?? 0,
            provider: (type as RepositoryWebhookProvider) || "github",
            auth_mode: type === "gitlab" ? "gitlab_signing" : "hmac",
        })
        setShowSecret(false)
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
        setShowSecret(false)
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

    const confirmDelete = async () => {
        if (!pendingDeleteHook) return
        try {
            await deleteHook.mutateAsync(pendingDeleteHook.id)
            toast.success(t("common.deleted"))
        } catch {
            toast.error(t("common.deleteFailed"))
        } finally {
            setPendingDeleteHook(null)
        }
    }

    return (
        <div className="flex min-h-0 flex-1 flex-col gap-4">
            {/* Toolbar — Search + Stats + Actions */}
            <div className="flex flex-none flex-wrap items-center justify-between gap-3">
                <div className="w-full max-w-sm">
                    <InputGroup>
                        <InputGroupAddon align="inline-start">
                            <InputGroupText>
                                <Search className="size-4" />
                            </InputGroupText>
                        </InputGroupAddon>
                        <InputGroupInput
                            placeholder={t("webhooks.repository.searchPlaceholder", { defaultValue: "按追踪器、来源或协议搜索..." })}
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                        />
                        {search && (
                            <InputGroupAddon align="inline-end">
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    className="size-6"
                                    onClick={() => setSearch("")}
                                    title={t("common.clear")}
                                >
                                    <X className="size-3.5" />
                                </Button>
                            </InputGroupAddon>
                        )}
                    </InputGroup>
                </div>

                <div className="flex items-center gap-2">
                    <span className="text-xs text-muted-foreground">
                        {t("webhooks.repository.totalCount", { count: filteredHooks.length, defaultValue: `共 ${filteredHooks.length} 个配置` })}
                    </span>
                    <Button
                        variant="outline"
                        size="icon"
                        onClick={() => refetch()}
                        disabled={isLoading || isFetching}
                        title={t("common.refresh")}
                    >
                        <RefreshCw className={`size-4 ${isFetching ? "animate-spin" : ""}`} />
                    </Button>
                    <Button
                        onClick={openCreate}
                        disabled={!repositorySources.some(({ source }) => !hooks.some((hook) => hook.tracker_source_id === source.id))}
                    >
                        <Plus className="mr-2 size-4" />
                        {t("webhooks.repository.add")}
                    </Button>
                </div>
            </div>

            {/* Main Table */}
            <div className="min-h-0 flex-1 overflow-auto rounded-md border">
                <Table containerClassName="overflow-visible">
                    <TableHeader className="sticky top-0 z-10 bg-background">
                        <TableRow>
                            <TableHead className="min-w-[14rem]">{t("webhooks.repository.source")}</TableHead>
                            <TableHead className="min-w-[10rem]">{t("webhooks.repository.events")}</TableHead>
                            <TableHead className="hidden min-w-[18rem] lg:table-cell">{t("webhooks.repository.endpoint")}</TableHead>
                            <TableHead className="w-[8rem]">{t("webhooks.repository.status")}</TableHead>
                            <TableHead className="w-[1%] text-right">{t("common.actions")}</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {isLoading ? (
                            <TableRow>
                                <TableCell colSpan={5} className="h-28 text-center text-sm text-muted-foreground">
                                    <div className="flex items-center justify-center gap-2">
                                        <RefreshCw className="size-4 animate-spin" />
                                        <span>{t("common.loading")}</span>
                                    </div>
                                </TableCell>
                            </TableRow>
                        ) : filteredHooks.length === 0 ? (
                            <TableRow>
                                <TableCell colSpan={5} className="h-28 text-center text-sm text-muted-foreground">
                                    <div className="flex flex-col items-center justify-center gap-1.5 py-4">
                                        <WebhookIcon className="size-6 text-muted-foreground/50" />
                                        <span>{search ? t("common.noData") : t("webhooks.repository.empty")}</span>
                                    </div>
                                </TableCell>
                            </TableRow>
                        ) : (
                            filteredHooks.map((hook) => (
                                <TableRow key={hook.id} className="transition-colors hover:bg-muted/40">
                                    <TableCell className="py-3 align-middle font-medium">
                                        <div className="min-w-0 space-y-1">
                                            <div className="flex items-center gap-2">
                                                <span className="truncate font-medium">{hook.tracker_name}</span>
                                            </div>
                                            <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                                                <span>{hook.source_key} · {hook.provider}</span>
                                                <Badge variant="outline" className="h-4 px-1 text-[10px] font-mono uppercase">
                                                    {hook.provider}
                                                </Badge>
                                                {hook.auth_mode === "gitlab_signing" && (
                                                    <span className="inline-flex items-center gap-0.5 text-[10px] text-primary">
                                                        <Shield className="size-2.5" />
                                                        Signing
                                                    </span>
                                                )}
                                            </div>
                                            {/* Mobile endpoint copy link */}
                                            <div className="pt-0.5 lg:hidden">
                                                <Button
                                                    variant="link"
                                                    size="sm"
                                                    className="h-auto p-0 text-xs text-primary"
                                                    onClick={() => {
                                                        navigator.clipboard.writeText(hook.endpoint_url)
                                                        toast.success(t("common.copied"))
                                                    }}
                                                >
                                                    <Copy className="mr-1 size-3" />
                                                    {t("webhooks.repository.copyEndpoint")}
                                                </Button>
                                            </div>
                                        </div>
                                    </TableCell>

                                    <TableCell className="py-3 align-middle">
                                        <div className="flex flex-wrap gap-1">
                                            {hook.release_published && (
                                                <Badge variant="outline" className="border-primary/20 bg-primary/5 text-primary text-[10px] font-normal">
                                                    <Layers className="mr-1 size-2.5" />
                                                    {t("webhooks.repository.release")}
                                                </Badge>
                                            )}
                                            {hook.workflow_success && (
                                                <Badge variant="outline" className="border-info/20 bg-info/5 text-info text-[10px] font-normal">
                                                    <Workflow className="mr-1 size-2.5" />
                                                    {t("webhooks.repository.workflow")}
                                                </Badge>
                                            )}
                                        </div>
                                    </TableCell>

                                    <TableCell className="hidden py-3 align-middle lg:table-cell">
                                        <div className="max-w-md">
                                            <CopyableCode
                                                value={hook.endpoint_url}
                                                className="w-full text-xs"
                                                title={t("webhooks.repository.copyEndpoint")}
                                            />
                                        </div>
                                    </TableCell>

                                    <TableCell className="py-3 align-middle">
                                        <Badge variant={hook.enabled ? "default" : "secondary"}>
                                            {t(hook.enabled ? "common.enabled" : "common.disabled")}
                                        </Badge>
                                    </TableCell>

                                    <TableCell className="py-3 align-middle text-right">
                                        <div className="flex justify-end gap-1">
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                className="size-8"
                                                aria-label={t("webhooks.repository.deliveries")}
                                                title={t("webhooks.repository.deliveries")}
                                                onClick={() => setDeliveryHook(hook)}
                                            >
                                                <History className="size-4" />
                                            </Button>
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                className="size-8"
                                                aria-label={t("common.edit")}
                                                title={t("common.edit")}
                                                onClick={() => openEdit(hook)}
                                            >
                                                <Edit className="size-4" />
                                            </Button>
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                className="size-8 text-destructive hover:bg-destructive/10 hover:text-destructive"
                                                aria-label={t("common.delete")}
                                                title={t("common.delete")}
                                                onClick={() => setPendingDeleteHook(hook)}
                                            >
                                                <Trash2 className="size-4" />
                                            </Button>
                                        </div>
                                    </TableCell>
                                </TableRow>
                            ))
                        )}
                    </TableBody>
                </Table>
            </div>

            {/* Create / Edit Dialog */}
            <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <WebhookIcon className="size-5 text-primary" />
                            {t(editing ? "webhooks.repository.edit" : "webhooks.repository.add")}
                        </DialogTitle>
                        <DialogDescription>{t("webhooks.repository.formDescription")}</DialogDescription>
                    </DialogHeader>

                    <div className="grid gap-5 py-2">
                        {/* Section 1: Source & Provider */}
                        <div className="rounded-lg border bg-card p-4 space-y-4">
                            <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                                <GitBranch className="size-3.5" />
                                {t("webhooks.repository.sourceSection", { defaultValue: "来源关联与协议" })}
                            </div>

                            <div className="grid gap-3 sm:grid-cols-2">
                                <div className="grid gap-2">
                                    <Label htmlFor="hook-source">{t("webhooks.repository.source")}</Label>
                                    <Select
                                        disabled={Boolean(editing)}
                                        value={form.tracker_source_id ? String(form.tracker_source_id) : ""}
                                        onValueChange={(value) => {
                                            const item = repositorySources.find(({ source }) => String(source.id) === value)
                                            const type = item?.source.source_type ?? item?.source.channel_type
                                            setForm({
                                                ...form,
                                                tracker_source_id: Number(value),
                                                provider: type as RepositoryWebhookProvider,
                                                auth_mode: type === "gitlab" ? "gitlab_signing" : "hmac",
                                                linked_source_ids: [],
                                            })
                                        }}
                                    >
                                        <SelectTrigger id="hook-source">
                                            <SelectValue placeholder={t("webhooks.repository.selectSource")} />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {repositorySources
                                                .filter(({ source }) => editing || !hooks.some((hook) => hook.tracker_source_id === source.id))
                                                .map(({ tracker, source }) => (
                                                    <SelectItem key={source.id} value={String(source.id)}>
                                                        {tracker.name} / {source.source_key}
                                                    </SelectItem>
                                                ))}
                                        </SelectContent>
                                    </Select>
                                </div>

                                {providers.length > 1 ? (
                                    <div className="grid gap-2">
                                        <Label>{t("webhooks.repository.provider")}</Label>
                                        <Select
                                            value={form.provider}
                                            onValueChange={(value) => setForm({ ...form, provider: value as RepositoryWebhookProvider })}
                                        >
                                            <SelectTrigger>
                                                <SelectValue />
                                            </SelectTrigger>
                                            <SelectContent>
                                                {providers.map((provider) => (
                                                    <SelectItem value={provider} key={provider}>
                                                        {provider}
                                                    </SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                    </div>
                                ) : (
                                    <div className="grid gap-2">
                                        <Label>{t("webhooks.repository.provider")}</Label>
                                        <div className="flex h-9 items-center rounded-md border bg-muted/30 px-3 text-sm font-mono capitalize text-muted-foreground">
                                            {form.provider}
                                        </div>
                                    </div>
                                )}
                            </div>

                            {form.provider === "gitlab" && (
                                <div className="grid gap-2 pt-1">
                                    <Label>{t("webhooks.repository.authMode")}</Label>
                                    <Select
                                        value={form.auth_mode}
                                        onValueChange={(value) => setForm({ ...form, auth_mode: value as RepositoryWebhookInput["auth_mode"] })}
                                    >
                                        <SelectTrigger>
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="gitlab_signing">{t("webhooks.repository.signingToken")}</SelectItem>
                                            <SelectItem value="gitlab_token">{t("webhooks.repository.secretToken")}</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>
                            )}
                        </div>

                        {/* Section 2: Secret */}
                        <div className="rounded-lg border bg-card p-4 space-y-3">
                            <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                                <Key className="size-3.5" />
                                {t("webhooks.repository.secretSection", { defaultValue: "安全与验签密钥" })}
                            </div>

                            <div className="grid gap-2">
                                <Label htmlFor="hook-secret">{t("webhooks.repository.secret")}</Label>
                                <div className="relative">
                                    <Input
                                        id="hook-secret"
                                        type={showSecret ? "text" : "password"}
                                        autoComplete="new-password"
                                        value={form.secret ?? ""}
                                        onChange={(event) => setForm({ ...form, secret: event.target.value })}
                                        placeholder={editing ? t("webhooks.repository.secretUnchanged") : t("webhooks.repository.secretPlaceholder")}
                                        className="pr-10 font-mono text-sm"
                                    />
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        size="icon"
                                        className="absolute right-0 top-0 size-9 text-muted-foreground"
                                        onClick={() => setShowSecret(!showSecret)}
                                    >
                                        {showSecret ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                                    </Button>
                                </div>
                                <p className="text-xs text-muted-foreground">
                                    {form.auth_mode === "gitlab_signing"
                                        ? t("webhooks.repository.gitlabSigningHelp")
                                        : form.provider === "gitlab"
                                        ? t("webhooks.repository.gitlabSecretHelp")
                                        : t("webhooks.repository.hmacSecretHelp")}
                                </p>
                            </div>
                        </div>

                        {/* Section 3: Events & Filters */}
                        <div className="rounded-lg border bg-card p-4 space-y-4">
                            <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                                <Activity className="size-3.5" />
                                {t("webhooks.repository.eventsSection", { defaultValue: "触发事件与过滤" })}
                            </div>

                            <div className="grid gap-3 sm:grid-cols-2">
                                <div className="flex items-center justify-between gap-3 rounded-md border p-3">
                                    <div className="space-y-0.5">
                                        <Label htmlFor="release-event" className="text-sm font-medium">
                                            {t("webhooks.repository.release")}
                                        </Label>
                                        <p className="text-xs text-muted-foreground">发布新 Release 时触发</p>
                                    </div>
                                    <Switch
                                        id="release-event"
                                        checked={form.release_published}
                                        onCheckedChange={(checked) => setForm({ ...form, release_published: checked })}
                                    />
                                </div>

                                <div className="flex items-center justify-between gap-3 rounded-md border p-3">
                                    <div className="space-y-0.5">
                                        <Label htmlFor="workflow-event" className="text-sm font-medium">
                                            {t("webhooks.repository.workflow")}
                                        </Label>
                                        <p className="text-xs text-muted-foreground">CI 工作流运行成功时触发</p>
                                    </div>
                                    <Switch
                                        id="workflow-event"
                                        checked={form.workflow_success}
                                        onCheckedChange={(checked) => setForm({ ...form, workflow_success: checked })}
                                    />
                                </div>
                            </div>

                            <div className="grid gap-3 sm:grid-cols-2">
                                <div className="grid gap-2">
                                    <Label htmlFor="hook-branches">{t("webhooks.repository.branches")}</Label>
                                    <Input
                                        id="hook-branches"
                                        value={branchesText}
                                        onChange={(event) => setBranchesText(event.target.value)}
                                        placeholder="main, release/*"
                                        className="font-mono text-xs"
                                    />
                                    <p className="text-xs text-muted-foreground">{t("webhooks.repository.branchesHelp")}</p>
                                </div>

                                <div className="grid gap-2">
                                    <Label htmlFor="hook-workflows">{t("webhooks.repository.workflows")}</Label>
                                    <Input
                                        id="hook-workflows"
                                        value={workflowsText}
                                        onChange={(event) => setWorkflowsText(event.target.value)}
                                        placeholder="publish.yml"
                                        className="font-mono text-xs"
                                    />
                                    <p className="text-xs text-muted-foreground">{t("webhooks.repository.workflowsHelp")}</p>
                                </div>
                            </div>
                        </div>

                        {/* Section 4: Linked Sources & Enable */}
                        {linkedSources.length > 0 && (
                            <div className="rounded-lg border bg-card p-4 space-y-3">
                                <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                                    <Layers className="size-3.5" />
                                    {t("webhooks.repository.linkedSources")}
                                </div>
                                <p className="text-xs text-muted-foreground">{t("webhooks.repository.linkedSourcesHelp")}</p>
                                <div className="grid gap-2 sm:grid-cols-2">
                                    {linkedSources.map((source) => (
                                        <Label
                                            htmlFor={`hook-linked-source-${source.id}`}
                                            key={source.id}
                                            className="flex min-h-10 cursor-pointer items-center gap-3 rounded-md border px-3 transition-colors hover:bg-muted/40"
                                        >
                                            <Checkbox
                                                id={`hook-linked-source-${source.id}`}
                                                checked={form.linked_source_ids.includes(source.id!)}
                                                onCheckedChange={(checked) =>
                                                    setForm({
                                                        ...form,
                                                        linked_source_ids: checked
                                                            ? [...form.linked_source_ids, source.id!]
                                                            : form.linked_source_ids.filter((id) => id !== source.id),
                                                    })
                                                }
                                            />
                                            <div className="flex flex-wrap items-center gap-1.5 text-xs">
                                                <span className="font-mono font-medium">{source.source_key}</span>
                                                <Badge variant="outline" className="h-4 px-1 text-[9px] uppercase">
                                                    {source.source_type ?? source.channel_type}
                                                </Badge>
                                            </div>
                                        </Label>
                                    ))}
                                </div>
                            </div>
                        )}

                        <div className="flex min-h-11 items-center justify-between gap-3 rounded-lg border bg-card px-4">
                            <div className="space-y-0.5">
                                <Label htmlFor="hook-enabled" className="text-sm font-medium">
                                    {t("common.enabled")}
                                </Label>
                                <p className="text-xs text-muted-foreground">停用后忽略所有入站请求</p>
                            </div>
                            <Switch
                                id="hook-enabled"
                                checked={form.enabled}
                                onCheckedChange={(checked) => setForm({ ...form, enabled: checked })}
                            />
                        </div>
                    </div>

                    <DialogFooter>
                        <Button variant="outline" onClick={() => setDialogOpen(false)}>
                            {t("common.cancel")}
                        </Button>
                        <Button
                            onClick={submit}
                            disabled={
                                !form.tracker_source_id ||
                                (!form.release_published && !form.workflow_success) ||
                                (!editing && !form.secret) ||
                                createHook.isPending ||
                                updateHook.isPending
                            }
                        >
                            {t("common.save")}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Delete Confirmation Alert Dialog */}
            <AlertDialog open={Boolean(pendingDeleteHook)} onOpenChange={(open) => !open && setPendingDeleteHook(null)}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>{t("common.deleteConfirm")}</AlertDialogTitle>
                        <AlertDialogDescription>
                            {t("webhooks.repository.deleteConfirm")}
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
                        <AlertDialogAction
                            onClick={confirmDelete}
                            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                        >
                            {t("common.delete")}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>

            {/* Deliveries Dialog */}
            <Dialog open={Boolean(deliveryHook)} onOpenChange={(open) => !open && setDeliveryHook(null)}>
                <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-3xl">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <History className="size-5 text-primary" />
                            {t("webhooks.repository.deliveries")}
                        </DialogTitle>
                        <DialogDescription className="font-mono text-xs">
                            {deliveryHook?.tracker_name} / {deliveryHook?.source_key}
                        </DialogDescription>
                    </DialogHeader>

                    <div className="space-y-3 py-2">
                        {loadingDeliveries ? (
                            <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
                                <RefreshCw className="size-4 animate-spin" />
                                <span>{t("common.loading")}</span>
                            </div>
                        ) : deliveries.length === 0 ? (
                            <div className="flex flex-col items-center justify-center gap-1.5 py-12 text-muted-foreground">
                                <History className="size-8 text-muted-foreground/40" />
                                <span className="text-sm">{t("webhooks.repository.noDeliveries")}</span>
                            </div>
                        ) : (
                            deliveries.map((delivery) => (
                                <div key={delivery.id} className="rounded-lg border bg-card p-4 transition-colors hover:border-muted-foreground/30">
                                    <div className="flex flex-wrap items-center justify-between gap-2">
                                        <div className="flex items-center gap-2">
                                            {getDeliveryStateBadge(delivery.state, t)}
                                            <span className="text-sm font-semibold">
                                                {t(`webhooks.repository.eventKinds.${delivery.summary.kind}`, { defaultValue: delivery.summary.kind })}
                                            </span>
                                            {Boolean(delivery.duplicates) && (
                                                <Badge variant="secondary" className="text-[10px]">
                                                    {t("webhooks.repository.duplicates", { count: delivery.duplicates })}
                                                </Badge>
                                            )}
                                        </div>
                                        <time className="flex items-center gap-1 text-xs text-muted-foreground">
                                            <Clock className="size-3" />
                                            {new Date(delivery.received_at * 1000).toLocaleString()}
                                        </time>
                                    </div>

                                    <div className="mt-2 text-xs font-mono text-muted-foreground break-all">
                                        {delivery.summary.ref || delivery.summary.workflow || delivery.reason || "—"}
                                    </div>

                                    {delivery.requests.length > 0 && (
                                        <div className="mt-3 space-y-1.5 pt-2 border-t">
                                            <div className="text-[11px] font-medium text-muted-foreground">
                                                {t("webhooks.repository.linkedRefreshTitle", { defaultValue: "关联来源拉取请求" })}
                                            </div>
                                            <div className="grid gap-1.5 sm:grid-cols-2">
                                                {delivery.requests.map((refresh) => (
                                                    <div
                                                        key={refresh.tracker_source_id}
                                                        className="flex items-center justify-between gap-2 rounded-md bg-muted/50 px-2.5 py-1.5 text-xs"
                                                    >
                                                        <span className="font-mono font-medium truncate max-w-[10rem]">
                                                            {refresh.source_key}
                                                        </span>
                                                        <div className="flex items-center gap-1.5 shrink-0">
                                                            {getDeliveryStateBadge(refresh.state, t)}
                                                            {refresh.reason && (
                                                                <span className="font-mono text-[10px] text-muted-foreground">
                                                                    {refresh.reason}
                                                                </span>
                                                            )}
                                                            {refresh.source_fetch_run_id ? (
                                                                <span className="font-mono text-[10px] text-muted-foreground">
                                                                    #{refresh.source_fetch_run_id}
                                                                </span>
                                                            ) : refresh.attempts > 0 ? (
                                                                <span className="text-[10px] text-muted-foreground">
                                                                    {t("webhooks.repository.attempts", { count: refresh.attempts })}
                                                                </span>
                                                            ) : null}
                                                        </div>
                                                    </div>
                                                ))}
                                            </div>
                                        </div>
                                    )}
                                </div>
                            ))
                        )}
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    )
}
