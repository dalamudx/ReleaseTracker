import { AlertTriangle, CheckCircle2, Layers3, Loader2, Plus, Search, Trash2 } from "lucide-react"
import type { UseFormReturn } from "react-hook-form"
import { useTranslation } from "react-i18next"

import type { RuntimeConnection, RuntimeTargetDiscoveryItem, TrackerStatus } from "@/api/types"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
    FormControl,
    FormField,
    FormItem,
    FormLabel,
    FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { getChannelLabel } from "@/lib/channel"

import {
    buildExecutorReviewImageChanges,
    buildExecutorTargetDisplay,
    DAY_OPTIONS,
    formatTargetRef,
    getGroupedBindingServiceOptions,
    normalizeExecutorServiceKey,
    resolveExecutorServiceBinding,
    type ExecutorServiceBindingFormValue,
    type ExecutorGroupedServiceOption,
    type ExecutorTargetDisplay,
    type ExecutorFormValues,
    type ExecutorReviewItem,
    type StepKey,
    STEP_ORDER,
    isHelmReleaseTarget,
    usesGroupedServiceBindings,
} from "./executorSheetHelpers"

function renderTargetDetailGrid(details: ExecutorTargetDisplay["details"]) {
    return (
        <div data-testid="executor-target-detail-grid" className="mt-3 grid gap-2 sm:grid-cols-2">
            {details.map((detail) => (
                <div key={`${detail.label}-${detail.value}`} className="rounded-md bg-muted px-3 py-2">
                    <div className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">{detail.label}</div>
                    <div className="mt-1 break-all text-xs font-medium text-foreground">{detail.value}</div>
                </div>
            ))}
        </div>
    )
}

function renderGroupedTargetDetails(targetDisplay: ExecutorTargetDisplay) {
    const details = targetDisplay.details
    const detailItems = targetDisplay.kind === "portainer_stack"
        ? [details[2], details[1], details[0], details[3], details[5], details[6], details[7]].filter(Boolean)
        : [details[0], details[4], details[1], details[2]].filter(Boolean)
    const serviceLabel = targetDisplay.kind === "portainer_stack"
        ? details[4]?.label
        : details[3]?.label
    const services = targetDisplay.groupedServices ?? []

    return (
        <div data-testid="executor-grouped-target-detail-group" className="mt-3 border-t border-border/60 pt-3">
            <div className="grid gap-2 sm:grid-cols-2 2xl:grid-cols-4">
                {detailItems.map((detail) => (
                    <div key={`${detail.label}-${detail.value}`} className="rounded-md bg-muted px-3 py-2">
                        <div className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">{detail.label}</div>
                        <div className="mt-1 break-all text-xs font-medium text-foreground">{detail.value}</div>
                    </div>
                ))}
                {services.length > 0 ? (
                    <div className="rounded-md bg-muted px-3 py-2 sm:col-span-2">
                        <div className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">{serviceLabel}</div>
                        <div className="mt-2 flex flex-wrap gap-2">
                            {services.map((service) => (
                                <Badge key={service.service} variant="outline" className="bg-background text-[10px]">
                                    {service.service}
                                </Badge>
                            ))}
                        </div>
                    </div>
                ) : null}
            </div>
        </div>
    )
}

function renderTargetDetails(targetDisplay: ExecutorTargetDisplay) {
    if (targetDisplay.kind === "portainer_stack" || targetDisplay.kind === "docker_compose" || targetDisplay.kind === "kubernetes_workload") {
        return renderGroupedTargetDetails(targetDisplay)
    }

    return renderTargetDetailGrid(targetDisplay.cardDetails ?? targetDisplay.details)
}

function renderDiscoveryTargetCard(
    target: RuntimeTargetDiscoveryItem,
    selectedTargetRef: Record<string, unknown>,
    onSelectTarget: (target: RuntimeTargetDiscoveryItem) => void,
    t: ReturnType<typeof useTranslation>["t"],
) {
    const selected = JSON.stringify(target.target_ref) === JSON.stringify(selectedTargetRef)
    const targetDisplay = buildExecutorTargetDisplay(target.runtime_type, target.target_ref, t)

    return (
        <div
            key={`${target.runtime_type}-${target.name}-${JSON.stringify(target.target_ref)}`}
            className={`flex h-full flex-col rounded-xl border p-3 transition-all ${selected ? "border-primary bg-primary/5 shadow-sm" : "border-border/60 bg-background"}`}
        >
            <div className="flex items-start justify-between gap-3">
                <div className="space-y-2">
                    <div className="flex items-center gap-2">
                        {targetDisplay.badges.map((badge) => (
                            <Badge key={badge} variant="outline" className="text-[10px]">{badge}</Badge>
                        ))}
                        {selected ? <Badge>{t("executors.discovery.selected")}</Badge> : null}
                    </div>
                    <div className="font-medium">{targetDisplay.title}</div>
                </div>
                <Layers3 className="h-4 w-4 text-muted-foreground" />
            </div>
            {renderTargetDetails(targetDisplay)}
            {target.image ? (
                <div className="mt-3 rounded-md bg-muted px-3 py-2 font-mono text-xs text-muted-foreground">
                    {target.image}
                </div>
            ) : null}
            <div className="mt-auto flex justify-end pt-4">
                <Button type="button" onClick={() => onSelectTarget(target)}>
                    {selected ? t("executors.discovery.rebind") : t("executors.discovery.bind")}
                </Button>
            </div>
        </div>
    )
}

