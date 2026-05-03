import { useEffect, useState } from "react"
import { Loader2, Save } from "lucide-react"
import { useFieldArray, useForm, useWatch } from "react-hook-form"
import { useTranslation } from "react-i18next"

import { api } from "@/api/client"
import type { ApiCredential, CreateTrackerRequest, ReleaseChannelInput } from "@/api/types"
import { Button } from "@/components/ui/button"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import { Form } from "@/components/ui/form"
import { toast } from "sonner"

import {
    TrackerDialogFetchingSection,
    TrackerDialogIdentitySection,
    TrackerDialogTrackerChannelsSection,
} from "./TrackerDialogSections"
import {
    buildNormalizedTrackerFormValues,
    buildTrackerFormValues,
    buildTrackerPayload,
    createDefaultSource,
    createDefaultReleaseChannel,
    createDefaultValues,
    getApiErrorDetailMessage,
    getEffectivePrimarySourceKey,
    getReleaseChannelIdentity,
    getRequiredConfigKeys,
    RELEASE_CHANNEL_PRESETS,
    supportsReleaseTypeFilter,
    trimOrUndefined,
    type TrackerFormValues,
    validateRegexPattern,
} from "./trackerDialogHelpers"

interface TrackerDialogProps {
    open: boolean
    onOpenChange: (open: boolean) => void
    onSuccess: (trackerName: string) => void
    trackerName?: string | null
}

