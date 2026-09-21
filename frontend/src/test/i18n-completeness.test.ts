// @vitest-environment node
import { describe, expect, it } from "vitest"
import en from "@/i18n/locales/en.json"
import zh from "@/i18n/locales/zh.json"

function flatten(value: unknown, prefix = ""): string[] {
    if (!value || typeof value !== "object" || Array.isArray(value)) return [prefix]
    return Object.entries(value).flatMap(([key, child]) =>
        flatten(child, prefix ? `${prefix}.${key}` : key),
    )
}

const requiredDynamicKeys = [
    ...["fetch", "deploy", "recover"].map((value) => `tasks.kind.${value}`),
    ...[
        "queued", "running", "retry_wait", "succeeded", "no_change", "skipped",
        "failed", "cancelled", "superseded", "needs_attention", "interrupted",
    ].map((value) => `tasks.state.${value}`),
    ...["manual", "automatic", "scheduled", "webhook", "bootstrap"].map(
        (value) => `tasks.trigger.${value}`,
    ),
    ...[
        "pending", "healthy", "unhealthy", "timeout", "unknown", "unsupported",
        "superseded", "not_checked",
    ].map((value) => `readiness.outcome.${value}`),
    ...["compose", "env_file", "process_environment", "default_or_unresolved", "unresolved"].map(
        (value) => `ssh.source_${value}`,
    ),
]

function valueAt(resource: Record<string, unknown>, path: string): unknown {
    return path.split(".").reduce<unknown>((value, key) =>
        value && typeof value === "object" ? (value as Record<string, unknown>)[key] : undefined,
    resource)
}

describe("i18n resource completeness", () => {
    it("keeps English and Chinese resource keys in parity", () => {
        expect(flatten(en).sort()).toEqual(flatten(zh).sort())
    })

    it.each(requiredDynamicKeys)("translates dynamic key %s", (key) => {
        expect(valueAt(en, key)).toEqual(expect.any(String))
        expect(valueAt(zh, key)).toEqual(expect.any(String))
        expect(valueAt(en, key)).not.toBe(key)
        expect(valueAt(zh, key)).not.toBe(key)
    })
})
