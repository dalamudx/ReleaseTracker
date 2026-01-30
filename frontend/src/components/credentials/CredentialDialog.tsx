import { useState, useEffect } from "react"
import { useForm } from "react-hook-form"
import { useTranslation } from "react-i18next"
import { Save } from "lucide-react"

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
import type { ApiCredential } from "@/api/types"
import { api } from "@/api/client"
import { toast } from "sonner"

interface CredentialDialogProps {
    open: boolean
    onOpenChange: (open: boolean) => void
    onSuccess: () => void
    credential?: ApiCredential | null
}

interface CredentialFormData {
    name: string
    type: "github" | "gitlab" | "helm"
    token: string
    description?: string
}

export function CredentialDialog({ open, onOpenChange, onSuccess, credential }: CredentialDialogProps) {
    const [loading, setLoading] = useState(false)

    const form = useForm<CredentialFormData>({
        defaultValues: {
            name: "",
            type: "github",
            token: "",
            description: "",
        },
    })

    useEffect(() => {
        if (open) {
            if (credential) {
                form.reset({
                    name: credential.name,
                    type: credential.type as "github" | "gitlab" | "helm",
                    token: "", // Don't fill token for security, user enters new one if updating
                    description: credential.description || ""
                })
            } else {
                form.reset({
                    name: "",
                    type: "github",
                    token: "",
                    description: ""
                })
            }
        }
    }, [open, credential, form])

    const onSubmit = async (data: CredentialFormData) => {
        setLoading(true)
        try {
            if (credential) {
                const { name, ...updateData } = data
                // avoid using name to satisfy linter
                void name
                if (!updateData.token) {
                    delete (updateData as { token?: string }).token
                }
                await api.updateCredential(credential.id, updateData)
            } else {
                await api.createCredential(data)
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

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-[425px]">
                <DialogHeader>
                    <DialogTitle>{credential ? t('credential.editTitle') : t('credential.addTitle')}</DialogTitle>
                    <DialogDescription>
                        {t('credential.description')}
                    </DialogDescription>
                </DialogHeader>

                <Form {...form}>
                    <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">

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
                                    <Select onValueChange={field.onChange} defaultValue={field.value} disabled={!!credential}>
                                        <FormControl>
                                            <SelectTrigger>
                                                <SelectValue placeholder={t('credential.fields.selectType')} />
                                            </SelectTrigger>
                                        </FormControl>
                                        <SelectContent>
                                            <SelectItem value="github">GitHub</SelectItem>
                                            <SelectItem value="gitlab">GitLab</SelectItem>
                                            <SelectItem value="helm">Helm Chart</SelectItem>
                                        </SelectContent>
                                    </Select>
                                    <FormMessage />
                                </FormItem>
                            )}
                        />

                        <FormField
                            control={form.control}
                            name="token"
                            render={({ field }) => (
                                <FormItem>
                                    <FormLabel>{t('credential.fields.token')}</FormLabel>
                                    <FormControl>
                                        <Input type="password" placeholder={credential ? t('credential.fields.tokenUnchanged') : t('credential.fields.tokenPlaceholder')} {...field} />
                                    </FormControl>
                                    <FormMessage />
                                </FormItem>
                            )}
                        />

                        <FormField
                            control={form.control}
                            name="description"
                            render={({ field }) => (
                                <FormItem>
                                    <FormLabel>{t('credential.fields.description')}</FormLabel>
                                    <FormControl>
                                        <Textarea placeholder={t('credential.fields.descriptionPlaceholder')} {...field} />
                                    </FormControl>
                                    <FormMessage />
                                </FormItem>
                            )}
                        />

                        <DialogFooter>
                            <Button type="submit" disabled={loading}>
                                {loading && <span className="mr-2 animate-spin">⚪</span>}
                                <Save className="mr-2 h-4 w-4" /> {t('credential.save')}
                            </Button>
                        </DialogFooter>
                    </form>
                </Form>
            </DialogContent>
        </Dialog>
    )
}
