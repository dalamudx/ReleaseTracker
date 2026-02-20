/**
 * OIDC 认证 API 函数
 * 所有请求使用相对路径，经 Vite 代理转发到后端
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

export interface UpdateOIDCProviderRequest extends Partial<Omit<CreateOIDCProviderRequest, 'slug'>> {
    client_secret?: string | null  // 不传则不更新
}

// ========== 公开 API（无需认证）==========

/** 获取已启用的 OIDC 提供商列表（供登录页展示） */
export async function getOIDCProviders(): Promise<OIDCProvider[]> {
    const res = await fetch('/api/auth/oidc/providers')
    if (!res.ok) return []
    return res.json()
}

/** 发起 OIDC 登录（重定向到 IdP，经后端 /api/auth/oidc/{slug}/authorize） */
export function initiateOIDCLogin(providerSlug: string) {
    window.location.href = `/api/auth/oidc/${providerSlug}/authorize`
}

/**
 * 从 URL Hash 中解析 OIDC 回调 token
 * token 格式：http://localhost:5173/#token=eyJ...
 * Hash 不会传到服务器，相对安全
 */
export function parseOIDCCallbackHash(): string | null {
    const hash = window.location.hash
    if (!hash || !hash.includes('token=')) return null
    const params = new URLSearchParams(hash.slice(1))  // 去掉 '#'
    const token = params.get('token')
    if (token) {
        // 清理 hash（不触发页面刷新）
        window.history.replaceState(null, '', window.location.pathname)
    }
    return token
}

// ========== 管理员 API（需要 Bearer Token）==========

function authHeaders(): HeadersInit {
    const token = localStorage.getItem('token')
    return {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
    }
}

/** 获取所有 OIDC 提供商配置（管理员） */
export async function getOIDCProvidersAdmin(): Promise<OIDCProviderConfig[]> {
    const res = await fetch('/api/oidc-providers', { headers: authHeaders() })
    if (!res.ok) throw new Error('获取 OIDC 提供商失败')
    return res.json()
}

/** 创建 OIDC 提供商配置（管理员） */
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

/** 更新 OIDC 提供商配置（管理员） */
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

/** 删除 OIDC 提供商配置（管理员） */
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
