import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react"
import { ArrowLeft, ArrowRight, Loader2 } from "lucide-react"
import { useForm, useWatch } from "react-hook-form"
import { useTranslation } from "react-i18next"

import { api } from "@/api/client"
import type { RuntimeConnection, RuntimeTargetDiscoveryItem, TrackerStatus } from "@/api/types"
import { Button } from "@/components/ui/button"
import {
    Sheet,
    SheetContent,
    SheetFooter,
    SheetHeader,
    SheetTitle,
} from "@/components/ui/sheet"
import { Form } from "@/components/ui/form"
import { toast } from "sonner"

import {
    ExecutorSheetBindingSection,
    ExecutorSheetPolicySection,
    ExecutorSheetReviewSection,
    ExecutorSheetStepTabs,
    ExecutorSheetTargetSection,
} from "./ExecutorSheetSections"
import {
    buildExecutorServiceBindingValues,
    buildExecutorTargetDiscoveryParams,
    buildExecutorFormValues,
    buildExecutorPayload,
    buildExecutorReviewItems,
    buildRuntimeConnectionOptions,
    buildScopedReleaseChannels,
    buildTrackerSourceOptions,
    createDefaultExecutorValues,
    EMPTY_TARGET_REF,
    filterTrackersWithBindableSources,
    getAutoSelectedTrackerSourceId,
    getApiErrorDetailMessage,
    getConfiguredKubernetesNamespaces,
    getExecutorServiceBindingValidationMessage,
    getExecutorBindingValidationMessage,
    getExecutorTargetValidationMessage,
    getExecutorValidationMessage,
    getGroupedBindingServiceOptions,
    getTrackerBindableSources,
    isHelmReleaseTarget,
    isTrackerSourceCompatibleWithTarget,
    isEquivalentDockerComposeTarget,
    isEquivalentPortainerStackTarget,
    isPortainerStackTarget,
    mergeDockerComposeTargetRef,
    resolveExecutorServiceBinding,
    runExecutorImplicitSubmitAction,
    type ExecutorServiceBindingFormValue,
    type ExecutorFormValues,
    type StepKey,
    STEP_ORDER,
    usesGroupedServiceBindings,
} from "./executorSheetHelpers"

interface ExecutorSheetProps {
    open: boolean
    onOpenChange: (open: boolean) => void
    executorId: number | null
    runtimeConnections: RuntimeConnection[]
    trackers: TrackerStatus[]
    systemTimezone: string
    onSuccess: () => void
}

