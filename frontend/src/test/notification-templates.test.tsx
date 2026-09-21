import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { NotificationTemplates } from '@/components/settings/NotificationTemplates'
import { notificationTemplates } from '@/api/notification-templates'

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }))
vi.mock('sonner', () => ({ toast: { success: vi.fn() } }))
const builtin = { id: null, name: 'Example template', title: '{{ subject.name }}', body: '{{ labels.version }}', translations: {}, revision: 1 }
vi.mock('@/api/notification-templates', () => ({ notificationTemplates: { list: vi.fn(), preview: vi.fn(), save: vi.fn(), remove: vi.fn() } }))
function setup() {
    vi.mocked(notificationTemplates.list).mockResolvedValue({ builtin, items: [], events: ['test'] })
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><NotificationTemplates /></QueryClientProvider>)
}
describe('notification template editor', () => {
    it('previews without sending, clears stale preview and saves a copy', async () => {
        setup()
        await screen.findByLabelText('notificationTemplates.bodyTemplate')
        vi.mocked(notificationTemplates.preview).mockResolvedValue({ title: 'Example', body: 'Preview only', content: 'Preview only', locale: 'zh', revision: 1 })
        fireEvent.click(screen.getByRole('button', { name: 'notificationTemplates.render' }))
        expect(await screen.findByText('Preview only')).toBeInTheDocument()
        fireEvent.change(screen.getByLabelText('notificationTemplates.bodyTemplate'), { target: { value: 'Changed {{ subject.name }}' } })
        expect(screen.queryByText('Preview only')).not.toBeInTheDocument()
        vi.mocked(notificationTemplates.save).mockResolvedValue({ ...builtin, id: 1, body: 'Changed {{ subject.name }}' })
        fireEvent.click(screen.getByRole('button', { name: 'notificationTemplates.saveCopy' }))
        await waitFor(() => expect(notificationTemplates.save).toHaveBeenCalledWith(expect.objectContaining({ id: null, body: 'Changed {{ subject.name }}' })))
    })
    it('shows a validation error without losing the draft', async () => {
        setup()
        await screen.findByLabelText('notificationTemplates.bodyTemplate')
        fireEvent.change(screen.getByLabelText('notificationTemplates.bodyTemplate'), { target: { value: '{{ missing }}' } })
        vi.mocked(notificationTemplates.preview).mockRejectedValue({ response: { data: { detail: 'UndefinedError (line ?)' } } })
        fireEvent.click(screen.getByRole('button', { name: 'notificationTemplates.render' }))
        expect(await screen.findByRole('alert')).toHaveTextContent('UndefinedError')
        expect(screen.getByLabelText('notificationTemplates.bodyTemplate')).toHaveValue('{{ missing }}')
    })
})
