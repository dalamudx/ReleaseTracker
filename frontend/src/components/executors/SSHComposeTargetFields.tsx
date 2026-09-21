import { useEffect, useId, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { api } from "@/api/client"
import type { ExecutorTargetRef, RuntimeConnection, SSHComposeExecutorTargetRef } from "@/api/types"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Spinner } from "@/components/ui/spinner"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { AlertTriangleIcon, FolderIcon, LockKeyholeIcon, RefreshCwIcon, ServerIcon, WrenchIcon } from "lucide-react"
import { getApiErrorDetailMessage } from "./executorSheetHelpers"

type Analysis = Awaited<ReturnType<typeof api.analyzeSSHCompose>>
type Discovery = Awaited<ReturnType<typeof api.discoverSSHCompose>>
const tools = ["docker_compose", "docker-compose", "podman_compose", "podman-compose"] as const
const lines = (value: string) => value.split("\n").map(v => v.trim()).filter(Boolean)
const join = (value: unknown, fallback = "") => Array.isArray(value) ? value.join("\n") : fallback
const initialFields = (value: ExecutorTargetRef) => ({
    discovery_id: typeof value.discovery_id === "string" ? value.discovery_id : null,
    working_dir: String(value.working_dir ?? ""), project: String(value.project ?? ""),
    config_files: join(value.config_files), env_files: join(value.env_files), profiles: join(value.profiles),
    tool: String(value.tool ?? ""), write_strategy: String(value.write_strategy ?? "source"),
})
// Match SSHComposeProject.path(): POSIX paths relative to the project directory.
// This is lexical comparison only; the backend still verifies runtime ownership.
function composePath(directory: string, path = ".") {
    const absolute = path.startsWith("/") ? path : `${directory}/${path}`
    const parts: string[] = []
    for (const part of absolute.split("/")) {
        if (!part || part === ".") continue
        if (part === "..") parts.pop()
        else parts.push(part)
    }
    const root = absolute.startsWith("//") && !absolute.startsWith("///") ? "//" : "/"
    return root + parts.join("/")
}
function sameComposePaths(directory: string, saved: string, observedDirectory: string, observed: string[]) {
    const paths = lines(saved)
    return paths.length === observed.length && paths.every((path, index) =>
        composePath(directory, path) === composePath(observedDirectory, observed[index]))
}
type Fields = ReturnType<typeof initialFields>
const build = (next: Fields): SSHComposeExecutorTargetRef => ({
    mode: "ssh_compose", discovery_id: next.discovery_id,
    project: next.project.trim(), working_dir: next.working_dir.trim(),
    config_files: lines(next.config_files), env_files: lines(next.env_files), profiles: lines(next.profiles),
    tool: next.tool as SSHComposeExecutorTargetRef["tool"], write_strategy: next.write_strategy as "source" | "override",
})

