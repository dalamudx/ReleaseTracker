import { ArrowDownToLine, ArrowUpFromLine } from "lucide-react"
import { useTranslation } from "react-i18next"

import { NotificationTemplates } from "@/components/settings/NotificationTemplates"
import { NotifierSettings } from "@/components/settings/NotifierSettings"
import { RepositoryWebhookSettings } from "@/components/settings/RepositoryWebhookSettings"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useNotifiers, useRepositoryWebhooks } from "@/hooks/queries"

export default function WebhooksPage() {
    const { t } = useTranslation()
    const { data: notifiersData } = useNotifiers({ limit: 1 })
    const { data: repoHooks = [] } = useRepositoryWebhooks()

    const outgoingTotal = notifiersData?.total ?? 0
    const repoTotal = repoHooks.length

    return (
        <div className="flex h-full min-h-0 flex-col gap-4">
            <Tabs defaultValue="outgoing" className="flex min-h-0 flex-1 flex-col gap-4">
                <div className="flex flex-none flex-wrap items-center justify-between gap-3">
                    <TabsList className="h-auto flex-wrap">
                        <TabsTrigger value="outgoing" className="flex items-center gap-1.5">
                            <ArrowUpFromLine className="size-4" />
                            <span>{t("webhooks.tabs.outgoing")}</span>
                            {outgoingTotal > 0 && (
                                <Badge variant="secondary" className="h-4.5 px-1.5 text-[10px] font-normal">
                                    {outgoingTotal}
                                </Badge>
                            )}
                        </TabsTrigger>
                        <TabsTrigger value="repository" className="flex items-center gap-1.5">
                            <ArrowDownToLine className="size-4" />
                            <span>{t("webhooks.tabs.repository")}</span>
                            {repoTotal > 0 && (
                                <Badge variant="secondary" className="h-4.5 px-1.5 text-[10px] font-normal">
                                    {repoTotal}
                                </Badge>
                            )}
                        </TabsTrigger>
                        <TabsTrigger value="templates">{t("notificationTemplates.title")}</TabsTrigger>
                    </TabsList>
                </div>

                <TabsContent value="outgoing" className="mt-0 flex min-h-0 flex-1 flex-col">
                    <NotifierSettings />
                </TabsContent>
                <TabsContent value="repository" className="mt-0 flex min-h-0 flex-1 flex-col">
                    <RepositoryWebhookSettings />
                </TabsContent>
                <TabsContent value="templates" className="mt-0 flex min-h-0 flex-1 flex-col">
                    <NotificationTemplates />
                </TabsContent>
            </Tabs>
        </div>
    )
}
