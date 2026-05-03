import { useState, useEffect } from "react"
import { useForm, useWatch, type UseFormReturn } from "react-hook-form"
import { useTranslation } from "react-i18next"
import { Loader2, Save } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import {
    Form,
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
import { Textarea } from "@/components/ui/textarea"
import type { ApiCredential, CredentialType } from "@/api/types"
import { api } from "@/api/client"
import { toast } from "sonner"
import { getCredentialTypeLabel } from "./credentialTypeLabels"

interface CredentialDialogProps {
    open: boolean
    onOpenChange: (open: boolean) => void
    onSuccess: () => void
    credential?: ApiCredential | null
}

interface CredentialFormData {
    name: string
    type: CredentialType
    token: string
    username: string
    password: string
    ca_cert: string
    client_cert: string
    client_key: string
    kubeconfig: string
    client_certificate: string
    certificate_authority: string
    api_key: string
    description?: string
}

export function CredentialDialog({ open, onOpenChange, onSuccess, credential }: CredentialDialogProps) {
    const [loading, setLoading] = useState(false)

    const form = useForm<CredentialFormData>({
        defaultValues: {
            name: "",
            type: "github",
            token: "",
            username: "",
            password: "",
            ca_cert: "",
            client_cert: "",
            client_key: "",
            kubeconfig: "",
            client_certificate: "",
            certificate_authority: "",
            api_key: "",
            description: "",
        },
    })

    useEffect(() => {
        if (open) {
            if (credential) {
                form.reset({
                    name: credential.name,
                    type: credential.type,
                    token: "",
                    username: "",
                    password: "",
                    ca_cert: "",
                    client_cert: "",
                    client_key: "",
                    kubeconfig: "",
                    client_certificate: "",
                    certificate_authority: "",
                    api_key: "",
                    description: credential.description || ""
                })
            } else {
                form.reset({
                    name: "",
                    type: "github",
                    token: "",
                    username: "",
                    password: "",
                    ca_cert: "",
                    client_cert: "",
                    client_key: "",
                    kubeconfig: "",
                    client_certificate: "",
                    certificate_authority: "",
                    api_key: "",
                    description: ""
                })
            }
        }
    }, [open, credential, form])

    const onSubmit = async (data: CredentialFormData) => {
        setLoading(true)
        try {
            const payload = buildCredentialPayload(data)
            if (credential) {
                const updateData = {
                    type: payload.type,
                    description: payload.description,
                    ...(payload.token ? { token: payload.token } : {}),
                    ...(Object.keys(payload.secrets || {}).length > 0 ? { secrets: payload.secrets } : {}),
                }
                await api.updateCredential(credential.id, updateData)
            } else {
                await api.createCredential(payload)
            }
            toast.success(t('common.saved'))
            onSuccess()
            onOpenChange(false)
        } catch (error: unknown) {
            console.error("Failed to save credential", error)
            // Handle duplicate name error if we had it, or generic
            const err = error as { response?: { status?: number; data?: { detail?: string } } }
            if (err.response?.status === 400) {
                toast.error(err.response.data?.detail || t('common.unexpectedError'));
            } else {
                toast.error(t('common.unexpectedError'));
            }
        } finally {
            setLoading(false)
        }
    }

    const { t } = useTranslation()
    const selectedType = useWatch({ control: form.control, name: "type" })

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-[720px]">
                <DialogHeader>
                    <DialogTitle>{credential ? t('credential.editTitle') : t('credential.addTitle')}</DialogTitle>
                    <DialogDescription>
                        {t('credential.description')}
                    </DialogDescription>
                </DialogHeader>

                <Form {...form}>
                    <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-5">
                        <div className="rounded-lg border border-border/50 bg-card/80 p-4">
                            <div className="mb-4 space-y-1">
                                <h3 className="text-sm font-semibold">{t('credential.sections.basic')}</h3>
                            </div>

                            <div className="grid gap-4 sm:grid-cols-2">
                                <FormField
                                    control={form.control}
                                    name="name"
                                    render={({ field }) => (
                                        <FormItem>
                                            <FormLabel>{t('credential.fields.name')}</FormLabel>
                                            <FormControl>
                                                <Input placeholder={t('credential.fields.namePlaceholder')} {...field} disabled={!!credential} />
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
                                            <FormLabel>{t('credential.fields.type')}</FormLabel>
                                            <Select value={field.value} onValueChange={field.onChange} disabled={!!credential}>
                                                <FormControl>
                                                    <SelectTrigger>
                                                        <SelectValue placeholder={t('credential.fields.selectType')} />
                                                    </SelectTrigger>
                                                </FormControl>
                                                <SelectContent>
                                                    {getCredentialTypeOptions(t).map((option) => (
                                                        <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                                                    ))}
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
                                    <FormItem className="mt-4">
                                        <FormLabel>{t('credential.fields.description')}</FormLabel>
                                        <FormControl>
                                            <Textarea placeholder={t('credential.fields.descriptionPlaceholder')} {...field} />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                        </div>

                        <div className="rounded-lg border border-border/50 bg-card/80 p-4">
                            <div className="mb-4 space-y-1">
                                <h3 className="text-sm font-semibold">{t('credential.sections.authentication')}</h3>
                                <p className="text-sm text-muted-foreground">{getCredentialAuthDescription(t, selectedType)}</p>
                            </div>

                            <CredentialSecretFields
                                selectedType={selectedType}
                                credential={credential}
                                form={form}
                            />
                        </div>

                        <DialogFooter>
                            <Button type="submit" disabled={loading}>
                                {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                                {t('credential.save')}
                            </Button>
                        </DialogFooter>
                    </form>
                </Form>
            </DialogContent>
        </Dialog>
    )
}

type CredentialTypeOption = {
    value: CredentialType
    label: string
}

function getCredentialTypeOptions(t: (key: string) => string): CredentialTypeOption[] {
    const types: CredentialType[] = [
        "github",
        "gitlab",
        "gitea",
        "helm",
        "docker",
        "docker_runtime",
        "podman_runtime",
        "kubernetes_runtime",
        "portainer_runtime",
    ]

    return types.map((type) => ({ value: type, label: getCredentialTypeLabel(t, type) }))
}

function getCredentialAuthDescription(t: (key: string) => string, selectedType: CredentialType): string {
    if (selectedType.endsWith("_runtime")) {
        return t('credential.hints.connectionAuthentication')
    }
    return t('credential.hints.trackerAuthentication')
}

function CredentialSecretFields({
    selectedType,
    credential,
    form,
}: {
    selectedType: CredentialType
    credential?: ApiCredential | null
    form: UseFormReturn<CredentialFormData>
}) {
    const { t } = useTranslation()

    if (selectedType === "kubernetes_runtime") {
        return (
            <div className="space-y-4">
                <SecretTextarea form={form} name="kubeconfig" label={t('credential.fields.kubeconfig')} placeholder={t('credential.placeholders.kubeconfig')} />
                <div className="grid gap-4 sm:grid-cols-2">
                    <SecretInput form={form} name="token" label={t('credential.fields.token')} placeholder={credential ? t('credential.fields.tokenUnchanged') : t('credential.fields.tokenPlaceholder')} />
                    <SecretInput form={form} name="client_certificate" label={t('credential.fields.clientCertificate')} placeholder={credential ? t('credential.fields.tokenUnchanged') : t('credential.placeholders.clientCertificate')} />
                    <SecretInput form={form} name="client_key" label={t('credential.fields.clientKey')} placeholder={credential ? t('credential.fields.tokenUnchanged') : t('credential.placeholders.clientKey')} />
                    <SecretInput form={form} name="certificate_authority" label={t('credential.fields.certificateAuthority')} placeholder={credential ? t('credential.fields.tokenUnchanged') : t('credential.placeholders.certificateAuthority')} />
                </div>
            </div>
        )
    }

    if (selectedType === "portainer_runtime") {
        return (
            <div className="grid gap-4 sm:grid-cols-2">
                <SecretInput form={form} name="api_key" label={t('credential.fields.apiKey')} placeholder={credential ? t('credential.fields.tokenUnchanged') : t('credential.placeholders.apiKey')} />
            </div>
        )
    }

    if (selectedType === "docker_runtime" || selectedType === "podman_runtime") {
        return (
            <div className="space-y-4">
                <div className="grid gap-4 sm:grid-cols-2">
                    <SecretInput form={form} name="username" label={t('credential.fields.username')} placeholder={t('credential.placeholders.username')} />
                    <SecretInput form={form} name="password" label={t('credential.fields.password')} type="password" placeholder={credential ? t('credential.fields.tokenUnchanged') : t('credential.placeholders.password')} />
                    <SecretInput form={form} name="token" label={t('credential.fields.token')} placeholder={credential ? t('credential.fields.tokenUnchanged') : t('credential.fields.tokenPlaceholder')} />
                </div>
                <div className="grid gap-4 sm:grid-cols-3">
                    <SecretInput form={form} name="ca_cert" label={t('credential.fields.caCert')} placeholder={credential ? t('credential.fields.tokenUnchanged') : t('credential.placeholders.caCert')} />
                    <SecretInput form={form} name="client_cert" label={t('credential.fields.clientCertificate')} placeholder={credential ? t('credential.fields.tokenUnchanged') : t('credential.placeholders.clientCertificate')} />
                    <SecretInput form={form} name="client_key" label={t('credential.fields.clientKey')} placeholder={credential ? t('credential.fields.tokenUnchanged') : t('credential.placeholders.clientKey')} />
                </div>
            </div>
        )
    }

    if (selectedType === "docker") {
        return (
            <div className="grid gap-4 sm:grid-cols-2">
                <SecretInput form={form} name="username" label={t('credential.fields.username')} placeholder={t('credential.placeholders.registryUsername')} />
                <SecretInput form={form} name="password" label={t('credential.fields.passwordOrPat')} type="password" placeholder={credential ? t('credential.fields.tokenUnchanged') : t('credential.placeholders.registryPasswordOrPat')} />
            </div>
        )
    }

    return (
        <SecretInput
            form={form}
            name="token"
            label={t('credential.fields.token')}
            placeholder={credential ? t('credential.fields.tokenUnchanged') : t(`credential.fields.tokenPlaceholder_${selectedType}` as const)}
        />
    )
}

function SecretInput({
    form,
    name,
    label,
    placeholder,
    type = "text",
}: {
    form: UseFormReturn<CredentialFormData>
    name: keyof CredentialFormData
    label: string
    placeholder: string
    type?: string
}) {
    return (
        <FormField
            control={form.control}
            name={name}
            render={({ field }) => (
                <FormItem>
                    <FormLabel>{label}</FormLabel>
                    <FormControl>
                        <Input type={type} placeholder={placeholder} {...field} value={typeof field.value === "string" ? field.value : ""} />
                    </FormControl>
                    <FormMessage />
                </FormItem>
            )}
        />
    )
}

function SecretTextarea({
    form,
    name,
    label,
    placeholder,
}: {
    form: UseFormReturn<CredentialFormData>
    name: keyof CredentialFormData
    label: string
    placeholder: string
}) {
    return (
        <FormField
            control={form.control}
            name={name}
            render={({ field }) => (
                <FormItem>
                    <FormLabel>{label}</FormLabel>
                    <FormControl>
                        <Textarea className="min-h-32 font-mono text-xs" placeholder={placeholder} {...field} value={typeof field.value === "string" ? field.value : ""} />
                    </FormControl>
                    <FormMessage />
                </FormItem>
            )}
        />
    )
}

function buildCredentialPayload(data: CredentialFormData) {
    const secrets: Record<string, string> = {}
    const assign = (key: keyof CredentialFormData) => {
        const value = data[key]
        if (typeof value === "string" && value.trim()) {
            secrets[key] = value.trim()
        }
    }

    if (data.type === "kubernetes_runtime") {
        assign("kubeconfig")
        assign("token")
        assign("client_certificate")
        assign("client_key")
        assign("certificate_authority")
    } else if (data.type === "portainer_runtime") {
        assign("api_key")
    } else if (data.type === "docker_runtime" || data.type === "podman_runtime") {
        assign("username")
        assign("password")
        assign("token")
        assign("ca_cert")
        assign("client_cert")
        assign("client_key")
    } else if (data.type === "docker") {
        assign("username")
        assign("password")
    }

    return {
        name: data.name.trim(),
        type: data.type,
        token: data.type === "docker" ? "" : data.token.trim(),
        secrets,
        description: data.description?.trim() || null,
    }
}
