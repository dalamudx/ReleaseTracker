import type { CredentialType } from "@/api/types"

type Translate = (key: string) => string

export function getCredentialTypeLabel(t: Translate, type: CredentialType): string {
    return t(`credential.types.${type}`)
}
