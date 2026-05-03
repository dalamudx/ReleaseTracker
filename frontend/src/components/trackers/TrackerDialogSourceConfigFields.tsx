import type { UseFormReturn } from "react-hook-form"
import { useTranslation } from "react-i18next"

import type { GitHubFetchMode, TrackerChannelType } from "@/api/types"
import {
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

import { GITHUB_FETCH_MODE_OPTIONS, type TrackerFormValues } from "./trackerDialogHelpers"

interface TrackerDialogSourceConfigFieldsProps {
    form: UseFormReturn<TrackerFormValues>
    index: number
    channelType: TrackerChannelType
    group: "primary" | "secondary"
}

export function TrackerDialogSourceConfigFields({
    form,
    index,
    channelType,
    group,
}: TrackerDialogSourceConfigFieldsProps) {
    const { t } = useTranslation()

    if (channelType === "github") {
        if (group === "primary") {
            return (
                <FormField
                    control={form.control}
                    name={`sources.${index}.source_config.repo`}
                    render={({ field }) => (
                        <FormItem className="w-full max-w-lg">
                            <FormLabel>{t("tracker.fields.repo")}</FormLabel>
                            <FormControl>
                                <Input {...field} value={field.value ?? ""} placeholder="owner/repo" />
                            </FormControl>
                            <FormMessage />
                        </FormItem>
                    )}
                />
            )
        }

        return (
            <FormField
                control={form.control}
                name={`sources.${index}.source_config.fetch_mode`}
                render={({ field }) => (
                    <FormItem className="w-full max-w-md">
                        <FormLabel>{t("tracker.fields.githubFetchMode")}</FormLabel>
                        <Select value={field.value ?? "rest_first"} onValueChange={(value) => field.onChange(value as GitHubFetchMode)}>
                            <FormControl>
                                <SelectTrigger>
                                    <SelectValue />
                                </SelectTrigger>
                            </FormControl>
                            <SelectContent>
                                {GITHUB_FETCH_MODE_OPTIONS.map((option) => (
                                    <SelectItem key={option.value} value={option.value}>
                                        {t(option.labelKey)}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <FormDescription>{t("tracker.fields.githubFetchModeDesc")}</FormDescription>
                        <FormMessage />
                    </FormItem>
                )}
            />
        )
    }

    if (channelType === "gitlab") {
        if (group === "primary") {
            return (
                <FormField
                    control={form.control}
                    name={`sources.${index}.source_config.project`}
                    render={({ field }) => (
                        <FormItem className="w-full max-w-md">
                            <FormLabel>{t("tracker.fields.projectId")}</FormLabel>
                            <FormControl>
                                <Input {...field} value={field.value ?? ""} placeholder="group/project" />
                            </FormControl>
                            <FormMessage />
                        </FormItem>
                    )}
                />
            )
        }

        return (
            <FormField
                control={form.control}
                name={`sources.${index}.source_config.instance`}
                render={({ field }) => (
                    <FormItem className="w-full max-w-md">
                        <FormLabel>{t("tracker.fields.instanceUrl")}</FormLabel>
                        <FormControl>
                            <Input {...field} value={field.value ?? ""} placeholder="https://gitlab.com" />
                        </FormControl>
                        <FormMessage />
                    </FormItem>
                )}
            />
        )
    }

    if (channelType === "gitea") {
        if (group === "primary") {
            return (
                <FormField
                    control={form.control}
                    name={`sources.${index}.source_config.repo`}
                    render={({ field }) => (
                        <FormItem className="w-full max-w-md">
                            <FormLabel>{t("tracker.fields.giteaRepo")}</FormLabel>
                            <FormControl>
                                <Input {...field} value={field.value ?? ""} placeholder="owner/repo" />
                            </FormControl>
                            <FormMessage />
                        </FormItem>
                    )}
                />
            )
        }

        return (
            <FormField
                control={form.control}
                name={`sources.${index}.source_config.instance`}
                render={({ field }) => (
                    <FormItem className="w-full max-w-md">
                        <FormLabel>{t("tracker.fields.instanceUrl")}</FormLabel>
                        <FormControl>
                            <Input {...field} value={field.value ?? ""} placeholder="https://gitea.example.com" />
                        </FormControl>
                        <FormMessage />
                    </FormItem>
                )}
            />
        )
    }

    if (channelType === "helm") {
        if (group === "primary") {
            return (
                <FormField
                    control={form.control}
                    name={`sources.${index}.source_config.chart`}
                    render={({ field }) => (
                        <FormItem className="w-full max-w-md">
                            <FormLabel>{t("tracker.fields.chartName")}</FormLabel>
                            <FormControl>
                                <Input {...field} value={field.value ?? ""} placeholder="nginx" />
                            </FormControl>
                            <FormMessage />
                        </FormItem>
                    )}
                />
            )
        }

        return (
            <FormField
                control={form.control}
                name={`sources.${index}.source_config.repo`}
                render={({ field }) => (
                    <FormItem className="w-full max-w-md">
                        <FormLabel>{t("tracker.fields.chartRepo")}</FormLabel>
                        <FormControl>
                            <Input {...field} value={field.value ?? ""} placeholder="https://charts.bitnami.com/bitnami" />
                        </FormControl>
                        <FormMessage />
                    </FormItem>
                )}
            />
        )
    }

    if (group === "primary") {
        return (
            <FormField
                control={form.control}
                name={`sources.${index}.source_config.image`}
                render={({ field }) => (
                    <FormItem className="w-full max-w-lg">
                        <FormLabel>{t("tracker.fields.image")}</FormLabel>
                        <FormControl>
                            <Input {...field} value={field.value ?? ""} placeholder="example/app" />
                        </FormControl>
                        <FormMessage />
                    </FormItem>
                )}
            />
        )
    }

    return (
        <FormField
            control={form.control}
            name={`sources.${index}.source_config.registry`}
            render={({ field }) => (
                <FormItem className="w-full max-w-md">
                    <FormLabel>{t("tracker.fields.registry")}</FormLabel>
                    <FormControl>
                        <Input {...field} value={field.value ?? ""} placeholder="ghcr.io" />
                    </FormControl>
                    <FormMessage />
                </FormItem>
            )}
        />
    )
}
