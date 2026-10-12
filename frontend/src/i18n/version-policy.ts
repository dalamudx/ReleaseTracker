import i18n from "./config"

// This namespace is loaded only by the executor/tasks routes.
i18n.addResourceBundle("en", "versionPolicy", {
    label: "Automatic version limit", all: "Allow major upgrades", minor: "Minor and patch only", patch: "Patch only",
    hint: "Other changes require approval. Unknown versions, prereleases and downgrades also require approval. Helm uses chart versions. Downgrades and unknown versions require approval even for manual deployments or major upgrades.",
    rulesChanged: "Version selection rules changed. Cancel this obsolete task and create a new review; the queued target has not been modified.",
    downgrade: "The target version is lower than the running version. Approving this plan authorizes a downgrade.",
    unknown: "The running and target versions cannot be safely compared. Review the exact artifact before approving.",
    approval: "This automatic update exceeds the version limit, or its current/target version cannot be verified. Review the change before approving.",
})
i18n.addResourceBundle("zh", "versionPolicy", {
    label: "自动升级版本限制", all: "允许跨主版本升级", minor: "仅次版本和补丁", patch: "仅补丁",
    hint: "超出范围时需要审批；未知版本、预发布与降级同样需要审批。Helm 按 Chart 版本判断；允许跨主版本或手动发起，也不能跳过降级与未知版本审批。",
    rulesChanged: "版本选择规则已更新。请取消旧任务并重新发起复核；队列中的旧目标未被修改。",
    downgrade: "目标版本低于实际运行版本。批准此计划即明确授权降级。",
    unknown: "无法安全比较当前与目标版本。请核对具体制品后再批准。",
    approval: "自动升级超出版本限制，或无法确认当前/目标版本。请审核变更后再批准。",
})
