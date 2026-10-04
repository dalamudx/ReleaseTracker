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
                    <TabsList className="flex w-fit max-w-full flex-wrap items-stretch justify-start gap-1 group-data-[orientation=horizontal]/tabs:h-auto">
                        <TabsTrigger value="outgoing" className="flex h-auto min-h-11 max-w-[calc(50%-0.125rem)] flex-none items-center gap-1.5 whitespace-normal max-[359px]:flex-wrap sm:min-h-9 sm:max-w-none">
                            <ArrowUpFromLine className="size-4" />
                            <span className="min-w-0 break-words max-[359px]:order-first max-[359px]:w-full">{t("webhooks.tabs.outgoing")}</span>
                            {outgoingTotal > 0 && (
                                <Badge variant="secondary" className="h-4.5 px-1.5 text-[10px] font-normal">
                                    {outgoingTotal}
                                </Badge>
                            )}
                        </TabsTrigger>
                        <TabsTrigger value="repository" className="flex h-auto min-h-11 max-w-[calc(50%-0.125rem)] flex-none items-center gap-1.5 whitespace-normal max-[359px]:flex-wrap sm:min-h-9 sm:max-w-none">
                            <ArrowDownToLine className="size-4" />
                            <span className="min-w-0 break-words max-[359px]:order-first max-[359px]:w-full">{t("webhooks.tabs.repository")}</span>
                            {repoTotal > 0 && (
                                <Badge variant="secondary" className="h-4.5 px-1.5 text-[10px] font-normal">
                                    {repoTotal}
                                </Badge>
                            )}
                        </TabsTrigger>
                        <TabsTrigger value="templates" className="h-auto min-h-11 max-w-full flex-none whitespace-normal sm:min-h-9">{t("notificationTemplates.title")}</TabsTrigger>
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