function renderGroupedBindingServiceRows(
    bindings: ExecutorServiceBindingFormValue[],
    serviceOptions: ExecutorGroupedServiceOption[],
    trackers: TrackerStatus[],
    onUpdateServiceBinding: (index: number, patch: Partial<ExecutorServiceBindingFormValue>) => void,
    onRemoveServiceBinding: (index: number) => void,
    t: ReturnType<typeof useTranslation>["t"],
) {
    return (
        <div className="overflow-hidden rounded-xl border border-border/60 bg-muted/20">
            <div className="border-b border-border/60 bg-background/70 px-4 py-3">
                <div className="text-sm font-medium text-foreground">{t("executors.binding.serviceBindingGroup")}</div>
            </div>
            <div className="divide-y divide-border/60">
            {bindings.map((binding, index) => {
                const {
                    selectedTracker,
                    selectedTrackerBindableSources: rowBindableSources,
                    effectiveTrackerSourceId: rowEffectiveTrackerSourceId,
                    trackerSourceOptions,
                    scopedReleaseChannels: rowScopedReleaseChannels,
                } = resolveExecutorServiceBinding(binding, trackers)
                const selectedServices = new Set(
                    bindings
                        .filter((_, bindingIndex) => bindingIndex !== index)
                        .map((item) => normalizeExecutorServiceKey(item.service))
                        .filter(Boolean),
                )
                const rowServiceOptions = serviceOptions.filter((option) => !selectedServices.has(normalizeExecutorServiceKey(option.service))
                    || normalizeExecutorServiceKey(option.service) === normalizeExecutorServiceKey(binding.service))

                if (binding.service && !rowServiceOptions.some((option) => normalizeExecutorServiceKey(option.service) === normalizeExecutorServiceKey(binding.service))) {
                    rowServiceOptions.unshift({ service: binding.service, image: null })
                }

                return (
                    <div key={`${binding.service || "binding"}-${index}`} className="grid gap-3 p-4 md:grid-cols-[minmax(160px,1fr)_minmax(160px,1fr)_minmax(140px,0.8fr)_minmax(140px,0.8fr)_auto] md:items-end">
                            <div className="space-y-2">
                                <FormLabel>{t("executors.fields.service")}</FormLabel>
                                <Select value={binding.service} onValueChange={(value) => onUpdateServiceBinding(index, { service: value })}>
                                    <SelectTrigger>
                                        <SelectValue placeholder={t("executors.fields.servicePlaceholder")} />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {rowServiceOptions.map((option) => (
                                            <SelectItem key={option.service} value={option.service}>{option.service}</SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>

                            <div className="space-y-2">
                                <FormLabel>{t("executors.fields.tracker")}</FormLabel>
                                <Select value={binding.tracker_name} onValueChange={(value) => onUpdateServiceBinding(index, { tracker_name: value })}>
                                    <SelectTrigger>
                                        <SelectValue placeholder={t("executors.fields.trackerPlaceholder")} />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {trackers.map((tracker) => (
                                            <SelectItem key={tracker.name} value={tracker.name}>{tracker.name}</SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>

                            <div className="space-y-2">
                                <FormLabel>{t("executors.fields.trackerSource")}</FormLabel>
                                <Select
                                    value={rowEffectiveTrackerSourceId}
                                    onValueChange={(value) => onUpdateServiceBinding(index, { tracker_source_id: value })}
                                    disabled={!selectedTracker || trackerSourceOptions.length === 0 || rowBindableSources.length === 1}
                                >
                                    <SelectTrigger>
                                        <SelectValue
                                            placeholder={!binding.tracker_name
                                                ? t("executors.fields.trackerSourcePlaceholderEmpty")
                                                : rowBindableSources.length <= 1
                                                    ? t("executors.fields.trackerSourceAutoSelected")
                                                    : t("executors.fields.trackerSourcePlaceholder")}
                                        />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {trackerSourceOptions.map((source) => (
                                            <SelectItem key={source.id ?? source.source_key} value={String(source.id)}>
                                                {source.source_key}{source.enabled ? "" : ` (${t("executors.fields.bindingUnavailable")})`}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>

                            <div className="space-y-2">
                                <FormLabel>{t("executors.fields.channel")}</FormLabel>
                                <Select value={binding.channel_name} onValueChange={(value) => onUpdateServiceBinding(index, { channel_name: value })} disabled={rowScopedReleaseChannels.length === 0}>
                                    <SelectTrigger>
                                        <SelectValue placeholder={rowScopedReleaseChannels.length === 0 ? t("executors.fields.channelPlaceholderEmpty") : t("executors.fields.channelPlaceholder")} />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {rowScopedReleaseChannels.map((channel) => (
                                            <SelectItem key={channel.release_channel_key ?? channel.name} value={channel.name}>
                                                {getChannelLabel(channel.name)}{channel.enabled ? "" : ` (${t("executors.fields.bindingUnavailable")})`}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>

                            <Button type="button" variant="ghost" size="icon" onClick={() => onRemoveServiceBinding(index)} aria-label={t("executors.binding.removeServiceBinding")}>
                                <Trash2 className="h-4 w-4" />
                            </Button>
                    </div>
                )
            })}
            </div>
        </div>
    )
}

interface ExecutorSheetStepTabsProps {
    step: StepKey
    onStepChange: (step: StepKey) => void
}

interface ExecutorSheetRuntimeSectionProps {
    form: UseFormReturn<ExecutorFormValues>
    enabledRuntimeConnections: RuntimeConnection[]
    handleSelectRuntimeConnection: (value: string) => void
}

interface ExecutorSheetBindingSectionProps {
    form: UseFormReturn<ExecutorFormValues>
    trackers: TrackerStatus[]
    isContainerRuntime: boolean
    trackerName: string
    effectiveTrackerSourceId: string
    selectedTrackerBindableSources: TrackerStatus["sources"]
    scopedReleaseChannels: NonNullable<TrackerStatus["sources"][number]["release_channels"]>
    runtimeType: ExecutorFormValues["runtime_type"]
    selectedTargetRef: Record<string, unknown>
    serviceBindings: ExecutorServiceBindingFormValue[]
    onSelectTracker: (value: string) => void
    onSelectTrackerSource: (value: string) => void
    onSelectChannel: (value: string) => void
    onAddServiceBinding: () => void
    onUpdateServiceBinding: (index: number, patch: Partial<ExecutorServiceBindingFormValue>) => void
    onRemoveServiceBinding: (index: number) => void
}

interface ExecutorSheetTargetSectionProps {
    form: UseFormReturn<ExecutorFormValues>
    runtimeType: ExecutorFormValues["runtime_type"]
    selectedRuntimeConnection: RuntimeConnection | null
    enabledRuntimeConnections: RuntimeConnection[]
    discovering: boolean
    discoveryMessage: string | null
    selectedTargetRef: Record<string, unknown>
    discoveredTargets: RuntimeTargetDiscoveryItem[]
    configuredDiscoveryNamespaces?: string[]
    selectedDiscoveryNamespace?: string
    onDiscoverTargets: () => void
    onSelectDiscoveryNamespace?: (namespace: string) => void
    onSelectRuntimeConnection: (value: string) => void
    onSelectTarget: (target: RuntimeTargetDiscoveryItem) => void
}

interface ExecutorSheetPolicySectionProps {
    form: UseFormReturn<ExecutorFormValues>
    updateMode: ExecutorFormValues["update_mode"]
    imageSelectionMode: ExecutorFormValues["image_selection_mode"]
    selectedTargetRef: Record<string, unknown>
    selectedTracker: TrackerStatus | null
    selectedBindableSource: TrackerStatus["sources"][number] | null
}

interface ExecutorSheetReviewSectionProps {
    reviewItems: ExecutorReviewItem[]
    trackers: TrackerStatus[]
    serviceBindings: ExecutorServiceBindingFormValue[]
    runtimeType: ExecutorFormValues["runtime_type"]
    selectedTargetRef: Record<string, unknown>
    imageSelectionMode: ExecutorFormValues["image_selection_mode"]
    validationMessage: string | null
}

function renderReviewServiceBindings(
    serviceBindings: ExecutorServiceBindingFormValue[],
    t: ReturnType<typeof useTranslation>["t"],
) {
    if (serviceBindings.length === 0) {
        return null
    }

    return (
        <div className="rounded-xl border border-border/60 bg-muted/20 p-4">
            <div className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">{t("executors.review.serviceBindings")}</div>
            <div className="mt-3 divide-y divide-border/60 overflow-hidden rounded-lg border border-border/60 bg-background">
                {serviceBindings.map((binding) => (
                    <div key={`${binding.service}-${binding.tracker_name}-${binding.channel_name}`} className="grid gap-2 px-3 py-2 text-sm sm:grid-cols-[1fr_auto_1fr] sm:items-center">
                        <div className="font-medium text-foreground">{binding.service || "-"}</div>
                        <div className="hidden text-muted-foreground sm:block">→</div>
                        <div className="text-muted-foreground sm:text-right">
                            <span className="font-medium text-foreground">{binding.tracker_name || "-"}</span>
                            <span className="px-1.5">/</span>
                            <span>{getChannelLabel(binding.channel_name)}</span>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    )
}

function renderReviewImageChanges(
    targetDisplay: ExecutorTargetDisplay,
    serviceBindings: ExecutorServiceBindingFormValue[],
    trackers: TrackerStatus[],
    imageSelectionMode: ExecutorFormValues["image_selection_mode"],
    t: ReturnType<typeof useTranslation>["t"],
) {
    const imageChanges = buildExecutorReviewImageChanges({
        targetDisplay,
        serviceBindings,
        trackers,
        imageSelectionMode,
    })

    if (imageChanges.length === 0) {
        return null
    }

    return (
        <div className="rounded-xl border border-border/60 bg-muted/20 p-4">
            <div className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">{t("executors.review.imageChanges")}</div>
            <div className="mt-3 grid gap-2">
                {imageChanges.map((change) => {
                    const targetImage = change.targetImage || t("executors.review.targetImageDeferred")
                    return (
                        <div key={`${change.service}-${change.sourceImage}-${targetImage}`} className="rounded-lg border border-border/60 bg-background px-3 py-2">
                            <div className="text-sm font-medium text-foreground">{change.service || "-"}</div>
                            <div className="mt-2 grid gap-2 text-xs md:grid-cols-[1fr_auto_1fr] md:items-center">
                                <div className="break-all rounded-md bg-muted px-2 py-1 font-mono text-muted-foreground">{change.sourceImage}</div>
                                <div className="text-center text-muted-foreground">→</div>
                                <div className="break-all rounded-md bg-primary/10 px-2 py-1 font-mono text-primary">{targetImage}</div>
                            </div>
                            <div className="mt-2 text-xs text-muted-foreground">
                                {t("executors.review.targetVersion")}: <span className="font-mono text-foreground">{change.targetVersion ?? "-"}</span>
                            </div>
                        </div>
                    )
                })}
            </div>
        </div>
    )
}

export function ExecutorSheetStepTabs({ step, onStepChange }: ExecutorSheetStepTabsProps) {
    const { t } = useTranslation()

    return (
        <div className="border-b border-border/50 px-4 py-3 sm:px-6">
            <div className="grid grid-cols-4 gap-2">
                {STEP_ORDER.map((item, index) => {
                    const active = item === step
                    const completed = STEP_ORDER.indexOf(step) > index

                    return (
                        <Button
                            key={item}
                            type="button"
                            variant="ghost"
                            className={`h-auto justify-start rounded-lg border px-3 py-2 text-left transition-colors ${active ? "border-primary bg-primary/5 text-foreground" : "border-border/60 text-muted-foreground hover:border-border"} ${completed ? "bg-muted/60" : ""}`}
                            onClick={() => onStepChange(item)}
                        >
                            <div className="flex items-center gap-2 text-xs uppercase tracking-[0.16em]">
                                <span>{String(index + 1).padStart(2, "0")}</span>
                                {completed ? <CheckCircle2 className="h-3.5 w-3.5 text-primary" /> : null}
                            </div>
                            <div className="mt-2 text-sm font-medium">
                                {t(`executors.steps.${item}`)}
                            </div>
                        </Button>
                    )
                })}
            </div>
        </div>
    )
}

export function ExecutorSheetRuntimeSection({
    form,
    enabledRuntimeConnections,
    handleSelectRuntimeConnection,
}: ExecutorSheetRuntimeSectionProps) {
    const { t } = useTranslation()

    return (
        <Card className="border-border/60 bg-card/80 shadow-sm">
            <CardHeader>
                <CardTitle>{t("executors.sections.targetSetup")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-6">
                <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] md:items-start">
                    <FormField
                        control={form.control}
                        name="name"
                        rules={{ required: t("executors.validation.nameRequired") }}
                        render={({ field }) => (
                            <FormItem>
                                <FormLabel>{t("executors.fields.name")}</FormLabel>
                                <FormControl>
                                    <Input placeholder={t("executors.fields.namePlaceholder")} {...field} />
                                </FormControl>
                                <FormMessage />
                            </FormItem>
                        )}
                    />
                    <FormField
                        control={form.control}
                        name="runtime_connection_id"
                        rules={{ required: t("executors.validation.runtimeRequired") }}
                        render={({ field }) => (
                            <FormItem>
                                <FormLabel>{t("executors.fields.runtimeConnection")}</FormLabel>
                                <Select value={field.value} onValueChange={handleSelectRuntimeConnection}>
                                    <FormControl>
                                        <SelectTrigger>
                                            <SelectValue placeholder={t("executors.fields.runtimeConnectionPlaceholder")} />
                                        </SelectTrigger>
                                    </FormControl>
                                    <SelectContent>
                                        {enabledRuntimeConnections.map((connection) => (
                                            <SelectItem key={connection.id} value={String(connection.id)}>
                                                {connection.name} ({connection.type}){connection.enabled ? '' : ` · ${t('executors.fields.bindingUnavailable')}`}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                                <FormMessage />
                            </FormItem>
                        )}
                    />
                    <FormField
                        control={form.control}
                        name="enabled"
                        render={({ field }) => (
                            <FormItem className="flex min-w-[120px] items-center justify-between gap-3 rounded-lg border border-border/60 bg-muted/20 px-4 py-2.5 md:mt-6">
                                <FormLabel>{t("executors.fields.enabled")}</FormLabel>
                                <FormControl>
                                    <Switch checked={field.value} onCheckedChange={field.onChange} />
                                </FormControl>
                            </FormItem>
                        )}
                    />
                </div>

                <FormField
                    control={form.control}
                    name="description"
                    render={({ field }) => (
                        <FormItem>
                            <FormLabel>{t("executors.sections.notes")}</FormLabel>
                            <FormControl>
                                <Textarea rows={3} placeholder={t("executors.fields.descriptionPlaceholder")} {...field} />
                            </FormControl>
                        </FormItem>
                    )}
                />
            </CardContent>
        </Card>
    )
}

export function ExecutorSheetBindingSection({
    form,
    trackers,
    isContainerRuntime,
    trackerName,
    effectiveTrackerSourceId,
    selectedTrackerBindableSources,
    scopedReleaseChannels,
    runtimeType,
    selectedTargetRef,
    serviceBindings,
    onSelectTracker,
    onSelectTrackerSource,
    onSelectChannel,
    onAddServiceBinding,
    onUpdateServiceBinding,
    onRemoveServiceBinding,
}: ExecutorSheetBindingSectionProps) {
    const { t } = useTranslation()
    const selectedTargetDisplay = buildExecutorTargetDisplay(runtimeType, selectedTargetRef, t)
    const hasSelectedTarget = formatTargetRef(runtimeType, selectedTargetRef) !== "-"
    const isGroupedBindingTarget = usesGroupedServiceBindings(selectedTargetRef)
    const groupedServiceOptions = getGroupedBindingServiceOptions(selectedTargetRef)

    return (
        <Card className="border-border/60 bg-card/80 shadow-sm">
            <CardHeader>
                <CardTitle>{t("executors.sections.binding")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-6">
                <div className="rounded-xl border border-border/60 bg-muted/20 p-4">
                    <div className="text-xs uppercase tracking-[0.18em] text-muted-foreground">{t("executors.review.target")}</div>
                    <div className="mt-3 flex flex-wrap gap-2">
                        {selectedTargetDisplay.badges.map((badge) => (
                            <Badge key={badge} variant="outline" className="text-[10px]">
                                {badge}
                            </Badge>
                        ))}
                    </div>
                    <div className="mt-3 text-sm font-medium">{hasSelectedTarget ? selectedTargetDisplay.title : t("executors.discovery.noTargetSelected")}</div>
                    {!hasSelectedTarget ? (
                        <div className="mt-1 text-xs text-muted-foreground">{t("executors.binding.targetSelectionRequired")}</div>
                    ) : null}
                </div>

                {!hasSelectedTarget ? (
                    <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-300">
                        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                        <span>{t("executors.binding.targetSelectionRequired")}</span>
                    </div>
                ) : isGroupedBindingTarget ? (
                    <div className="space-y-4">
                        {renderGroupedBindingServiceRows(serviceBindings, groupedServiceOptions, trackers, onUpdateServiceBinding, onRemoveServiceBinding, t)}

                        {serviceBindings.length === 0 ? (
                            <div className="rounded-lg border border-dashed border-border/60 bg-muted/20 p-4 text-sm text-muted-foreground">
                                {t("executors.binding.portainerBindingEmpty")}
                            </div>
                        ) : null}

                        <div className="flex justify-end">
                            <Button
                                type="button"
                                variant="outline"
                                onClick={onAddServiceBinding}
                                disabled={groupedServiceOptions.length === 0 || serviceBindings.length >= groupedServiceOptions.length}
                            >
                                <Plus className="mr-2 h-4 w-4" />
                                {t("executors.binding.addServiceBinding")}
                            </Button>
                        </div>
                    </div>
                ) : (
                    <div className="grid gap-4 md:grid-cols-3">
                        <FormField
                            control={form.control}
                            name="tracker_name"
                            rules={{ required: t("executors.validation.trackerRequired") }}
                            render={({ field }) => (
                                <FormItem>
                                    <FormLabel>{t("executors.fields.tracker")}</FormLabel>
                                    <Select value={field.value} onValueChange={onSelectTracker}>
                                        <FormControl>
                                            <SelectTrigger>
                                                <SelectValue placeholder={t("executors.fields.trackerPlaceholder")} />
                                            </SelectTrigger>
                                        </FormControl>
                                        <SelectContent>
                                            {trackers.map((tracker) => (
                                                <SelectItem key={tracker.name} value={tracker.name}>
                                                    {tracker.name}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                    <FormMessage />
                                </FormItem>
                            )}
                        />
                        <FormField
                            control={form.control}
                            name="tracker_source_id"
                            render={() => (
                                <FormItem>
                                    <FormLabel>{t("executors.fields.trackerSource")}</FormLabel>
                                    <Select
                                        value={effectiveTrackerSourceId}
                                        onValueChange={onSelectTrackerSource}
                                        disabled={!isContainerRuntime || selectedTrackerBindableSources.length === 0 || selectedTrackerBindableSources.length === 1}
                                    >
                                        <FormControl>
                                            <SelectTrigger>
                                                <SelectValue
                                                    placeholder={
                                                        !trackerName
                                                            ? t("executors.fields.trackerSourcePlaceholderEmpty")
                                                            : selectedTrackerBindableSources.length <= 1
                                                                ? t("executors.fields.trackerSourceAutoSelected")
                                                                : t("executors.fields.trackerSourcePlaceholder")
                                                    }
                                                />
                                            </SelectTrigger>
                                        </FormControl>
                                        <SelectContent>
                                            {selectedTrackerBindableSources.map((source) => (
                                                <SelectItem key={source.id ?? source.source_key} value={String(source.id)}>
                                                    {source.source_key}{source.enabled ? "" : ` (${t("executors.fields.bindingUnavailable")})`}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                    <FormMessage />
                                </FormItem>
                            )}
                        />
                        <FormField
                            control={form.control}
                            name="channel_name"
                            rules={{ required: t("executors.validation.channelRequired") }}
                            render={({ field }) => (
                                <FormItem>
                                    <FormLabel>{t("executors.fields.channel")}</FormLabel>
                                    <Select value={field.value} onValueChange={onSelectChannel} disabled={scopedReleaseChannels.length === 0}>
                                        <FormControl>
                                            <SelectTrigger>
                                                <SelectValue placeholder={scopedReleaseChannels.length === 0 ? t("executors.fields.channelPlaceholderEmpty") : t("executors.fields.channelPlaceholder")} />
                                            </SelectTrigger>
                                        </FormControl>
                                        <SelectContent>
                                            {scopedReleaseChannels.map((channel) => (
                                                <SelectItem key={channel.release_channel_key ?? channel.name} value={channel.name}>
                                                    {getChannelLabel(channel.name)}{channel.enabled ? "" : ` (${t("executors.fields.bindingUnavailable")})`}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                    <FormMessage />
                                </FormItem>
                            )}
                        />
                    </div>
                )}
            </CardContent>
        </Card>
    )
}

export function ExecutorSheetTargetSection({
    form,
    runtimeType,
    selectedRuntimeConnection,
    enabledRuntimeConnections,
    discovering,
    discoveryMessage,
    selectedTargetRef,
    discoveredTargets,
    configuredDiscoveryNamespaces = [],
    selectedDiscoveryNamespace = "",
    onDiscoverTargets,
    onSelectDiscoveryNamespace = () => undefined,
    onSelectRuntimeConnection,
    onSelectTarget,
}: ExecutorSheetTargetSectionProps) {
    const { t } = useTranslation()
    const selectedTargetDisplay = buildExecutorTargetDisplay(runtimeType, selectedTargetRef, t)
    const hasSelectedTarget = formatTargetRef(runtimeType, selectedTargetRef) !== "-"

    return (
        <div className="space-y-4">
            <ExecutorSheetRuntimeSection
                form={form}
                enabledRuntimeConnections={enabledRuntimeConnections}
                handleSelectRuntimeConnection={onSelectRuntimeConnection}
            />

            <div className="space-y-4 rounded-xl border border-border/60 bg-muted/20 p-4">
                <div className="text-sm font-medium text-foreground">{t("executors.sections.targetSelection")}</div>

                <div className={`rounded-lg border border-border/60 bg-background px-4 py-3 ${hasSelectedTarget ? "grid gap-4 xl:grid-cols-[minmax(220px,0.9fr)_minmax(0,1.6fr)] xl:items-start" : ""}`}>
                    <div className="min-w-0">
                        <div className="flex min-h-5 flex-wrap items-center gap-2">
                            {hasSelectedTarget
                                ? selectedTargetDisplay.badges.map((badge) => (
                                    <Badge key={badge} variant="outline" className="text-[10px]">
                                        {badge}
                                    </Badge>
                                ))
                                : null}
                        </div>
                        <div className="mt-3 break-words text-sm font-medium">
                            {hasSelectedTarget
                                ? selectedTargetDisplay.title
                                : t("executors.discovery.noTargetSelected")}
                        </div>
                    </div>
                    {selectedTargetDisplay.details.length > 0 && hasSelectedTarget ? (
                        <div className="min-w-0 xl:[&>[data-testid=executor-grouped-target-detail-group]]:mt-0 xl:[&>[data-testid=executor-grouped-target-detail-group]]:border-t-0 xl:[&>[data-testid=executor-grouped-target-detail-group]]:pt-0 xl:[&>[data-testid=executor-target-detail-grid]]:mt-0">
                            {renderTargetDetails(selectedTargetDisplay)}
                        </div>
                    ) : null}
                </div>

                <div className="grid gap-3 lg:grid-cols-2">
                    {(runtimeType === "docker" || runtimeType === "podman") && selectedTargetDisplay.kind === "container" ? (
                        <div className="rounded-lg border border-border/60 bg-background px-3 py-2 text-xs text-muted-foreground">
                            {t("executors.discovery.containerFixedHint")}
                        </div>
                    ) : null}
                    {runtimeType === "portainer" && selectedTargetDisplay.kind === "portainer_stack" ? (
                        <div className="flex items-start gap-2 rounded-lg border border-primary/20 bg-primary/5 p-3 text-sm text-muted-foreground">
                            <Layers3 className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                            <span>{t("executors.discovery.portainerStackHint")}</span>
                        </div>
                    ) : null}
                    {selectedTargetDisplay.kind === "docker_compose" ? (
                        <div className="flex items-start gap-2 rounded-lg border border-primary/20 bg-primary/5 p-3 text-sm text-muted-foreground">
                            <Layers3 className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                            <span>{t("executors.discovery.dockerComposeHint")}</span>
                        </div>
                    ) : null}
                </div>

                <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
                    <div className="text-sm font-medium text-foreground">{t("executors.sections.discovery")}</div>
                    <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] lg:min-w-[360px] lg:items-end">
                        {selectedRuntimeConnection?.type === "kubernetes" ? (
                            <Select value={selectedDiscoveryNamespace} onValueChange={onSelectDiscoveryNamespace} disabled={configuredDiscoveryNamespaces.length <= 1}>
                                <SelectTrigger>
                                    <SelectValue placeholder={t("executors.discovery.namespacePlaceholder")} />
                                </SelectTrigger>
                                <SelectContent>
                                    {configuredDiscoveryNamespaces.map((namespace) => (
                                        <SelectItem key={namespace} value={namespace}>
                                            {namespace}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        ) : <div className="hidden sm:block" />}
                        <Button type="button" variant="outline" className="w-full sm:w-auto" onClick={onDiscoverTargets} disabled={discovering || !selectedRuntimeConnection}>
                            {discovering ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Search className="mr-2 h-4 w-4" />}
                            {t("executors.actions.discover")}
                        </Button>
                    </div>
                </div>

                {discoveryMessage ? (
                    <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-300">
                        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                        <span>{discoveryMessage}</span>
                    </div>
                ) : null}

                <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-3">
                    {discoveredTargets.map((target) => renderDiscoveryTargetCard(target, selectedTargetRef, onSelectTarget, t))}
                </div>
            </div>
         </div>
    )
}

export function ExecutorSheetPolicySection({
    form,
    updateMode,
    imageSelectionMode,
    selectedTargetRef,
    selectedTracker,
    selectedBindableSource,
}: ExecutorSheetPolicySectionProps) {
    const { t } = useTranslation()
    const helmReleaseTarget = isHelmReleaseTarget(selectedTargetRef)

    return (
        <Card className="border-border/60 bg-card/80">
            <CardHeader>
                <CardTitle>{t("executors.sections.policy")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
                <FormField
                    control={form.control}
                    name="update_mode"
                    render={({ field }) => (
                        <FormItem>
                            <FormLabel>{t("executors.fields.updateMode")}</FormLabel>
                            <Select value={field.value} onValueChange={field.onChange}>
                                <FormControl>
                                    <SelectTrigger>
                                        <SelectValue />
                                    </SelectTrigger>
                                </FormControl>
                                <SelectContent>
                                    <SelectItem value="manual">{t("executors.modes.manual")}</SelectItem>
                                    <SelectItem value="maintenance_window">{t("executors.modes.maintenance_window")}</SelectItem>
                                    <SelectItem value="immediate">{t("executors.modes.immediate")}</SelectItem>
                                </SelectContent>
                            </Select>
                        </FormItem>
                    )}
                />

                {!helmReleaseTarget ? (
                    <>
                        <FormField
                            control={form.control}
                            name="image_selection_mode"
                            render={({ field }) => (
                                <FormItem>
                                    <FormLabel>{t("executors.fields.imageSelectionMode")}</FormLabel>
                                    <Select value={field.value} onValueChange={field.onChange}>
                                        <FormControl>
                                            <SelectTrigger>
                                                <SelectValue />
                                            </SelectTrigger>
                                        </FormControl>
                                        <SelectContent>
                                            <SelectItem value="replace_tag_on_current_image">{t("executors.imageStrategy.replace_tag_on_current_image")}</SelectItem>
                                            <SelectItem value="use_tracker_image_and_tag">{t("executors.imageStrategy.use_tracker_image_and_tag")}</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </FormItem>
                            )}
                        />

                        <FormField
                            control={form.control}
                            name="image_reference_mode"
                            render={({ field }) => (
                                <FormItem>
                                    <FormLabel>{t("executors.fields.imageReferenceMode")}</FormLabel>
                                    <Select value={field.value} onValueChange={field.onChange}>
                                        <FormControl>
                                            <SelectTrigger>
                                                <SelectValue />
                                            </SelectTrigger>
                                        </FormControl>
                                        <SelectContent>
                                            <SelectItem value="digest">{t("executors.imageReferenceStrategy.digest")}</SelectItem>
                                            <SelectItem value="tag">{t("executors.imageReferenceStrategy.tag")}</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </FormItem>
                            )}
                        />
                    </>
                ) : null}

                {!helmReleaseTarget && imageSelectionMode === "use_tracker_image_and_tag" && selectedTracker && !selectedBindableSource?.source_config?.image ? (
                    <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
                        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                        <span>{t("executors.validation.trackerImageStrategyIncompatible")}</span>
                    </div>
                ) : null}

                {updateMode === "maintenance_window" ? (
                    <>
                        <Separator />
                        <div className="space-y-4 rounded-xl border border-border/60 bg-muted/20 p-4">
                            <div className="text-sm font-medium">{t("executors.modes.maintenance_window")}</div>
                            <div className="grid gap-4 md:grid-cols-3">
                                <FormField
                                    control={form.control}
                                    name="maintenance_days"
                                    render={({ field }) => (
                                        <FormItem>
                                            <div className="flex flex-wrap gap-2">
                                                {DAY_OPTIONS.map((day) => {
                                                    const active = field.value.includes(day.value)

                                                    return (
                                                        <Button
                                                            key={day.value}
                                                            type="button"
                                                            variant={active ? "default" : "outline"}
                                                            size="sm"
                                                            onClick={() => {
                                                                const nextValue = active
                                                                    ? field.value.filter((item) => item !== day.value)
                                                                    : [...field.value, day.value]
                                                                field.onChange(nextValue)
                                                            }}
                                                        >
                                                            {t(day.labelKey)}
                                                        </Button>
                                                    )
                                                })}
                                            </div>
                                        </FormItem>
                                    )}
                                />
                                <FormField
                                    control={form.control}
                                    name="maintenance_start_time"
                                    rules={{ required: t("executors.validation.maintenanceTimeRequired") }}
                                    render={({ field }) => (
                                        <FormItem>
                                            <FormLabel>{t("executors.fields.maintenanceStart")}</FormLabel>
                                            <FormControl>
                                                <Input type="time" {...field} />
                                            </FormControl>
                                            <FormMessage />
                                        </FormItem>
                                    )}
                                />
                                <FormField
                                    control={form.control}
                                    name="maintenance_end_time"
                                    rules={{ required: t("executors.validation.maintenanceTimeRequired") }}
                                    render={({ field }) => (
                                        <FormItem>
                                            <FormLabel>{t("executors.fields.maintenanceEnd")}</FormLabel>
                                            <FormControl>
                                                <Input type="time" {...field} />
                                            </FormControl>
                                            <FormMessage />
                                        </FormItem>
                                    )}
                                />
                            </div>
                        </div>
                    </>
                ) : null}
            </CardContent>
        </Card>
    )
}

export function ExecutorSheetReviewSection({
    reviewItems,
    trackers,
    serviceBindings,
    runtimeType,
    selectedTargetRef,
    imageSelectionMode,
    validationMessage,
}: ExecutorSheetReviewSectionProps) {
    const { t } = useTranslation()
    const targetDisplay = buildExecutorTargetDisplay(runtimeType, selectedTargetRef, t)
    const hiddenReviewItems = new Set([
        t("executors.review.serviceBindings"),
        t("executors.review.targetType"),
        t("executors.review.target"),
    ])
    const visibleReviewItems = reviewItems.filter((item) => !hiddenReviewItems.has(item.label))

    return (
        <Card className="border-border/60 bg-card/80">
            <CardHeader>
                <CardTitle>{t("executors.sections.review")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
                <div className="rounded-xl border border-border/60 bg-muted/20 p-4">
                    <div className="text-sm font-semibold">{targetDisplay.title}</div>
                    <div className="mt-3 grid gap-2 md:grid-cols-2">
                        {targetDisplay.details.map((detail) => (
                            <div key={`${detail.label}-${detail.value}`} className="rounded-md border border-border/60 bg-background px-3 py-2">
                                <div className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">{detail.label}</div>
                                <div className="mt-1 break-all text-xs font-medium text-foreground">{detail.value}</div>
                            </div>
                        ))}
                        {visibleReviewItems.map((item) => (
                            <div key={item.label} className="rounded-md border border-border/60 bg-background px-3 py-2">
                                <div className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">{item.label}</div>
                                <div className="mt-1 break-all text-xs font-medium text-foreground">{item.value}</div>
                            </div>
                        ))}
                    </div>
                </div>

                {renderReviewServiceBindings(serviceBindings, t)}
                {renderReviewImageChanges(targetDisplay, serviceBindings, trackers, imageSelectionMode, t)}

                <Separator />

                <div className="space-y-2">
                    <div className="text-sm font-medium">{t("executors.review.ready")}</div>
                    {validationMessage ? (
                        <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
                            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                            <span>{validationMessage}</span>
                        </div>
                    ) : (
                        <div className="flex items-start gap-2 rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3 text-sm text-emerald-700 dark:text-emerald-300">
                            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
                            <span>{t("executors.review.ready")}</span>
                        </div>
                    )}
                </div>
            </CardContent>
        </Card>
    )
}
