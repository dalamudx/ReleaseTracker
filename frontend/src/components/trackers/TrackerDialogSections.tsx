import { ChevronDown, ChevronRight, Plus, Trash2 } from "lucide-react"
import type { UseFieldArrayReturn, UseFormReturn } from "react-hook-form"
import { useTranslation } from "react-i18next"

import type { ApiCredential, ReleaseChannelInput, TrackerChannelType } from "@/api/types"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
    FormControl,
    FormDescription,
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
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"

import { TrackerDialogSourceConfigFields } from "./TrackerDialogSourceConfigFields"
import { getCredentialTypeLabel } from "@/components/credentials/credentialTypeLabels"
import {
    getCredentialTypeFilter,
    getReleaseChannelHeaderLabel,
    getReleaseChannelIdentity,
    getTrackerSourceHeaderLabel,
    RELEASE_CHANNEL_PRESETS,
    RELEASE_TYPE_OPTIONS,
    SOURCE_TYPE_OPTIONS,
    supportsReleaseTypeFilter,
    type TrackerFormValues,
} from "./trackerDialogHelpers"
import { getChannelLabel } from "@/lib/channel"

interface TrackerDialogIdentitySectionProps {
    form: UseFormReturn<TrackerFormValues>
    trackerName?: string | null
}

export function TrackerDialogIdentitySection({ form, trackerName }: TrackerDialogIdentitySectionProps) {
    const { t } = useTranslation()

    return (
        <Card className="h-full gap-4 border-border/70 bg-background shadow-sm">
            <CardHeader className="pb-3">
                <CardTitle>{t("trackers.aggregate.identity.title")}</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-4 md:grid-cols-2">
                <FormField
                    control={form.control}
                    name="name"
                    rules={{
                        required: t("trackers.aggregate.validation.nameRequired"),
                        validate: (value) => value.trim().length > 0 || t("trackers.aggregate.validation.nameRequired"),
                    }}
                    render={({ field }) => (
                        <FormItem className="w-full max-w-lg">
                            <FormLabel>{t("trackers.aggregate.fields.name")}</FormLabel>
                            <FormControl>
                                <Input {...field} placeholder={t("trackers.aggregate.fields.namePlaceholder")} disabled={!!trackerName} />
                            </FormControl>
                            <FormMessage />
                        </FormItem>
                    )}
                />

                <FormField
                    control={form.control}
                    name="description"
                    render={({ field }) => (
                        <FormItem className="md:col-span-2 w-full max-w-3xl">
                            <FormLabel>{t("common.description")}</FormLabel>
                            <FormControl>
                                <Textarea {...field} value={field.value ?? ""} placeholder={t("trackers.aggregate.fields.descriptionPlaceholder")} />
                            </FormControl>
                            <FormMessage />
                        </FormItem>
                    )}
                />

                <FormField
                    control={form.control}
                    name="enabled"
                    render={({ field }) => (
                        <FormItem className="flex items-center justify-between rounded-lg border border-border/50 px-4 py-3 md:col-span-2">
                            <div className="space-y-1">
                                <FormLabel>{t("trackers.aggregate.fields.enabled")}</FormLabel>
                                <FormDescription>{t("trackers.aggregate.fields.enabledDescription")}</FormDescription>
                            </div>
                            <FormControl>
                                <Switch checked={field.value} onCheckedChange={field.onChange} />
                            </FormControl>
                        </FormItem>
                    )}
                />
            </CardContent>
        </Card>
    )
}

interface TrackerDialogTrackerChannelsSectionProps {
    form: UseFormReturn<TrackerFormValues>
    trackerChannelsFieldArray: UseFieldArrayReturn<TrackerFormValues, "sources">
    watchedTrackerChannels: TrackerFormValues["sources"]
    credentials: ApiCredential[]
    expandedReleaseChannels: Record<string, boolean>
    sourceListError: string | null
    releaseChannelListError: string | null
    onAddSource: () => void
    onRemoveSource: (index: number) => void
    onAddReleaseChannel: (trackerChannelIndex: number) => void
    onRemoveReleaseChannel: (trackerChannelIndex: number, releaseChannelIndex: number) => void
    onToggleReleaseChannelExpanded: (releaseChannelKey: string) => void
    onClearReleaseChannelListError: () => void
}

