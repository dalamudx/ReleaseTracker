import { useCallback, useEffect, useState } from "react"
import type { ReactNode } from "react"
import { api as client, clearAuthStorage } from "@/api/client"
import { getCsrfToken } from "@/lib/browser-session"
import { toast } from "sonner"
import { useTranslation } from "react-i18next"
import { AuthContext } from "@/context/auth-context"
import type { AuthUser, LoginData } from "@/types/auth"

export function AuthProvider({ children }: { children: ReactNode }) {
    const { t } = useTranslation()
    const [user, setUser] = useState<AuthUser | null>(null)
    const [isLoading, setIsLoading] = useState(true)

    const logout = useCallback(async () => {
        try {
            await client.logout() // Revoke server-side session before updating UI.
            clearAuthStorage()
            setUser(null)
            toast.info(t('auth.logout.success'), { id: 'auth-logout' })
        } catch {
            toast.error(t('common.error'))
        }
    }, [t])

    useEffect(() => {
        let active = true
        const checkAuth = async () => {
            const params = new URLSearchParams(window.location.hash.slice(1))
            const oidc = params.get('oidc') === 'success'
            // Never accept credentials from URLs; clear legacy fragments.
            const legacyHash = params.has('token') || params.has('access_token') || params.has('refresh_token')
            if (oidc || legacyHash) window.history.replaceState(null, '', window.location.pathname + window.location.search)
            try {
                await client.migrateSession()
                if (getCsrfToken()) {
                    const currentUser = await client.getCurrentUser({ suppressAuthRedirect: true })
                    if (active) setUser(currentUser)
                    if (active && oidc) toast.success(t('auth.oidc.loginSuccess'))
                }
            } catch {
                clearAuthStorage()
                if (active) setUser(null)
                if (active && oidc) toast.error(t('auth.oidc.loginFailed'))
            } finally {
                if (active) setIsLoading(false)
            }
        }
        void checkAuth()
        return () => { active = false }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])

    const login = useCallback(async (data: LoginData) => {
        try {
            const { user: loggedInUser } = await client.login(data)
            clearAuthStorage()
            setUser(loggedInUser)
            toast.success(t('auth.login.success'))
        } catch (error: unknown) {
            const errorMessage = error instanceof Error ? error.message : t('auth.login.failed')
            toast.error(errorMessage)
            throw error
        }
    }, [t])

    return <AuthContext.Provider value={{user, isLoading, login, logout, isAuthenticated: !!user}}>{children}</AuthContext.Provider>
}
