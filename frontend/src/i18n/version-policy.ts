import i18n from "./config"

// This namespace is loaded only by the executor/tasks routes.
i18n.addResourceBundle("en", "versionPolicy", {
    label: "Automatic version limit", all: "No version limit", minor: "Minor and patch only", patch: "Patch only",
    hint: "Other changes require approval. Unknown versions, prereleases and downgrades also require approval. Helm uses chart versions; manual deployments are not limited.",
    approval: "This automatic update exceeds the version limit, or its current/target version cannot be verified. Review the change before approving.",
})
i18n.addResourceBundle("zh", "versionPolicy", {
    label: "自动升级版本限制", all: "不限版本", minor: "仅次版本和补丁", patch: "仅补丁",
    hint: "超出范围时需要审批；未知版本、预发布与降级同样需要审批。Helm 按图表版本判断，手动部署不受此限制。",
    approval: "自动升级超出版本限制，或无法确认当前/目标版本。请审核变更后再批准。",
})
