/**
 * OIDC authentication API functions.
 * Paths remain same-origin under an optional deployment sub-path.
 */
import { appPath } from "@/lib/base-path"
import { apiClient } from "./client"

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

export interface AdminOIDCBindingStatus {
    bound: boolean
    issuer: string | null
    subject: string | null
    provider_id: number | null
    provider_slug: string | null
}

export interface AdminOIDCBindingAuthorizeResponse {
    authorization_url: string
}

// ========== Public APIs without authentication==========

/** Get enabled OIDC providers for display on the login page */
export async function getOIDCProviders(): Promise<OIDCProvider[]> {
    const res = await fetch(appPath('/api/auth/oidc/providers'))
    if (!res.ok) return []
    return res.json()
}

/** Start OIDC login and redirect to the IdP via backend /api/auth/oidc/{slug}/authorize */
export function initiateOIDCLogin(providerSlug: string) {
    window.location.href = appPath(`/api/auth/oidc/${providerSlug}/authorize`)
}

// Administrator requests share cookie/CSRF handling and lazy refresh with the
// rest of the application. Never read a browser-accessible JWT here.

export async function getAdminOIDCBinding(): Promise<AdminOIDCBindingStatus> {
    return (await apiClient.get<AdminOIDCBindingStatus>('/api/oidc-providers/admin-binding')).data
}

export async function authorizeAdminOIDCBinding(providerId: number, currentPassword: string): Promise<AdminOIDCBindingAuthorizeResponse> {
    return (await apiClient.post<AdminOIDCBindingAuthorizeResponse>(`/api/oidc-providers/${providerId}/admin-binding/authorize`, {current_password:currentPassword})).data
}

export async function unbindAdminOIDC(currentPassword: string): Promise<{message:string}> {
    return (await apiClient.post<{message:string}>('/api/oidc-providers/admin-binding/unbind', {current_password:currentPassword})).data
}

export async function getOIDCProvidersAdmin(): Promise<OIDCProviderConfig[]> {
    return (await apiClient.get<OIDCProviderConfig[]>('/api/oidc-providers')).data
}

export async function createOIDCProvider(data: CreateOIDCProviderRequest): Promise<{message:string; id:number}> {
    return (await apiClient.post<{message:string; id:number}>('/api/oidc-providers', data)).data
}

export async function updateOIDCProvider(id: number, data: UpdateOIDCProviderRequest): Promise<{message:string}> {
    return (await apiClient.put<{message:string}>(`/api/oidc-providers/${id}`, data)).data
}

export async function deleteOIDCProvider(id: number): Promise<{message:string}> {
    return (await apiClient.delete<{message:string}>(`/api/oidc-providers/${id}`)).data
}
