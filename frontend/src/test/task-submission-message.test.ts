import { describe, expect, it } from "vitest"
import i18n from "@/i18n/config"

describe("concise task submission feedback", () => {
    it.each([
        ["tasks.kind.fetch", "版本拉取"],
        ["tasks.kind.deploy", "部署更新"],
        ["executors.snapshots.actions.rollback", "回滚"],
        ["sshExecutor.restoreFiles", "恢复配置文件"],
        ["sshExecutor.verifyUnlock", "核验并解锁"],
    ])("identifies the target and operation: %s", (key) => {
        const t = i18n.getFixedT("zh")
        const operation = t(key)
        expect(operation).not.toBe(key)
        expect(t("tasks.submitted", {name: "affine", operation})).toBe(`「affine」的${operation}任务已入队`)
    })
    it("matches the requested wording exactly", () => {
        const t = i18n.getFixedT("zh")
        expect(t("tasks.submitted", {name: "affine", operation: t("tasks.kind.fetch")})).toBe("「affine」的版本拉取任务已入队")
    })
    it("keeps English feedback equally concise", () => {
        const t = i18n.getFixedT("en")
        expect(t("tasks.submitted", {name: "affine", operation: t("tasks.kind.fetch")})).toBe("Version fetch task for “affine” queued.")
    })
})
