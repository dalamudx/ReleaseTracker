import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import remarkEmoji from "remark-emoji"
import { ExternalLink } from "lucide-react"
import { useTranslation } from "react-i18next"
import { CHANNEL_LABELS } from "@/lib/channel"
import { useDateFormatter } from "@/hooks/use-date-formatter"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"

import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import type { Release } from "@/api/types"

interface ReleaseNotesModalProps {
    release: Release | null
    open: boolean
    onOpenChange: (open: boolean) => void
}

export function ReleaseNotesModal({ release, open, onOpenChange }: ReleaseNotesModalProps) {
    const { t } = useTranslation()
    const formatDate = useDateFormatter()

    if (!release) return null

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent showCloseButton={false} className="max-w-4xl lg:max-w-6xl max-h-[85vh] flex flex-col">
                <DialogHeader>
                    <div className="flex items-center gap-2 mb-3">
                        <Badge variant="outline" className="text-sm px-2.5 py-0.5">
                            {release.tracker_name}
                        </Badge>
                    </div>
                    <DialogTitle className="text-2xl">
                        <div className="relative inline-flex items-center">
                            {release.name || release.tag_name}
                            <Badge
                                variant="outline"
                                className={`absolute left-full -top-1.5 ml-0.5 h-3.5 px-1 text-[9px] font-medium leading-none rounded-full bg-transparent hover:bg-transparent transition-colors ${release.prerelease
                                    ? "border-amber-500 text-amber-500"
                                    : "border-emerald-500 text-emerald-500"
                                    }`}
                            >
                                {release.channel_name && CHANNEL_LABELS[release.channel_name]
                                    ? t(CHANNEL_LABELS[release.channel_name])
                                    : (release.channel_name || (release.prerelease ? t('channel.prerelease') : t('channel.stable')))}
                            </Badge>
                        </div>
                    </DialogTitle>
                    <DialogDescription>
                        {t('dashboard.releaseNotes.releasedAt', { date: formatDate(release.published_at) })}
                    </DialogDescription>
                </DialogHeader>

                <div className="flex-1 min-h-0 mt-4 border rounded-md bg-muted/10 flex flex-col overflow-hidden">
                    <div className="flex-1 overflow-y-auto p-4">
                        <div className="text-sm leading-relaxed break-words">
                            <ReactMarkdown
                                remarkPlugins={[remarkGfm, remarkEmoji]}
                                components={{
                                    h1: ({ node, ...props }) => { void node; return <h1 className="text-xl font-bold mt-6 mb-4 pb-2 border-b" {...props} /> },
                                    h2: ({ node, ...props }) => { void node; return <h2 className="text-lg font-semibold mt-6 mb-3" {...props} /> },
                                    h3: ({ node, ...props }) => { void node; return <h3 className="text-base font-semibold mt-4 mb-2" {...props} /> },
                                    h4: ({ node, ...props }) => { void node; return <h4 className="text-sm font-semibold mt-4 mb-2" {...props} /> },
                                    p: ({ node, ...props }) => { void node; return <p className="mb-4 last:mb-0" {...props} /> },
                                    ul: ({ node, ...props }) => { void node; return <ul className="mb-4 list-disc pl-5 space-y-1" {...props} /> },
                                    ol: ({ node, ...props }) => { void node; return <ol className="mb-4 list-decimal pl-5 space-y-1" {...props} /> },
                                    li: ({ node, ...props }) => { void node; return <li className="pl-1" {...props} /> },
                                    table: ({ node, ...props }) => { void node; return <div className="my-4 w-full overflow-y-auto"><table className="w-full border-collapse border border-muted text-sm" {...props} /></div> },
                                    thead: ({ node, ...props }) => { void node; return <thead className="bg-muted/50" {...props} /> },
                                    tbody: ({ node, ...props }) => { void node; return <tbody {...props} /> },
                                    tr: ({ node, ...props }) => { void node; return <tr className="border-b border-muted transition-colors hover:bg-muted/50 data-[state=selected]:bg-muted" {...props} /> },
                                    th: ({ node, ...props }) => { void node; return <th className="h-12 px-4 text-left align-middle font-medium text-muted-foreground [&:has([role=checkbox])]:pr-0 border-r border-muted last:border-r-0" {...props} /> },
                                    td: ({ node, ...props }) => { void node; return <td className="p-4 align-middle [&:has([role=checkbox])]:pr-0 border-r border-muted last:border-r-0" {...props} /> },
                                    a: ({ node, href, children, ...props }) => {
                                        void node;
                                        let content = children;
                                        // 仅当链接文本看起来像 URL 时才缩短（即不是 [Link](url) 这种自定义名称）
                                        if (href && (typeof children === 'string' && children.trim() === href)) {
                                            try {
                                                const url = new URL(href);
                                                if (url.hostname === 'github.com') {
                                                    const path = url.pathname.split('/').filter(Boolean);
                                                    // 处理 PR 和 Issue: /owner/repo/pull/123 或 /owner/repo/issues/123
                                                    if (path.length >= 4 && (path[2] === 'pull' || path[2] === 'issues')) {
                                                        content = `#${path[3]}`;
                                                    }
                                                    // 处理用户: /username (排除保留路径)
                                                    else if (path.length === 1 && !['login', 'pricing', 'join', 'explore'].includes(path[0])) {
                                                        content = `@${path[0]}`;
                                                    }
                                                }
                                            } catch {
                                                // 无效 URL，忽略
                                            }
                                        }
                                        const isMention = typeof content === 'string' && content.startsWith('@');
                                        const className = isMention
                                            ? "font-bold text-foreground hover:underline"
                                            : "text-blue-500 hover:underline font-medium";

                                        return <a href={href} className={className} target="_blank" rel="noopener noreferrer" {...props}>{content}</a>
                                    },
                                    blockquote: ({ node, ...props }) => { void node; return <blockquote className="border-l-4 border-muted pl-4 italic text-muted-foreground my-4" {...props} /> },
                                    code: ({ node, className, children, ...props }: React.ComponentPropsWithoutRef<'code'> & { node?: unknown }) => {
                                        void node // Satisfy no-unused-vars
                                        const match = /language-(\w+)/.exec(className || '')
                                        // 行内代码
                                        if (!match) {
                                            return <code className="bg-muted px-1.5 py-0.5 rounded font-mono text-xs" {...props}>{children}</code>
                                        }
                                        // 代码块 (简化版)
                                        return <pre className="bg-muted p-4 rounded-lg overflow-x-auto my-4 text-xs font-mono">{children}</pre>
                                    },
                                }}
                            >
                                {(release.body || t('dashboard.releaseNotes.noNotes'))
                                    // 自动链接提及 @username -> [@username](https://github.com/username)
                                    // 修改：使用回调检查下一个字符，防止匹配到 @scope/pkg
                                    .replace(/(^|\s)(@[a-zA-Z0-9-]+)/g, (match, prefix, username, offset, string) => {
                                        const nextChar = string[offset + match.length];
                                        if (nextChar === '/') return match; // 如果是 @scope/pkg，忽略
                                        return `${prefix}[${username}](https://github.com/${username.slice(1)})`;
                                    })
                                }
                            </ReactMarkdown>
                        </div>
                    </div>
                </div>
                <DialogFooter className="mt-2">
                    <div className="flex w-full justify-between sm:justify-end gap-2">
                        <Button variant="outline" onClick={() => window.open(release.url, '_blank')}>
                            <ExternalLink className="mr-2 h-4 w-4" />
                            {t('dashboard.releaseNotes.viewSource')}
                        </Button>
                        <Button onClick={() => onOpenChange(false)}>
                            {t('common.close')}
                        </Button>
                    </div>
                </DialogFooter>
            </DialogContent>
        </Dialog >
    )
}
