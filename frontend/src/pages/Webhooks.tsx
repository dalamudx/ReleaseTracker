import { ArrowDownToLine, ArrowUpFromLine } from "lucide-react"
import { useTranslation } from "react-i18next"

import { NotifierSettings } from "@/components/settings/NotifierSettings"
import { RepositoryWebhookSettings } from "@/components/settings/RepositoryWebhookSettings"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

export default function WebhooksPage() {
    const { t } = useTranslation()
    return (
        <Tabs defaultValue="outgoing" className="flex h-full min-h-0 flex-col gap-4">
            <TabsList className="flex-none">
                <TabsTrigger value="outgoing"><ArrowUpFromLine className="size-4" />{t("webhooks.tabs.outgoing")}</TabsTrigger>
                <TabsTrigger value="repository"><ArrowDownToLine className="size-4" />{t("webhooks.tabs.repository")}</TabsTrigger>
            </TabsList>
            <TabsContent value="outgoing" className="flex min-h-0 flex-1 flex-col"><NotifierSettings /></TabsContent>
            <TabsContent value="repository" className="flex min-h-0 flex-1 flex-col"><RepositoryWebhookSettings /></TabsContent>
        </Tabs>
    )
}