export function TrackerDialog({ open, onOpenChange, onSuccess, trackerName }: TrackerDialogProps) {
    const { t } = useTranslation()
    const [credentials, setCredentials] = useState<ApiCredential[]>([])
    const [loading, setLoading] = useState(false)
    const [sourceListError, setSourceListError] = useState<string | null>(null)
    const [releaseChannelListError, setReleaseChannelListError] = useState<string | null>(null)
    const [expandedReleaseChannels, setExpandedReleaseChannels] = useState<Record<string, boolean>>({})

    const form = useForm<TrackerFormValues>({
        defaultValues: createDefaultValues(),
    })

    const trackerChannelsFieldArray = useFieldArray({
        control: form.control,
        name: "sources",
    })

    const watchedTrackerChannels = useWatch({ control: form.control, name: "sources" })
    const trackerChannelKeySignature = watchedTrackerChannels.map((channel) => channel.source_key ?? "").join("\u0000")

    useEffect(() => {
        if (!open) {
            void Promise.resolve().then(() => setExpandedReleaseChannels({}))
            return
        }

        const loadData = async () => {
            try {
                const [credentialsData, trackerData] = await Promise.all([
                    api.getCredentials({ limit: 1000 }),
                    trackerName ? api.getTrackerConfig(trackerName) : Promise.resolve(null),
                ])

                setCredentials(credentialsData.items)
                setSourceListError(null)
                setReleaseChannelListError(null)
                setExpandedReleaseChannels({})

                if (!trackerData) {
                    form.reset(createDefaultValues())
                    return
                }

                form.reset(buildTrackerFormValues(trackerData))
            } catch (error) {
                console.error("Failed to load tracker dialog data", error)
                toast.error(t("common.unexpectedError"))
            }
        }

        void loadData()
    }, [form, open, t, trackerName])

    useEffect(() => {
        const effectivePrimaryChannelKey = getEffectivePrimarySourceKey({
            ...form.getValues(),
            sources: watchedTrackerChannels,
        })

        form.setValue("primary_changelog_source_key", effectivePrimaryChannelKey, {
            shouldDirty: true,
        })
    }, [form, trackerChannelKeySignature, watchedTrackerChannels])

    const handleAddSource = () => {
        const nextIndex = watchedTrackerChannels.length
        const nextChannel = createDefaultSource(nextIndex)
        trackerChannelsFieldArray.append(nextChannel)

        if (watchedTrackerChannels.length === 0) {
            form.setValue("primary_changelog_source_key", nextChannel.source_key)
        }

        setSourceListError(null)
    }

    const handleRemoveSource = (index: number) => {
        const currentChannels = form.getValues("sources")
        const removedChannel = currentChannels[index]

        if (currentChannels.length <= 1) {
            setSourceListError(t("trackers.aggregate.validation.atLeastOneSource"))
            return
        }

        trackerChannelsFieldArray.remove(index)

        if (removedChannel?.source_key === form.getValues("primary_changelog_source_key")) {
            const remainingChannels = currentChannels.filter((_, currentIndex) => currentIndex !== index)
            form.setValue("primary_changelog_source_key", remainingChannels[0]?.source_key ?? "")
        }
    }

    const handleAddReleaseChannel = (trackerChannelIndex: number) => {
        const ownerChannelKey = form.getValues(`sources.${trackerChannelIndex}.source_key`) || `source-${trackerChannelIndex + 1}`
        const currentChannels = form.getValues(`sources.${trackerChannelIndex}.release_channels`) ?? []
        const usedNames = new Set(currentChannels.map((channel) => channel.name))
        const nextPreset = RELEASE_CHANNEL_PRESETS.find((preset) => !usedNames.has(preset.name))

        if (!nextPreset) {
            return
        }

        const nextReleaseChannel = createDefaultReleaseChannel(nextPreset.name, "release", ownerChannelKey, currentChannels.length)
        const nextChannels = [...currentChannels, nextReleaseChannel]

        form.setValue(`sources.${trackerChannelIndex}.release_channels`, nextChannels, {
            shouldDirty: true,
        })

        const releaseChannelKey = getReleaseChannelIdentity(nextReleaseChannel, ownerChannelKey, currentChannels.length)
        setExpandedReleaseChannels((current) => ({
            ...current,
            [releaseChannelKey]: true,
        }))
        setReleaseChannelListError(null)
    }

    const handleRemoveReleaseChannel = (trackerChannelIndex: number, releaseChannelIndex: number) => {
        const currentChannels = form.getValues(`sources.${trackerChannelIndex}.release_channels`) ?? []
        const ownerChannelKey = form.getValues(`sources.${trackerChannelIndex}.source_key`) || `source-${trackerChannelIndex + 1}`
        const removedChannelKey = currentChannels[releaseChannelIndex]
            ? getReleaseChannelIdentity(currentChannels[releaseChannelIndex], ownerChannelKey, releaseChannelIndex)
            : null
        const nextChannels = currentChannels.filter((_, index) => index !== releaseChannelIndex)

        form.setValue(`sources.${trackerChannelIndex}.release_channels`, nextChannels, {
            shouldDirty: true,
        })

        if (removedChannelKey) {
            setExpandedReleaseChannels((current) => {
                const nextState = { ...current }
                delete nextState[removedChannelKey]
                return nextState
            })
        }

        setReleaseChannelListError(null)
    }

    const toggleReleaseChannelExpanded = (releaseChannelKey: string) => {
        setExpandedReleaseChannels((current) => ({
            ...current,
            [releaseChannelKey]: !(current[releaseChannelKey] ?? false),
        }))
    }

    const validateSources = (values: TrackerFormValues): boolean => {
        let isValid = true
        const seenSourceKeys = new Set<string>()
        const effectivePrimaryChannelKey = getEffectivePrimarySourceKey(values)

        setSourceListError(null)

        if (values.sources.length === 0) {
            setSourceListError(t("trackers.aggregate.validation.atLeastOneSource"))
            return false
        }

        values.sources.forEach((channel, index) => {
            const normalizedChannelKey = channel.source_key.trim()

            if (!normalizedChannelKey) {
                form.setError(`sources.${index}.source_key`, {
                    type: "manual",
                    message: t("trackers.aggregate.validation.sourceKeyRequired"),
                })
                isValid = false
            } else if (seenSourceKeys.has(normalizedChannelKey)) {
                form.setError(`sources.${index}.source_key`, {
                    type: "manual",
                    message: t("trackers.aggregate.validation.sourceKeyUnique"),
                })
                isValid = false
            } else {
                seenSourceKeys.add(normalizedChannelKey)
            }

            for (const configKey of getRequiredConfigKeys(channel.source_type)) {
                const value = channel.source_config[configKey]?.trim()
                if (!value) {
                    form.setError(`sources.${index}.source_config.${configKey}`, {
                        type: "manual",
                        message: t("trackers.aggregate.validation.requiredField"),
                    })
                    isValid = false
                }
            }
        })

        if (!effectivePrimaryChannelKey || !values.sources.some((channel) => channel.source_key.trim() === effectivePrimaryChannelKey)) {
            isValid = false
        }

        return isValid
    }

    const validateReleaseChannels = (values: TrackerFormValues): boolean => {
        let isValid = true
        let totalReleaseChannels = 0

        setReleaseChannelListError(null)

        values.sources.forEach((trackerChannel, trackerChannelIndex) => {
            const seenReleaseChannelNames = new Set<ReleaseChannelInput["name"]>()
            const channels = trackerChannel.release_channels ?? []
            totalReleaseChannels += channels.length

            channels.forEach((channel, index) => {
                if (seenReleaseChannelNames.has(channel.name)) {
                    form.setError(`sources.${trackerChannelIndex}.release_channels.${index}.name`, {
                        type: "manual",
                        message: t("trackers.aggregate.validation.releaseChannelUnique"),
                    })
                    isValid = false
                } else {
                    seenReleaseChannelNames.add(channel.name)
                }

                if (supportsReleaseTypeFilter(trackerChannel.source_type) && !channel.type) {
                    form.setError(`sources.${trackerChannelIndex}.release_channels.${index}.type`, {
                        type: "manual",
                        message: t("trackers.aggregate.validation.requiredField"),
                    })
                    isValid = false
                }

                const includeRegexError = validateRegexPattern(
                    trimOrUndefined(channel.include_pattern),
                    t("trackers.aggregate.validation.invalidIncludePattern"),
                    t("common.unexpectedError"),
                )
                if (includeRegexError) {
                    form.setError(`sources.${trackerChannelIndex}.release_channels.${index}.include_pattern`, {
                        type: "manual",
                        message: includeRegexError,
                    })
                    isValid = false
                }

                const excludeRegexError = validateRegexPattern(
                    trimOrUndefined(channel.exclude_pattern),
                    t("trackers.aggregate.validation.invalidExcludePattern"),
                    t("common.unexpectedError"),
                )
                if (excludeRegexError) {
                    form.setError(`sources.${trackerChannelIndex}.release_channels.${index}.exclude_pattern`, {
                        type: "manual",
                        message: excludeRegexError,
                    })
                    isValid = false
                }
            })
        })

        if (totalReleaseChannels === 0) {
            setReleaseChannelListError(t("trackers.aggregate.validation.atLeastOneReleaseChannel"))
            return false
        }

        return isValid
    }

    const onSubmit = async (values: TrackerFormValues) => {
        form.clearErrors()

        const effectivePrimaryChannelKey = getEffectivePrimarySourceKey(values)
        form.setValue("primary_changelog_source_key", effectivePrimaryChannelKey, {
            shouldDirty: true,
        })

        const normalizedValues = buildNormalizedTrackerFormValues(values, effectivePrimaryChannelKey)
        const sourcesAreValid = validateSources(normalizedValues)
        const releaseChannelsAreValid = validateReleaseChannels(normalizedValues)

        if (!sourcesAreValid || !releaseChannelsAreValid) {
            return
        }

        const payload: CreateTrackerRequest = buildTrackerPayload(normalizedValues, effectivePrimaryChannelKey)

        setLoading(true)
        try {
            if (trackerName) {
                await api.updateTracker(trackerName, payload)
            } else {
                await api.createTracker(payload)
            }

            toast.success(t("common.saved"))
            onSuccess(payload.name)
            onOpenChange(false)
        } catch (error: unknown) {
            console.error("Failed to save tracker", error)
            const detailMessage = getApiErrorDetailMessage(error)
            const normalizedDetailMessage = detailMessage?.toLowerCase() ?? ""

            if (detailMessage?.includes("名称已存在") || normalizedDetailMessage.includes("exist")) {
                form.setError("name", {
                    type: "manual",
                    message: t("tracker.errors.nameExists", { defaultValue: "Tracker name already exists." }),
                })
            }

            toast.error(detailMessage || t("common.unexpectedError"))
        } finally {
            setLoading(false)
        }
    }

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="flex max-h-[92vh] flex-col gap-0 overflow-hidden p-0 sm:max-w-[min(94vw,68rem)]">
                <DialogHeader className="border-b border-border/60 bg-background/95 px-6 py-5 backdrop-blur">
                    <DialogTitle className="text-xl">{trackerName ? t("trackers.aggregate.editTitle") : t("trackers.aggregate.addTitle")}</DialogTitle>
                    <DialogDescription className="max-w-3xl">{t("trackers.aggregate.description")}</DialogDescription>
                </DialogHeader>

                <Form {...form}>
                    <form onSubmit={form.handleSubmit(onSubmit)} className="flex min-h-0 flex-1 flex-col">
                        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto bg-muted/20 px-6 py-5">
                            <div className="grid gap-5 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
                                <TrackerDialogIdentitySection form={form} trackerName={trackerName} />
                                <TrackerDialogFetchingSection form={form} />
                            </div>
                            <TrackerDialogTrackerChannelsSection
                                form={form}
                                trackerChannelsFieldArray={trackerChannelsFieldArray}
                                watchedTrackerChannels={watchedTrackerChannels}
                                credentials={credentials}
                                expandedReleaseChannels={expandedReleaseChannels}
                                sourceListError={sourceListError}
                                releaseChannelListError={releaseChannelListError}
                                onAddSource={handleAddSource}
                                onRemoveSource={handleRemoveSource}
                                onAddReleaseChannel={handleAddReleaseChannel}
                                onRemoveReleaseChannel={handleRemoveReleaseChannel}
                                onToggleReleaseChannelExpanded={toggleReleaseChannelExpanded}
                                onClearReleaseChannelListError={() => setReleaseChannelListError(null)}
                            />
                        </div>

                        <DialogFooter className="border-t border-border/60 bg-background/95 px-6 py-4 backdrop-blur">
                            <Button type="submit" disabled={loading}>
                                {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                                {loading ? t("common.saving") : t("common.save")}
                            </Button>
                        </DialogFooter>
                    </form>
                </Form>
            </DialogContent>
        </Dialog>
    )
}
