import { useMemo, useState } from "react"
import { AlertTriangle, Clock3, Database, KeyRound, Link2, RotateCw, Save, Settings2 } from "lucide-react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import { OIDCProvidersManagement } from "@/components/admin/OIDCProvidersManagement"
import { clearAuthStorage } from "@/api/client"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
    useRotateEncryptionKey,
    useRotateJwtSecret,
    useSecurityKeys,
    useSettings,
    useUpdateSetting,
} from "@/hooks/queries"

export const SYSTEM_TIMEZONE_SETTING_KEY = "system.timezone"
export const SYSTEM_LOG_LEVEL_SETTING_KEY = "system.log_level"
export const SYSTEM_BASE_URL_SETTING_KEY = "system.base_url"
export const SYSTEM_RELEASE_HISTORY_RETENTION_COUNT_SETTING_KEY = "system.release_history_retention_count"

const DEFAULT_LOG_LEVEL = "INFO"
const LOG_LEVEL_OPTIONS = ["DEBUG", "INFO", "WARNING", "ERROR"]
const DEFAULT_RELEASE_HISTORY_RETENTION_COUNT = 20
const MIN_RELEASE_HISTORY_RETENTION_COUNT = 1
const MAX_RELEASE_HISTORY_RETENTION_COUNT = 1000

const FALLBACK_TIMEZONES = [
    "UTC",
    "Asia/Shanghai",
    "Asia/Hong_Kong",
    "Asia/Taipei",
    "Asia/Tokyo",
    "Asia/Seoul",
    "Asia/Singapore",
    "Asia/Kolkata",
    "Europe/London",
    "Europe/Berlin",
    "Europe/Paris",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "Australia/Sydney",
]

function getBrowserTimezone() {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
}

function getSupportedTimezones() {
    const intlWithSupportedValues = Intl as typeof Intl & {
        supportedValuesOf?: (key: "timeZone") => string[]
    }

    return intlWithSupportedValues.supportedValuesOf?.("timeZone") ?? FALLBACK_TIMEZONES
}

function BaseUrlSettingItem({
    baseUrl,
    onBaseUrlChange,
}: {
    baseUrl: string
    onBaseUrlChange: (value: string) => void
}) {
    const { t } = useTranslation()

    return (
        <div className="grid gap-4 py-5 md:grid-cols-[minmax(0,1fr)_minmax(18rem,22rem)] md:items-start">
            <div className="flex min-w-0 gap-3">
                <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                    <Link2 className="h-4 w-4" />
                </div>
                <div className="min-w-0 space-y-1">
                    <h3 className="text-sm font-semibold text-foreground">
                        {t("systemSettings.global.baseUrl.title")}
                    </h3>
                    <p className="text-sm leading-relaxed text-muted-foreground">
                        {t("systemSettings.global.baseUrl.description")}
                    </p>
                </div>
            </div>
            <div className="min-w-0 space-y-2">
                <label className="text-sm font-medium" htmlFor="system-base-url">
                    {t("systemSettings.global.baseUrl.label")}
                </label>
                <Input
                    id="system-base-url"
                    value={baseUrl}
                    onChange={(event) => onBaseUrlChange(event.target.value)}
                    placeholder={t("systemSettings.global.baseUrl.placeholder")}
                    className="min-w-0"
                />
            </div>
        </div>
    )
}

