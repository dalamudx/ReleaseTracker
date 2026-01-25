import { createContext, useContext, useEffect, useState } from "react"

export type Theme = "dark" | "light" | "system"
export type Color = "zinc" | "red" | "rose" | "orange" | "green" | "blue" | "yellow" | "violet"
export type Radius = number
export type Zoom = "default" | "scaled" | "mono"

interface ThemeConfig {
    mode: Theme
    color: Color
    zoom: Zoom
    radius: Radius
}

type ThemeProviderProps = {
    children: React.ReactNode
    defaultTheme?: Theme
    defaultColor?: Color
    defaultRadius?: Radius
    defaultZoom?: Zoom
    storageKey?: string
}

type ThemeProviderState = {
    theme: Theme
    setTheme: (theme: Theme) => void
    color: Color
    setColor: (color: Color) => void
    radius: Radius
    setRadius: (radius: Radius) => void
    zoom: Zoom
    setZoom: (zoom: Zoom) => void
    themeConfig: ThemeConfig
    updateTheme: (config: Partial<ThemeConfig>) => void
}

// 颜色主题配置（使用 OKLCH 色彩空间）
export const COLOR_THEMES = [
    {
        name: "默认",
        value: "zinc" as Color,
        color: "oklch(0.45 0.008 264)",
        foreground: "oklch(0.985 0 0)",
    },
    {
        name: "红色",
        value: "red" as Color,
        color: "oklch(0.645 0.246 16.439)",
        foreground: "oklch(0.985 0 0)",
    },
    {
        name: "玫瑰",
        value: "rose" as Color,
        color: "oklch(0.645 0.246 350)",
        foreground: "oklch(0.985 0 0)",
    },
    {
        name: "橙色",
        value: "orange" as Color,
        color: "oklch(0.769 0.188 45)",
        foreground: "oklch(0.145 0 0)",
    },
    {
        name: "绿色",
        value: "green" as Color,
        color: "oklch(0.6 0.118 184.704)",
        foreground: "oklch(0.985 0 0)",
    },
    {
        name: "蓝色",
        value: "blue" as Color,
        color: "oklch(0.488 0.243 264.376)",
        foreground: "oklch(0.985 0 0)",
    },
    {
        name: "黄色",
        value: "yellow" as Color,
        color: "oklch(0.828 0.189 85)",
        foreground: "oklch(0.145 0 0)",
    },
    {
        name: "紫罗兰",
        value: "violet" as Color,
        color: "oklch(0.627 0.265 280)",
        foreground: "oklch(0.985 0 0)",
    },
]

const DEFAULT_THEME_CONFIG: ThemeConfig = {
    mode: "system",
    color: "zinc",
    zoom: "default",
    radius: 0.5,
}

const initialState: ThemeProviderState = {
    theme: "system",
    setTheme: () => null,
    color: "zinc",
    setColor: () => null,
    radius: 0.5,
    setRadius: () => null,
    zoom: "default",
    setZoom: () => null,
    themeConfig: DEFAULT_THEME_CONFIG,
    updateTheme: () => null,
}

const ThemeProviderContext = createContext<ThemeProviderState>(initialState)

export function ThemeProvider({
    children,
    defaultTheme = "system",
    defaultColor = "zinc",
    defaultRadius = 0.5,
    defaultZoom = "default",
    storageKey = "vite-ui-theme",
    ...props
}: ThemeProviderProps) {
    const [themeConfig, setThemeConfig] = useState<ThemeConfig>(() => {
        // 尝试从 localStorage 读取完整配置
        const saved = localStorage.getItem(storageKey + "-config")
        if (saved) {
            try {
                return JSON.parse(saved)
            } catch {
                return DEFAULT_THEME_CONFIG
            }
        }
        return {
            mode: defaultTheme,
            color: defaultColor,
            zoom: defaultZoom,
            radius: defaultRadius,
        }
    })

    const [mounted, setMounted] = useState(false)

    // 应用主题配置到 DOM
    const applyTheme = (config: ThemeConfig) => {
        const root = window.document.documentElement

        // 1. 应用明暗模式
        root.classList.remove("light", "dark")
        if (config.mode === "system") {
            const systemTheme = window.matchMedia("(prefers-color-scheme: dark)").matches
                ? "dark"
                : "light"
            root.classList.add(systemTheme)
        } else {
            root.classList.add(config.mode)
        }

        // 2. 应用颜色主题
        root.setAttribute("data-theme", config.color)

        // 3. 应用缩放模式
        root.setAttribute("data-scale", config.zoom)

        // 4. 应用圆角大小
        root.style.setProperty("--radius", `${config.radius}rem`)

        // 5. 【关键】动态设置 CSS 变量
        const colorTheme = COLOR_THEMES.find((t) => t.value === config.color)
        if (colorTheme && config.color !== "zinc") {
            // 非默认主题：覆盖 CSS 变量
            root.style.setProperty("--primary", colorTheme.color)
            root.style.setProperty("--primary-foreground", colorTheme.foreground)
            root.style.setProperty("--ring", colorTheme.color)
        } else if (config.color === "zinc") {
            // 默认主题：移除覆盖，恢复原始 CSS 定义
            root.style.removeProperty("--primary")
            root.style.removeProperty("--primary-foreground")
            root.style.removeProperty("--ring")
        }
    }

    // 统一的更新主题方法
    const updateTheme = (newConfig: Partial<ThemeConfig>) => {
        const updatedConfig = { ...themeConfig, ...newConfig }
        setThemeConfig(updatedConfig)
        applyTheme(updatedConfig)
        localStorage.setItem(storageKey + "-config", JSON.stringify(updatedConfig))

        // 触发自定义事件，通知其他组件
        window.dispatchEvent(new CustomEvent("theme-change", { detail: updatedConfig }))
    }

    // 初始挂载时应用主题
    useEffect(() => {
        setMounted(true)
        applyTheme(themeConfig)
    }, [])

    // 监听系统主题变化
    useEffect(() => {
        if (themeConfig.mode !== "system") return

        const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)")
        const handleChange = () => {
            applyTheme(themeConfig)
        }

        mediaQuery.addEventListener("change", handleChange)
        return () => mediaQuery.removeEventListener("change", handleChange)
    }, [themeConfig])

    if (!mounted) {
        return null
    }

    const value = {
        theme: themeConfig.mode,
        setTheme: (theme: Theme) => updateTheme({ mode: theme }),
        color: themeConfig.color,
        setColor: (color: Color) => updateTheme({ color }),
        radius: themeConfig.radius,
        setRadius: (radius: Radius) => updateTheme({ radius }),
        zoom: themeConfig.zoom,
        setZoom: (zoom: Zoom) => updateTheme({ zoom }),
        themeConfig,
        updateTheme,
    }

    return (
        <ThemeProviderContext.Provider {...props} value={value}>
            {children}
        </ThemeProviderContext.Provider>
    )
}

export const useTheme = () => {
    const context = useContext(ThemeProviderContext)

    if (context === undefined)
        throw new Error("useTheme must be used within a ThemeProvider")

    return context
}
