import { useTranslation } from "react-i18next"
import { Settings } from "lucide-react"
import { OIDCProvidersManagement } from "@/components/admin/OIDCProvidersManagement"

export function SystemSettingsPage() {
    const { t } = useTranslation()

    return (
        <div className="container mx-auto py-6 px-4 space-y-6 max-w-3xl">
            <div className="flex items-center gap-3">
                <Settings className="h-6 w-6 text-primary" />
                <div>
                    <h1 className="text-2xl font-bold">{t('systemSettings.system.title')}</h1>
                    <p className="text-sm text-muted-foreground">{t('systemSettings.system.description')}</p>
                </div>
            </div>

            <div className="rounded-xl border border-border/50 bg-card p-6">
                <OIDCProvidersManagement />
            </div>
        </div>
    )
}
