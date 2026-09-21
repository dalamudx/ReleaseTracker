import {act, fireEvent, render, screen, waitFor} from '@testing-library/react'
import {beforeEach, expect, it, vi} from 'vitest'
import {api} from '@/api/client'
import {SSHComposeTargetFields} from '@/components/executors/SSHComposeTargetFields'
import {SSHRecoveryActions} from '@/components/executors/SSHRecoveryActions'
import type {RuntimeConnection} from '@/api/types'

vi.mock('@/api/client', () => ({api: {discoverSSHCompose: vi.fn(), analyzeSSHCompose: vi.fn(), recoverSSHCompose: vi.fn()}}))
vi.mock('react-i18next', () => ({useTranslation: () => ({t: (key: string, options?: { name?: string; operation?: string }) => key === "tasks.submitted" ? `${key}:${options?.name}:${options?.operation}` : key})}))
const connection: RuntimeConnection = {id: 1, name: 'SSH', type: 'ssh', enabled: true, config: {}, credential_id: 4, secrets: {}}
const analysis = {tools: [], selected_tool: 'docker_compose', requires_tool_selection: false, services: [{service: 'web', image: 'app:1', expression: 'app:1', source: 'compose', variable: null, write_file: '/app/compose.yml', safe_to_edit: true, warnings: []}]}
const project = {id: '0123456789abcdefabcd', engine: 'docker', project: 'app', working_dir: '/app', config_files: ['compose.yml', 'production.yml'], env_files: ['prod.env'], profiles: [], tool: 'docker_compose', tool_choices: ['docker_compose'], write_strategy: 'source' as const, services: ['web'], warnings: []}
const discovery = {items: [project], tools: [], warnings: [], truncated: false, read_only: true}
beforeEach(() => {
    vi.clearAllMocks()
    Element.prototype.scrollIntoView = vi.fn()
    vi.mocked(api.discoverSSHCompose).mockResolvedValue(discovery)
    vi.mocked(api.analyzeSSHCompose).mockResolvedValue(analysis)
})
async function choose(label: string, option: string) {
    const trigger = screen.getByRole('combobox', {name: label})
    fireEvent.keyDown(trigger, {key: 'ArrowDown'})
    fireEvent.click(await screen.findByRole('option', {name: option}))
}
async function selectProject(option = 'app · docker · /app') {
    await waitFor(() => expect(screen.getByRole('button', {name: 'sshExecutor.refreshProjects'})).toBeEnabled())
    await choose('sshExecutor.discoveredProjects', option)
}

it('disables occupied and unverifiable projects without silently selecting them', async () => {
    vi.mocked(api.discoverSSHCompose).mockResolvedValue({...discovery, items: [{...project, owner: {executor_id: 8, name: 'Owner'}, ownership_verified: true}, {...project, id: 'unknown', project: 'unknown', ownership_verified: false}]})
    render(<SSHComposeTargetFields connection={connection} value={{}} onChange={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('button', {name: 'sshExecutor.refreshProjects'})).toBeEnabled())
    fireEvent.keyDown(screen.getByRole('combobox', {name: 'sshExecutor.discoveredProjects'}), {key: 'ArrowDown'})
    expect(await screen.findByRole('option', {name: /app · docker/})).toHaveAttribute('aria-disabled', 'true')
    expect(screen.getByRole('option', {name: /unknown · docker/})).toHaveAttribute('aria-disabled', 'true')
    expect(api.analyzeSSHCompose).not.toHaveBeenCalled()
})

it('locks project identity but permits service configuration on an existing executor', async () => {
    render(<SSHComposeTargetFields executorId={8} connection={connection} value={project} onChange={vi.fn()} />)
    expect(screen.getByRole('combobox', {name: 'sshExecutor.discoveredProjects'})).toBeDisabled()
    expect(screen.getByLabelText('sshExecutor.project')).toHaveTextContent('app')
    expect(screen.getByLabelText('sshExecutor.working_dir')).toHaveTextContent('/app')
    expect(screen.getByLabelText('sshExecutor.config_files')).toHaveTextContent('compose.yml')
    expect(screen.getByLabelText('sshExecutor.tool')).toHaveTextContent('docker compose')
    expect(screen.queryByRole('textbox', {name: 'sshExecutor.working_dir'})).not.toBeInTheDocument()
    expect(screen.getByText('sshExecutor.projectFixed')).toBeVisible()
    await screen.findByRole('cell', {name: 'web'}, {timeout: 2000})
})

