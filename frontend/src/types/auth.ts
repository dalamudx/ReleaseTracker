
export interface AuthUser {
    id: number
    username: string
    email: string
    is_admin?: boolean
    avatar_url?: string
}

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