export function TrackerDialogTrackerChannelsSection({
    form,
    trackerChannelsFieldArray,
    watchedTrackerChannels,
    credentials,
    expandedReleaseChannels,
    sourceListError,
    releaseChannelListError,
    onAddSource,
    onRemoveSource,
    onAddReleaseChannel,
    onRemoveReleaseChannel,
    onToggleReleaseChannelExpanded,
    onClearReleaseChannelListError,
}: TrackerDialogTrackerChannelsSectionProps) {
    const { t } = useTranslation()

    return (
        <Card className="gap-4 border-border/70 bg-background shadow-sm">
            <CardHeader className="flex flex-row items-start justify-between gap-4 border-b border-border/50 pb-4">
                <div className="space-y-1.5">
                    <CardTitle>{t("trackers.aggregate.trackerChannels.title")}</CardTitle>
                </div>
                <Button type="button" variant="outline" onClick={onAddSource}>
                    <Plus className="mr-2 h-4 w-4" />
                    {t("trackers.aggregate.trackerChannels.add")}
                </Button>
            </CardHeader>
            <CardContent className="space-y-5">
                {sourceListError ? <p className="text-destructive text-sm font-medium">{sourceListError}</p> : null}
                {releaseChannelListError ? <p className="text-destructive text-sm font-medium">{releaseChannelListError}</p> : null}

                {trackerChannelsFieldArray.fields.map((field, index) => {
                    const channelType = watchedTrackerChannels[index]?.source_type ?? "github"
                    const credentialOptions = credentials.filter((credential) => getCredentialTypeFilter(channelType).includes(credential.type))
                    const releaseChannels = watchedTrackerChannels[index]?.release_channels ?? []

                    return (
                        <div key={field.id} className="mx-auto w-full max-w-5xl overflow-hidden rounded-2xl border border-border/70 bg-background shadow-sm">
                            <div className="flex flex-col gap-3 border-b border-border/60 bg-muted/35 px-4 py-3 md:flex-row md:items-center md:justify-between">
                                <div className="flex min-w-0 items-start gap-3">
                                    <Badge variant="outline" className="mt-0.5 shrink-0 bg-background/80 text-[10px] uppercase tracking-[0.16em]">
                                        #{index + 1}
                                    </Badge>
                                    <div className="min-w-0">
                                        <div className="truncate text-sm font-semibold">
                                            {getTrackerSourceHeaderLabel(watchedTrackerChannels[index]?.source_key, t)}
                                        </div>
                                        <div className="mt-1 text-xs uppercase tracking-[0.14em] text-muted-foreground">
                                            {(() => {
                                                const labelKey = SOURCE_TYPE_OPTIONS.find((option) => option.value === channelType)?.labelKey
                                                return labelKey ? t(labelKey) : channelType
                                            })()}
                                        </div>
                                    </div>
                                </div>

                                <div className="flex items-center gap-2">
                                    <FormField
                                        control={form.control}
                                        name={`sources.${index}.enabled`}
                                        render={({ field: enabledField }) => (
                                            <FormItem className="flex items-center gap-2 space-y-0">
                                                <FormLabel className="text-sm font-normal">{t("common.enabled")}</FormLabel>
                                                <FormControl>
                                                    <Switch checked={enabledField.value} onCheckedChange={enabledField.onChange} />
                                                </FormControl>
                                            </FormItem>
                                        )}
                                    />

                                    <Button
                                        type="button"
                                        variant="ghost"
                                        size="icon"
                                        onClick={() => onRemoveSource(index)}
                                        disabled={watchedTrackerChannels.length <= 1}
                                    >
                                        <Trash2 className="h-4 w-4" />
                                    </Button>
                                </div>
                            </div>

                            <div className="space-y-4 p-4">
                                <div className="grid items-start gap-4 md:grid-cols-2">
                                    <FormField
                                        control={form.control}
                                        name={`sources.${index}.source_key`}
                                        render={({ field: channelKeyField }) => (
                                            <FormItem className="w-full max-w-md">
                                                <FormLabel>{t("trackers.aggregate.fields.sourceKey")}</FormLabel>
                                                <FormControl>
                                                    <Input
                                                        {...channelKeyField}
                                                        onChange={(event) => {
                                                            channelKeyField.onChange(event)
                                                        }}
                                                        placeholder={t("trackers.aggregate.fields.sourceKeyPlaceholder")}
                                                    />
                                                </FormControl>
                                                <FormMessage />
                                            </FormItem>
                                        )}
                                    />

                                    <TrackerDialogSourceConfigFields form={form} index={index} channelType={channelType} group="primary" />
                                </div>

                                <div className="grid items-start gap-4 md:grid-cols-3">
                                    <FormField
                                        control={form.control}
                                        name={`sources.${index}.source_type`}
                                        render={({ field: channelTypeField }) => (
                                            <FormItem className="w-full max-w-xs">
                                                <FormLabel>{t("trackers.aggregate.fields.sourceType")}</FormLabel>
                                                <Select value={channelTypeField.value} onValueChange={(value) => channelTypeField.onChange(value as TrackerChannelType)}>
                                                    <FormControl>
                                                        <SelectTrigger>
                                                            <SelectValue />
                                                        </SelectTrigger>
                                                    </FormControl>
                                                    <SelectContent>
                                                        {SOURCE_TYPE_OPTIONS.map((option) => (
                                                            <SelectItem key={option.value} value={option.value}>{t(option.labelKey)}</SelectItem>
                                                        ))}
                                                    </SelectContent>
                                                </Select>
                                                <FormMessage />
                                            </FormItem>
                                        )}
                                    />

                                    <FormField
                                        control={form.control}
                                        name={`sources.${index}.credential_name`}
                                        render={({ field: credentialField }) => (
                                            <FormItem className="w-full max-w-sm">
                                                <FormLabel>{t("trackers.aggregate.fields.credential")}</FormLabel>
                                                <Select value={credentialField.value || "none"} onValueChange={(value) => credentialField.onChange(value === "none" ? "" : value)}>
                                                    <FormControl>
                                                        <SelectTrigger>
                                                            <SelectValue placeholder={t("trackers.aggregate.fields.credentialPlaceholder")} />
                                                        </SelectTrigger>
                                                    </FormControl>
                                                    <SelectContent>
                                                        <SelectItem value="none">{t("trackers.aggregate.fields.none")}</SelectItem>
                                                        {credentialOptions.map((credential) => (
                                                            <SelectItem key={credential.id} value={credential.name}>
                                                                {credential.name} ({getCredentialTypeLabel(t, credential.type)})
                                                            </SelectItem>
                                                        ))}
                                                    </SelectContent>
                                                </Select>
                                                <FormMessage />
                                            </FormItem>
                                        )}
                                    />

                                    <TrackerDialogSourceConfigFields form={form} index={index} channelType={channelType} group="secondary" />
                                </div>
                            </div>

                            <div className="mx-4 mb-4 space-y-3 border-t border-border/50 pt-4">
                                <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                                    <div className="max-w-2xl space-y-1">
                                        <div className="text-sm font-semibold text-foreground/90">{t("trackers.aggregate.releaseChannels.title")}</div>
                                        <p className="text-sm text-muted-foreground">{t("trackers.aggregate.releaseChannels.description")}</p>
                                    </div>
                                    <Button
                                        type="button"
                                        variant="outline"
                                        size="sm"
                                        onClick={() => onAddReleaseChannel(index)}
                                        disabled={releaseChannels.length >= RELEASE_CHANNEL_PRESETS.length}
                                    >
                                        <Plus className="mr-2 h-4 w-4" />
                                        {t("trackers.aggregate.releaseChannels.add")}
                                    </Button>
                                </div>

                                {releaseChannels.length === 0 ? (
                                    <p className="text-sm text-muted-foreground">{t("trackers.aggregate.releaseChannels.empty")}</p>
                                ) : releaseChannels.map((releaseChannel, releaseChannelIndex) => (
                                    <TrackerDialogReleaseChannelPanel
                                        key={getReleaseChannelIdentity(
                                            releaseChannel,
                                            watchedTrackerChannels[index]?.source_key || `source-${index + 1}`,
                                            releaseChannelIndex,
                                        )}
                                        form={form}
                                        trackerChannelIndex={index}
                                        releaseChannel={releaseChannel}
                                        sourceType={channelType}
                                        releaseChannelIndex={releaseChannelIndex}
                                        releaseChannels={releaseChannels}
                                        ownerChannelKey={watchedTrackerChannels[index]?.source_key || `source-${index + 1}`}
                                        expandedReleaseChannels={expandedReleaseChannels}
                                        onRemoveReleaseChannel={onRemoveReleaseChannel}
                                        onToggleReleaseChannelExpanded={onToggleReleaseChannelExpanded}
                                        onClearReleaseChannelListError={onClearReleaseChannelListError}
                                    />
                                ))}
                            </div>
                        </div>
                    )
                })}
            </CardContent>
        </Card>
    )
}