it('automatically discovers projects then analyzes selection, preserving file order', async () => {
    const onChange = vi.fn()
    render(<SSHComposeTargetFields connection={connection} value={{}} onChange={onChange} />)
    expect(api.discoverSSHCompose).toHaveBeenCalledWith(1, expect.any(AbortSignal))
    expect(api.analyzeSSHCompose).not.toHaveBeenCalled()
    await selectProject()
    await screen.findByRole('cell', {name: 'web'}, {timeout: 2000})
    expect(api.analyzeSSHCompose).toHaveBeenCalledTimes(1)
    expect(api.analyzeSSHCompose).toHaveBeenCalledWith({runtime_connection_id: 1, target: expect.objectContaining({discovery_id: '0123456789abcdefabcd', config_files: ['compose.yml', 'production.yml'], env_files: ['prod.env']})}, expect.any(AbortSignal))
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({discovery_id: '0123456789abcdefabcd', services: [{service: 'web', image: 'app:1'}]}))
    expect(screen.queryByRole('button', {name: 'sshExecutor.analyze'})).not.toBeInTheDocument()
    expect(screen.getByLabelText('sshExecutor.env_files')).toHaveTextContent('prod.env')
    expect(screen.queryByRole('textbox', {name: 'sshExecutor.env_files'})).not.toBeInTheDocument()
    expect(api.analyzeSSHCompose).toHaveBeenCalledTimes(1)
})

it('blocks analysis when refreshed discovery no longer matches the bound configuration', async () => {
    render(<SSHComposeTargetFields connection={connection} value={{}} onChange={vi.fn()} />)
    await selectProject()
    await screen.findByRole('cell', {name: 'web'}, {timeout: 2000})
    expect(api.analyzeSSHCompose).toHaveBeenCalledTimes(1)
    vi.mocked(api.discoverSSHCompose).mockResolvedValueOnce({
        ...discovery,
        items: [{...project, working_dir: '/moved'}],
    })
    fireEvent.click(screen.getByRole('button', {name: 'sshExecutor.refreshProjects'}))
    await screen.findByText('sshExecutor.discoveredConfigurationChanged')
    expect(screen.getByLabelText('sshExecutor.working_dir')).toHaveTextContent('/app')
    expect(screen.queryByRole('textbox', {name: 'sshExecutor.working_dir'})).not.toBeInTheDocument()
    await new Promise(resolve => setTimeout(resolve, 700))
    expect(api.analyzeSSHCompose).toHaveBeenCalledTimes(1)
})

it('keeps an existing executor usable after Compose emits absolute paths', async () => {
    const onChange = vi.fn()
    vi.mocked(api.discoverSSHCompose).mockResolvedValue({...discovery, items: [{...project, config_files: ['/app/compose.yml', '/app/production.yml'], env_files: ['/app/prod.env']}]})
    render(<SSHComposeTargetFields executorId={8} connection={connection} value={{...project, discovery_id: project.id}} onChange={onChange} />)
    expect(screen.queryByText('sshExecutor.discoveredConfigurationChanged')).not.toBeInTheDocument()
    await screen.findByRole('cell', {name: 'web'}, {timeout: 2000})
    expect(screen.queryByText('sshExecutor.discoveredConfigurationChanged')).not.toBeInTheDocument()
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({config_files: project.config_files, env_files: project.env_files}))
    expect(screen.getByRole('combobox', {name: 'sshExecutor.discoveredProjects'})).toBeDisabled()
})

it('does not report configuration drift while discovery is pending or has failed', async () => {
    let reject!: (reason: Error) => void
    vi.mocked(api.discoverSSHCompose).mockReturnValue(new Promise((_, fail) => {reject = fail}))
    render(<SSHComposeTargetFields executorId={8} connection={connection} value={{...project, discovery_id: project.id}} onChange={vi.fn()} />)
    expect(screen.queryByText('sshExecutor.discoveredConfigurationChanged')).not.toBeInTheDocument()
    expect(api.analyzeSSHCompose).not.toHaveBeenCalled()
    await act(async () => reject(new Error('unavailable')))
    expect(screen.queryByText('sshExecutor.discoveredConfigurationChanged')).not.toBeInTheDocument()
    expect(api.analyzeSSHCompose).not.toHaveBeenCalled()
})

