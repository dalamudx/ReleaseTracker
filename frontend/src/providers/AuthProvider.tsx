import { useState, useEffect, useCallback } from "react"
import type { ReactNode } from "react"
import { api as client } from "@/api/client"
import { toast } from "sonner"
import { useTranslation } from "react-i18next"
import { AuthContext } from "@/context/auth-context"
import type { AuthUser, LoginData } from "@/types/auth"



export function AuthProvider({ children }: { children: ReactNode }) {
    const { t } = useTranslation()
    const [user, setUser] = useState<AuthUser | null>(null)
    const [isLoading, setIsLoading] = useState(true)

    const logout = useCallback(() => {
        localStorage.removeItem("token")
        localStorage.removeItem("refresh_token")
        localStorage.removeItem("user")
        setUser(null)
        // 可以调用后端 logout 接口
        // 客户端登出逻辑 
        toast.info(t('auth.logout.success'), { id: 'auth-logout' })
    }, [t])

    useEffect(() => {
        // 初始化时检查本地是否有 User 信息或尝试获取当前用户
        const checkAuth = async () => {
            // 1. 检查是否来自 OIDC 回调（URL hash 中有 token）
            const hash = window.location.hash
            if (hash && hash.includes('token=')) {
                const params = new URLSearchParams(hash.slice(1))
                const oidcToken = params.get('token')
                if (oidcToken) {
                    // 清理 hash（不触发刷新）
                    window.history.replaceState(null, '', window.location.pathname)
                    try {
                        localStorage.setItem('token', oidcToken)
                        const currentUser = await client.getCurrentUser()
                        setUser(currentUser as unknown as AuthUser)
                        toast.success(t('auth.oidc.loginSuccess'))
                    } catch (error) {
                        console.error('OIDC 登录失败', error)
                        localStorage.removeItem('token')
                        toast.error(t('auth.oidc.loginFailed'))
                    }
                    setIsLoading(false)
                    return
                }
            }

            // 2. 检查本地 token
            const token = localStorage.getItem("token")
            if (token) {
                try {
                    const currentUser = await client.getCurrentUser()
                    setUser(currentUser as unknown as AuthUser)
                } catch (error) {
                    console.error("Session expired or invalid", error)
                    logout()
                }
            }
            setIsLoading(false)
        }

        checkAuth()
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [logout]) // 故意不添加 t：避免语言切换触发重复认证


    const login = async (data: LoginData) => {
        try {
            const response = await client.login(data)
            // response 结构应该匹配后端: { user: User, token: { access_token: string, ... } }
            const { user, token } = response

            localStorage.setItem("token", token.access_token)
            localStorage.setItem("refresh_token", token.refresh_token) // 如果有
            localStorage.setItem("user", JSON.stringify(user)) // 缓存用户信息，可选

            setUser(user as unknown as AuthUser)
            toast.success(t('auth.login.success'))
        } catch (error: unknown) {
            console.error("Login failed", error)
            const errorMessage = error instanceof Error ? error.message : t('auth.login.failed')
            toast.error(errorMessage)
            throw error
        }
    }

    return (
        <AuthContext.Provider
            value={{
                user,
                isLoading,
                login,
                logout,
                isAuthenticated: !!user
            }}
        >
            {children}
        </AuthContext.Provider>
    )
}