export function ExecutorSheet({
    open,
    onOpenChange,
    executorId,
    runtimeConnections,
    trackers,
    systemTimezone,
    onSuccess,
}: ExecutorSheetProps) {
    const { t } = useTranslation()
    const [step, setStep] = useState<StepKey>("target")
    const [saving, setSaving] = useState(false)
    const [loadingConfig, setLoadingConfig] = useState(false)
    const [discovering, setDiscovering] = useState(false)
    const [discoveryMessage, setDiscoveryMessage] = useState<string | null>(null)
    const [discoveredTargets, setDiscoveredTargets] = useState<RuntimeTargetDiscoveryItem[]>([])
    const [selectedDiscoveryNamespace, setSelectedDiscoveryNamespace] = useState("")
    const [selectedTargetRef, setSelectedTargetRef] = useState<Record<string, unknown>>(EMPTY_TARGET_REF)
    const [serviceBindings, setServiceBindings] = useState<ExecutorServiceBindingFormValue[]>([])
    const stepScrollRef = useRef<HTMLDivElement | null>(null)

    const form = useForm<ExecutorFormValues>({
        defaultValues: createDefaultExecutorValues(),
    })

    const formValues = useWatch({ control: form.control }) as ExecutorFormValues
    const runtimeConnectionId = formValues.runtime_connection_id
    const runtimeType = formValues.runtime_type
    const updateMode = formValues.update_mode
    const imageSelectionMode = formValues.image_selection_mode
    const trackerName = formValues.tracker_name
    const trackerSourceId = formValues.tracker_source_id

    const enabledRuntimeConnections = useMemo(
        () => runtimeConnections.filter((connection) => connection.enabled),
        [runtimeConnections],
    )

    const currentRuntimeConnection = useMemo(
        () => runtimeConnections.find((connection) => String(connection.id) === runtimeConnectionId) ?? null,
        [runtimeConnections, runtimeConnectionId],
    )

    const selectedRuntimeConnection = useMemo(
        () => currentRuntimeConnection,
        [currentRuntimeConnection],
    )

    const runtimeConnectionOptions = useMemo(() => {
        return buildRuntimeConnectionOptions(currentRuntimeConnection, enabledRuntimeConnections)
    }, [currentRuntimeConnection, enabledRuntimeConnections])

    const isContainerRuntime = runtimeType === "docker" || runtimeType === "podman" || runtimeType === "kubernetes"
    const isHelmReleaseBinding = isHelmReleaseTarget(selectedTargetRef)

    const selectedTargetNamespace = typeof selectedTargetRef.namespace === "string" ? selectedTargetRef.namespace : ""

    const configuredDiscoveryNamespaces = useMemo(() => {
        const namespaces = getConfiguredKubernetesNamespaces(selectedRuntimeConnection)
        if (selectedTargetNamespace && !namespaces.includes(selectedTargetNamespace)) {
            return [selectedTargetNamespace, ...namespaces]
        }
        return namespaces
    }, [selectedRuntimeConnection, selectedTargetNamespace])

    const selectedTracker = useMemo(
        () => trackers.find((tracker) => tracker.name === trackerName) ?? null,
        [trackerName, trackers],
    )

    const allTrackerContainerSources = useMemo(
        () => isContainerRuntime
            ? (selectedTracker?.sources ?? []).filter((source) => isTrackerSourceCompatibleWithTarget(source, selectedTargetRef))
            : [],
        [isContainerRuntime, selectedTargetRef, selectedTracker],
    )

    const selectedTrackerBindableSources = useMemo(
        () => isContainerRuntime ? getTrackerBindableSources(selectedTracker, selectedTargetRef) : [],
        [isContainerRuntime, selectedTargetRef, selectedTracker],
    )

    const autoSelectedTrackerSourceId = useMemo(
        () => getAutoSelectedTrackerSourceId(selectedTrackerBindableSources),
        [selectedTrackerBindableSources],
    )

    const effectiveTrackerSourceId = trackerSourceId || autoSelectedTrackerSourceId

    const isGroupedBinding = usesGroupedServiceBindings(selectedTargetRef)

    const currentBindableSource = useMemo(
        () => allTrackerContainerSources.find((source) => String(source.id) === effectiveTrackerSourceId) ?? null,
        [allTrackerContainerSources, effectiveTrackerSourceId],
    )

    const selectedBindableSource = useMemo(
        () => currentBindableSource,
        [currentBindableSource],
    )

    const trackerSourceOptions = useMemo(() => {
        return buildTrackerSourceOptions(currentBindableSource, selectedTrackerBindableSources)
    }, [currentBindableSource, selectedTrackerBindableSources])

    const scopedReleaseChannels = useMemo(() => {
        return buildScopedReleaseChannels(selectedBindableSource, formValues.channel_name)
    }, [formValues.channel_name, selectedBindableSource])

    const containerCompatibleTrackers = useMemo(
        () => isContainerRuntime ? filterTrackersWithBindableSources(trackers, selectedTargetRef) : trackers,
        [trackers, isContainerRuntime, selectedTargetRef],
    )

    const serviceBindingValidationMessage = useMemo(
        () => isGroupedBinding
            ? getExecutorServiceBindingValidationMessage({
                bindings: serviceBindings,
                trackers: containerCompatibleTrackers,
                t,
            })
            : null,
        [containerCompatibleTrackers, isGroupedBinding, serviceBindings, t],
    )

    const targetValidationMessage = useMemo(
        () => getExecutorTargetValidationMessage({
            values: formValues,
            t,
            selectedRuntimeConnection,
            selectedTargetRef,
        }),
        [formValues, selectedRuntimeConnection, selectedTargetRef, t],
    )

    const bindingValidationMessage = useMemo(
        () => isGroupedBinding
            ? serviceBindingValidationMessage
            : getExecutorBindingValidationMessage({
                values: formValues,
                t,
                selectedRuntimeConnection,
                isContainerRuntime,
                selectedTracker,
                selectedTrackerBindableSources,
                trackerSourceId,
                selectedBindableSource,
            }),
        [formValues, isGroupedBinding, isContainerRuntime, selectedBindableSource, selectedRuntimeConnection, selectedTracker, selectedTrackerBindableSources, serviceBindingValidationMessage, t, trackerSourceId],
    )

    const reviewItems = useMemo(
        () => buildExecutorReviewItems({
            values: formValues,
            t,
            selectedRuntimeConnection,
            selectedBindableSource,
            selectedTargetRef,
            serviceBindings,
        }),
        [formValues, selectedBindableSource, selectedRuntimeConnection, selectedTargetRef, serviceBindings, t],
    )

    const validationMessage = useMemo(
        () => {
            if (!isGroupedBinding) {
                return getExecutorValidationMessage({
                    values: formValues,
                    t,
                    selectedRuntimeConnection,
                    isContainerRuntime,
                    selectedTracker,
                    selectedTrackerBindableSources,
                    trackerSourceId,
                    selectedBindableSource,
                    selectedTargetRef,
                })
            }

            const targetMessage = getExecutorTargetValidationMessage({
                values: formValues,
                t,
                selectedRuntimeConnection,
                selectedTargetRef,
            })
            if (targetMessage) {
                return targetMessage
            }

            if (serviceBindingValidationMessage) {
                return serviceBindingValidationMessage
            }

            if (!isHelmReleaseBinding && formValues.image_selection_mode === "use_tracker_image_and_tag") {
                const missingImageBinding = serviceBindings.find((binding) => !resolveExecutorServiceBinding(binding, containerCompatibleTrackers).selectedBindableSource?.source_config?.image)
                if (missingImageBinding) {
                    return t("executors.validation.trackerImageStrategyIncompatible")
                }
            }

            if (formValues.update_mode === "maintenance_window") {
                if (!formValues.maintenance_start_time || !formValues.maintenance_end_time) {
                    return t("executors.validation.maintenanceTimeRequired")
                }
            }

            return null
        },
        [containerCompatibleTrackers, formValues, isGroupedBinding, isContainerRuntime, isHelmReleaseBinding, selectedBindableSource, selectedRuntimeConnection, selectedTargetRef, selectedTracker, selectedTrackerBindableSources, serviceBindingValidationMessage, serviceBindings, t, trackerSourceId],
    )

    useEffect(() => {
        if (!isContainerRuntime) {
            if (form.getValues("tracker_source_id")) {
                form.setValue("tracker_source_id", "", { shouldDirty: true })
            }
            return
        }

        if (selectedTrackerBindableSources.length === 1) {
            const onlySourceId = String(selectedTrackerBindableSources[0].id ?? "")
            const currentSourceId = form.getValues("tracker_source_id")
            const currentSourceIsCompatible = allTrackerContainerSources.some((source) => String(source.id) === currentSourceId)
            if (onlySourceId && (!currentSourceId || executorId === null || !currentSourceIsCompatible)) {
                form.setValue("tracker_source_id", onlySourceId, { shouldDirty: true })
            }
            return
        }

        if (trackerSourceId && allTrackerContainerSources.some((source) => String(source.id) === trackerSourceId)) {
            return
        }

        if (form.getValues("tracker_source_id")) {
            form.setValue("tracker_source_id", "", { shouldDirty: true })
            form.setValue("channel_name", "", { shouldDirty: true })
        }
    }, [allTrackerContainerSources, executorId, form, isContainerRuntime, selectedTrackerBindableSources, trackerSourceId])

    useEffect(() => {
        if (!open) {
            return
        }

        void Promise.resolve().then(() => {
            setStep("target")
            setDiscoveryMessage(null)
            setDiscoveredTargets([])
            setSelectedTargetRef(EMPTY_TARGET_REF)
            setServiceBindings([])
        })

        if (executorId === null) {
            form.reset({
                ...createDefaultExecutorValues(enabledRuntimeConnections[0]),
                maintenance_timezone: systemTimezone,
            })
            return
        }

        const loadExecutor = async () => {
            setLoadingConfig(true)
            try {
                const config = await api.getExecutorConfig(executorId)
                form.reset({
                    ...buildExecutorFormValues(config),
                    maintenance_timezone: systemTimezone,
                })
                setSelectedTargetRef(config.target_ref ?? EMPTY_TARGET_REF)
                setServiceBindings(buildExecutorServiceBindingValues(config, trackers))
            } catch (error) {
                console.error("Failed to load executor config", error)
                toast.error(t("executors.toasts.loadConfigFailed"))
            } finally {
                setLoadingConfig(false)
            }
        }

        void loadExecutor()
    }, [enabledRuntimeConnections, executorId, form, open, systemTimezone, t, trackers])

    useEffect(() => {
        if (selectedRuntimeConnection?.type !== "kubernetes") {
            if (selectedDiscoveryNamespace) {
                void Promise.resolve().then(() => setSelectedDiscoveryNamespace(""))
            }
            return
        }

        if (selectedTargetNamespace && configuredDiscoveryNamespaces.includes(selectedTargetNamespace)) {
            if (selectedDiscoveryNamespace !== selectedTargetNamespace) {
                void Promise.resolve().then(() => setSelectedDiscoveryNamespace(selectedTargetNamespace))
            }
            return
        }

        if (configuredDiscoveryNamespaces.length === 1) {
            const onlyNamespace = configuredDiscoveryNamespaces[0]
            if (selectedDiscoveryNamespace !== onlyNamespace) {
                void Promise.resolve().then(() => setSelectedDiscoveryNamespace(onlyNamespace))
            }
            return
        }

        if (selectedDiscoveryNamespace && !configuredDiscoveryNamespaces.includes(selectedDiscoveryNamespace)) {
            void Promise.resolve().then(() => {
                setSelectedDiscoveryNamespace("")
                setDiscoveredTargets([])
                setDiscoveryMessage(null)
            })
        }
    }, [configuredDiscoveryNamespaces, selectedDiscoveryNamespace, selectedRuntimeConnection, selectedTargetNamespace])

    useLayoutEffect(() => {
        const scrollContainer = stepScrollRef.current
        if (!scrollContainer) {
            return
        }

        scrollContainer.scrollTop = 0
        const frame = window.requestAnimationFrame(() => {
            scrollContainer.scrollTop = 0
        })

        return () => window.cancelAnimationFrame(frame)
    }, [step])

    useEffect(() => {
        if (!selectedRuntimeConnection) {
            return
        }

        if (
            selectedRuntimeConnection.type !== "docker"
            && selectedRuntimeConnection.type !== "podman"
            && selectedRuntimeConnection.type !== "kubernetes"
            && selectedRuntimeConnection.type !== "portainer"
        ) {
            return
        }

        if (form.getValues("runtime_type") !== selectedRuntimeConnection.type) {
            form.setValue("runtime_type", selectedRuntimeConnection.type, { shouldDirty: true })
        }
    }, [form, selectedRuntimeConnection])

    const handleDiscoverTargets = async () => {
        if (!selectedRuntimeConnection) {
            toast.error(t("executors.validation.runtimeRequired"))
            return
        }

        if (selectedRuntimeConnection.type === "kubernetes" && !selectedDiscoveryNamespace) {
            toast.error(t("executors.discovery.namespaceRequired"))
            return
        }

        setDiscovering(true)
        setDiscoveryMessage(null)

        try {
            const response = await api.discoverExecutorTargets(
                selectedRuntimeConnection.id,
                buildExecutorTargetDiscoveryParams(
                    selectedRuntimeConnection,
                    selectedDiscoveryNamespace,
                ),
            )
            setDiscoveredTargets(response.items)
            if (response.items.length === 0) {
                setDiscoveryMessage(t("executors.discovery.empty"))
                return
            }

            const currentTarget = JSON.stringify(selectedTargetRef)
            const matched = response.items.find((item) => JSON.stringify(item.target_ref) === currentTarget) ?? response.items[0]

            if (isPortainerStackTarget(selectedTargetRef)) {
                const matchedStack = response.items.find((item) => isEquivalentPortainerStackTarget(item.target_ref, selectedTargetRef))

                if (matchedStack) {
                    setSelectedTargetRef(matchedStack.target_ref)
                    return
                }
            }

            const matchedComposeProject = response.items.find((item) => isEquivalentDockerComposeTarget(item.target_ref, selectedTargetRef))
            if (matchedComposeProject) {
                setSelectedTargetRef(mergeDockerComposeTargetRef(matchedComposeProject.target_ref, selectedTargetRef))
                return
            }

            if (matched && JSON.stringify(matched.target_ref) === currentTarget) {
                setSelectedTargetRef(matched.target_ref)
                return
            }

            setSelectedTargetRef(EMPTY_TARGET_REF)
            setServiceBindings([])
        } catch (error: unknown) {
            console.error("Failed to discover runtime targets", error)
            const detail = getApiErrorDetailMessage(error)
            setDiscoveryMessage(detail || t("executors.discovery.failed"))
        } finally {
            setDiscovering(false)
        }
    }

    const handleSelectRuntimeConnection = (value: string) => {
        const currentConnectionId = currentRuntimeConnection ? String(currentRuntimeConnection.id) : form.getValues("runtime_connection_id")
        if (value === currentConnectionId) {
            return
        }

        form.setValue("runtime_connection_id", value, { shouldDirty: true })
        const connection = enabledRuntimeConnections.find((item) => String(item.id) === value)
        if (
            connection
            && (connection.type === "docker" || connection.type === "podman" || connection.type === "kubernetes" || connection.type === "portainer")
        ) {
            form.setValue("runtime_type", connection.type, { shouldDirty: true })
        }
        setDiscoveredTargets([])
        setSelectedTargetRef(EMPTY_TARGET_REF)
        setServiceBindings([])
        setSelectedDiscoveryNamespace("")
        setDiscoveryMessage(null)
    }

    const handleSelectDiscoveryNamespace = (namespace: string) => {
        if (namespace === selectedDiscoveryNamespace) {
            return
        }

        setSelectedDiscoveryNamespace(namespace)
        setDiscoveredTargets([])
        setDiscoveryMessage(null)
    }

    const handleSelectTarget = (target: RuntimeTargetDiscoveryItem) => {
        const nextTargetRef = target.target_ref
        if (isHelmReleaseTarget(nextTargetRef) !== isHelmReleaseTarget(selectedTargetRef)) {
            form.setValue("tracker_name", "", { shouldDirty: true })
            form.setValue("tracker_source_id", "", { shouldDirty: true })
            form.setValue("channel_name", "", { shouldDirty: true })
        }
        setSelectedTargetRef(nextTargetRef)
        setServiceBindings([])
        setDiscoveryMessage(null)
        setStep("binding")
    }

    const handleSelectTracker = (value: string) => {
        form.setValue("tracker_name", value, { shouldDirty: true })
        form.setValue("tracker_source_id", "", { shouldDirty: true })
        form.setValue("channel_name", "", { shouldDirty: true })
    }

    const handleSelectTrackerSource = (value: string) => {
        form.setValue("tracker_source_id", value, { shouldDirty: true })
        form.setValue("channel_name", "", { shouldDirty: true })
    }

    const handleSelectChannel = (value: string) => {
        form.setValue("channel_name", value, { shouldDirty: true })
    }

    const handleAddServiceBinding = () => {
        const usedServices = new Set(serviceBindings.map((binding) => binding.service.trim().toLowerCase()).filter(Boolean))
        const nextService: string = getGroupedBindingServiceOptions(selectedTargetRef)
            .map((option) => option.service)
            .find((service) => service && !usedServices.has(service.trim().toLowerCase())) ?? ""

        setServiceBindings((current) => [
            ...current,
            {
                service: nextService,
                tracker_name: "",
                tracker_source_id: "",
                channel_name: "",
            },
        ])
    }

    const handleUpdateServiceBinding = (index: number, patch: Partial<ExecutorServiceBindingFormValue>) => {
        setServiceBindings((current) => current.map((binding, bindingIndex) => {
            if (bindingIndex !== index) {
                return binding
            }

            const nextBinding = { ...binding, ...patch }
            if (patch.tracker_name !== undefined) {
                nextBinding.tracker_source_id = ""
                nextBinding.channel_name = ""
            }
            if (patch.tracker_source_id !== undefined) {
                nextBinding.channel_name = ""
            }
            return nextBinding
        }))
    }

    const handleRemoveServiceBinding = (index: number) => {
        setServiceBindings((current) => current.filter((_, bindingIndex) => bindingIndex !== index))
    }

    const handleNext = async () => {
        if (step === "target") {
            const valid = await form.trigger(["name", "runtime_connection_id"])
            if (!valid) {
                return
            }

            if (targetValidationMessage) {
                toast.error(targetValidationMessage)
                return
            }

            setStep("binding")
            return
        }

        if (step === "binding") {
            if (bindingValidationMessage) {
                toast.error(bindingValidationMessage)
                return
            }
            setStep("policy")
            return
        }

        if (step === "policy") {
            if (updateMode === "maintenance_window") {
                const valid = await form.trigger(["maintenance_start_time", "maintenance_end_time"])
                if (!valid) {
                    return
                }
            }
            setStep("review")
        }
    }

    const handleBack = () => {
        const index = STEP_ORDER.indexOf(step)
        if (index > 0) {
            setStep(STEP_ORDER[index - 1])
        }
    }

    const handleSave = async (values: ExecutorFormValues) => {
        if (validationMessage) {
            toast.error(validationMessage)
            return
        }

        setSaving(true)
        try {
            const payload = buildExecutorPayload({
                values: { ...values, maintenance_timezone: systemTimezone || values.maintenance_timezone },
                effectiveTrackerSourceId,
                selectedTargetRef,
                trackers: containerCompatibleTrackers,
                serviceBindings,
            })

            if (executorId === null) {
                await api.createExecutor(payload)
            } else {
                await api.updateExecutor(executorId, payload)
            }

            toast.success(t("common.saved"))
            onSuccess()
            onOpenChange(false)
        } catch (error: unknown) {
            console.error("Failed to save executor", error)
            const detail = getApiErrorDetailMessage(error)
            toast.error(detail || t("common.unexpectedError"))
        } finally {
            setSaving(false)
        }
    }

    const handleImplicitFormSubmit = (event: React.FormEvent<HTMLFormElement>) => {
        event.preventDefault()
        runExecutorImplicitSubmitAction(step, {
            onNext: () => void handleNext(),
        })
    }

    const handleExplicitSave = form.handleSubmit((values) => {
        void handleSave(values)
    })

    return (
        <Sheet open={open} onOpenChange={onOpenChange}>
            <SheetContent side="right" className="w-full border-l sm:max-w-5xl">
                <SheetHeader className="border-b border-border/60 pb-4">
                    <SheetTitle>{executorId === null ? t("executors.sheet.addTitle") : t("executors.sheet.editTitle")}</SheetTitle>
                </SheetHeader>

                <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
                    <ExecutorSheetStepTabs step={step} onStepChange={setStep} />

                    <Form {...form}>
                        <form onSubmit={handleImplicitFormSubmit} className="flex min-h-0 flex-1 flex-col overflow-hidden">
                            <div ref={stepScrollRef} className="flex-1 overflow-y-auto px-4 py-4 sm:px-6">
                                {loadingConfig ? (
                                    <div className="flex h-full items-center justify-center py-20 text-muted-foreground">
                                        <Loader2 className="mr-2 h-5 w-5 animate-spin" />
                                        {t("common.loading")}
                                    </div>
                                ) : (
                                    <div className="space-y-4">
                                        {step === "target" ? (
                                            <ExecutorSheetTargetSection
                                                form={form}
                                                runtimeType={runtimeType}
                                                selectedRuntimeConnection={selectedRuntimeConnection}
                                                enabledRuntimeConnections={runtimeConnectionOptions}
                                                discovering={discovering}
                                                 discoveryMessage={discoveryMessage}
                                                 selectedTargetRef={selectedTargetRef}
                                                 discoveredTargets={discoveredTargets}
                                                 configuredDiscoveryNamespaces={configuredDiscoveryNamespaces}
                                                 selectedDiscoveryNamespace={selectedDiscoveryNamespace}
                                                 onDiscoverTargets={handleDiscoverTargets}
                                                 onSelectDiscoveryNamespace={handleSelectDiscoveryNamespace}
                                                 onSelectRuntimeConnection={handleSelectRuntimeConnection}
                                                 onSelectTarget={handleSelectTarget}
                                            />
                                        ) : null}

                                        {step === "binding" ? (
                                            <ExecutorSheetBindingSection
                                                form={form}
                                                trackers={containerCompatibleTrackers}
                                                isContainerRuntime={isContainerRuntime}
                                                trackerName={trackerName}
                                                effectiveTrackerSourceId={effectiveTrackerSourceId}
                                                selectedTrackerBindableSources={trackerSourceOptions}
                                                scopedReleaseChannels={scopedReleaseChannels}
                                                runtimeType={runtimeType}
                                                selectedTargetRef={selectedTargetRef}
                                                serviceBindings={serviceBindings}
                                                onSelectTracker={handleSelectTracker}
                                                onSelectTrackerSource={handleSelectTrackerSource}
                                                onSelectChannel={handleSelectChannel}
                                                onAddServiceBinding={handleAddServiceBinding}
                                                onUpdateServiceBinding={handleUpdateServiceBinding}
                                                onRemoveServiceBinding={handleRemoveServiceBinding}
                                            />
                                        ) : null}

                                        {step === "policy" ? (
                                            <ExecutorSheetPolicySection
                                                form={form}
                                                updateMode={updateMode}
                                                imageSelectionMode={imageSelectionMode}
                                                selectedTargetRef={selectedTargetRef}
                                                selectedTracker={selectedTracker}
                                                selectedBindableSource={selectedBindableSource}
                                            />
                                        ) : null}

                                        {step === "review" ? (
                                            <ExecutorSheetReviewSection
                                                reviewItems={reviewItems}
                                                trackers={containerCompatibleTrackers}
                                                serviceBindings={serviceBindings}
                                                runtimeType={runtimeType}
                                                selectedTargetRef={selectedTargetRef}
                                                imageSelectionMode={imageSelectionMode}
                                                validationMessage={validationMessage}
                                            />
                                        ) : null}
                                    </div>
                                )}
                            </div>

                            <SheetFooter className="border-t border-border/60 px-4 py-4 sm:px-6">
                                <div className="flex w-full items-center justify-between gap-3">
                                    <Button type="button" variant="ghost" onClick={handleBack} disabled={step === "target"}>
                                        <ArrowLeft className="mr-2 h-4 w-4" />
                                        {t("common.back")}
                                    </Button>
                                    <div className="flex items-center gap-2">
                                        <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                                            {t("common.cancel")}
                                        </Button>
                                        {step === "review" ? (
                                            <Button type="button" onClick={handleExplicitSave} disabled={saving || loadingConfig || !!validationMessage}>
                                                {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                                                {executorId === null ? t("executors.actions.create") : t("common.save")}
                                            </Button>
                                        ) : (
                                            <Button type="button" onClick={handleNext} disabled={loadingConfig}>
                                                {t("executors.actions.continue")}
                                                <ArrowRight className="ml-2 h-4 w-4" />
                                            </Button>
                                        )}
                                    </div>
                                </div>
                            </SheetFooter>
                        </form>
                    </Form>
                </div>
            </SheetContent>
        </Sheet>
    )
}
