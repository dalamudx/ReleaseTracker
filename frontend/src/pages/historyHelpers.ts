import type { ReleaseHistoryItem } from "@/api/types"

export function buildReleaseIdentityPrefix(release: Pick<ReleaseHistoryItem, "digest" | "commit_sha">): string | null {
    const value = release.digest || release.commit_sha
    if (!value) {
        return null
    }
    const normalizedValue = value.startsWith("sha256:") ? value.slice("sha256:".length) : value
    return normalizedValue.slice(0, 12)
}
