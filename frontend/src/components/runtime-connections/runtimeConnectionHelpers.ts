import type {
    CreateRuntimeConnectionRequest,
    RuntimeConnection,
    RuntimeConnectionType,
    UpdateRuntimeConnectionRequest,
} from "@/api/types"

export type ConnectionSummary = {
    primary: string
    secondary?: string
}

export type RuntimeConnectionFormValues = {
    name: string
    type: RuntimeConnectionType
    enabled: boolean
    description: string
    credential_id: string
    socket: string
    tls_verify: boolean
    api_version: string
    context: string
    namespaces: string[]
    in_cluster: boolean
    base_url: string
    endpoint_id: string
}

export function buildConnectionSummary(runtimeConnection: RuntimeConnection): ConnectionSummary {
    const config = runtimeConnection.config ?? {}

    if (runtimeConnection.type === 'kubernetes') {
        const label = buildConnectionLabel(runtimeConnection)
        return { primary: label }
    }

    if (runtimeConnection.type === 'portainer') {
        const baseUrl = stringifyValue(config.base_url)
        const endpointId = stringifyNumericValue(config.endpoint_id)

        if (!baseUrl && !endpointId) {
            return { primary: '—' }
        }

        return {
            primary: baseUrl || '—',
            secondary: endpointId ? `Endpoint ${endpointId}` : undefined,
        }
    }

    const socket = stringifyValue(config.socket) || stringifyValue(config.host)
    const apiVersion = stringifyValue(config.api_version)

    return {
        primary: socket || '—',
        secondary: apiVersion || undefined,
    }
}

export function buildConnectionLabel(runtimeConnection: RuntimeConnection): string {
    const config = runtimeConnection.config ?? {}

    if (runtimeConnection.type === 'kubernetes') {
        const endpoint = stringifyValue(runtimeConnection.endpoint)
        const context = stringifyValue(config.context)
        const inCluster = config.in_cluster === true

        if (endpoint) {
            return endpoint
        }

        if (inCluster) {
            return 'in-cluster'
        }

        return context || '—'
    }

    if (runtimeConnection.type === 'portainer') {
        const baseUrl = stringifyValue(config.base_url)
        const endpointId = stringifyNumericValue(config.endpoint_id)
        return [baseUrl, endpointId ? `endpoint ${endpointId}` : ''].filter(Boolean).join(' / ') || '—'
    }

    const socket = stringifyValue(config.socket) || stringifyValue(config.host)
    const apiVersion = stringifyValue(config.api_version)

    return [socket, apiVersion].filter(Boolean).join(' / ') || '—'
}

export function buildPayload(values: RuntimeConnectionFormValues): CreateRuntimeConnectionRequest {
    const config: Record<string, unknown> = {}
    if (values.type === 'kubernetes') {
        assignIfFilled(config, 'context', values.context)
        if (values.namespaces.length > 0) {
            config.namespaces = values.namespaces
        }
        if (values.in_cluster) {
            config.in_cluster = true
        }
    } else if (values.type === 'portainer') {
        assignIfFilled(config, 'base_url', values.base_url)
        assignIfPositiveInteger(config, 'endpoint_id', values.endpoint_id)
    } else {
        assignIfFilled(config, 'socket', values.socket)
        assignIfFilled(config, 'api_version', values.api_version)
        if (values.tls_verify) {
            config.tls_verify = true
        }
    }

    return {
        name: values.name.trim(),
        type: values.type,
        enabled: values.enabled,
        description: values.description.trim() || null,
        config,
        credential_id: parseOptionalPositiveInteger(values.credential_id),
        secrets: {},
    }
}

export function buildUpdatePayload(payload: CreateRuntimeConnectionRequest): UpdateRuntimeConnectionRequest {
    const updatePayload: UpdateRuntimeConnectionRequest = {
        name: payload.name,
        type: payload.type,
        enabled: payload.enabled,
        description: payload.description,
        config: payload.config,
    }

    if (payload.credential_id !== undefined) {
        updatePayload.credential_id = payload.credential_id
    }
    if (Object.keys(payload.secrets).length > 0) {
        updatePayload.secrets = payload.secrets
    }

    return updatePayload
}

function stringifyValue(value: unknown): string {
    return typeof value === 'string' && value.trim() ? value : ''
}

function stringifyNumericValue(value: unknown): string {
    return typeof value === 'number' && Number.isFinite(value) ? String(value) : ''
}

function parseOptionalPositiveInteger(value: string | undefined): number | null {
    const normalized = (value || '').trim()
    if (!normalized) {
        return null
    }

    if (!/^\d+$/.test(normalized)) {
        return null
    }

    const numericValue = Number(normalized)
    return Number.isSafeInteger(numericValue) && numericValue > 0 ? numericValue : null
}

function assignIfFilled(target: Record<string, unknown>, key: string, value: string) {
    if (value.trim()) {
        target[key] = value.trim()
    }
}

function assignIfPositiveInteger(target: Record<string, unknown>, key: string, value: string) {
    const normalized = value.trim()
    if (!normalized || !/^\d+$/.test(normalized)) {
        return
    }

    const numericValue = Number(normalized)
    if (Number.isSafeInteger(numericValue) && numericValue > 0) {
        target[key] = numericValue
    }
}
