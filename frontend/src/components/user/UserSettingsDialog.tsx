import { useState } from "react"
import { useTranslation } from "react-i18next"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Input } from "@/components/ui/input"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { toast } from "sonner"
import { api } from "@/api/client"
import { useAuth } from "@/providers/AuthProvider"
import { User, Lock, Loader2 } from "lucide-react"

interface UserSettingsDialogProps {
    open: boolean
    onOpenChange: (open: boolean) => void
}

export function UserSettingsDialog({ open, onOpenChange }: UserSettingsDialogProps) {
    const { t } = useTranslation()
    const { user } = useAuth()
    const [isLoading, setIsLoading] = useState(false)

    // Password Reset State
    const [passwords, setPasswords] = useState({
        current: "",
        new: "",
        confirm: ""
    })

    const handlePasswordChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        setPasswords(prev => ({ ...prev, [e.target.name]: e.target.value }))
    }

    const handleUpdatePassword = async (e: React.FormEvent) => {
        e.preventDefault()
        if (passwords.new !== passwords.confirm) {
            toast.error(t('user.messages.passwordMismatch'))
            return
        }
        if (passwords.new.length < 6) {
            toast.error(t('user.messages.passwordTooShort'))
            return
        }

        setIsLoading(true)
        try {
            await api.changePassword({
                old_password: passwords.current,
                new_password: passwords.new
            })
            toast.success(t('user.messages.passwordUpdated'))
            setPasswords({ current: "", new: "", confirm: "" })
            onOpenChange(false)
        } catch (error: any) {
            console.error(error)
            toast.error(error.response?.data?.detail || t('user.messages.passwordUpdateFailed'))
        } finally {
            setIsLoading(false)
        }
    }

    if (!user) return null

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-[425px]">
                <DialogHeader>
                    <DialogTitle>{t('user.settings')}</DialogTitle>
                    <DialogDescription>
                        {t('user.description')}
                    </DialogDescription>
                </DialogHeader>
                <Tabs defaultValue="account" className="w-full">
                    <TabsList className="grid w-full grid-cols-2">
                        <TabsTrigger value="account">{t('user.tabs.account')}</TabsTrigger>
                        <TabsTrigger value="security">{t('user.tabs.security')}</TabsTrigger>
                    </TabsList>

                    <TabsContent value="account" className="space-y-4 pt-4">
                        <div className="space-y-4 rounded-lg border p-4 bg-muted/50">
                            <div className="flex items-center gap-4">
                                <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary/10">
                                    <User className="h-6 w-6 text-primary" />
                                </div>
                                <div className="space-y-1">
                                    <p className="font-medium leading-none">{user.username}</p>
                                    <p className="text-sm text-muted-foreground">{user.email}</p>
                                </div>
                            </div>
                        </div>

                    </TabsContent>

                    <TabsContent value="security" className="space-y-4 pt-4">
                        <form onSubmit={handleUpdatePassword} className="space-y-4">
                            {/* Hidden username for accessibility/password managers */}
                            <input
                                type="text"
                                name="username"
                                value={user.username}
                                autoComplete="username"
                                className="hidden"
                                readOnly
                            />
                            <div className="space-y-2">
                                <Label htmlFor="current">{t('user.fields.currentPassword')}</Label>
                                <div className="relative">
                                    <Lock className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                                    <Input
                                        id="current"
                                        name="current"
                                        type="password"
                                        className="pl-9"
                                        value={passwords.current}
                                        onChange={handlePasswordChange}
                                        required
                                        autoComplete="current-password"
                                    />
                                </div>
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="new">{t('user.fields.newPassword')}</Label>
                                <div className="relative">
                                    <Lock className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                                    <Input
                                        id="new"
                                        name="new"
                                        type="password"
                                        className="pl-9"
                                        value={passwords.new}
                                        onChange={handlePasswordChange}
                                        required
                                        autoComplete="new-password"
                                    />
                                </div>
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="confirm">{t('user.fields.confirmPassword')}</Label>
                                <div className="relative">
                                    <Lock className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                                    <Input
                                        id="confirm"
                                        name="confirm"
                                        type="password"
                                        className="pl-9"
                                        value={passwords.confirm}
                                        onChange={handlePasswordChange}
                                        required
                                        autoComplete="new-password"
                                    />
                                </div>
                            </div>
                            <div className="flex justify-end pt-2">
                                <Button type="submit" disabled={isLoading}>
                                    {isLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                                    {t('user.actions.updatePassword')}
                                </Button>
                            </div>
                        </form>
                    </TabsContent>
                </Tabs>
            </DialogContent>
        </Dialog>
    )
}
