import { useEffect, useState } from 'react'
import { useWatch, type UseFormReturn } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/client'
import type { ApiCredential, RuntimeConnection } from '@/api/types'
import { Button } from '@/components/ui/button'
import { FormControl, FormDescription, FormField, FormItem, FormLabel, FormMessage } from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Switch } from '@/components/ui/switch'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { buildPayload, type RuntimeConnectionFormValues } from './runtimeConnectionHelpers'

export function SSHConnectionFields({ form, credentials, connection }: {
    form: UseFormReturn<RuntimeConnectionFormValues>
    credentials: ApiCredential[]
    connection: RuntimeConnection | null
}) {
    const { t } = useTranslation()
    const [proxies, setProxies] = useState<RuntimeConnection[]>([])
    const [busy, setBusy] = useState(false)
    const [message, setMessage] = useState('')
    const [error, setError] = useState('')
    const [candidate, setCandidate] = useState<{host_key: string; fingerprint: string; identity: string} | null>(null)
    const values = useWatch({ control: form.control })
    const identity = JSON.stringify([values.ssh_host, values.ssh_port, values.ssh_username, values.ssh_proxy_id, values.credential_id])
    const allowProxy = values.ssh_allow_proxy === true
    const referenced = (connection?.proxy_dependents?.length ?? 0) > 0

    useEffect(() => {
        let cancelled = false
        async function load() {
            const items: RuntimeConnection[] = []
            for (let skip = 0; ; skip += 100) {
                const page = await api.getRuntimeConnections({skip, limit: 100})
                items.push(...page.items)
                if (!page.items.length || items.length >= page.total) break
            }
            if (!cancelled) setProxies(items.filter(c => c.id !== connection?.id && c.type === 'ssh' && c.enabled && c.config.allow_proxy === true && !c.config.proxy_connection_id))
        }
        void load().catch(() => { if (!cancelled) setError(t('ssh.failed')) })
        return () => { cancelled = true }
    }, [connection?.id, t])

    const changeIdentity = () => {
        form.setValue('ssh_host_key', '', { shouldDirty: true })
        setCandidate(null)
        setMessage(t('ssh.changed'))
    }
    async function probe(discover: boolean) {
        setBusy(true); setError(''); setMessage(''); setCandidate(null)
        try {
            const payload = { ...buildPayload(form.getValues()), id: connection?.id }
            if (discover) {
                const result = await api.discoverSSHHostKey(payload)
                setCandidate({...result, identity})
            } else {
                await api.testSSHConnection(payload)
                setMessage(t('ssh.success'))
            }
        } catch (e) {
            const detail = (e as {response?: {data?: {detail?: unknown}}}).response?.data?.detail
            setError(typeof detail === 'string' ? detail : t('ssh.failed'))
        } finally { setBusy(false) }
    }
    return <fieldset disabled={busy} className="min-w-0 space-y-4 rounded-lg border p-4">
        <legend className="px-1 text-sm font-semibold">SSH</legend>
        {error && <p role="alert" className="break-words text-sm text-destructive">{error}</p>}
        <div className="grid gap-4 sm:grid-cols-2">
            {(['ssh_host', 'ssh_port', 'ssh_username'] as const).map((name) => <FormField key={name} control={form.control} name={name} rules={{required: true}} render={({field}) => <FormItem>
                <FormLabel>{t(`ssh.${name === 'ssh_host' ? 'host' : name === 'ssh_port' ? 'port' : 'username'}`)}</FormLabel>
                <FormControl><Input {...field} value={field.value ?? ''} onChange={e => {field.onChange(e); changeIdentity()}} /></FormControl><FormMessage />
            </FormItem>} />)}
            <FormField control={form.control} name="credential_id" rules={{required: t('ssh.credentialRequired')}} render={({field}) => <FormItem>
                <FormLabel>{t('ssh.credential')}</FormLabel>
                <Select value={field.value} onValueChange={v => {field.onChange(v); changeIdentity()}}><FormControl><SelectTrigger><SelectValue /></SelectTrigger></FormControl>
                    <SelectContent>{credentials.filter(c => c.type === 'ssh').map(c => <SelectItem key={c.id} value={String(c.id)}>{c.name}</SelectItem>)}</SelectContent>
                </Select><FormMessage />
            </FormItem>} />
        </div>
        <FormField control={form.control} name="ssh_allow_proxy" render={({field}) => <FormItem className="flex items-center justify-between gap-3">
            <div><FormLabel>{t('ssh.allowProxy')}</FormLabel><FormDescription>{t('ssh.singleHop')}</FormDescription></div>
            <FormControl><Switch disabled={!!values.ssh_proxy_id || referenced} checked={field.value ?? false} onCheckedChange={field.onChange} /></FormControl>
        </FormItem>} />
        {referenced && <p className="text-sm text-muted-foreground">{t('ssh.dependents')}: {connection?.proxy_dependents?.map(c => c.name).join(', ')}</p>}
        <FormField control={form.control} name="ssh_proxy_id" render={({field}) => <FormItem>
            <FormLabel>{t('ssh.proxy')}</FormLabel>
            <Select disabled={allowProxy} value={field.value || 'none'} onValueChange={v => {field.onChange(v === 'none' ? '' : v); changeIdentity()}}>
                <FormControl><SelectTrigger><SelectValue /></SelectTrigger></FormControl>
                <SelectContent><SelectItem value="none">{t('ssh.direct')}</SelectItem>{proxies.map(c => <SelectItem key={c.id} value={String(c.id)}>{c.name}</SelectItem>)}</SelectContent>
            </Select><FormMessage />
        </FormItem>} />
        <FormField control={form.control} name="ssh_host_key" render={({field}) => <FormItem>
            <FormLabel>{t('ssh.hostKey')}</FormLabel><FormControl><Textarea {...field} value={field.value ?? ''} className="break-all font-mono text-xs" /></FormControl><FormDescription>{t('ssh.unverified')}</FormDescription><FormMessage />
        </FormItem>} />
        <div className="flex flex-wrap gap-2">
            <Button type="button" variant="outline" disabled={busy || !values.credential_id} onClick={() => void probe(true)}>{t('ssh.discover')}</Button>
            <Button type="button" variant="outline" disabled={busy || !values.ssh_host_key} onClick={() => void probe(false)}>{t('ssh.test')}</Button>
        </div>
        {candidate && candidate.identity === identity && <div className="space-y-2 rounded border p-3">
            <p className="text-sm">{t('ssh.unverified')}</p><code className="block break-all text-xs">{candidate.fingerprint}</code>
            <Button type="button" variant="outline" className="h-auto whitespace-normal" onClick={() => {form.setValue('ssh_host_key', candidate.host_key, {shouldDirty: true}); setCandidate(null)}}>{t('ssh.confirm')}</Button>
        </div>}
        <p role="status" className="text-sm text-muted-foreground">{busy ? t('common.loading') : message}</p>
    </fieldset>
}
