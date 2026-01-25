import { useState, useEffect } from "react"
import { Sun, Moon, Laptop, Check, Palette } from "lucide-react"
import { useTheme } from "@/providers/theme-provider"

import { Button } from "@/components/ui/button"
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { Slider } from "@/components/ui/slider"


type Theme = "light" | "dark" | "system"

const THEME_MODES = [
    {
        name: "浅色",
        value: "light" as Theme,
        icon: Sun,
        description: "浅色主题"
    },
    {
        name: "深色",
        value: "dark" as Theme,
        icon: Moon,
        description: "深色主题"
    },
    {
        name: "跟随系统",
        value: "system" as Theme,
        icon: Laptop,
        description: "跟随系统设置"
    }
]

const COLOR_THEMES = [
    { name: "默认", value: "zinc", color: "oklch(0.45 0.008 264)" },
    { name: "红色", value: "red", color: "oklch(0.645 0.246 16.439)" },
    { name: "玫瑰", value: "rose", color: "oklch(0.645 0.246 350)" },
    { name: "橙色", value: "orange", color: "oklch(0.769 0.188 45)" },
    { name: "绿色", value: "green", color: "oklch(0.6 0.118 184.704)" },
    { name: "蓝色", value: "blue", color: "oklch(0.488 0.243 264.376)" },
    { name: "黄色", value: "yellow", color: "oklch(0.828 0.189 85)" },
    { name: "紫罗兰", value: "violet", color: "oklch(0.627 0.265 280)" },
] as const

const SCALE_MODES = [
    { name: "默认", value: "default" },
    { name: "缩放", value: "scaled" },
    { name: "等宽", value: "mono" },
]

export function ThemeCustomizer() {
    const { theme, setTheme, color, setColor, radius, setRadius, zoom, setZoom } = useTheme()
    const [mounted, setMounted] = useState(false)

    useEffect(() => {
        setMounted(true)
    }, [])

    const handleThemeModeChange = (mode: string) => {
        if (mode) {
            setTheme(mode as Theme)
        }
    }

    if (!mounted) {
        return (
            <Button variant="ghost" size="icon" className="h-7 w-7">
                <Palette className="h-4 w-4" />
                <span className="sr-only">切换主题</span>
            </Button>
        )
    }

    // 获取当前主题模式图标
    const getCurrentModeIcon = () => {
        if (theme === "system") {
            return Laptop
        }
        const modeConfig = THEME_MODES.find(m => m.value === theme)
        return modeConfig?.icon || Laptop
    }

    const ModeIcon = getCurrentModeIcon()
    const currentColorTheme = COLOR_THEMES.find(t => t.value === color)

    return (
        <DropdownMenu>
            <DropdownMenuTrigger asChild>
                <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 hover:bg-accent hover:text-accent-foreground relative group-data-[collapsible=icon]:w-7 group-data-[collapsible=icon]:overflow-hidden"
                >
                    <div className="relative">
                        <ModeIcon className="h-4 w-4" />
                        {/* 颜色主题指示器 - 只在非默认主题时显示 */}
                        {color !== "zinc" && (
                            <div
                                className="absolute -bottom-0.5 -right-0.5 h-2 w-2 rounded-full border border-background"
                                style={{ backgroundColor: currentColorTheme?.color }}
                            />
                        )}
                    </div>
                    <span className="sr-only">切换主题</span>
                </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
                {/* 主题模式 */}
                <div className="px-2 py-1.5">
                    <div className="text-sm font-medium">主题模式</div>
                    <div className="text-xs text-muted-foreground">选择明暗主题</div>
                </div>
                <div className="px-2 py-2">
                    <ToggleGroup
                        type="single"
                        value={theme}
                        onValueChange={handleThemeModeChange}
                        className="grid grid-cols-3 w-full"
                    >
                        {THEME_MODES.map((mode) => {
                            const Icon = mode.icon
                            return (
                                <ToggleGroupItem
                                    key={mode.value}
                                    value={mode.value}
                                    className="flex flex-col items-center gap-1 p-2 h-auto rounded-none first:rounded-l-md last:rounded-r-md data-[state=on]:bg-accent data-[state=on]:text-accent-foreground border-r border-border last:border-r-0"
                                    title={`${mode.name} - ${mode.description}`}
                                >
                                    <Icon className="h-4 w-4" />
                                    <div className="text-xs text-center">{mode.name}</div>
                                </ToggleGroupItem>
                            )
                        })}
                    </ToggleGroup>
                </div>

                <div className="border-t my-1" />

                {/* 颜色主题 */}
                <div className="px-2 py-1.5">
                    <div className="text-sm font-medium">颜色主题</div>
                    <div className="text-xs text-muted-foreground">选择主色调</div>
                </div>
                <div className="px-2 py-2">
                    <div className="grid grid-cols-3 gap-2">
                        {COLOR_THEMES.map((colorTheme) => (
                            <button
                                key={colorTheme.value}
                                onClick={() => setColor(colorTheme.value as any)}
                                className="relative flex flex-col items-center gap-1 p-2 rounded-md hover:bg-accent hover:text-accent-foreground transition-colors"
                                title={colorTheme.name}
                            >
                                <div
                                    className="h-6 w-6 rounded-full border-2 border-border"
                                    style={{ backgroundColor: colorTheme.color }}
                                />
                                <div className="text-xs text-center">{colorTheme.name}</div>
                                {color === colorTheme.value && (
                                    <Check className="absolute -top-1 -right-1 h-3 w-3 text-primary bg-background rounded-full" />
                                )}
                            </button>
                        ))}
                    </div>
                </div>

                <DropdownMenuSeparator />

                {/* 缩放模式 */}
                <div className="px-2 py-1.5">
                    <div className="text-sm font-medium">缩放模式</div>
                    <div className="text-xs text-muted-foreground">选择界面缩放</div>
                </div>
                <div className="px-2 py-2">
                    <ToggleGroup
                        type="single"
                        value={zoom}
                        onValueChange={(value) => value && setZoom(value as any)}
                        className="grid grid-cols-3 w-full"
                    >
                        {SCALE_MODES.map((scaleMode) => (
                            <ToggleGroupItem
                                key={scaleMode.value}
                                value={scaleMode.value}
                                className="flex flex-col items-center gap-1 p-2 h-auto rounded-none first:rounded-l-md last:rounded-r-md data-[state=on]:bg-accent data-[state=on]:text-accent-foreground border-r border-border last:border-r-0"
                                title={scaleMode.name}
                            >
                                <div className="text-xs text-center">{scaleMode.name}</div>
                            </ToggleGroupItem>
                        ))}
                    </ToggleGroup>
                </div>

                <DropdownMenuSeparator />

                {/* 圆角大小 */}
                <div className="px-2 py-1.5">
                    <div className="text-sm font-medium">圆角大小</div>
                    <div className="text-xs text-muted-foreground">调整界面圆角</div>
                </div>
                <div className="px-2 py-2">
                    <div className="flex items-center gap-3">
                        <div className="text-xs text-muted-foreground min-w-[20px]">0</div>
                        <Slider
                            value={[radius ?? 0.5]}
                            onValueChange={(value) => setRadius(value[0] as any)}
                            max={1.5}
                            min={0}
                            step={0.05}
                            className="flex-1"
                        />
                        <div className="text-xs text-muted-foreground min-w-[30px]">
                            {(radius ?? 0.5).toFixed(2)}
                        </div>
                    </div>
                </div>
            </DropdownMenuContent>
        </DropdownMenu>
    )
}
