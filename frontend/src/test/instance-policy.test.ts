// @vitest-environment node
import { describe, expect, it } from "vitest"

import {
    formatPolicyList,
    INSTANCE_POLICY_SETTING_KEYS,
    isValidPolicyInteger,
} from "@/lib/instance-policy"

const keys = INSTANCE_POLICY_SETTING_KEYS

describe("instance policy helpers", () => {
    it("accepts only canonical integers inside the shared bounds", () => {
        expect(isValidPolicyInteger(keys.mutationConcurrency, "1")).toBe(true)
        expect(isValidPolicyInteger(keys.mutationConcurrency, "3")).toBe(true)
        expect(isValidPolicyInteger(keys.mutationConcurrency, "4")).toBe(false)
        expect(isValidPolicyInteger(keys.workerPollSeconds, "0")).toBe(false)
        expect(isValidPolicyInteger(keys.workerPollSeconds, "60")).toBe(true)
        expect(isValidPolicyInteger(keys.readinessPullGraceSeconds, "3601")).toBe(false)
        expect(isValidPolicyInteger(keys.mutationConcurrency, "1.5")).toBe(false)
        // Whitespace is stripped, as the settings API does before validating.
        expect(isValidPolicyInteger(keys.mutationConcurrency, " 2 ")).toBe(true)
        expect(isValidPolicyInteger(keys.corsOrigins, "1")).toBe(false)
    })

    it("accepts a disabled or a long runtime health interval", () => {
        expect(isValidPolicyInteger(keys.runtimeHealthIntervalSeconds, "0")).toBe(true)
        expect(isValidPolicyInteger(keys.runtimeHealthIntervalSeconds, "299")).toBe(false)
        expect(isValidPolicyInteger(keys.runtimeHealthIntervalSeconds, "300")).toBe(true)
        expect(isValidPolicyInteger(keys.runtimeHealthIntervalSeconds, "86400")).toBe(true)
        expect(isValidPolicyInteger(keys.runtimeHealthIntervalSeconds, "86401")).toBe(false)
    })

    it("keeps newline-delimited lists trimmed and free of blank lines", () => {
        expect(formatPolicyList(" https://a.example.com \n\nhttps://b.example.com\n")).toBe(
            "https://a.example.com\nhttps://b.example.com",
        )
        expect(formatPolicyList("  \n  ")).toBe("")
    })
})