it.each([
    {config_files: ['/app/production.yml', '/app/compose.yml']},
    {config_files: ['/other/compose.yml', '/app/production.yml']},
    {env_files: ['/app/other.env']},
])('blocks real path or loading order changes: %j', async changes => {
    vi.mocked(api.discoverSSHCompose).mockResolvedValue({...discovery, items: [{...project, ...changes}]})
    render(<SSHComposeTargetFields executorId={8} connection={connection} value={{...project, discovery_id: project.id}} onChange={vi.fn()} />)
    await screen.findByText('sshExecutor.discoveredConfigurationChanged')
    expect(api.analyzeSSHCompose).not.toHaveBeenCalled()
})

it('debounces rapid changes and does not overwrite edited inputs with stale responses', async () => {
    let resolve!: (data: typeof analysis) => void
    vi.mocked(api.analyzeSSHCompose).mockReturnValueOnce(new Promise(r => {resolve = r}))
    const onChange = vi.fn()
    render(<SSHComposeTargetFields connection={connection} value={{}} onChange={onChange} />)
    await selectProject()
    await waitFor(() => expect(api.analyzeSSHCompose).toHaveBeenCalledTimes(1), {timeout: 2000})
    await choose('sshExecutor.discoveredProjects', 'sshExecutor.manualProject')
    fireEvent.change(screen.getByLabelText('sshExecutor.env_files'), {target: {value: 'a.env'}})
    fireEvent.change(screen.getByLabelText('sshExecutor.env_files'), {target: {value: 'b.env'}})
    await act(async () => resolve(analysis))
    expect(onChange.mock.lastCall?.[0].services).toBeUndefined()
    expect(screen.queryByRole('cell', {name: 'web'})).not.toBeInTheDocument()
    await waitFor(() => expect(api.analyzeSSHCompose).toHaveBeenCalledTimes(2), {timeout: 2000})
    expect(api.analyzeSSHCompose).toHaveBeenLastCalledWith(expect.objectContaining({target: expect.objectContaining({env_files: ['b.env']})}), expect.any(AbortSignal))
})

it('does not choose among multiple Compose tools or guess missing paths', async () => {
    vi.mocked(api.discoverSSHCompose).mockResolvedValue({...discovery, items: [{...project, tool: null, warnings: ['choose_compose_tool']}]})
    render(<SSHComposeTargetFields connection={connection} value={{}} onChange={vi.fn()} />)
    await selectProject()
    expect(api.analyzeSSHCompose).not.toHaveBeenCalled()
    await choose('sshExecutor.tool', 'docker compose')
    await screen.findByRole('cell', {name: 'web'}, {timeout: 2000})
})

it('supports manual fallback and explicit retry without saving or deploying', async () => {
    vi.mocked(api.discoverSSHCompose).mockResolvedValue({...discovery, items: []})
    vi.mocked(api.analyzeSSHCompose).mockRejectedValueOnce(new Error('network'))
    render(<SSHComposeTargetFields connection={connection} value={{}} onChange={vi.fn()} />)
    await screen.findByText('sshExecutor.noProjects')
    await choose('sshExecutor.discoveredProjects', 'sshExecutor.manualProject')
    fireEvent.change(screen.getByLabelText('sshExecutor.working_dir'), {target: {value: '/app'}})
    fireEvent.change(screen.getByLabelText('sshExecutor.project'), {target: {value: 'app'}})
    fireEvent.change(screen.getByLabelText('sshExecutor.config_files'), {target: {value: 'compose.yml'}})
    await choose('sshExecutor.tool', 'docker compose')
    await screen.findByRole('alert', {}, {timeout: 2000})
    expect(api.analyzeSSHCompose).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', {name: 'sshExecutor.retryAnalysis'}))
    await screen.findByRole('cell', {name: 'web'}, {timeout: 2000})
})

it('aborts discovery on unmount and never starts an analysis', async () => {
    const {unmount} = render(<SSHComposeTargetFields connection={connection} value={{}} onChange={vi.fn()} />)
    const signal = vi.mocked(api.discoverSSHCompose).mock.calls[0][1]!
    unmount()
    expect(signal.aborted).toBe(true)
    expect(api.analyzeSSHCompose).not.toHaveBeenCalled()
})

