import { useState, useEffect, useCallback } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import { Plus, Pencil, Trash2, RefreshCw, Shield, Check, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
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
import {
    getOIDCProvidersAdmin,
    createOIDCProvider,
    updateOIDCProvider,
    deleteOIDCProvider,
    type OIDCProviderConfig,
    type CreateOIDCProviderRequest,
    type UpdateOIDCProviderRequest,
} from "@/api/oidc"

const EMPTY_FORM: CreateOIDCProviderRequest = {
    name: "",
    slug: "",
    client_id: "",
    client_secret: "",
    issuer_url: "",
    discovery_enabled: true,
    authorization_url: "",
    token_url: "",
    userinfo_url: "",
    scopes: "openid email profile",
    enabled: true,
    icon_url: "",
    description: "",
}

export function OIDCProvidersManagement() {
    const { t } = useTranslation()
    const [providers, setProviders] = useState<OIDCProviderConfig[]>([])
    const [isLoading, setIsLoading] = useState(false)
    const [dialogOpen, setDialogOpen] = useState(false)
    const [editingProvider, setEditingProvider] = useState<OIDCProviderConfig | null>(null)
    const [deleteTarget, setDeleteTarget] = useState<OIDCProviderConfig | null>(null)
    const [form, setForm] = useState<CreateOIDCProviderRequest>(EMPTY_FORM)
    const [isSubmitting, setIsSubmitting] = useState(false)

    const loadProviders = useCallback(async () => {
        setIsLoading(true)
        try {
            const data = await getOIDCProvidersAdmin()
            setProviders(data)
        } catch {
            toast.error(t('systemSettings.oidc.loadFailed'))
        } finally {
            setIsLoading(false)
        }
    }, [t])

    useEffect(() => { loadProviders() }, [loadProviders])

    const openCreate = () => {
        setEditingProvider(null)
        setForm(EMPTY_FORM)
        setDialogOpen(true)
    }

    const openEdit = (p: OIDCProviderConfig) => {
        setEditingProvider(p)
        setForm({
            name: p.name,
            slug: p.slug,
            client_id: p.client_id,
            client_secret: "",  // 不回填密钥
            issuer_url: p.issuer_url ?? "",
            discovery_enabled: p.discovery_enabled,
            authorization_url: p.authorization_url ?? "",
            token_url: p.token_url ?? "",
            userinfo_url: p.userinfo_url ?? "",
            scopes: p.scopes,
            enabled: p.enabled,
            icon_url: p.icon_url ?? "",
            description: p.description ?? "",
        })
        setDialogOpen(true)
    }

    const handleSubmit = async () => {
        if (!form.name || !form.slug || !form.client_id) {
            toast.error(t('systemSettings.oidc.requiredFields'))
            return
        }
        setIsSubmitting(true)
        try {
            const payload = {
                ...form,
                issuer_url: form.issuer_url || null,
                authorization_url: form.authorization_url || null,
                token_url: form.token_url || null,
                userinfo_url: form.userinfo_url || null,
                icon_url: form.icon_url || null,
                description: form.description || null,
            }
            if (editingProvider) {
                const updatePayload: UpdateOIDCProviderRequest = { ...payload }
                if (!form.client_secret) delete updatePayload.client_secret
                await updateOIDCProvider(editingProvider.id, updatePayload)
                toast.success(t('systemSettings.oidc.updated'))
            } else {
                if (!form.client_secret) {
                    toast.error(t('systemSettings.oidc.secretRequired'))
                    return
                }
                await createOIDCProvider(payload)
                toast.success(t('systemSettings.oidc.created'))
            }
            setDialogOpen(false)
            loadProviders()
        } catch (e: unknown) {
            toast.error(e instanceof Error ? e.message : t('systemSettings.oidc.saveFailed'))
        } finally {
            setIsSubmitting(false)
        }
    }

    const handleDelete = async () => {
        if (!deleteTarget) return
        try {
            await deleteOIDCProvider(deleteTarget.id)
            toast.success(t('systemSettings.oidc.deleted'))
            setDeleteTarget(null)
            loadProviders()
        } catch (e: unknown) {
            toast.error(e instanceof Error ? e.message : t('systemSettings.oidc.deleteFailed'))
        }
    }

    const setField = (key: keyof CreateOIDCProviderRequest, value: string | boolean) => {
        setForm(prev => ({ ...prev, [key]: value }))
    }

    return (
        <div className="space-y-4">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                    <Shield className="h-5 w-5 text-primary" />
                    <div>
                        <h3 className="font-semibold">{t('systemSettings.oidc.title')}</h3>
                        <p className="text-sm text-muted-foreground">{t('systemSettings.oidc.description')}</p>
                    </div>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={loadProviders} disabled={isLoading}>
                        <RefreshCw className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
                    </Button>
                    <Button size="sm" onClick={openCreate}>
                        <Plus className="h-4 w-4 mr-1" />
                        {t('systemSettings.oidc.add')}
                    </Button>
                </div>
            </div>

            {/* Provider List */}
            {providers.length === 0 ? (
                <div className="rounded-lg border border-dashed border-border/60 py-12 text-center text-muted-foreground">
                    <Shield className="h-10 w-10 mx-auto mb-3 opacity-30" />
                    <p className="text-sm">{t('systemSettings.oidc.empty')}</p>
                    <Button variant="outline" size="sm" className="mt-4" onClick={openCreate}>
                        <Plus className="h-4 w-4 mr-1" />
                        {t('systemSettings.oidc.add')}
                    </Button>
                </div>
            ) : (
                <div className="space-y-2">
                    {providers.map((p) => (
                        <div
                            key={p.id}
                            className="flex items-center justify-between rounded-lg border border-border/50 bg-card p-4"
                        >
                            <div className="flex items-center gap-3">
                                {p.icon_url ? (
                                    <img src={p.icon_url} alt={p.name} className="h-8 w-8 rounded object-contain" />
                                ) : (
                                    <div className="h-8 w-8 rounded bg-primary/10 flex items-center justify-center font-bold text-primary text-sm">
                                        {p.name.charAt(0)}
                                    </div>
                                )}
                                <div>
                                    <div className="flex items-center gap-2">
                                        <span className="font-medium text-sm">{p.name}</span>
                                        <code className="text-xs text-muted-foreground bg-muted rounded px-1.5 py-0.5">{p.slug}</code>
                                        {p.enabled
                                            ? <span className="text-xs text-green-600 flex items-center gap-0.5"><Check className="h-3 w-3" />{t('common.enabled')}</span>
                                            : <span className="text-xs text-muted-foreground flex items-center gap-0.5"><X className="h-3 w-3" />{t('common.disabled')}</span>
                                        }
                                    </div>
                                    <p className="text-xs text-muted-foreground mt-0.5">
                                        {p.issuer_url || t('systemSettings.oidc.manualEndpoints')}
                                    </p>
                                </div>
                            </div>
                            <div className="flex gap-1">
                                <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => openEdit(p)}>
                                    <Pencil className="h-3.5 w-3.5" />
                                </Button>
                                <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive hover:text-destructive" onClick={() => setDeleteTarget(p)}>
                                    <Trash2 className="h-3.5 w-3.5" />
                                </Button>
                            </div>
                        </div>
                    ))}
                </div>
            )}

            {/* Create / Edit Dialog */}
            <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                <DialogContent className="max-w-lg max-h-[85vh] overflow-y-auto">
                    <DialogHeader>
                        <DialogTitle>
                            {editingProvider ? t('systemSettings.oidc.editTitle') : t('systemSettings.oidc.addTitle')}
                        </DialogTitle>
                        <DialogDescription>{t('systemSettings.oidc.dialogDesc')}</DialogDescription>
                    </DialogHeader>

                    <div className="space-y-4 py-2">
                        {/* 基本信息 */}
                        <div className="grid grid-cols-2 gap-3">
                            <div className="space-y-1.5">
                                <Label>{t('systemSettings.oidc.fields.name')} *</Label>
                                <Input value={form.name} onChange={e => setField('name', e.target.value)} placeholder="e.g. Authentik" />
                            </div>
                            <div className="space-y-1.5">
                                <Label>{t('systemSettings.oidc.fields.slug')} *</Label>
                                <Input
                                    value={form.slug}
                                    onChange={e => setField('slug', e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, '-'))}
                                    placeholder="e.g. authentik"
                                    disabled={!!editingProvider}
                                />
                            </div>
                        </div>

                        <div className="space-y-1.5">
                            <Label>{t('systemSettings.oidc.fields.clientId')} *</Label>
                            <Input value={form.client_id} onChange={e => setField('client_id', e.target.value)} />
                        </div>
                        <div className="space-y-1.5">
                            <Label>
                                {t('systemSettings.oidc.fields.clientSecret')}
                                {editingProvider && <span className="text-xs text-muted-foreground ml-1">({t('systemSettings.oidc.secretHint')})</span>}
                                {!editingProvider && ' *'}
                            </Label>
                            <Input
                                type="password"
                                value={form.client_secret}
                                onChange={e => setField('client_secret', e.target.value)}
                                placeholder={editingProvider ? t('systemSettings.oidc.secretPlaceholder') : ''}
                            />
                        </div>

                        {/* Discovery */}
                        <div className="flex items-center justify-between rounded-lg border border-border/50 px-3 py-2.5">
                            <div>
                                <Label>{t('systemSettings.oidc.fields.discoveryEnabled')}</Label>
                                <p className="text-xs text-muted-foreground">{t('systemSettings.oidc.fields.discoveryDesc')}</p>
                            </div>
                            <Switch
                                checked={form.discovery_enabled}
                                onCheckedChange={v => setField('discovery_enabled', v)}
                            />
                        </div>

                        {form.discovery_enabled ? (
                            <div className="space-y-1.5">
                                <Label>{t('systemSettings.oidc.fields.issuerUrl')}</Label>
                                <Input value={form.issuer_url ?? ""} onChange={e => setField('issuer_url', e.target.value)} placeholder="https://your-idp.example.com" />
                            </div>
                        ) : (
                            <div className="space-y-3">
                                <div className="space-y-1.5">
                                    <Label>{t('systemSettings.oidc.fields.authorizationUrl')}</Label>
                                    <Input value={form.authorization_url ?? ""} onChange={e => setField('authorization_url', e.target.value)} />
                                </div>
                                <div className="space-y-1.5">
                                    <Label>{t('systemSettings.oidc.fields.tokenUrl')}</Label>
                                    <Input value={form.token_url ?? ""} onChange={e => setField('token_url', e.target.value)} />
                                </div>
                                <div className="space-y-1.5">
                                    <Label>{t('systemSettings.oidc.fields.userinfoUrl')}</Label>
                                    <Input value={form.userinfo_url ?? ""} onChange={e => setField('userinfo_url', e.target.value)} />
                                </div>
                            </div>
                        )}

                        <div className="space-y-1.5">
                            <Label>{t('systemSettings.oidc.fields.scopes')}</Label>
                            <Input value={form.scopes} onChange={e => setField('scopes', e.target.value)} />
                        </div>

                        <div className="grid grid-cols-2 gap-3">
                            <div className="space-y-1.5">
                                <Label>{t('systemSettings.oidc.fields.iconUrl')}</Label>
                                <Input value={form.icon_url ?? ""} onChange={e => setField('icon_url', e.target.value)} placeholder="https://..." />
                            </div>
                            <div className="space-y-1.5">
                                <Label>{t('systemSettings.oidc.fields.description')}</Label>
                                <Input value={form.description ?? ""} onChange={e => setField('description', e.target.value)} />
                            </div>
                        </div>

                        <div className="flex items-center justify-between rounded-lg border border-border/50 px-3 py-2.5">
                            <Label>{t('systemSettings.oidc.fields.enabled')}</Label>
                            <Switch
                                checked={form.enabled ?? true}
                                onCheckedChange={v => setField('enabled', v)}
                            />
                        </div>
                    </div>

                    <DialogFooter>
                        <Button variant="outline" onClick={() => setDialogOpen(false)}>
                            {t('common.cancel')}
                        </Button>
                        <Button onClick={handleSubmit} disabled={isSubmitting}>
                            {isSubmitting ? t('common.saving') : t('common.save')}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Delete Confirm */}
            <AlertDialog open={!!deleteTarget} onOpenChange={open => !open && setDeleteTarget(null)}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>{t('systemSettings.oidc.deleteTitle')}</AlertDialogTitle>
                        <AlertDialogDescription>
                            {t('systemSettings.oidc.deleteDesc', { name: deleteTarget?.name })}
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>{t('common.cancel')}</AlertDialogCancel>
                        <AlertDialogAction onClick={handleDelete} className="bg-destructive text-destructive-foreground hover:bg-destructive/90">
                            {t('common.delete')}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    )
}
