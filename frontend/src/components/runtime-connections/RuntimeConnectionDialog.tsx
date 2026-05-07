import { useEffect, useRef, useState } from "react"
import { Loader2, Save } from "lucide-react"
import { useForm, useWatch, type UseFormReturn } from "react-hook-form"
import { useTranslation } from "react-i18next"

import { api } from "@/api/client"
import type {
    ApiCredential,
    RuntimeConnection,
    RuntimeConnectionType,
} from "@/api/types"
import { Button } from "@/components/ui/button"
import {
    Combobox,
    ComboboxChip,
    ComboboxChips,
    ComboboxContent,
    ComboboxEmpty,
    ComboboxItem,
    ComboboxList,
    useComboboxAnchor,
} from "@/components/ui/combobox"
import {
    Dialog,
    DialogContent,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import {
    Form,
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
import {
    buildPayload,
    buildUpdatePayload,
    type RuntimeConnectionFormValues,
} from "./runtimeConnectionHelpers"
import { toast } from "sonner"

interface RuntimeConnectionDialogProps {
    open: boolean
    onOpenChange: (open: boolean) => void
    runtimeConnection: RuntimeConnection | null
    onSuccess: () => void
}

const DEFAULT_VALUES: RuntimeConnectionFormValues = {
    name: "",
    type: "docker",
    enabled: true,
    description: "",
    credential_id: "",
    socket: "",
    tls_verify: false,
    api_version: "",
    context: "",
    namespaces: [],
    in_cluster: false,
    base_url: "",
    endpoint_id: "",
}

export function RuntimeConnectionDialog({ open, onOpenChange, runtimeConnection, onSuccess }: RuntimeConnectionDialogProps) {
    const { t } = useTranslation()
    const [loading, setLoading] = useState(false)
    const [discoveringNamespaces, setDiscoveringNamespaces] = useState(false)
    const [discoveredNamespaces, setDiscoveredNamespaces] = useState<string[]>([])
    const [credentials, setCredentials] = useState<ApiCredential[]>([])
    const dialogContentRef = useRef<HTMLDivElement | null>(null)

    const form = useForm<RuntimeConnectionFormValues>({
        defaultValues: DEFAULT_VALUES,
    })

    useEffect(() => {
        if (!open) {
            return
        }

        api.getCredentials({ limit: 100 })
            .then((result) => setCredentials(result.items))
            .catch((error) => {
                console.error('Failed to load credentials', error)
                toast.error(t('common.unexpectedError'))
            })

        if (!runtimeConnection) {
            form.reset(DEFAULT_VALUES)
            return
        }

        form.reset({
            ...DEFAULT_VALUES,
            name: runtimeConnection.name,
            type: runtimeConnection.type,
            enabled: runtimeConnection.enabled,
            description: runtimeConnection.description || "",
            credential_id: runtimeConnection.credential_id ? String(runtimeConnection.credential_id) : "",
            socket: getStringValue(runtimeConnection.config.socket) || getStringValue(runtimeConnection.config.host),
            tls_verify: runtimeConnection.config.tls_verify === true,
            api_version: getStringValue(runtimeConnection.config.api_version),
            context: getStringValue(runtimeConnection.config.context),
            namespaces: getArrayStringValues(runtimeConnection.config.namespaces).length > 0
                ? getArrayStringValues(runtimeConnection.config.namespaces)
                : (getStringValue(runtimeConnection.config.namespace) ? [getStringValue(runtimeConnection.config.namespace)] : []),
            in_cluster: runtimeConnection.config.in_cluster === true,
            base_url: getStringValue(runtimeConnection.config.base_url),
            endpoint_id: stringifyInteger(runtimeConnection.config.endpoint_id),
        })
    }, [open, runtimeConnection, form])

    const selectedType = useWatch({ control: form.control, name: 'type' })
    const useInClusterAuth = useWatch({ control: form.control, name: 'in_cluster' })

    useEffect(() => {
        if (selectedType === 'kubernetes' && useInClusterAuth) {
            form.setValue('credential_id', '')
        }
    }, [selectedType, useInClusterAuth, form])

    useEffect(() => {
        if (!open) {
            void Promise.resolve().then(() => {
                setDiscoveringNamespaces(false)
                setDiscoveredNamespaces([])
            })
        }
    }, [open])

    const onSubmit = async (values: RuntimeConnectionFormValues) => {
        const validationError = validateRuntimeCredentialSelection(values)
        if (validationError) {
            toast.error(t(validationError))
            return
        }

        setLoading(true)

        try {
            const payload = buildPayload(values)

            if (runtimeConnection) {
                const updatePayload = buildUpdatePayload(payload)
                await api.updateRuntimeConnection(runtimeConnection.id, updatePayload)
                toast.success(t('runtimeConnections.dialog.updateSuccess'))
            } else {
                await api.createRuntimeConnection(payload)
                toast.success(t('runtimeConnections.dialog.createSuccess'))
            }

            onSuccess()
            onOpenChange(false)
        } catch (error: unknown) {
            console.error('Failed to save runtime connection', error)
            const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
            toast.error(detail || t('common.unexpectedError'))
        } finally {
            setLoading(false)
        }
    }

    const handleDiscoverNamespaces = async () => {
        const values = form.getValues()
        const hasSelectedCredential = Boolean(values.credential_id.trim())
        if (!values.in_cluster && !hasSelectedCredential) {
            toast.error(t('runtimeConnections.dialog.errors.kubeconfigRequiredForNamespaceDiscovery'))
            return
        }

        setDiscoveringNamespaces(true)

        try {
            const payload = buildPayload(values)
            const result = await api.discoverKubernetesNamespaces({
                ...(runtimeConnection ? { id: runtimeConnection.id } : {}),
                ...payload,
            })
            setDiscoveredNamespaces(result.items)
        } catch (error: unknown) {
            console.error('Failed to discover kubernetes namespaces', error)
            const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
            toast.error(detail || t('common.unexpectedError'))
        } finally {
            setDiscoveringNamespaces(false)
        }
    }

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent ref={dialogContentRef} className="max-h-[90vh] overflow-y-auto sm:max-w-[760px] lg:max-w-[880px]">
                <DialogHeader>
                    <DialogTitle>
                        {runtimeConnection ? t('runtimeConnections.dialog.editTitle') : t('runtimeConnections.dialog.addTitle')}
                    </DialogTitle>
                </DialogHeader>

                <Form {...form}>
                    <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-5">
                        <div className="grid gap-4 sm:grid-cols-2">
                            <FormField
                                control={form.control}
                                name="name"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>{t('runtimeConnections.dialog.fields.name')}</FormLabel>
                                        <FormControl>
                                            <Input placeholder={t('runtimeConnections.dialog.placeholders.name')} {...field} />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />

                            <FormField
                                control={form.control}
                                name="type"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>{t('runtimeConnections.dialog.fields.type')}</FormLabel>
                                        <Select value={field.value} onValueChange={(value) => field.onChange(value as RuntimeConnectionType)}>
                                            <FormControl>
                                                <SelectTrigger>
                                                    <SelectValue placeholder={t('runtimeConnections.dialog.placeholders.type')} />
                                                </SelectTrigger>
                                            </FormControl>
                                            <SelectContent>
                                                <SelectItem value="docker">Docker</SelectItem>
                                                <SelectItem value="podman">Podman</SelectItem>
                                                <SelectItem value="kubernetes">Kubernetes</SelectItem>
                                                <SelectItem value="portainer">Portainer</SelectItem>
                                            </SelectContent>
                                        </Select>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                        </div>

                        <FormField
                            control={form.control}
                            name="description"
                            render={({ field }) => (
                                <FormItem>
                                    <FormLabel>{t('common.description')}</FormLabel>
                                    <FormControl>
                                        <Textarea placeholder={t('runtimeConnections.dialog.placeholders.description')} {...field} />
                                    </FormControl>
                                    <FormMessage />
                                </FormItem>
                            )}
                        />

                        <FormField
                            control={form.control}
                            name="enabled"
                            render={({ field }) => (
                                <FormItem className="flex items-center justify-between rounded-lg border border-border/50 px-3 py-2.5">
                                    <div className="space-y-1">
                                        <FormLabel>{t('runtimeConnections.dialog.fields.enabled')}</FormLabel>
                                        <FormDescription>{t('runtimeConnections.dialog.fields.enabledDescription')}</FormDescription>
                                    </div>
                                    <FormControl>
                                        <Switch checked={field.value} onCheckedChange={field.onChange} />
                                    </FormControl>
                                </FormItem>
                            )}
                        />

                        {selectedType === 'kubernetes' ? (
                            <KubernetesFields
                                form={form}
                                            credentials={credentials}
                                discoveringNamespaces={discoveringNamespaces}
                                discoveredNamespaces={discoveredNamespaces}
                                onDiscoverNamespaces={handleDiscoverNamespaces}
                                dialogContentRef={dialogContentRef}
                            />
                        ) : selectedType === 'portainer' ? (
                            <PortainerFields form={form} credentials={credentials} />
                        ) : (
                            <ContainerRuntimeFields form={form} credentials={credentials} />
                        )}

                        <DialogFooter>
                            <Button type="submit" disabled={loading}>
                                {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                                {loading ? t('common.saving') : t('common.save')}
                            </Button>
                        </DialogFooter>
                    </form>
                </Form>
            </DialogContent>
        </Dialog>
    )
}

function ContainerRuntimeFields({
    form,
    credentials,
}: {
    form: UseFormReturn<RuntimeConnectionFormValues>
    credentials: ApiCredential[]
}) {
    const { t } = useTranslation()

    return (
        <div className="space-y-5">
            <div className="rounded-lg border border-border/50 bg-card/80 p-4">
                <div className="mb-4 space-y-1">
                    <h3 className="text-sm font-semibold">{t('runtimeConnections.dialog.sections.connection')}</h3>
                </div>

                <div className="grid gap-4 sm:grid-cols-2">
                    <FormField
                        control={form.control}
                        name="socket"
                        render={({ field }) => (
                            <FormItem>
                                <FormLabel>{t('runtimeConnections.dialog.fields.socket')}</FormLabel>
                                <FormControl>
                                    <Input placeholder={t('runtimeConnections.dialog.placeholders.socket')} {...field} />
                                </FormControl>
                                <FormDescription>{t('runtimeConnections.dialog.hints.socket')}</FormDescription>
                                <FormMessage />
                            </FormItem>
                        )}
                    />
                </div>

                <div className="mt-4 grid gap-4 sm:grid-cols-2">
                    <FormField
                        control={form.control}
                        name="api_version"
                        render={({ field }) => (
                            <FormItem>
                                <FormLabel>{t('runtimeConnections.dialog.fields.apiVersion')}</FormLabel>
                                <FormControl>
                                    <Input placeholder={t('runtimeConnections.dialog.placeholders.apiVersion')} {...field} />
                                </FormControl>
                                <FormMessage />
                            </FormItem>
                        )}
                    />

                    <FormField
                        control={form.control}
                        name="tls_verify"
                        render={({ field }) => (
                            <FormItem className="flex items-center justify-between rounded-lg border border-border/50 px-3 py-2.5">
                                <div className="space-y-1">
                                    <FormLabel>{t('runtimeConnections.dialog.fields.tlsVerify')}</FormLabel>
                                    <FormDescription>{t('runtimeConnections.dialog.hints.tlsVerify')}</FormDescription>
                                </div>
                                <FormControl>
                                    <Switch checked={field.value} onCheckedChange={field.onChange} />
                                </FormControl>
                            </FormItem>
                        )}
                    />
                </div>
            </div>

            <div className="rounded-lg border border-border/50 bg-card/80 p-4">
                <div className="mb-4 space-y-1">
                    <h3 className="text-sm font-semibold">{t('runtimeConnections.dialog.sections.authentication')}</h3>
                </div>

                <RuntimeCredentialField
                    form={form}
                    runtimeType={form.watch('type')}
                    credentials={credentials}
                />
            </div>
        </div>
    )
}

function PortainerFields({
    form,
    credentials,
}: {
    form: UseFormReturn<RuntimeConnectionFormValues>
    credentials: ApiCredential[]
}) {
    const { t } = useTranslation()

    return (
        <div className="space-y-5">
            <div className="rounded-lg border border-border/50 bg-card/80 p-4">
                <div className="mb-4 space-y-1">
                    <h3 className="text-sm font-semibold">{t('runtimeConnections.dialog.sections.connection')}</h3>
                </div>

                <div className="grid gap-4 sm:grid-cols-2">
                    <FormField
                        control={form.control}
                        name="base_url"
                        render={({ field }) => (
                            <FormItem>
                                <FormLabel>{t('runtimeConnections.dialog.fields.baseUrl')}</FormLabel>
                                <FormControl>
                                    <Input placeholder={t('runtimeConnections.dialog.placeholders.baseUrl')} {...field} />
                                </FormControl>
                                <FormDescription>{t('runtimeConnections.dialog.hints.baseUrl')}</FormDescription>
                                <FormMessage />
                            </FormItem>
                        )}
                    />

                    <FormField
                        control={form.control}
                        name="endpoint_id"
                        render={({ field }) => (
                            <FormItem>
                                <FormLabel>{t('runtimeConnections.dialog.fields.endpointId')}</FormLabel>
                                <FormControl>
                                    <Input inputMode="numeric" placeholder={t('runtimeConnections.dialog.placeholders.endpointId')} {...field} />
                                </FormControl>
                                <FormDescription>{t('runtimeConnections.dialog.hints.endpointId')}</FormDescription>
                                <FormMessage />
                            </FormItem>
                        )}
                    />
                </div>
            </div>

            <div className="rounded-lg border border-border/50 bg-card/80 p-4">
                <div className="mb-4 space-y-1">
                    <h3 className="text-sm font-semibold">{t('runtimeConnections.dialog.sections.authentication')}</h3>
                </div>

                <RuntimeCredentialField
                    form={form}
                    runtimeType="portainer"
                    credentials={credentials}
                />
            </div>
        </div>
    )
}

function KubernetesFields({
    form,
    credentials,
    discoveringNamespaces,
    discoveredNamespaces,
    onDiscoverNamespaces,
    dialogContentRef,
}: {
    form: UseFormReturn<RuntimeConnectionFormValues>
    credentials: ApiCredential[]
    discoveringNamespaces: boolean
    discoveredNamespaces: string[]
    onDiscoverNamespaces: () => Promise<void>
    dialogContentRef: React.RefObject<HTMLDivElement | null>
}) {
    const { t } = useTranslation()
    const namespaceAnchor = useComboboxAnchor()
    const useInClusterAuth = form.watch('in_cluster')

    return (
        <div className="space-y-5">
            <div className="rounded-lg border border-border/50 bg-card/80 p-4">
                <div className="mb-4 space-y-1">
                    <h3 className="text-sm font-semibold">{t('runtimeConnections.dialog.sections.connection')}</h3>
                </div>

                <div className="grid gap-4 sm:grid-cols-2">
                    <FormField
                        control={form.control}
                        name="context"
                        render={({ field }) => (
                            <FormItem>
                                <FormLabel>{t('runtimeConnections.dialog.fields.context')}</FormLabel>
                                <FormControl>
                                    <Input placeholder={t('runtimeConnections.dialog.placeholders.context')} {...field} />
                                </FormControl>
                                <FormMessage />
                            </FormItem>
                        )}
                    />

                </div>

                <div className="mt-4 rounded-lg border border-border/50 px-3 py-3">
                    <div className="flex items-center justify-between gap-3">
                        <div className="space-y-1">
                            <FormLabel>{t('runtimeConnections.dialog.fields.namespaces')}</FormLabel>
                            <FormDescription>{t('runtimeConnections.dialog.hints.namespaces')}</FormDescription>
                        </div>
                        <Button type="button" variant="outline" size="sm" onClick={() => void onDiscoverNamespaces()} disabled={discoveringNamespaces}>
                            {discoveringNamespaces ? t('common.loading') : t('runtimeConnections.dialog.actions.discoverNamespaces')}
                        </Button>
                    </div>

                    <FormField
                        control={form.control}
                        name="namespaces"
                        render={({ field }) => (
                            <FormItem className="mt-3 space-y-3">
                                {[...new Set([...field.value, ...discoveredNamespaces])].length > 0 ? (
                                    <Combobox<string, true>
                                        multiple
                                        value={field.value}
                                        onValueChange={(value) => field.onChange(value)}
                                        itemToStringValue={(value) => value}
                                        itemToStringLabel={(value) => value}
                                    >
                                        <ComboboxChips ref={namespaceAnchor} className="w-full min-h-11 max-h-28 items-start overflow-y-auto">
                                            {field.value.map((namespace) => (
                                                <ComboboxChip key={namespace}>
                                                    {namespace}
                                                </ComboboxChip>
                                            ))}
                                        </ComboboxChips>
                                        <ComboboxContent container={dialogContentRef} anchor={namespaceAnchor} align="start" sideOffset={8} className="w-[min(40rem,calc(100vw-4rem))] max-w-[calc(100vw-4rem)]">
                                            <ComboboxEmpty>{t('runtimeConnections.dialog.empty.namespaceSearch')}</ComboboxEmpty>
                                            <ComboboxList className="max-h-[min(22rem,calc(100vh-14rem))] overscroll-contain">
                                                {[...new Set([...field.value, ...discoveredNamespaces])].map((namespace) => (
                                                    <ComboboxItem key={namespace} value={namespace}>
                                                        {namespace}
                                                    </ComboboxItem>
                                                ))}
                                            </ComboboxList>
                                        </ComboboxContent>
                                    </Combobox>
                                ) : (
                                    <div className="text-sm text-muted-foreground">{t('runtimeConnections.dialog.empty.namespaces')}</div>
                                )}
                                <FormMessage />
                            </FormItem>
                        )}
                    />
                </div>

                <div className="mt-4">
                    <FormField
                        control={form.control}
                        name="in_cluster"
                        render={({ field }) => (
                            <FormItem className="flex items-center justify-between rounded-lg border border-border/50 px-3 py-2.5">
                                <div className="space-y-1">
                                    <FormLabel>{t('runtimeConnections.dialog.fields.inCluster')}</FormLabel>
                                    <FormDescription>{t('runtimeConnections.dialog.hints.inCluster')}</FormDescription>
                                </div>
                                <FormControl>
                                    <Switch checked={field.value} onCheckedChange={field.onChange} />
                                </FormControl>
                            </FormItem>
                        )}
                    />
                </div>
            </div>

            <div className="rounded-lg border border-border/50 bg-card/80 p-4">
                <div className="mb-4 space-y-1">
                    <h3 className="text-sm font-semibold">{t('runtimeConnections.dialog.sections.authentication')}</h3>
                </div>

                <RuntimeCredentialField
                    form={form}
                    runtimeType="kubernetes"
                    credentials={credentials}
                    disabled={useInClusterAuth}
                />

            </div>
        </div>
    )
}

function validateRuntimeCredentialSelection(values: RuntimeConnectionFormValues): string | null {
    const hasCredential = Boolean(values.credential_id.trim())

    if (values.type === 'kubernetes' && !values.in_cluster && !hasCredential) {
        return 'runtimeConnections.dialog.errors.kubernetesCredentialRequired'
    }

    if (values.type === 'portainer' && !hasCredential) {
        return 'runtimeConnections.dialog.errors.portainerCredentialRequired'
    }

    return null
}

function RuntimeCredentialField({
    form,
    runtimeType,
    credentials,
    disabled = false,
}: {
    form: UseFormReturn<RuntimeConnectionFormValues>
    runtimeType: RuntimeConnectionType
    credentials: ApiCredential[]
    disabled?: boolean
}) {
    const { t } = useTranslation()
    const compatibleCredentials = credentials.filter((credential) =>
        credentialTypeMatchesRuntime(credential.type, runtimeType),
    )

    return (
        <FormField
            control={form.control}
            name="credential_id"
            render={({ field }) => (
                <FormItem>
                    <FormLabel>{t('runtimeConnections.dialog.fields.credential')}</FormLabel>
                    <Select value={field.value || "none"} onValueChange={(value) => field.onChange(value === "none" ? "" : value)} disabled={disabled}>
                        <FormControl>
                            <SelectTrigger>
                                <SelectValue placeholder={t('runtimeConnections.dialog.placeholders.credential')} />
                            </SelectTrigger>
                        </FormControl>
                        <SelectContent>
                            <SelectItem value="none">{t('runtimeConnections.dialog.fields.noCredential')}</SelectItem>
                            {compatibleCredentials.map((credential) => (
                                <SelectItem key={credential.id} value={String(credential.id)}>
                                    {credential.name}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                    <FormDescription>{t('runtimeConnections.dialog.hints.credential')}</FormDescription>
                    <FormMessage />
                </FormItem>
            )}
        />
    )
}


function credentialTypeMatchesRuntime(type: ApiCredential['type'], runtimeType: RuntimeConnectionType): boolean {
    if (runtimeType === 'docker') {
        return type === 'docker_runtime'
    }
    if (runtimeType === 'podman') {
        return type === 'podman_runtime' || type === 'docker_runtime'
    }
    if (runtimeType === 'kubernetes') {
        return type === 'kubernetes_runtime'
    }
    if (runtimeType === 'portainer') {
        return type === 'portainer_runtime'
    }
    return false
}


function getStringValue(value: unknown): string {
    return typeof value === 'string' ? value : ''
}

function getArrayStringValues(value: unknown): string[] {
    return Array.isArray(value)
        ? value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
        : []
}

function stringifyInteger(value: unknown): string {
    return typeof value === 'number' && Number.isInteger(value) ? String(value) : ''
}