it('retains the sole podman-compose tool while project paths need completion', async () => {
    vi.mocked(api.discoverSSHCompose).mockResolvedValue({...discovery, tools: [{tool: 'podman_compose', engine: 'podman', available: true, reason: null, alias_of: 'podman-compose'}, {tool: 'podman-compose', engine: 'podman', available: true, reason: null}], items: [{...project, engine: 'podman', working_dir: '', tool: 'podman-compose', tool_choices: ['podman-compose'], warnings: ['incomplete_project_metadata']}]})
    vi.mocked(api.analyzeSSHCompose).mockResolvedValue({...analysis, selected_tool: 'podman-compose'})
    render(<SSHComposeTargetFields connection={connection} value={{}} onChange={vi.fn()} />)
    await selectProject('app · podman · —')
    expect(screen.getByLabelText('sshExecutor.tool')).toHaveTextContent('podman-compose')
    expect(screen.queryByText('sshExecutor.choose_compose_tool')).not.toBeInTheDocument()
    expect(api.analyzeSSHCompose).not.toHaveBeenCalled()
    expect(screen.getByLabelText('sshExecutor.working_dir')).toHaveTextContent('—')
    await choose('sshExecutor.discoveredProjects', 'sshExecutor.manualProject')
    fireEvent.change(screen.getByLabelText('sshExecutor.working_dir'), {target: {value: '/app'}})
    await screen.findByRole('cell', {name: 'web'}, {timeout: 2000})
})

it('uses shadcn cards and selects without native form controls', async () => {
    const submit = vi.fn((event: React.FormEvent) => event.preventDefault())
    const {container} = render(<form onSubmit={submit}><SSHComposeTargetFields connection={connection} value={{}} onChange={vi.fn()} /></form>)
    expect(container.querySelectorAll('[data-slot="card"]').length).toBeGreaterThanOrEqual(2)
    expect(screen.getAllByRole('combobox')).toHaveLength(2)
    for (const select of screen.getAllByRole('combobox')) expect(select).toHaveAttribute('data-slot', 'select-trigger')
    await selectProject()
    expect(screen.getByLabelText('sshExecutor.config_files')).toHaveTextContent('compose.yml')
    expect(screen.queryByRole('textbox', {name: 'sshExecutor.config_files'})).not.toBeInTheDocument()
    await choose('sshExecutor.strategy', 'sshExecutor.override')
    expect(screen.getByRole('combobox', {name: 'sshExecutor.strategy'})).toHaveTextContent('sshExecutor.override')
    expect(screen.getByText('sshExecutor.overrideWarning')).toBeVisible()
    expect(container.querySelector('details, summary, select:not([aria-hidden="true"])')).toBeNull()
    expect(submit).not.toHaveBeenCalled()
})

it.each(['restoreFiles', 'verifyUnlock'] as const)('names the executor and %s operation in queued recovery feedback', async operation => {
    vi.mocked(api.recoverSSHCompose).mockResolvedValue({task_id: 7, status: 'queued'})
    const onSuccess = vi.fn()
    render(<SSHRecoveryActions executorId={2} executorName="compose-test" snapshotId={3} onSuccess={onSuccess} />)
    fireEvent.click(screen.getByRole('button', {name: `sshExecutor.${operation}`}))
    fireEvent.click(screen.getByRole('button', {name: 'sshExecutor.confirmStopped'}))
    await screen.findByText(`tasks.submitted:compose-test:sshExecutor.${operation}`)
    expect(onSuccess).not.toHaveBeenCalled()
})

it('never sends a restore until the operator confirms stopped remote commands', async () => {
    vi.mocked(api.recoverSSHCompose).mockResolvedValue({status: 'files_restored', lock_retained: true})
    render(<SSHRecoveryActions executorId={2} executorName="compose-test" snapshotId={3} onSuccess={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', {name: 'sshExecutor.restoreFiles'}))
    expect(api.recoverSSHCompose).not.toHaveBeenCalled()
    expect(screen.getByText('sshExecutor.recoveryWarning')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', {name: 'sshExecutor.confirmStopped'}))
    await waitFor(() => expect(api.recoverSSHCompose).toHaveBeenCalledWith(2, 3, 'restore_files'))
    await screen.findByText('sshExecutor.restored')
})
