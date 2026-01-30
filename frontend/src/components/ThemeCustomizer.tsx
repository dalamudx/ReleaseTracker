// import { useState, useEffect } from "react" // Removed unused
import { Sun, Moon, Laptop, Check, Palette } from "lucide-react"
import { useTheme } from "@/context/theme-context"
import { type Theme, type Color, type Zoom } from "@/types/theme"
import { useMounted } from "@/hooks/use-mounted"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { Slider } from "@/components/ui/slider"


// type Theme = "light" | "dark" | "system" // Imported from provider

export function ThemeCustomizer() {
    const { t } = useTranslation()
    const { theme, setTheme, color, setColor, radius, setRadius, zoom, setZoom } = useTheme()
    const mounted = useMounted()

    const THEME_MODES = [
        {
            name: t('theme.mode.light'),
            value: "light" as Theme,
            icon: Sun,
            description: t('theme.mode.lightDesc')
        },
        {
            name: t('theme.mode.dark'),
            value: "dark" as Theme,
            icon: Moon,
            description: t('theme.mode.darkDesc')
        },
        {
            name: t('theme.mode.system'),
            value: "system" as Theme,
            icon: Laptop,
            description: t('theme.mode.systemDesc')
        }
    ]

    const COLOR_THEMES = [
        { name: t('theme.color.neutral'), value: "neutral" as Color, color: "oklch(0.45 0.008 264)" },
        { name: t('theme.color.red'), value: "red" as Color, color: "oklch(0.645 0.246 16.439)" },
        { name: t('theme.color.rose'), value: "rose" as Color, color: "oklch(0.645 0.246 350)" },
        { name: t('theme.color.orange'), value: "orange" as Color, color: "oklch(0.769 0.188 45)" },
        { name: t('theme.color.green'), value: "green" as Color, color: "oklch(0.6 0.118 184.704)" },
        { name: t('theme.color.blue'), value: "blue" as Color, color: "oklch(0.488 0.243 264.376)" },
        { name: t('theme.color.yellow'), value: "yellow" as Color, color: "oklch(0.828 0.189 85)" },
        { name: t('theme.color.violet'), value: "violet" as Color, color: "oklch(0.627 0.265 280)" },
    ]

    const SCALE_MODES = [
        { name: t('theme.scale.default'), value: "default" as Zoom },
        { name: t('theme.scale.scaled'), value: "scaled" as Zoom },
        { name: t('theme.scale.mono'), value: "mono" as Zoom },
    ]

    const handleThemeModeChange = (mode: string) => {
        if (mode) {
            setTheme(mode as Theme)
        }
    }

    if (!mounted) {
        return (
            <Button variant="ghost" size="icon" className="h-7 w-7">
                <Palette className="h-4 w-4" />
                <span className="sr-only">{t('theme.toggle')}</span>
            </Button>
        )
    }

    // 获取当前主题模式图标
    const CurrentModeIcon = (() => {
        if (theme === "system") {
            return Laptop
        }
        const modeConfig = THEME_MODES.find(m => m.value === theme)
        return modeConfig?.icon || Laptop
    })()

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
                        <CurrentModeIcon className="h-4 w-4" />
                        {/* 颜色主题指示器 - 只在非默认主题时显示 */}
                        {color !== "neutral" && (
                            <div
                                className="absolute -bottom-0.5 -right-0.5 h-2 w-2 rounded-full border border-background"
                                style={{ backgroundColor: currentColorTheme?.color }}
                            />
                        )}
                    </div>
                    <span className="sr-only">{t('theme.toggle')}</span>
                </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
                {/* 主题模式 */}
                <div className="px-2 py-1.5">
                    <div className="text-sm font-medium">{t('theme.mode.title')}</div>
                    <div className="text-xs text-muted-foreground">{t('theme.mode.description')}</div>
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
                    <div className="text-sm font-medium">{t('theme.color.title')}</div>
                    <div className="text-xs text-muted-foreground">{t('theme.color.description')}</div>
                </div>
                <div className="px-2 py-2">
                    <div className="grid grid-cols-3 gap-2">
                        {COLOR_THEMES.map((colorTheme) => (
                            <button
                                key={colorTheme.value}
                                onClick={() => setColor(colorTheme.value)}
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
                    <div className="text-sm font-medium">{t('theme.scale.title')}</div>
                    <div className="text-xs text-muted-foreground">{t('theme.scale.description')}</div>
                </div>
                <div className="px-2 py-2">
                    <ToggleGroup
                        type="single"
                        value={zoom}
                        onValueChange={(value) => value && setZoom(value as Zoom)}
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
                    <div className="text-sm font-medium">{t('theme.radius.title')}</div>
                    <div className="text-xs text-muted-foreground">{t('theme.radius.description')}</div>
                </div>
                <div className="px-2 py-2">
                    <div className="flex items-center gap-3">
                        <div className="text-xs text-muted-foreground min-w-[20px]">0</div>
                        <Slider
                            value={[radius ?? 0.5]}
                            onValueChange={(value) => setRadius(value[0])}
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
