import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/client'
import type { RuntimeConnection } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { ChevronDownIcon } from 'lucide-react'
import { Textarea } from '@/components/ui/textarea'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

type Analysis = Awaited<ReturnType<typeof api.analyzeSSHCompose>>
const lines = (value: string) => value.split('\n').map(v => v.trim()).filter(Boolean)

export function SSHComposeAnalysis({connection, disabled}: {connection: Partial<RuntimeConnection>; disabled: boolean}) {
    const { t } = useTranslation()
    const id = useId()
    const [directory, setDirectory] = useState('')
    const [project, setProject] = useState('')
    const [files, setFiles] = useState('compose.yml')
    const [envFiles, setEnvFiles] = useState('')
    const [profiles, setProfiles] = useState('')
    const [tool, setTool] = useState('auto')
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState('')
    const [result, setResult] = useState<{identity: string; data: Analysis} | null>(null)
    const identity = JSON.stringify([connection, directory, project, files, envFiles, profiles, tool])
    const data = result?.identity === identity ? result.data : null

    async function analyze() {
        setBusy(true); setError(''); setResult(null)
        try {
            const data = await api.analyzeSSHCompose({connection, project: {
                working_dir: directory, project, config_files: lines(files), env_files: lines(envFiles), profiles: lines(profiles), tool,
            }})
            setResult({identity, data})
        } catch(e) {
            const detail = (e as {response?: {data?: {detail?: unknown}}}).response?.data?.detail
            setError(typeof detail === 'string' ? detail : t('ssh.failed'))
        } finally { setBusy(false) }
    }
    return <Collapsible className="min-w-0 rounded border p-3">
        <CollapsibleTrigger asChild><Button type="button" variant="ghost" className="h-auto w-full justify-between whitespace-normal text-left [&[data-state=open]>svg]:rotate-180">{t('ssh.composeTitle')}<ChevronDownIcon aria-hidden="true" className="size-4 shrink-0" /></Button></CollapsibleTrigger>
        <CollapsibleContent>
        <fieldset disabled={disabled || busy} className="mt-3 min-w-0 space-y-3">
            <p className="text-sm text-muted-foreground">{t('ssh.composeReadOnly')}</p>
            {error && <p role="alert" className="break-words text-sm text-destructive">{error}</p>}
            <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1"><Label htmlFor={`${id}-dir`}>{t('ssh.projectDirectory')}</Label><Input id={`${id}-dir`} value={directory} onChange={e => setDirectory(e.target.value)} placeholder="/opt/app" /></div>
                <div className="space-y-1"><Label htmlFor={`${id}-project`}>{t('ssh.projectName')}</Label><Input id={`${id}-project`} value={project} onChange={e => setProject(e.target.value)} /></div>
                <div className="space-y-1"><Label htmlFor={`${id}-files`}>{t('ssh.composeFiles')}</Label><Textarea id={`${id}-files`} value={files} onChange={e => setFiles(e.target.value)} /></div>
                <div className="space-y-1"><Label htmlFor={`${id}-env`}>{t('ssh.environmentFiles')}</Label><Textarea id={`${id}-env`} value={envFiles} onChange={e => setEnvFiles(e.target.value)} /></div>
                <div className="space-y-1"><Label htmlFor={`${id}-profiles`}>{t('ssh.profiles')}</Label><Textarea id={`${id}-profiles`} value={profiles} onChange={e => setProfiles(e.target.value)} /></div>
                <div className="space-y-1"><Label htmlFor={`${id}-tool`}>{t('ssh.composeTool')}</Label><Select value={tool} onValueChange={setTool} disabled={disabled || busy}>
                    <SelectTrigger id={`${id}-tool`}><SelectValue /></SelectTrigger><SelectContent>
                        {['auto','docker_compose','docker-compose','podman_compose','podman-compose'].map(v => <SelectItem key={v} value={v}>{v === 'auto' ? t('ssh.detect') : v.replace('_', ' ')}</SelectItem>)}
                    </SelectContent></Select></div>
            </div>
            <p className="text-xs text-muted-foreground">{t('ssh.fileOrder')}</p>
            <Button type="button" variant="outline" disabled={disabled || busy || !directory || !project || !files.trim()} onClick={() => void analyze()}>{busy ? t('common.loading') : t('ssh.analyze')}</Button>
        </fieldset>
        {data && <div role="status" className="mt-3 min-w-0 space-y-3">
            <p className="text-sm">{data.requires_tool_selection ? t('ssh.selectTool') : `${t('ssh.composeTool')}: ${data.selected_tool}`}</p>
            <ul className="space-y-1 text-xs">{data.tools.map(item => <li key={item.tool}>{item.tool.replace('_',' ')}: {t(item.available ? 'ssh.available' : 'ssh.unavailable')}</li>)}</ul>
            {data.services.map(service => <div key={service.service} className="min-w-0 space-y-1 rounded border p-3 text-sm">
                <h4 className="font-medium">{service.service}</h4>
                <p className="break-all font-mono text-xs">{service.image ?? '—'}</p>
                <p className="break-all">{t('ssh.versionSource')}: {t(`ssh.source_${service.source}`, {defaultValue: service.source})} {service.variable ? `(${service.variable})` : ''}</p>
                {service.write_file && <p className="break-all text-xs">{service.write_file}</p>}
                <p>{t(service.safe_to_edit ? 'ssh.sourceIdentified' : 'ssh.needsReview')}</p>
                {service.warnings.map(warning => <p key={warning} className="break-words text-xs text-muted-foreground">{t(`ssh.warning_${warning}`, {defaultValue: warning})}</p>)}
            </div>)}
        </div>}
        </CollapsibleContent>
    </Collapsible>
}
