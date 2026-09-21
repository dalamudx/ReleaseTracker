import { expect, test } from '@playwright/test'

for (const [width, mode, language] of [[1280, 'light', 'en'], [390, 'dark', 'zh']] as const) {
    test(`notification template editor ${width} ${mode}`, async ({ page }) => {
        const errors: string[] = []
        page.on('pageerror', error => errors.push(error.message))
        await page.setViewportSize({ width, height: 900 })
        await page.addInitScript(({ mode, language }) => {
            localStorage.setItem('token', 'example-token')
            localStorage.setItem('language', language)
            localStorage.setItem('vite-ui-theme-config', JSON.stringify({ mode, color: 'blue', radius: 0.5, zoom: 'default' }))
        }, { mode, language })
        const builtin = { id: null, name: 'Example template', title: '{{ labels.events[event] }} · {{ subject.name }}', body: '{% if services %}\n{% for service in services %}\n{{ service.name }}: {{ service.from_display }} → {{ service.to_display }}\n{% endfor %}\n{% endif %}', translations: {}, revision: 1 }
        let previews = 0
        await page.route('**/api/**', async route => {
            const path = new URL(route.request().url()).pathname
            let body: unknown = {}
            if (path === '/api/auth/me') body = { id: 1, username: 'example-admin', is_admin: true }
            else if (path === '/api/tasks' || path === '/api/webhooks/repositories') body = []
            else if (path === '/api/notifiers') body = { items: [], total: 0 }
            else if (path === '/api/notification-templates/preview') { previews++; body = { title: 'Sample executor', body: 'service-a: 1.2.0 → 1.3.0', content: '### Sample executor\n\nservice-a: 1.2.0 → 1.3.0\n\nRuntime readiness only.', locale: language, revision: 1 } }
            else if (path === '/api/notification-templates') body = { builtin, items: [], events: ['new_release', 'executor_run_failed', 'test'] }
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
        })
        await page.goto('/webhooks')
        await page.getByRole('tab', { name: language === 'zh' ? '通知模板' : 'Notification templates' }).click()
        await expect(page.locator('#template-body')).toBeVisible()
        await page.getByRole('button', { name: language === 'zh' ? '生成预览' : 'Render preview' }).click()
        await expect(page.getByText('service-a: 1.2.0 → 1.3.0', { exact: false })).toBeVisible()
        expect(previews).toBe(1)
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
        await page.screenshot({ path: `/tmp/rt-notification-templates-${width}-${mode}.png`, fullPage: true })
        expect(errors).toEqual([])
    })
}