function LogLevelSettingItem({
    logLevel,
    onLogLevelChange,
}: {
    logLevel: string
    onLogLevelChange: (value: string) => void
}) {
    const { t } = useTranslation()

    return (
        <div className="grid gap-4 py-5 md:grid-cols-[minmax(0,1fr)_minmax(18rem,22rem)] md:items-start">
            <div className="flex min-w-0 gap-3">
                <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                    <Settings2 className="h-4 w-4" />
                </div>
                <div className="min-w-0 space-y-1">
                    <h3 className="text-sm font-semibold text-foreground">
                        {t("systemSettings.global.logLevel.title")}
                    </h3>
                    <p className="text-sm leading-relaxed text-muted-foreground">
                        {t("systemSettings.global.logLevel.description")}
                    </p>
                </div>
            </div>
            <div className="min-w-0 space-y-2">
                <label className="text-sm font-medium" htmlFor="system-log-level">
                    {t("systemSettings.global.logLevel.label")}
                </label>
                <Select value={logLevel} onValueChange={onLogLevelChange}>
                    <SelectTrigger id="system-log-level" className="min-w-0">
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        {LOG_LEVEL_OPTIONS.map((option) => (
                            <SelectItem key={option} value={option}>
                                {option}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            </div>
        </div>
    )
}

function ReleaseHistoryRetentionSettingItem({
    retentionDraft,
    onRetentionDraftChange,
}: {
    retentionDraft: string
    onRetentionDraftChange: (value: string) => void
}) {
    const { t } = useTranslation()

    return (
        <div className="grid gap-4 py-5 md:grid-cols-[minmax(0,1fr)_minmax(18rem,22rem)] md:items-start">
            <div className="flex min-w-0 gap-3">
                <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                    <Database className="h-4 w-4" />
                </div>
                <div className="min-w-0 space-y-1">
                    <h3 className="text-sm font-semibold text-foreground">
                        {t("systemSettings.global.releaseHistoryRetention.title")}
                    </h3>
                    <p className="text-sm leading-relaxed text-muted-foreground">
                        {t("systemSettings.global.releaseHistoryRetention.description")}
                    </p>
                </div>
            </div>
            <div className="min-w-0 space-y-2">
                <label className="text-sm font-medium" htmlFor="release-history-retention-count">
                    {t("systemSettings.global.releaseHistoryRetention.label")}
                </label>
                <Input
                    id="release-history-retention-count"
                    type="number"
                    min={MIN_RELEASE_HISTORY_RETENTION_COUNT}
                    max={MAX_RELEASE_HISTORY_RETENTION_COUNT}
                    value={retentionDraft}
                    onChange={(event) => onRetentionDraftChange(event.target.value)}
                    className="min-w-0"
                />
            </div>
        </div>
    )
}

function TimezoneSettingItem({
    timezone,
    timezoneOptions,
    onTimezoneChange,
}: {
    timezone: string
    timezoneOptions: string[]
    onTimezoneChange: (value: string) => void
}) {
    const { t } = useTranslation()

    return (
        <div className="grid gap-4 py-5 md:grid-cols-[minmax(0,1fr)_minmax(18rem,22rem)] md:items-start">
            <div className="flex min-w-0 gap-3">
                <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                    <Clock3 className="h-4 w-4" />
                </div>
                <div className="min-w-0 space-y-1">
                    <h3 className="text-sm font-semibold text-foreground">
                        {t("systemSettings.global.timezone.title")}
                    </h3>
                    <p className="text-sm leading-relaxed text-muted-foreground">
                        {t("systemSettings.global.timezone.description")}
                    </p>
                </div>
            </div>
            <div className="min-w-0 space-y-2">
                <label className="text-sm font-medium" htmlFor="system-timezone">
                    {t("systemSettings.global.timezone.label")}
                </label>
                <Select value={timezone} onValueChange={onTimezoneChange}>
                    <SelectTrigger id="system-timezone" className="min-w-0">
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        {timezoneOptions.map((option) => (
                            <SelectItem key={option} value={option}>
                                {option}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            </div>
        </div>
    )
}

function SecurityKeyRow({
    title,
    description,
    fingerprint,
    details,
    rotateLabel,
    disabled,
    onRotate,
}: {
    title: string
    description: string
    fingerprint?: string
    details: Array<{ label: string; value: number | string }>
    rotateLabel: string
    disabled?: boolean
    onRotate: () => void
}) {
    return (
        <div className="space-y-4 py-5">
            <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_auto] md:items-start">
                <div className="flex min-w-0 gap-3">
                    <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                        <KeyRound className="h-4 w-4" />
                    </div>
                    <div className="min-w-0 space-y-2">
                        <div className="space-y-1">
                            <h3 className="text-sm font-semibold text-foreground">{title}</h3>
                            <p className="text-sm leading-relaxed text-muted-foreground">{description}</p>
                        </div>
                        <div className="inline-flex max-w-full rounded bg-muted px-2 py-1 font-mono text-xs text-muted-foreground">
                            <span className="truncate">{fingerprint ?? "—"}</span>
                        </div>
                    </div>
                </div>
                <Button type="button" variant="outline" disabled={disabled} onClick={onRotate}>
                    <RotateCw className="mr-2 h-4 w-4" />
                    {rotateLabel}
                </Button>
            </div>
            {details.length > 0 ? (
                <div className="grid gap-3 pl-12 sm:grid-cols-2 lg:grid-cols-4">
                    {details.map((detail) => (
                        <div key={detail.label} className="rounded-lg border border-border/60 bg-muted/30 px-3 py-2">
                            <div className="text-xs text-muted-foreground">{detail.label}</div>
                            <div className="mt-1 text-sm font-medium text-foreground">{detail.value}</div>
                        </div>
                    ))}
                </div>
            ) : null}
        </div>
    )
}

function RotateSecurityKeyDialog({
    open,
    title,
    description,
    warning,
    manualLabel,
    isPending,
    onOpenChange,
    onSubmit,
}: {
    open: boolean
    title: string
    description: string
    warning: string
    manualLabel: string
    isPending: boolean
    onOpenChange: (open: boolean) => void
    onSubmit: (payload: { generate: boolean; value?: string | null }) => Promise<void>
}) {
    const { t } = useTranslation()
    const [mode, setMode] = useState<"generate" | "manual">("generate")
    const [manualValue, setManualValue] = useState("")

    const handleSubmit = async () => {
        await onSubmit({
            generate: mode === "generate",
            value: mode === "manual" ? manualValue : null,
        })
        setManualValue("")
        setMode("generate")
    }

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>{title}</DialogTitle>
                    <DialogDescription>{description}</DialogDescription>
                </DialogHeader>
                <div className="space-y-4">
                    <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
                        {warning}
                    </div>
                    <div className="grid gap-2 sm:grid-cols-2">
                        <Button
                            type="button"
                            variant={mode === "generate" ? "default" : "outline"}
                            onClick={() => setMode("generate")}
                        >
                            {t("systemSettings.securityKeys.generate")}
                        </Button>
                        <Button
                            type="button"
                            variant={mode === "manual" ? "default" : "outline"}
                            onClick={() => setMode("manual")}
                        >
                            {t("systemSettings.securityKeys.manual")}
                        </Button>
                    </div>
                    {mode === "manual" ? (
                        <div className="space-y-2">
                            <label className="text-sm font-medium" htmlFor="security-key-manual-value">
                                {manualLabel}
                            </label>
                            <Textarea
                                id="security-key-manual-value"
                                value={manualValue}
                                onChange={(event) => setManualValue(event.target.value)}
                                className="min-h-24 font-mono text-xs"
                            />
                        </div>
                    ) : null}
                </div>
                <DialogFooter showCloseButton>
                    <Button
                        type="button"
                        variant="destructive"
                        disabled={isPending || (mode === "manual" && !manualValue.trim())}
                        onClick={handleSubmit}
                    >
                        {isPending ? t("common.saving") : t("systemSettings.securityKeys.confirmRotate")}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    )
}

export function SystemSettingsPage() {
    const { t } = useTranslation()
    const { data: settings = [] } = useSettings()
    const { data: securityKeys } = useSecurityKeys()
    const updateSetting = useUpdateSetting()
    const rotateJwtSecret = useRotateJwtSecret()
    const rotateEncryptionKey = useRotateEncryptionKey()
    const [jwtDialogOpen, setJwtDialogOpen] = useState(false)
    const [encryptionDialogOpen, setEncryptionDialogOpen] = useState(false)
    const currentTimezone = useMemo(() => {
        const value = settings.find((item) => item.key === SYSTEM_TIMEZONE_SETTING_KEY)?.value
        return typeof value === "string" && value.trim() ? value.trim() : getBrowserTimezone()
    }, [settings])
    const currentLogLevel = useMemo(() => {
        const value = settings.find((item) => item.key === SYSTEM_LOG_LEVEL_SETTING_KEY)?.value
        const normalizedValue = String(value ?? DEFAULT_LOG_LEVEL).trim().toUpperCase()
        return LOG_LEVEL_OPTIONS.includes(normalizedValue) ? normalizedValue : DEFAULT_LOG_LEVEL
    }, [settings])
    const currentBaseUrl = useMemo(() => {
        const value = settings.find((item) => item.key === SYSTEM_BASE_URL_SETTING_KEY)?.value
        return typeof value === "string" ? value.trim() : ""
    }, [settings])
    const currentRetentionCount = useMemo(() => {
        const value = settings.find(
            (item) => item.key === SYSTEM_RELEASE_HISTORY_RETENTION_COUNT_SETTING_KEY,
        )?.value
        const parsed = Number.parseInt(String(value ?? ""), 10)
        return Number.isInteger(parsed) &&
            parsed >= MIN_RELEASE_HISTORY_RETENTION_COUNT &&
            parsed <= MAX_RELEASE_HISTORY_RETENTION_COUNT
            ? parsed
            : DEFAULT_RELEASE_HISTORY_RETENTION_COUNT
    }, [settings])
    const [timezoneDraft, setTimezoneDraft] = useState<string | null>(null)
    const [logLevelDraft, setLogLevelDraft] = useState<string | null>(null)
    const [baseUrlDraft, setBaseUrlDraft] = useState<string | null>(null)
    const [retentionDraft, setRetentionDraft] = useState<string | null>(null)
    const timezone = timezoneDraft ?? currentTimezone
    const logLevel = logLevelDraft ?? currentLogLevel
    const baseUrl = baseUrlDraft ?? currentBaseUrl
    const retentionValue = retentionDraft ?? String(currentRetentionCount)
    const timezoneOptions = useMemo(() => {
        const options = new Set(getSupportedTimezones())
        options.add(currentTimezone)
        options.add("UTC")
        return [...options].sort((left, right) => left.localeCompare(right))
    }, [currentTimezone])
    const normalizedRetention = Number.parseInt(retentionValue, 10)
    const isValidRetention =
        Number.isInteger(normalizedRetention) &&
        normalizedRetention >= MIN_RELEASE_HISTORY_RETENTION_COUNT &&
        normalizedRetention <= MAX_RELEASE_HISTORY_RETENTION_COUNT &&
        String(normalizedRetention) === retentionValue.trim()
    const normalizedBaseUrl = baseUrl.trim().replace(/\/+$/, "")
    const isValidBaseUrl = !normalizedBaseUrl || /^https?:\/\/[^\s/?#]+[^\s?#]*$/i.test(normalizedBaseUrl)

    const handleSaveGlobalSettings = async () => {
        if (!isValidRetention) {
            toast.error(t("systemSettings.global.releaseHistoryRetention.invalid"))
            return
        }
        if (!isValidBaseUrl) {
            toast.error(t("systemSettings.global.baseUrl.invalid"))
            return
        }

        try {
            await Promise.all([
                updateSetting.mutateAsync({
                    key: SYSTEM_TIMEZONE_SETTING_KEY,
                    value: timezone.trim() || "UTC",
                }),
                updateSetting.mutateAsync({
                    key: SYSTEM_LOG_LEVEL_SETTING_KEY,
                    value: logLevel,
                }),
                updateSetting.mutateAsync({
                    key: SYSTEM_BASE_URL_SETTING_KEY,
                    value: normalizedBaseUrl,
                }),
                updateSetting.mutateAsync({
                    key: SYSTEM_RELEASE_HISTORY_RETENTION_COUNT_SETTING_KEY,
                    value: String(normalizedRetention),
                }),
            ])
            setTimezoneDraft(null)
            setLogLevelDraft(null)
            setBaseUrlDraft(null)
            setRetentionDraft(null)
            toast.success(t("common.saved"))
        } catch (error) {
            console.error("Failed to save global settings", error)
            toast.error(t("common.unexpectedError"))
        }
    }

    const handleRotateJwtSecret = async (payload: { generate: boolean; value?: string | null }) => {
        try {
            const result = await rotateJwtSecret.mutateAsync(payload)
            toast.success(t("systemSettings.securityKeys.jwt.rotated", { count: result.invalidated_sessions }))
            setJwtDialogOpen(false)
            clearAuthStorage()
            window.location.href = "/login"
        } catch (error) {
            console.error("Failed to rotate JWT secret", error)
            toast.error(t("common.unexpectedError"))
        }
    }

    const handleRotateEncryptionKey = async (payload: { generate: boolean; value?: string | null }) => {
        try {
            const result = await rotateEncryptionKey.mutateAsync(payload)
            toast.success(t("systemSettings.securityKeys.encryption.rotated", { count: result.plaintext_reencrypted }))
            setEncryptionDialogOpen(false)
        } catch (error) {
            console.error("Failed to rotate encryption key", error)
            toast.error(t("common.unexpectedError"))
        }
    }

    const inventory = securityKeys?.encryption_key.inventory
    const encryptedInventoryDetails = inventory
        ? [
            {
                label: t("systemSettings.securityKeys.summary.credentialsToken"),
                value: inventory.credentials_token,
            },
            {
                label: t("systemSettings.securityKeys.summary.credentialsSecrets"),
                value: inventory.credentials_secrets,
            },
            {
                label: t("systemSettings.securityKeys.summary.oidcSecrets"),
                value: inventory.oauth_provider_client_secret,
            },
            {
                label: t("systemSettings.securityKeys.summary.runtimeSecrets"),
                value: inventory.runtime_connection_secrets,
            },
        ]
        : []
    const undecryptableCount = securityKeys?.encryption_key.undecryptable_count ?? 0
    const hasUndecryptableValues = undecryptableCount > 0

    return (
        <div className="container mx-auto max-w-5xl space-y-6 px-4 py-6">
            <div className="space-y-1">
                <h1 className="text-2xl font-semibold tracking-tight text-foreground">
                    {t("systemSettings.system.title")}
                </h1>
                <p className="text-sm text-muted-foreground">
                    {t("systemSettings.system.description")}
                </p>
            </div>

            <Tabs defaultValue="general" className="space-y-4">
                <TabsList className="w-full justify-start sm:w-fit">
                    <TabsTrigger value="general">{t("systemSettings.tabs.general")}</TabsTrigger>
                    <TabsTrigger value="security">{t("systemSettings.tabs.security")}</TabsTrigger>
                    <TabsTrigger value="oidc">{t("systemSettings.tabs.oidc")}</TabsTrigger>
                </TabsList>

                <TabsContent value="general">
                    <Card className="border-border/60 bg-card/80">
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2">
                                <Settings2 className="h-5 w-5 text-primary" />
                                {t("systemSettings.global.title")}
                            </CardTitle>
                            <CardDescription>{t("systemSettings.global.description")}</CardDescription>
                        </CardHeader>
                        <CardContent>
                            <div className="divide-y divide-border/60">
                                <BaseUrlSettingItem
                                    baseUrl={baseUrl}
                                    onBaseUrlChange={setBaseUrlDraft}
                                />
                                <TimezoneSettingItem
                                    timezone={timezone}
                                    timezoneOptions={timezoneOptions}
                                    onTimezoneChange={setTimezoneDraft}
                                />
                                <LogLevelSettingItem
                                    logLevel={logLevel}
                                    onLogLevelChange={setLogLevelDraft}
                                />
                                <ReleaseHistoryRetentionSettingItem
                                    retentionDraft={retentionValue}
                                    onRetentionDraftChange={setRetentionDraft}
                                />
                            </div>
                            <div className="flex justify-end border-t border-border/60 pt-5">
                                <Button
                                    type="button"
                                    onClick={handleSaveGlobalSettings}
                                    disabled={updateSetting.isPending}
                                >
                                    <Save className="mr-2 h-4 w-4" />
                                    {t("common.save")}
                                </Button>
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="security">
                    <Card className="border-border/60 bg-card/80">
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2">
                                <KeyRound className="h-5 w-5 text-primary" />
                                {t("systemSettings.securityKeys.title")}
                            </CardTitle>
                            <CardDescription>{t("systemSettings.securityKeys.description")}</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            {hasUndecryptableValues ? (
                                <div className="flex gap-3 rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
                                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                                    <span>
                                        {t("systemSettings.securityKeys.blocked", { count: undecryptableCount })}
                                    </span>
                                </div>
                            ) : null}
                            <div className="divide-y divide-border/60">
                                <SecurityKeyRow
                                    title={t("systemSettings.securityKeys.jwt.title")}
                                    description={t("systemSettings.securityKeys.jwt.description")}
                                    fingerprint={securityKeys?.jwt_secret.fingerprint}
                                    details={[
                                        {
                                            label: t("systemSettings.securityKeys.summary.activeSessions"),
                                            value: securityKeys?.jwt_secret.active_sessions ?? 0,
                                        },
                                    ]}
                                    rotateLabel={t("systemSettings.securityKeys.rotate")}
                                    onRotate={() => setJwtDialogOpen(true)}
                                />
                                <SecurityKeyRow
                                    title={t("systemSettings.securityKeys.encryption.title")}
                                    description={t("systemSettings.securityKeys.encryption.description")}
                                    fingerprint={securityKeys?.encryption_key.fingerprint}
                                    details={[
                                        ...encryptedInventoryDetails,
                                        {
                                            label: t("systemSettings.securityKeys.summary.undecryptable"),
                                            value: undecryptableCount,
                                        },
                                    ]}
                                    rotateLabel={t("systemSettings.securityKeys.rotate")}
                                    disabled={hasUndecryptableValues}
                                    onRotate={() => setEncryptionDialogOpen(true)}
                                />
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="oidc">
                    <div className="rounded-xl border border-border/50 bg-card p-6">
                        <OIDCProvidersManagement />
                    </div>
                </TabsContent>
            </Tabs>

            <RotateSecurityKeyDialog
                open={jwtDialogOpen}
                onOpenChange={setJwtDialogOpen}
                title={t("systemSettings.securityKeys.jwt.rotateTitle")}
                description={t("systemSettings.securityKeys.jwt.rotateDescription")}
                warning={t("systemSettings.securityKeys.jwt.warning")}
                manualLabel={t("systemSettings.securityKeys.jwt.manualLabel")}
                isPending={rotateJwtSecret.isPending}
                onSubmit={handleRotateJwtSecret}
            />
            <RotateSecurityKeyDialog
                open={encryptionDialogOpen}
                onOpenChange={setEncryptionDialogOpen}
                title={t("systemSettings.securityKeys.encryption.rotateTitle")}
                description={t("systemSettings.securityKeys.encryption.rotateDescription")}
                warning={t("systemSettings.securityKeys.encryption.warning")}
                manualLabel={t("systemSettings.securityKeys.encryption.manualLabel")}
                isPending={rotateEncryptionKey.isPending}
                onSubmit={handleRotateEncryptionKey}
            />
        </div>
    )
}
