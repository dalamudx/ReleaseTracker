import { expect, test } from "@playwright/test"

for (const language of ["zh", "en"]) for (const width of [1280, 390]) {
    test(`normal deployment configuration review ${language} ${width}`, async ({ page }) => {
        await page.setViewportSize({ width, height: 1000 })
        await page.addInitScript(language => {
            document.cookie = "releasetracker-csrf=fixture-csrf; path=/"
            localStorage.setItem("language", language)
        }, language)
        const errors: string[] = []
        page.on("pageerror", error => errors.push(error.message))
        const task = { id:42, kind:"deploy", state:"awaiting_approval", approval_pending:true, target_label:"isolated-nginx", attempts:0, max_retries:0, due_at:1789705000, created_at:1789704900, updated_at:1789705000, error_code:"deployment_approval_required", result:{}, target:{executor_id:7} }
        let version = 1
        let approved = false
        await page.route(url => url.pathname.startsWith("/api/"), async route => {
            const url = new URL(route.request().url())
            const send = (data: unknown, status=200) => route.fulfill({status,contentType:"application/json",body:JSON.stringify(data)})
            if (url.pathname === "/api/auth/me") return send({id:1,username:"fixture-admin",is_admin:true})
            if (url.pathname === "/api/tasks") return send([task])
            if (url.pathname === "/api/tasks/42") return send(task)
            if (url.pathname === "/api/tasks/42/deployment-plan") {
                if (url.searchParams.get("refresh") === "true") version++
                return send({id:77+version,task_id:42,fingerprint:`review-${version}`,state:"pending",reason:"manual_update_review",expires_at:1789706800,summary:{configuration_diff:{scope:"container_configuration",truncated:false,lines:[
                    {operation:"-",path:"/create_config/image",value:'"nginx:stable"',redacted:false},
                    {operation:"+",path:"/create_config/image",value:`"nginx:stable@sha256:${"a".repeat(64)}"`,redacted:false},
                ]}}})
            }
            if (url.pathname === "/api/tasks/42/approve") {
                expect(route.request().postDataJSON()).toMatchObject({fingerprint:`review-${version}`,plan_reviewed:true})
                approved=true
                return send({task_id:42,status:"queued"},202)
            }
            return send([])
        })
        await page.goto("/tasks")
        await page.getByRole("button",{name:language === "zh" ? "任务 #42 详情" : "Task #42 details"}).click()
        const diff=page.getByTestId("deployment-configuration-diff")
        await expect(diff.locator("li")).toHaveCount(2)
        await expect(diff).toContainText("stable@sha256:")
        await expect(diff.locator("li").first()).toContainText("-")
        await expect(diff.locator("li").nth(1)).toContainText("+")
        expect(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth)).toBe(true)
        await page.getByRole("button",{name:language === "zh" ? "刷新计划" : "Refresh plan",exact:true}).click()
        await expect(page.getByText(/review-2/)).toBeVisible()
        await page.getByRole("button",{name:language === "zh" ? "确认并继续部署" : "Approve and continue",exact:true}).click()
        await expect.poll(()=>approved).toBe(true)
        expect(errors).toEqual([])
        await expect(page.getByRole("dialog")).toHaveCount(0)
    })
}
