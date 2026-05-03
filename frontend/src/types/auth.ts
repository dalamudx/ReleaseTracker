
import type { User } from "@/api/types"

export type AuthUser = User

export interface LoginData {
    username: string
    password: string
}

export interface AuthContextType {
    user: AuthUser | null
    isLoading: boolean
    login: (data: LoginData) => Promise<void>
    logout: () => void
    isAuthenticated: boolean
}
