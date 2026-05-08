import { useCallback, useEffect, useState } from "react"
import type { ReactNode } from "react"
import { api as client, clearAuthStorage } from "@/api/client"
import { toast } from "sonner"
import { useTranslation } from "react-i18next"
import { AuthContext } from "@/context/auth-context"
import type { AuthUser, LoginData } from "@/types/auth"

export function AuthProvider({ children }: { children: ReactNode }) {
    const { t } = useTranslation()
    const [user, setUser] = useState<AuthUser | null>(null)
    const [isLoading, setIsLoading] = useState(true)

    const logout = useCallback(() => {
        clearAuthStorage()
        setUser(null)
        toast.info(t('auth.logout.success'), { id: 'auth-logout' })
    }, [t])

    useEffect(() => {
        // On initialization, check local user info or try fetching the current user
        const checkAuth = async () => {
            const loadCurrentUser = async (context: 'bootstrap' | 'oidc') => {
                try {
                    const currentUser = await client.getCurrentUser({ suppressAuthRedirect: true })
                    setUser(currentUser)
                    return true
                } catch (error) {
                    if (context === 'oidc') {
                        console.error('OIDC login failed', error)
                    } else {
                        console.error("Session expired or invalid", error)
                    }
                    setUser(null)
                    return false
                }
            }

            // 1. Check whether this is from an OIDC callback with a token in URL hash
            const hash = window.location.hash
            if (hash && hash.includes('token=')) {
                const params = new URLSearchParams(hash.slice(1))
                const oidcToken = params.get('token') ?? params.get('access_token')
                const oidcRefreshToken = params.get('refresh_token')
                if (oidcToken) {
                    // Clear the hash without triggering a refresh
                    window.history.replaceState(null, '', window.location.pathname)
                    localStorage.setItem('token', oidcToken)
                    if (oidcRefreshToken) {
                        localStorage.setItem('refresh_token', oidcRefreshToken)
                    }
                    const loaded = await loadCurrentUser('oidc')
                    if (loaded) {
                        toast.success(t('auth.oidc.loginSuccess'))
                    } else {
                        clearAuthStorage()
                        toast.error(t('auth.oidc.loginFailed'))
                    }
                    setIsLoading(false)
                    return
                }
            }

            // 2. Check local token
            const token = localStorage.getItem("token")
            if (token) {
                await loadCurrentUser('bootstrap')
            }
            setIsLoading(false)
        }

        checkAuth()
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []) // Intentionally omit t to avoid repeated authentication when switching languages

    const login = useCallback(async (data: LoginData) => {
        try {
            const response = await client.login(data)
            // response structure should match the backend: { user: User, token: { access_token: string, ... } }
            const { user: loggedInUser, token } = response

            localStorage.setItem("token", token.access_token)
            if (token.refresh_token) {
                localStorage.setItem("refresh_token", token.refresh_token)
            }
            localStorage.setItem("user", JSON.stringify(loggedInUser))

            setUser(loggedInUser)
            toast.success(t('auth.login.success'))
        } catch (error: unknown) {
            console.error("Login failed", error)
            const errorMessage = error instanceof Error ? error.message : t('auth.login.failed')
            toast.error(errorMessage)
            throw error
        }
    }, [t])

    return (
        <AuthContext.Provider
            value={{
                user,
                isLoading,
                login,
                logout,
                isAuthenticated: !!user,
            }}
        >
            {children}
        </AuthContext.Provider>
    )
}
