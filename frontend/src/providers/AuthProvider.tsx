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
            const token = localStorage.getItem("token")
            if (token) {
                try {
                    // 这里应该调用 /api/auth/me 接口验证 token 有效性并获取最新用户信息
                    // 暂时简单实现，如果有 token 且 localStorage 有 user，就恢复状态
                    // 实际应该:
                    const currentUser = await client.getCurrentUser()
                    setUser(currentUser as unknown as AuthUser) // Cast if API User type differs slightly, or update types
                } catch (error) {
                    console.error("Session expired or invalid", error)
                    logout() //Token 无效，清理
                }
            }
            setIsLoading(false)
        }

        checkAuth()
    }, [logout])

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
