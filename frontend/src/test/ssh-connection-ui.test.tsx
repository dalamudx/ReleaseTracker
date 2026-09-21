import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/client'
import type { RuntimeConnection } from '@/api/types'
import { RuntimeConnectionDialog } from '@/components/runtime-connections/RuntimeConnectionDialog'
import { buildPayload } from '@/components/runtime-connections/runtimeConnectionHelpers'

vi.mock('@/api/client', () => ({api: {
    getCredentials: vi.fn(), getRuntimeConnections: vi.fn(), discoverSSHHostKey: vi.fn(), testSSHConnection: vi.fn(),
    updateRuntimeConnection: vi.fn(), analyzeSSHCompose: vi.fn(),
}}))
vi.mock('sonner', () => ({toast: {success: vi.fn(), error: vi.fn()}}))
vi.mock('react-i18next', () => {
    const t = (key: string) => key
    return {useTranslation: () => ({t})}
})
const connection: RuntimeConnection = {id: 1, name: 'ssh-host', type: 'ssh', enabled: true,
    config: {host: 'host.example', port: 22, username: 'deploy', allow_proxy: false, host_key: ''},
    credential_id: 3, secrets: {}}

function show(config = connection) {
    render(<RuntimeConnectionDialog open onOpenChange={vi.fn()} runtimeConnection={config} onSuccess={vi.fn()} />)
}

describe('SSH connection management', () => {
    beforeAll(() => {
        vi.stubGlobal('ResizeObserver', class {observe() {} unobserve() {} disconnect() {}})
        Element.prototype.scrollIntoView = vi.fn()
    })
    beforeEach(() => {
        vi.clearAllMocks()
        vi.mocked(api.getCredentials).mockResolvedValue({items: [{id: 3, name: 'ssh-key', type: 'ssh', token: '', created_at: '', secrets: {private_key: '****'}}], total: 1})
        vi.mocked(api.getRuntimeConnections).mockResolvedValue({items: [], total: 0})
    })
    it('requires explicit host-key confirmation before enabling a test', async () => {
        vi.mocked(api.discoverSSHHostKey).mockResolvedValue({host_key: 'ssh-ed25519 test-public-key', fingerprint: 'SHA256:test', verified: false})
        vi.mocked(api.testSSHConnection).mockResolvedValue({success: true})
        show()
        await waitFor(() => expect(api.getCredentials).toHaveBeenCalled())
        expect(screen.getByRole('button', {name: 'ssh.test'})).toBeDisabled()
        fireEvent.click(screen.getByRole('button', {name: 'ssh.discover'}))
        await screen.findByText('SHA256:test')
        expect(screen.getByRole('button', {name: 'ssh.test'})).toBeDisabled()
        fireEvent.click(screen.getByRole('button', {name: 'ssh.confirm'}))
        expect(screen.getByLabelText('ssh.hostKey')).toHaveValue('ssh-ed25519 test-public-key')
        fireEvent.click(screen.getByRole('button', {name: 'ssh.test'}))
        await waitFor(() => expect(api.testSSHConnection).toHaveBeenCalledWith(expect.objectContaining({id: 1, type: 'ssh', credential_id: 3})))
        await screen.findByText('ssh.success')
        fireEvent.change(screen.getByLabelText('ssh.host'), {target: {value: 'other.example'}})
        expect(screen.getByLabelText('ssh.hostKey')).toHaveValue('')
        expect(screen.getByRole('button', {name: 'ssh.test'})).toBeDisabled()
    })
    it('prevents a proxy from selecting another proxy and lists affected connections', async () => {
        show({...connection, config: {...connection.config, allow_proxy: true}, proxy_dependents: [{id: 2, name: 'dependent-host'}]})
        await waitFor(() => expect(api.getRuntimeConnections).toHaveBeenCalled())
        expect(screen.getByRole('combobox', {name: 'ssh.proxy'})).toBeDisabled()
        expect(screen.getByRole('switch', {name: 'ssh.allowProxy'})).toBeDisabled()
        expect(screen.getByText(/dependent-host/)).toBeInTheDocument()
    })
    it('keeps Compose discovery out of runtime connection management', async () => {
        show({...connection, config: {...connection.config, host_key: 'ssh-ed25519 existing'}})
        await waitFor(() => expect(api.getCredentials).toHaveBeenCalled())
        expect(screen.queryByText('ssh.composeTitle')).not.toBeInTheDocument()
        expect(screen.queryByLabelText('ssh.projectDirectory')).not.toBeInTheDocument()
        expect(api.analyzeSSHCompose).not.toHaveBeenCalled()
    })
    it('serializes only SSH configuration, never runtime credentials or Docker settings', () => {
        const data = buildPayload({name: 'ssh', type: 'ssh', enabled: true, description: '', credential_id: '3',
            ssh_host: 'host', ssh_port: '2222', ssh_username: 'deploy', ssh_proxy_id: '4', ssh_host_key: 'public', ssh_allow_proxy: false,
            socket: 'old', tls_verify: false, api_version: '', context: '', namespaces: [], in_cluster: false, base_url: '', endpoint_id: '', endpoint_name: ''})
        expect(data.config).toEqual({host: 'host', port: 2222, username: 'deploy', host_key: 'public', proxy_connection_id: 4, allow_proxy: false})
        expect(data.credential_id).toBe(3)
        expect(data.secrets).toEqual({})
    })
})
