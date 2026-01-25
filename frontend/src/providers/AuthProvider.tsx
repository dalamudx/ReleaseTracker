import { createContext, useContext, useState, useEffect } from "react"
import type { ReactNode } from "react"
import { api as client } from "@/api/client"
import { toast } from "sonner"

interface User {
    id: number
    username: string
    email: string
    role: string
    avatar_url?: string
}

interface AuthContextType {
    user: User | null
    isLoading: boolean
    login: (data: any) => Promise<void>
    logout: () => void
    isAuthenticated: boolean
}

const AuthContext = createContext<AuthContextType | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
    const [user, setUser] = useState<User | null>(null)
    const [isLoading, setIsLoading] = useState(true)

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
                    setUser(currentUser)
                } catch (error) {
                    console.error("Session expired or invalid", error)
                    logout() //Token 无效，清理
                }
            }
            setIsLoading(false)
        }

        checkAuth()
    }, [])

    const login = async (data: any) => {
        try {
            const response = await client.login(data)
            // response 结构应该匹配后端: { user: User, token: { access_token: string, ... } }
            const { user, token } = response

            localStorage.setItem("token", token.access_token)
            localStorage.setItem("refresh_token", token.refresh_token) // 如果有
            localStorage.setItem("user", JSON.stringify(user)) // 缓存用户信息，可选

            setUser(user)
            toast.success("登录成功")
        } catch (error: any) {
            console.error("Login failed", error)
            toast.error(error.message || "登录失败，请检查用户名和密码")
            throw error
        }
    }

    const logout = () => {
        localStorage.removeItem("token")
        localStorage.removeItem("refresh_token")
        localStorage.removeItem("user")
        setUser(null)
        // 可以调用后端 logout 接口
        // client.logout() 
        toast.info("已退出登录")
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

export function useAuth() {
    const context = useContext(AuthContext)
    if (context === undefined) {
        throw new Error("useAuth must be used within an AuthProvider")
    }
    return context
}
