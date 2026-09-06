import { defineConfig, devices } from "@playwright/test"

const isCI = Boolean(process.env.CI)

export default defineConfig({
    testDir: "./e2e",
    fullyParallel: true,
    forbidOnly: isCI,
    retries: isCI ? 2 : 0,
    timeout: 30_000,
    expect: {
        timeout: 10_000,
    },
    reporter: isCI
        ? [["github"], ["html", { open: "never" }]]
        : "list",
    use: {
        baseURL: "http://127.0.0.1:4173",
        trace: "on-first-retry",
        screenshot: "only-on-failure",
    },
    projects: [
        {
            name: "chromium",
            use: { ...devices["Desktop Chrome"] },
        },
    ],
    webServer: {
        command: "npm run dev -- --host 127.0.0.1 --port 4173 --strictPort",
        url: "http://127.0.0.1:4173",
        reuseExistingServer: !isCI,
        timeout: 120_000,
    },
})
