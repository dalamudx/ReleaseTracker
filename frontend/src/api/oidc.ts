/**
 * OIDC authentication API functions
 * All requests use relative paths and are proxied to the backend by Vite
 */

export interface OIDCProvider {
    slug: string
    name: string
    icon_url: string | null
    description: string | null
}

export interface OIDCProviderConfig extends OIDCProvider {
    id: number
    issuer_url: string | null
    discovery_enabled: boolean
    client_id: string
    authorization_url: string | null
    token_url: string | null
    userinfo_url: string | null
    jwks_uri: string | null
    scopes: string
    enabled: boolean
    created_at: string
    updated_at: string
}

export interface CreateOIDCProviderRequest {
    name: string
    slug: string
    issuer_url?: string | null
    discovery_enabled?: boolean
    client_id: string
    client_secret: string
    authorization_url?: string | null
    token_url?: string | null
    userinfo_url?: string | null
    jwks_uri?: string | null
    scopes?: string
    enabled?: boolean
    icon_url?: string | null
    description?: string | null
}

export interface UpdateOIDCProviderRequest extends Partial<Omit<CreateOIDCProviderRequest, 'slug' | 'client_secret'>> {
    client_secret?: string | null  // Omit this field to leave it unchanged
}

// ========== Public APIs without authentication==========

/** Get enabled OIDC providers for display on the login page */
export async function getOIDCProviders(): Promise<OIDCProvider[]> {
    const res = await fetch('/api/auth/oidc/providers')
    if (!res.ok) return []
    return res.json()
}

/** Start OIDC login and redirect to the IdP via backend /api/auth/oidc/{slug}/authorize */
export function initiateOIDCLogin(providerSlug: string) {
    window.location.href = `/api/auth/oidc/${providerSlug}/authorize`
}

/**
 * Parse OIDC callback token from URL hash
 * token format：http://localhost:5173/#token=eyJ...
 * Hash is not sent to the server, so it is relatively safe
 */
export function parseOIDCCallbackHash(): string | null {
    const hash = window.location.hash
    if (!hash || !hash.includes('token=')) return null
    const params = new URLSearchParams(hash.slice(1))  // Remove '#'
    const token = params.get('token')
    if (token) {
        // Clear hash without triggering a page reload
        window.history.replaceState(null, '', window.location.pathname)
    }
    return token
}

// ========== Admin APIs requiring a Bearer token==========

function authHeaders(): HeadersInit {
    const token = localStorage.getItem('token')
    return {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
    }
}

/** Get all OIDC provider configurations. Administrators only. */
export async function getOIDCProvidersAdmin(): Promise<OIDCProviderConfig[]> {
    const res = await fetch('/api/oidc-providers', { headers: authHeaders() })
    if (!res.ok) throw new Error('获取 OIDC 提供商失败')
    return res.json()
}

/** Create OIDC provider configuration. Administrators only. */
export async function createOIDCProvider(data: CreateOIDCProviderRequest): Promise<{ message: string; id: number }> {
    const res = await fetch('/api/oidc-providers', {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify(data),
    })
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: '创建失败' }))
        throw new Error(err.detail || '创建失败')
    }
    return res.json()
}

/** Update OIDC provider configuration. Administrators only. */
export async function updateOIDCProvider(id: number, data: UpdateOIDCProviderRequest): Promise<{ message: string }> {
    const res = await fetch(`/api/oidc-providers/${id}`, {
        method: 'PUT',
        headers: authHeaders(),
        body: JSON.stringify(data),
    })
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: '更新失败' }))
        throw new Error(err.detail || '更新失败')
    }
    return res.json()
}

/** Delete OIDC provider configuration. Administrators only. */
export async function deleteOIDCProvider(id: number): Promise<{ message: string }> {
    const res = await fetch(`/api/oidc-providers/${id}`, {
        method: 'DELETE',
        headers: authHeaders(),
    })
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: '删除失败' }))
        throw new Error(err.detail || '删除失败')
    }
    return res.json()
}