interface TrackerDialogReleaseChannelPanelProps {
    form: UseFormReturn<TrackerFormValues>
    trackerChannelIndex: number
    releaseChannel: ReleaseChannelInput
    sourceType: TrackerChannelType
    releaseChannelIndex: number
    releaseChannels: ReleaseChannelInput[]
    ownerChannelKey: string
    expandedReleaseChannels: Record<string, boolean>
    onRemoveReleaseChannel: (trackerChannelIndex: number, releaseChannelIndex: number) => void
    onToggleReleaseChannelExpanded: (releaseChannelKey: string) => void
    onClearReleaseChannelListError: () => void
}

function TrackerDialogReleaseChannelPanel({
    form,
    trackerChannelIndex,
    releaseChannel,
    sourceType,
    releaseChannelIndex,
    releaseChannels,
    ownerChannelKey,
    expandedReleaseChannels,
    onRemoveReleaseChannel,
    onToggleReleaseChannelExpanded,
    onClearReleaseChannelListError,
}: TrackerDialogReleaseChannelPanelProps) {
    const { t } = useTranslation()

    const releaseChannelIdentity = getReleaseChannelIdentity(releaseChannel, ownerChannelKey, releaseChannelIndex)
    const releaseChannelErrors = form.formState.errors.sources?.[trackerChannelIndex]?.release_channels?.[releaseChannelIndex]
    const hasReleaseChannelError = Boolean(releaseChannelErrors)
    const isReleaseChannelExpanded = hasReleaseChannelError || expandedReleaseChannels[releaseChannelIdentity] === true
    const canFilterByReleaseType = supportsReleaseTypeFilter(sourceType)
    const availableNames = RELEASE_CHANNEL_PRESETS.filter((preset) => (
        preset.name === releaseChannel.name
        || !releaseChannels.some((channel, channelIndex) => channelIndex !== releaseChannelIndex && channel.name === preset.name)
    ))

    return (
        <div className="rounded-xl border-l-2 border-l-primary/35 bg-muted/20 p-3 shadow-sm shadow-transparent">
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div className="flex flex-col gap-1">
                    <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="-ml-2 h-auto px-2 py-1 text-left"
                        onClick={() => onToggleReleaseChannelExpanded(releaseChannelIdentity)}
                    >
                        {isReleaseChannelExpanded ? <ChevronDown className="mr-2 h-4 w-4" /> : <ChevronRight className="mr-2 h-4 w-4" />}
                        <span className="text-sm font-semibold">{getReleaseChannelHeaderLabel(releaseChannel, t, sourceType)}</span>
                    </Button>
                </div>

                <p className="text-sm text-muted-foreground md:flex-1">
                    {t("trackers.aggregate.releaseChannels.collapsedSummary")}
                </p>

                <div className="flex items-center gap-2">
                    <FormField
                        control={form.control}
                        name={`sources.${trackerChannelIndex}.release_channels.${releaseChannelIndex}.enabled`}
                        render={({ field }) => (
                            <FormItem className="flex items-center gap-2 space-y-0">
                                <FormLabel className="text-sm font-normal">{t("common.enabled")}</FormLabel>
                                <FormControl>
                                    <Switch checked={field.value ?? true} onCheckedChange={field.onChange} />
                                </FormControl>
                            </FormItem>
                        )}
                    />

                    <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        onClick={() => onRemoveReleaseChannel(trackerChannelIndex, releaseChannelIndex)}
                    >
                        <Trash2 className="h-4 w-4" />
                    </Button>
                </div>
            </div>

            {isReleaseChannelExpanded ? (
                <div className="mt-4 grid gap-4 rounded-lg bg-background/70 p-4 md:grid-cols-2 xl:grid-cols-4">
                    <FormField
                        control={form.control}
                        name={`sources.${trackerChannelIndex}.release_channels.${releaseChannelIndex}.name`}
                        render={({ field }) => (
                            <FormItem className="w-full max-w-sm">
                                <FormLabel>{t("trackers.aggregate.releaseChannels.fields.name")}</FormLabel>
                                <Select
                                    value={field.value}
                                    onValueChange={(value) => {
                                        field.onChange(value as ReleaseChannelInput["name"])
                                        onClearReleaseChannelListError()
                                    }}
                                >
                                    <FormControl>
                                        <SelectTrigger>
                                            <SelectValue />
                                        </SelectTrigger>
                                    </FormControl>
                                    <SelectContent>
                                        {availableNames.map((preset) => (
                                            <SelectItem key={preset.name} value={preset.name}>
                                                {getChannelLabel(preset.name)}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                                <FormMessage />
                            </FormItem>
                        )}
                    />

                    {canFilterByReleaseType ? (
                        <FormField
                            control={form.control}
                            name={`sources.${trackerChannelIndex}.release_channels.${releaseChannelIndex}.type`}
                            render={({ field }) => (
                                <FormItem className="w-full max-w-sm">
                                    <FormLabel>{t("trackers.aggregate.releaseChannels.fields.type")}</FormLabel>
                                    <Select
                                        value={field.value ?? undefined}
                                        onValueChange={(value) => field.onChange(value as NonNullable<ReleaseChannelInput["type"]>)}
                                    >
                                        <FormControl>
                                            <SelectTrigger>
                                                <SelectValue placeholder={t("trackers.aggregate.releaseChannels.fields.typePlaceholder")} />
                                            </SelectTrigger>
                                        </FormControl>
                                        <SelectContent>
                                            {RELEASE_TYPE_OPTIONS.map((option) => (
                                                <SelectItem key={option.value} value={option.value}>
                                                    {t(option.labelKey)}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                    <FormMessage />
                                </FormItem>
                            )}
                        />
                    ) : null}

                    <FormField
                        control={form.control}
                        name={`sources.${trackerChannelIndex}.release_channels.${releaseChannelIndex}.include_pattern`}
                        render={({ field }) => (
                            <FormItem className="xl:col-span-2 w-full max-w-2xl">
                                <FormLabel>{t("trackers.aggregate.releaseChannels.fields.includePattern")}</FormLabel>
                                <FormControl>
                                    <Input
                                        {...field}
                                        value={field.value ?? ""}
                                        placeholder={t("trackers.aggregate.releaseChannels.fields.includePatternPlaceholder")}
                                    />
                                </FormControl>
                                <FormMessage />
                            </FormItem>
                        )}
                    />

                    <FormField
                        control={form.control}
                        name={`sources.${trackerChannelIndex}.release_channels.${releaseChannelIndex}.exclude_pattern`}
                        render={({ field }) => (
                            <FormItem className="xl:col-span-2 w-full max-w-2xl">
                                <FormLabel>{t("trackers.aggregate.releaseChannels.fields.excludePattern")}</FormLabel>
                                <FormControl>
                                    <Input
                                        {...field}
                                        value={field.value ?? ""}
                                        placeholder={t("trackers.aggregate.releaseChannels.fields.excludePatternPlaceholder")}
                                    />
                                </FormControl>
                                <FormMessage />
                            </FormItem>
                        )}
                    />
                </div>
            ) : null}
        </div>
    )
}

interface TrackerDialogFetchingSectionProps {
    form: UseFormReturn<TrackerFormValues>
}

export function TrackerDialogFetchingSection({ form }: TrackerDialogFetchingSectionProps) {
    const { t } = useTranslation()

    return (
        <Card className="h-full gap-4 border-border/70 bg-background shadow-sm">
            <CardHeader className="pb-3">
                <CardTitle>{t("trackers.aggregate.fetching.title")}</CardTitle>
            </CardHeader>
            <CardContent className="grid items-start gap-4 md:grid-cols-2">
                <FormField
                    control={form.control}
                    name="interval"
                    render={({ field }) => (
                        <FormItem className="w-full max-w-xs">
                            <FormLabel>{t("tracker.fields.interval")}</FormLabel>
                            <FormControl>
                                <Input type="number" min={1} value={field.value} onChange={(event) => field.onChange(Number(event.target.value) || 360)} />
                            </FormControl>
                            <FormMessage />
                        </FormItem>
                    )}
                />

                <FormField
                    control={form.control}
                    name="fetch_limit"
                    render={({ field }) => (
                        <FormItem className="w-full max-w-xs">
                            <FormLabel>{t("tracker.fields.fetchLimit")}</FormLabel>
                            <FormControl>
                                <Input type="number" min={1} max={100} value={field.value} onChange={(event) => field.onChange(Number(event.target.value) || 10)} />
                            </FormControl>
                            <FormMessage />
                        </FormItem>
                    )}
                />

                <FormField
                    control={form.control}
                    name="fetch_timeout"
                    render={({ field }) => (
                        <FormItem className="w-full max-w-xs">
                            <FormLabel>{t("tracker.fields.fetchTimeout")}</FormLabel>
                            <FormControl>
                                <Input type="number" min={1} max={180} value={field.value} onChange={(event) => field.onChange(Number(event.target.value) || 15)} />
                            </FormControl>
                            <FormMessage />
                        </FormItem>
                    )}
                />

                <FormField
                    control={form.control}
                    name="version_sort_mode"
                    render={({ field }) => (
                        <FormItem className="w-full max-w-sm">
                            <FormLabel>{t("tracker.fields.versionSortMode")}</FormLabel>
                            <Select value={field.value} onValueChange={(value) => field.onChange(value as "published_at" | "semver")}>
                                <FormControl>
                                    <SelectTrigger>
                                        <SelectValue />
                                    </SelectTrigger>
                                </FormControl>
                                <SelectContent>
                                    <SelectItem value="published_at">{t("tracker.fields.sortModePublished")}</SelectItem>
                                    <SelectItem value="semver">{t("tracker.fields.sortModeSemVer")}</SelectItem>
                                </SelectContent>
                            </Select>
                            <FormMessage />
                        </FormItem>
                    )}
                />

                <FormField
                    control={form.control}
                    name="fallback_tags"
                    render={({ field }) => (
                            <FormItem className="flex items-center justify-between rounded-lg border border-border/50 bg-muted/20 px-4 py-3 md:col-span-2">
                            <div className="space-y-1">
                                <FormLabel>{t("tracker.fields.fallbackTags")}</FormLabel>
                            </div>
                            <FormControl>
                                <Switch checked={field.value} onCheckedChange={field.onChange} />
                            </FormControl>
                        </FormItem>
                    )}
                />

            </CardContent>
        </Card>
    )
}
