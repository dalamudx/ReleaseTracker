import type { UseFormReturn } from "react-hook-form"
import { useTranslation } from "react-i18next"

import type { ExecutorTargetRef } from "@/api/types"
import {
    FormControl,
    FormDescription,
    FormField,
    FormItem,
    FormLabel,
    FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { READINESS_FIELDS } from "@/lib/readiness"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"

import type { ExecutorFormValues } from "./executorSheetHelpers"


interface ExecutorSheetHealthCheckFieldsProps {
    form: UseFormReturn<ExecutorFormValues>
    selectedTargetRef: ExecutorTargetRef | Record<string, unknown>
}


export function ExecutorSheetHealthCheckFields({
    form,
    selectedTargetRef,
}: ExecutorSheetHealthCheckFieldsProps) {
    const { t } = useTranslation()
    const configuredStrategy = form.watch("health_check_strategy")
    const isSshCompose = selectedTargetRef.mode === "ssh_compose"
    const readinessEnabled = form.watch("health_check_readiness_enabled") ?? true
    const useDefaults = form.watch("health_check_use_system_readiness_defaults") ?? true
    const strategy = readinessEnabled
        && !isSshCompose
        && (configuredStrategy === "manual_http" || configuredStrategy === "manual_tcp")
        ? configuredStrategy
        : "none"
    const probeActive = strategy !== "none"
    const isHttpProbe = strategy === "manual_http"
    const isTcpProbe = strategy === "manual_tcp"

    return (
        <div className="space-y-4 border-t border-border/60 pt-4">
            <FormField
                control={form.control}
                name="health_check_readiness_enabled"
                render={({ field }) => (
                    <FormItem className="flex flex-col gap-3 rounded-lg border border-border/60 bg-muted/20 p-4 sm:flex-row sm:items-center sm:justify-between">
                        <div className="space-y-1">
                            <FormLabel>{t("executors.healthCheck.title")}</FormLabel>
                            <FormDescription>{t("executors.healthCheck.description")}</FormDescription>
                        </div>
                        <FormControl>
                            <Switch checked={field.value ?? true} onCheckedChange={field.onChange} />
                        </FormControl>
                    </FormItem>
                )}
            />

            {readinessEnabled && (
                <div className="space-y-4">
                    {!isSshCompose && (
                        <FormField
                            control={form.control}
                            name="health_check_strategy"
                            render={({ field }) => (
                                <FormItem>
                                    <FormLabel>{t("executors.healthCheck.fields.supplementalStrategy")}</FormLabel>
                                    <Select
                                        value={strategy}
                                        onValueChange={(value) => {
                                            field.onChange(value)
                                            if (value === "none") {
                                                form.setValue("health_check_failure_policy", "mark_failed")
                                            }
                                        }}
                                    >
                                        <FormControl>
                                            <SelectTrigger><SelectValue /></SelectTrigger>
                                        </FormControl>
                                        <SelectContent>
                                            <SelectItem value="none">{t("executors.healthCheck.strategy.none")}</SelectItem>
                                            <SelectItem value="manual_http">{t("executors.healthCheck.strategy.manual_http")}</SelectItem>
                                            <SelectItem value="manual_tcp">{t("executors.healthCheck.strategy.manual_tcp")}</SelectItem>
                                        </SelectContent>
                                    </Select>
                                    <FormDescription>{t("executors.healthCheck.supplementalStrategyHelp")}</FormDescription>
                                    <FormMessage />
                                </FormItem>
                            )}
                        />
                    )}
                    {([
                        ["health_check_use_system_readiness_defaults", "useDefaults", true],
                        ["health_check_notify_result", "notify", false],
                    ] as const).map(([name, label, fallback]) => (
                        <FormField key={name} control={form.control} name={name} render={({ field }) => (
                            <FormItem className="flex items-center justify-between gap-4">
                                <div><FormLabel>{t(`readiness.${label}`)}</FormLabel>
                                    <FormDescription>{t(`readiness.${label}Help`)}</FormDescription></div>
                                <FormControl><Switch checked={field.value ?? fallback} onCheckedChange={field.onChange} /></FormControl>
                            </FormItem>
                        )} />
                    ))}
                    {!useDefaults && <div className="grid gap-4 sm:grid-cols-2">
                        {READINESS_FIELDS.map((setting) => <FormField key={setting.key} control={form.control}
                            name={`health_check_${setting.key}`} render={({ field }) => (
                                <FormItem><FormLabel>{t(`readiness.${setting.key}`)}</FormLabel>
                                    <FormControl><Input type="number" min={setting.min} max={setting.max} step={1} {...field} value={field.value ?? String(setting.defaultValue)} /></FormControl>
                                    <FormMessage />
                                </FormItem>
                            )} />)}
                    </div>}
                </div>
            )}
            {probeActive ? (
                <div className="space-y-4">
                    <div className="grid gap-4 md:grid-cols-2">
                        <FormField
                            control={form.control}
                            name="health_check_failure_policy"
                            render={({ field }) => (
                                <FormItem>
                                    <FormLabel>
                                        {t("executors.healthCheck.fields.failurePolicy")}
                                    </FormLabel>
                                    <Select
                                        value={field.value}
                                        onValueChange={field.onChange}
                                    >
                                        <FormControl>
                                            <SelectTrigger>
                                                <SelectValue />
                                            </SelectTrigger>
                                        </FormControl>
                                        <SelectContent>
                                            <SelectItem value="mark_failed">
                                                {t("executors.healthCheck.failurePolicy.mark_failed")}
                                            </SelectItem>
                                            <SelectItem value="mark_degraded">
                                                {t("executors.healthCheck.failurePolicy.mark_degraded")}
                                            </SelectItem>
                                        </SelectContent>
                                    </Select>
                                    <FormMessage />
                                </FormItem>
                            )}
                        />
                    </div>

                    {isHttpProbe || isTcpProbe ? (
                        <div className="space-y-3">
                            <div>
                                <div className="text-sm font-medium">
                                    {t("executors.healthCheck.sections.probe")}: {t(`executors.healthCheck.strategy.${strategy}`)}
                                </div>
                                <p className="mt-1 text-xs text-muted-foreground">
                                    {isHttpProbe
                                        ? t("executors.healthCheck.hints.httpProbe")
                                        : t("executors.healthCheck.hints.tcpProbe")}
                                </p>
                            </div>

                            {isHttpProbe ? (
                                <div className="grid gap-4 md:grid-cols-2">
                                    <FormField
                                        control={form.control}
                                        name="health_check_http_host"
                                        rules={{ required: t("executors.validation.healthCheckHostRequired") }}
                                        render={({ field }) => (
                                            <FormItem>
                                                <FormLabel>
                                                    {t("executors.healthCheck.fields.host")}
                                                </FormLabel>
                                                <FormControl>
                                                    <Input placeholder="127.0.0.1" {...field} />
                                                </FormControl>
                                                <FormMessage />
                                            </FormItem>
                                        )}
                                    />
                                    <FormField
                                        control={form.control}
                                        name="health_check_http_port"
                                        rules={{ required: t("executors.validation.healthCheckTcpPortRequired") }}
                                        render={({ field }) => (
                                            <FormItem>
                                                <FormLabel>
                                                    {t("executors.healthCheck.fields.httpPort")}
                                                </FormLabel>
                                                <FormControl>
                                                    <Input type="number" min={1} max={65535} step={1} placeholder="8080" {...field} />
                                                </FormControl>
                                                <FormMessage />
                                            </FormItem>
                                        )}
                                    />
                                    <FormField
                                        control={form.control}
                                        name="health_check_http_path"
                                        rules={{
                                            required: t("executors.validation.healthCheckHttpPathRequired"),
                                            validate: (value) => value.startsWith("/") || t("executors.validation.healthCheckHttpPathInvalid"),
                                        }}
                                        render={({ field }) => (
                                            <FormItem>
                                                <FormLabel>
                                                    {t("executors.healthCheck.fields.httpPath")}
                                                </FormLabel>
                                                <FormControl>
                                                    <Input placeholder="/health" {...field} />
                                                </FormControl>
                                                <FormMessage />
                                            </FormItem>
                                        )}
                                    />
                                    <FormField
                                        control={form.control}
                                        name="health_check_http_scheme"
                                        render={({ field }) => (
                                            <FormItem>
                                                <FormLabel>
                                                    {t("executors.healthCheck.fields.httpScheme")}
                                                </FormLabel>
                                                <Select value={field.value} onValueChange={field.onChange}>
                                                    <FormControl>
                                                        <SelectTrigger>
                                                            <SelectValue />
                                                        </SelectTrigger>
                                                    </FormControl>
                                                    <SelectContent>
                                                        <SelectItem value="http">http</SelectItem>
                                                        <SelectItem value="https">https</SelectItem>
                                                    </SelectContent>
                                                </Select>
                                                <FormMessage />
                                            </FormItem>
                                        )}
                                    />
                                    <FormField
                                        control={form.control}
                                        name="health_check_http_method"
                                        render={({ field }) => (
                                            <FormItem>
                                                <FormLabel>
                                                    {t("executors.healthCheck.fields.httpMethod")}
                                                </FormLabel>
                                                <Select value={field.value} onValueChange={field.onChange}>
                                                    <FormControl>
                                                        <SelectTrigger>
                                                            <SelectValue />
                                                        </SelectTrigger>
                                                    </FormControl>
                                                    <SelectContent>
                                                        <SelectItem value="GET">GET</SelectItem>
                                                        <SelectItem value="HEAD">HEAD</SelectItem>
                                                    </SelectContent>
                                                </Select>
                                                <FormMessage />
                                            </FormItem>
                                        )}
                                    />
                                    <FormField
                                        control={form.control}
                                        name="health_check_http_expected_status_codes"
                                        render={({ field }) => (
                                            <FormItem>
                                                <FormLabel>
                                                    {t("executors.healthCheck.fields.httpStatusCodes")}
                                                </FormLabel>
                                                <FormControl>
                                                    <Input placeholder="200,204" {...field} />
                                                </FormControl>
                                                <FormDescription className="text-xs">
                                                    {t("executors.healthCheck.hints.httpStatusCodes")}
                                                </FormDescription>
                                                <FormMessage />
                                            </FormItem>
                                        )}
                                    />
                                </div>
                            ) : null}

                            {isTcpProbe ? (
                                <div className="grid gap-4 md:grid-cols-2">
                                    <FormField
                                        control={form.control}
                                        name="health_check_tcp_host"
                                        rules={{ required: t("executors.validation.healthCheckHostRequired") }}
                                        render={({ field }) => (
                                            <FormItem>
                                                <FormLabel>
                                                    {t("executors.healthCheck.fields.host")}
                                                </FormLabel>
                                                <FormControl>
                                                    <Input placeholder="127.0.0.1" {...field} />
                                                </FormControl>
                                                <FormMessage />
                                            </FormItem>
                                        )}
                                    />
                                    <FormField
                                        control={form.control}
                                        name="health_check_tcp_port"
                                        rules={{ required: t("executors.validation.healthCheckTcpPortRequired") }}
                                        render={({ field }) => (
                                            <FormItem>
                                                <FormLabel>
                                                    {t("executors.healthCheck.fields.tcpPort")}
                                                </FormLabel>
                                                <FormControl>
                                                    <Input type="number" min={1} max={65535} step={1} placeholder="8080" {...field} />
                                                </FormControl>
                                                <FormMessage />
                                            </FormItem>
                                        )}
                                    />
                                </div>
                            ) : null}
                        </div>
                    ) : null}

                    <div className="space-y-3">
                        <div>
                            <div className="text-sm font-medium">
                                {t("executors.healthCheck.sections.timing")}
                            </div>
                            <p className="mt-1 text-xs text-muted-foreground">
                                {t("executors.healthCheck.hints.probeWindow")}
                            </p>
                        </div>
                        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                            <FormField
                                control={form.control}
                                name="health_check_grace_period_seconds"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>
                                            {t("executors.healthCheck.fields.gracePeriod")}
                                        </FormLabel>
                                        <FormControl>
                                            <Input type="number" min={0} step={1} {...field} />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                            <FormField
                                control={form.control}
                                name="health_check_attempt_timeout_seconds"
                                rules={{
                                    validate: (value) => Number.parseInt(value, 10) > 0 || t("executors.validation.healthCheckTimingRequired"),
                                }}
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>
                                            {t("executors.healthCheck.fields.attemptTimeout")}
                                        </FormLabel>
                                        <FormControl>
                                            <Input type="number" min={1} step={1} {...field} />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                            <FormField
                                control={form.control}
                                name="health_check_interval_seconds"
                                rules={{
                                    validate: (value) => Number.parseInt(value, 10) > 0 || t("executors.validation.healthCheckTimingRequired"),
                                }}
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>
                                            {t("executors.healthCheck.fields.interval")}
                                        </FormLabel>
                                        <FormControl>
                                            <Input type="number" min={1} step={1} {...field} />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                            <FormField
                                control={form.control}
                                name="health_check_probe_window_seconds"
                                rules={{
                                    validate: (value) => Number.parseInt(value, 10) > 0 || t("executors.validation.healthCheckTimingRequired"),
                                }}
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>
                                            {t("executors.healthCheck.fields.probeWindow")}
                                        </FormLabel>
                                        <FormControl>
                                            <Input type="number" min={1} step={1} {...field} />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                        </div>
                    </div>
                </div>
            ) : null}
        </div>
    )
}