export function SSHComposeTargetFields({connection, value, onChange, executorId = null}: {
    executorId?: number | null
    connection: RuntimeConnection
    value: ExecutorTargetRef
    onChange: (value: ExecutorTargetRef) => void
}) {
    const {t} = useTranslation()
    const id = useId()
    const generation = useRef(0)
    const onChangeRef = useRef(onChange)
    useEffect(() => { onChangeRef.current = onChange }, [onChange])
    const [fields, setFields] = useState(() => initialFields(value))
    const [selection, setSelection] = useState(
        typeof value.discovery_id === "string" ? value.discovery_id : value.working_dir ? "manual" : "",
    )
    const [discovery, setDiscovery] = useState<Discovery | null>(null)
    const [discovering, setDiscovering] = useState(true)
    const [discoveryError, setDiscoveryError] = useState("")
    const [discoveryRevision, setDiscoveryRevision] = useState(0)
    const [retry, setRetry] = useState(0)
    const [busy, setBusy] = useState(false)
    const [result, setResult] = useState<Analysis | null>(null)
    const [error, setError] = useState("")
    const selected = discovery?.items.find(item => item.id === selection)
    const discoveredConfiguration = fields.discovery_id !== null && selection !== "manual"
    const discoveredConfigurationMatches = !discoveredConfiguration || (!discovering && !discoveryError && !!selected && (
        composePath(fields.working_dir) === composePath(selected.working_dir)
        && fields.project === selected.project
        && sameComposePaths(fields.working_dir, fields.config_files, selected.working_dir, selected.config_files)
        && sameComposePaths(fields.working_dir, fields.env_files, selected.working_dir, selected.env_files)
        && fields.profiles === selected.profiles.join("\n")
        && selected.tool_choices.includes(fields.tool)
    ))
    const ready = discoveredConfigurationMatches && connection.enabled && fields.working_dir.startsWith("/") && /^[a-z0-9][a-z0-9_-]{0,127}$/.test(fields.project.trim()) && !!lines(fields.config_files).length && tools.some(tool => tool === fields.tool)

    useEffect(() => {
        const controller = new AbortController()
        if (!connection.enabled) return
        api.discoverSSHCompose(connection.id, controller.signal).then(data => {
            if (!controller.signal.aborted) setDiscovery(data)
        }).catch(e => {
            if (!controller.signal.aborted) setDiscoveryError(getApiErrorDetailMessage(e) || "sshExecutor.discoveryFailed")
        }).finally(() => { if (!controller.signal.aborted) setDiscovering(false) })
        return () => controller.abort()
    }, [connection.id, connection.enabled, discoveryRevision])

    useEffect(() => {
        const request = ++generation.current
        const controller = new AbortController()
        const target = build(fields)
        onChangeRef.current(target)
        if (!ready) return () => controller.abort()
        const timer = window.setTimeout(async () => {
            setBusy(true)
            setError("")
            setResult(null)
            try {
                const data = await api.analyzeSSHCompose({runtime_connection_id: connection.id, target}, controller.signal)
                if (request !== generation.current || controller.signal.aborted) return
                if (data.selected_tool !== target.tool || !data.services.length) {
                    setError("sshExecutor.analyzeFirst")
                    return
                }
                setResult(data)
                onChangeRef.current({...target, services: data.services.map(s => ({service: s.service, image: s.image}))})
            } catch (e) {
                if (request === generation.current && !controller.signal.aborted) setError(getApiErrorDetailMessage(e) || "sshExecutor.analysisFailed")
            } finally {
                if (request === generation.current && !controller.signal.aborted) setBusy(false)
            }
        }, 600)
        return () => { window.clearTimeout(timer); controller.abort() }
    }, [fields, connection.id, ready, retry])

    const update = (next: Fields) => {
        generation.current++
        setFields(next)
        setResult(null)
        setError("")
        setBusy(false)
        onChange(build(next))
    }
    const change = (key: keyof Fields, text: string) => {
        if (executorId !== null && key !== "write_strategy") return
        if (
            discoveredConfiguration
            && key !== "write_strategy"
            && !(key === "tool" && !selected?.tool)
        ) return
        update({...fields, [key]: text})
    }
    const selectProject = (key: string) => {
        if (executorId !== null) return
        const item = discovery?.items.find(item => item.id === key)
        if (item && (item.owner || item.ownership_verified === false)) return
        setSelection(key)
        if (!item) {
            update({...fields, discovery_id: null})
            return
        }
        update({discovery_id: item.id, working_dir: item.working_dir, project: item.project, config_files: item.config_files.join("\n"), env_files: item.env_files.join("\n"), profiles: item.profiles.join("\n"), tool: item.tool ?? "", write_strategy: item.write_strategy})
    }
    const showReadOnlyConfiguration = (discoveredConfiguration || executorId !== null) && !!(fields.project || fields.working_dir)
    const showManualConfiguration = selection === "manual" && executorId === null
    const engine = selected?.engine ?? (fields.tool.startsWith("podman") ? "podman" : "docker")

    return <div className="min-w-0 space-y-4" aria-label={t("sshExecutor.title")}>
        <Card className="border-border/60 bg-card/80 shadow-sm" aria-label={t("sshExecutor.title")}>
            <CardHeader className="gap-1 px-4 sm:px-6">
                <div className="flex items-center gap-2">
                    <ServerIcon aria-hidden="true" className="size-4 text-primary" />
                    <CardTitle className="text-base">{t("sshExecutor.title")}</CardTitle>
                </div>
                <CardDescription>{t("sshExecutor.autoDiscovery")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4 px-4 sm:px-6">
                <div className="space-y-2">
                    <div className="flex items-center justify-between gap-2">
                        <Label htmlFor={`${id}-project-choice`} className="text-xs font-medium text-foreground">{t("sshExecutor.discoveredProjects")}</Label>
                        {executorId !== null && <span className="text-xs text-muted-foreground">{t("sshExecutor.projectFixed")}</span>}
                    </div>
                    <div className="flex min-w-0 flex-col gap-2 sm:flex-row">
                        <Select value={selection} onValueChange={selectProject} disabled={executorId !== null}>
                            <SelectTrigger id={`${id}-project-choice`} className="min-w-0 flex-1"><SelectValue placeholder={t("sshExecutor.chooseProject")} /></SelectTrigger>
                            <SelectContent className="max-w-[calc(100vw-2rem)]">
                                {discovery?.items.map(item => <SelectItem key={item.id} value={item.id} disabled={!!item.owner || item.ownership_verified === false} className="break-all">{item.project} · {item.engine} · {item.working_dir || "—"}{item.owner ? ` · ${t("sshExecutor.projectOwned", {name: item.owner.name})}` : item.ownership_verified === false ? ` · ${t("sshExecutor.identityUnavailable")}` : ""}</SelectItem>)}
                                <SelectItem value="manual">{t("sshExecutor.manualProject")}</SelectItem>
                            </SelectContent>
                        </Select>
                        <Button type="button" variant="outline" className="shrink-0" disabled={discovering || !connection.enabled} onClick={() => {setDiscovering(true); setDiscoveryError(""); setDiscoveryRevision(n => n + 1)}}>
                            <RefreshCwIcon aria-hidden="true" className={discovering ? "animate-spin" : ""} />
                            {t("sshExecutor.refreshProjects")}
                        </Button>
                    </div>
                    {discovering && <div role="status" className="flex items-center gap-2 text-xs text-muted-foreground"><Spinner role="presentation" aria-hidden="true" className="size-3.5" />{t("sshExecutor.discovering")}</div>}
                    {discoveryError && <div role="alert" className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive"><AlertTriangleIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0" /><span className="break-words">{t(discoveryError, {defaultValue: discoveryError})}</span></div>}
                    {discovery && !discovery.items.length && <p className="text-xs text-muted-foreground">{t("sshExecutor.noProjects")}</p>}
                    {discovery?.truncated && <div className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-xs text-warning">{t("sshExecutor.discoveryTruncated")}</div>}
                    {discovery?.warnings.map(w => <div key={w} className="break-words rounded-lg border border-warning/30 bg-warning/5 p-3 text-xs text-warning">{w}</div>)}
                    {selected?.warnings.map(w => <div key={w} className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-xs text-warning">{t(`sshExecutor.${w}`)}</div>)}
                    {discovery && !discovering && !discoveryError && !discoveredConfigurationMatches && <div role="alert" className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive"><AlertTriangleIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />{t("sshExecutor.discoveredConfigurationChanged")}</div>}
                </div>

                {showReadOnlyConfiguration && <div className="rounded-xl border border-border/60 bg-muted/20 p-4 space-y-4">
                    <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between border-b border-border/40 pb-3">
                        <div className="flex items-center gap-2 min-w-0">
                            <FolderIcon aria-hidden="true" className="size-4 shrink-0 text-primary" />
                            <span aria-label={t("sshExecutor.project")} className="font-semibold text-sm truncate">{fields.project || t("sshExecutor.notConfigured")}</span>
                            <span className="font-mono text-xs text-muted-foreground break-all">({fields.working_dir || "—"})</span>
                        </div>
                        <div className="flex flex-wrap items-center gap-1.5 shrink-0">
                            <Badge variant="secondary" className="text-[11px]">{engine}</Badge>
                            {fields.tool && <Badge variant="outline" aria-label={t("sshExecutor.tool")} className="text-[11px]"><WrenchIcon aria-hidden="true" className="size-3 mr-1" />{fields.tool.replace("_", " ")}</Badge>}
                            <Badge variant="outline" className="text-[11px]"><LockKeyholeIcon aria-hidden="true" className="size-3 mr-1" />{t("sshExecutor.readOnly")}</Badge>
                        </div>
                    </div>

                    <div className="grid gap-3 sm:grid-cols-2 text-xs">
                        <div className="space-y-1 sm:col-span-2">
                            <span className="text-muted-foreground font-medium">{t("sshExecutor.working_dir")}</span>
                            <div aria-label={t("sshExecutor.working_dir")} className="font-mono text-foreground break-all rounded-md bg-background border border-border/60 px-2.5 py-1.5">{fields.working_dir || "—"}</div>
                        </div>
                        <div className="space-y-1 sm:col-span-2">
                            <span className="text-muted-foreground font-medium">{t("sshExecutor.config_files")}</span>
                            <div aria-label={t("sshExecutor.config_files")} className="space-y-1">
                                {lines(fields.config_files).length ? lines(fields.config_files).map(file => (
                                    <code key={file} className="block font-mono text-foreground break-all rounded-md bg-background border border-border/60 px-2.5 py-1.5">{file}</code>
                                )) : <span className="text-muted-foreground">{t("sshExecutor.notConfigured")}</span>}
                            </div>
                        </div>
                        <div className="space-y-1">
                            <span className="text-muted-foreground font-medium">{t("sshExecutor.env_files")}</span>
                            <div aria-label={t("sshExecutor.env_files")} className="font-mono text-foreground break-all rounded-md bg-background border border-border/60 px-2.5 py-1.5">{lines(fields.env_files).length ? lines(fields.env_files).join(", ") : t("sshExecutor.notConfigured")}</div>
                        </div>
                        <div className="space-y-1">
                            <span className="text-muted-foreground font-medium">{t("sshExecutor.profiles")}</span>
                            <div aria-label={t("sshExecutor.profiles")} className="font-mono text-foreground break-all rounded-md bg-background border border-border/60 px-2.5 py-1.5">{lines(fields.profiles).length ? lines(fields.profiles).join(", ") : t("sshExecutor.notConfigured")}</div>
                        </div>
                    </div>

                    {discoveredConfiguration && selected && !selected.tool && executorId === null && (
                        <div className="space-y-1.5 pt-3 border-t border-border/40">
                            <Label htmlFor={`${id}-tool`}>{t("sshExecutor.tool")}</Label>
                            <Select value={fields.tool} onValueChange={tool => change("tool", tool)}>
                                <SelectTrigger id={`${id}-tool`}><SelectValue placeholder={t("sshExecutor.chooseTool")} /></SelectTrigger>
                                <SelectContent>{tools.map(tool => <SelectItem key={tool} value={tool}>{tool.replace("_", " ")}</SelectItem>)}</SelectContent>
                            </Select>
                        </div>
                    )}
                </div>}

                {showManualConfiguration && <div className="rounded-xl border border-border/60 bg-muted/20 p-4 space-y-4">
                    <div className="space-y-1 border-b border-border/40 pb-3">
                        <h4 className="text-sm font-semibold">{t("sshExecutor.manualProject")}</h4>
                        <p className="text-xs text-muted-foreground">{t("sshExecutor.manualProjectDescription")}</p>
                    </div>
                    <div className="grid min-w-0 gap-4 sm:grid-cols-2">
                        <div className="min-w-0 space-y-1.5">
                            <Label htmlFor={`${id}-project`}>{t("sshExecutor.project")}</Label>
                            <Input id={`${id}-project`} value={fields.project} onChange={e => change("project", e.target.value)} />
                        </div>
                        <div className="min-w-0 space-y-1.5">
                            <Label htmlFor={`${id}-tool`}>{t("sshExecutor.tool")}</Label>
                            <Select value={fields.tool} onValueChange={tool => change("tool", tool)}>
                                <SelectTrigger id={`${id}-tool`}><SelectValue placeholder={t("sshExecutor.chooseTool")} /></SelectTrigger>
                                <SelectContent>{tools.map(tool => <SelectItem key={tool} value={tool}>{tool.replace("_", " ")}</SelectItem>)}</SelectContent>
                            </Select>
                        </div>
                        <div className="min-w-0 space-y-1.5 sm:col-span-2">
                            <Label htmlFor={`${id}-working_dir`}>{t("sshExecutor.working_dir")}</Label>
                            <Input id={`${id}-working_dir`} value={fields.working_dir} onChange={e => change("working_dir", e.target.value)} />
                        </div>
                        {(["config_files", "env_files", "profiles"] as const).map(key => <div className={`min-w-0 space-y-1.5 ${key === "profiles" ? "sm:col-span-2" : ""}`} key={key}>
                            <Label htmlFor={`${id}-${key}`}>{t(`sshExecutor.${key}`)}</Label>
                            <Textarea id={`${id}-${key}`} value={fields[key]} onChange={e => change(key, e.target.value)} rows={key === "config_files" ? 3 : 2} />
                        </div>)}
                    </div>
                </div>}

                {(busy || (ready && !result && !error) || !ready) && (
                    <div role="status" className="flex items-center gap-2 rounded-lg border border-border/60 bg-background px-3 py-2 text-xs text-muted-foreground">
                        {busy && <Spinner role="presentation" aria-hidden="true" className="size-3.5" />}
                        {busy ? t("sshExecutor.analyzing") : ready && !result && !error ? t("sshExecutor.analysisPending") : t("sshExecutor.analysisIncomplete")}
                    </div>
                )}

                {error && (
                    <div className="space-y-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3">
                        <div role="alert" className="flex items-start gap-2 text-xs text-destructive">
                            <AlertTriangleIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0" /><span className="break-words">{t(error, {defaultValue: error})}</span>
                        </div>
                        <Button type="button" size="sm" variant="outline" onClick={() => setRetry(n => n + 1)}>{t("sshExecutor.retryAnalysis")}</Button>
                    </div>
                )}

                {result && result.services.length > 0 && (
                    <div className="space-y-2 rounded-xl border border-border/60 bg-muted/20 p-4" aria-live="polite">
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
                                <WrenchIcon aria-hidden="true" className="size-3.5 text-muted-foreground" />
                                <span>{t("sshExecutor.detectedServices")}</span>
                            </div>
                            <Badge variant="secondary" className="text-[10px]">{t("sshExecutor.serviceCount", {count: result.services.length})}</Badge>
                        </div>
                        <div className="rounded-lg border border-border/60 bg-background overflow-hidden">
                            <Table className="text-xs">
                                <TableHeader>
                                    <TableRow className="hover:bg-transparent bg-muted/40">
                                        <TableHead className="h-8 text-[11px] font-medium">{t("sshExecutor.service")}</TableHead>
                                        <TableHead className="h-8 text-[11px] font-medium">{t("sshExecutor.updateTarget")}</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {result.services.map(row => (
                                        <TableRow key={row.service} className="hover:bg-muted/30">
                                            <TableCell className="font-medium py-2">
                                                <div>{row.service}</div>
                                                {row.warnings.map(w => <div key={w} className="break-words text-[11px] text-warning font-normal mt-0.5">{t(`ssh.warning_${w}`, {defaultValue: w})}</div>)}
                                            </TableCell>
                                            <TableCell className="py-2 text-muted-foreground">
                                                <div className="flex items-center gap-1.5 flex-wrap">
                                                    <Badge variant="outline" className="text-[10px] font-normal">{t(`ssh.source_${row.source}`, {defaultValue: row.source})}</Badge>
                                                    <span className="font-mono text-[11px]">{row.write_file ?? row.variable ?? "—"}</span>
                                                </div>
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </div>
                    </div>
                )}
            </CardContent>
        </Card>

        <Card className="border-border/60 bg-card/80 shadow-sm" aria-label={t("sshExecutor.strategy")}>
            <CardHeader className="gap-1 px-4 sm:px-6">
                <CardTitle className="text-base">{t("sshExecutor.strategy")}</CardTitle>
                <CardDescription>{t("sshExecutor.strategyDescription")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3 px-4 sm:px-6">
                <div className="space-y-1.5">
                    <Label htmlFor={`${id}-strategy`}>{t("sshExecutor.strategy")}</Label>
                    <Select value={fields.write_strategy} onValueChange={strategy => change("write_strategy", strategy)}>
                        <SelectTrigger id={`${id}-strategy`}><SelectValue /></SelectTrigger>
                        <SelectContent><SelectItem value="source">{t("sshExecutor.source")}</SelectItem><SelectItem value="override">{t("sshExecutor.override")}</SelectItem></SelectContent>
                    </Select>
                </div>
                {fields.write_strategy === "override" && <div className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-xs text-warning flex items-start gap-2"><AlertTriangleIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0" /><span>{t("sshExecutor.overrideWarning")}</span></div>}
                <p className="text-xs leading-relaxed text-muted-foreground">{t("sshExecutor.safety")}</p>
            </CardContent>
        </Card>
    </div>
}
