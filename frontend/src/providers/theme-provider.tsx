import { useEffect, useState, useCallback } from "react"
import { THEME_COLORS } from "@/config/theme-config"
import { ThemeProviderContext, DEFAULT_THEME_CONFIG } from "@/context/theme-context"
import { useMounted } from "@/hooks/use-mounted"
import type { Theme, Color, Radius, Zoom, ThemeConfig } from "@/types/theme"



type ThemeProviderProps = {
    children: React.ReactNode
    defaultTheme?: Theme
    defaultColor?: Color
    defaultRadius?: Radius
    defaultZoom?: Zoom
    storageKey?: string
}

export function ThemeProvider({
    children,
    defaultTheme = "system",
    defaultColor = "neutral",
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

    const mounted = useMounted()

    // 应用主题配置到 DOM
    const applyTheme = useCallback((config: ThemeConfig) => {
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
        const colorTheme = THEME_COLORS.find((t) => t.value === config.color)
        if (colorTheme && config.color !== "neutral") {
            document.documentElement.setAttribute("data-theme", config.color)
            root.style.setProperty("--primary", colorTheme.color)
            root.style.setProperty("--primary-foreground", colorTheme.foreground)
            root.style.setProperty("--ring", colorTheme.color)
        } else {
            // 移除自定义主题，使用默认 neutral
            document.documentElement.removeAttribute("data-theme")
            // 默认主题：移除覆盖，恢复原始 CSS 定义
            root.style.removeProperty("--primary")
            root.style.removeProperty("--primary-foreground")
            root.style.removeProperty("--ring")
        }
    }, [])

    // 统一的更新主题方法
    const updateTheme = useCallback((newConfig: Partial<ThemeConfig>) => {
        setThemeConfig((prev) => {
            const updatedConfig = { ...prev, ...newConfig }
            applyTheme(updatedConfig)
            localStorage.setItem(storageKey + "-config", JSON.stringify(updatedConfig))
            // 触发自定义事件，通知其他组件
            window.dispatchEvent(new CustomEvent("theme-change", { detail: updatedConfig }))
            return updatedConfig
        })
    }, [applyTheme, storageKey])

    // 监听配置变化应用主题
    useEffect(() => {
        if (mounted) {
            applyTheme(themeConfig)
        }
    }, [applyTheme, themeConfig, mounted])

    // 监听系统主题变化
    useEffect(() => {
        if (themeConfig.mode !== "system") return

        const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)")
        const handleChange = () => {
            applyTheme(themeConfig)
        }

        mediaQuery.addEventListener("change", handleChange)
        return () => mediaQuery.removeEventListener("change", handleChange)
    }, [themeConfig, applyTheme])

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
