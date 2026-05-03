import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import remarkEmoji from "remark-emoji"
import rehypeRaw from "rehype-raw"
import rehypeSanitize, { defaultSchema } from "rehype-sanitize"
import { ExternalLink } from "lucide-react"
import { Children } from "react"
import type { ComponentPropsWithoutRef, ReactNode } from "react"
import { useTranslation } from "react-i18next"
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
import type { ReleaseNotesSubject } from "@/api/types"
import { getReleaseChannelBadgeText } from "./releaseNotesModalHelpers"

interface ReleaseNotesModalProps {
    release: ReleaseNotesSubject | null
    open: boolean
    onOpenChange: (open: boolean) => void
}

type MarkdownCodeProps = ComponentPropsWithoutRef<"code"> & {
    node?: unknown
}

type MarkdownTextNode = {
    type: "text"
    value: string
}

type MarkdownLinkNode = {
    type: "link"
    url: string
    children: MarkdownTextNode[]
}

type MarkdownNode = {
    type?: string
    value?: string
    url?: string
    children?: MarkdownNode[]
}

type AutolinkPlatform = "github" | "gitlab" | "gitea"

type AutolinkContext = {
    platform: AutolinkPlatform
    repoBase: string
    origin: string
}

const TRANSFORM_EXCLUDED_NODE_TYPES = new Set([
    "link",
    "linkReference",
    "definition",
    "inlineCode",
    "code",
    "html",
    "heading",
])

const releaseNotesSanitizeSchema = {
    ...defaultSchema,
    tagNames: [
        ...(defaultSchema.tagNames ?? []),
        "details",
        "summary",
        "img",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        "hr",
        "br",
    ],
    attributes: {
        ...defaultSchema.attributes,
        a: [...(defaultSchema.attributes?.a ?? []), "target", "rel"],
        details: [...(defaultSchema.attributes?.details ?? []), "open"],
        img: [...(defaultSchema.attributes?.img ?? []), "src", "alt", "title", "width", "height"],
        th: [...(defaultSchema.attributes?.th ?? []), "colspan", "rowspan", "align"],
        td: [...(defaultSchema.attributes?.td ?? []), "colspan", "rowspan", "align"],
        code: [...(defaultSchema.attributes?.code ?? []), ["className"]],
        pre: [...(defaultSchema.attributes?.pre ?? []), ["className"]],
    },
}

const AUTOLINK_PATTERN = /(^|[\s([{])(@[a-zA-Z0-9._-]+|!(\d+)\b|[0-9a-f]{7,40}\b)/gi
const GITHUB_BACKPORT_PREFIX_PATTERN = /\(#(\d+)\s+by\s+(@[a-zA-Z0-9-]+)\)\s+backported\s+by\s+(@[a-zA-Z0-9-]+)\s+in\s*/gi
const GIT_FORGE_ASSIGNEE_PATTERN = /(assign(?:ed|eed) to )(@[a-zA-Z0-9._-]+)/gi

function isGitLabHost(hostname: string): boolean {
    return hostname === 'gitlab.com' || hostname.startsWith('gitlab.') || hostname.endsWith('.gitlab.com') || hostname.includes('.gitlab.')
}

function isGitHubHost(hostname: string): boolean {
    return hostname === "github.com" || hostname.endsWith('.github.com')
}

function createTextNode(value: string): MarkdownTextNode {
    return { type: "text", value }
}

function createLinkNode(label: string, url: string): MarkdownLinkNode {
    return {
        type: "link",
        url,
        children: [createTextNode(label)],
    }
}

function buildSimpleAutolinkNodes(text: string, autolinkContext?: AutolinkContext): Array<MarkdownTextNode | MarkdownLinkNode> {
    if (!text) return [createTextNode("")]
    if (!autolinkContext) return [createTextNode(text)]

    const nodes: Array<MarkdownTextNode | MarkdownLinkNode> = []
    let lastIndex = 0

    for (const match of text.matchAll(AUTOLINK_PATTERN)) {
        const prefix = match[1] ?? ""
        const token = match[2]
        const bangReferenceNumber = match[3]
        const matchIndex = match.index ?? 0
        const tokenStart = matchIndex + prefix.length
        const tokenEnd = tokenStart + token.length
        const nextChar = text[tokenEnd]
        const lastPrefixChar = prefix[prefix.length - 1]

        let replacement: MarkdownLinkNode | null = null

        if (token.startsWith("@")) {
            if (nextChar !== "/") {
                replacement = createLinkNode(token, `${autolinkContext.origin}/${token.slice(1)}`)
            }
        } else if (bangReferenceNumber) {
            if (autolinkContext.platform === "gitlab") {
                replacement = createLinkNode(token, `${autolinkContext.repoBase}/-/merge_requests/${bangReferenceNumber}`)
            } else if (autolinkContext.platform === "gitea") {
                replacement = createLinkNode(token, `${autolinkContext.repoBase}/pulls/${bangReferenceNumber}`)
            }
        } else {
            const isNumericToken = /^\d+$/.test(token)
            const isPathLikeToken = lastPrefixChar === "/" || lastPrefixChar === "="

            if (!isNumericToken && !isPathLikeToken) {
                const commitPath = autolinkContext.platform === "gitlab"
                    ? `${autolinkContext.repoBase}/-/commit/${token}`
                    : `${autolinkContext.repoBase}/commit/${token}`
                replacement = createLinkNode(token.slice(0, 7), commitPath)
            }
        }

        if (!replacement) {
            continue
        }

        if (matchIndex > lastIndex) {
            nodes.push(createTextNode(text.slice(lastIndex, matchIndex)))
        }

        if (prefix) {
            nodes.push(createTextNode(prefix))
        }

        nodes.push(replacement)
        lastIndex = tokenEnd
    }

    if (nodes.length === 0) {
        return [createTextNode(text)]
    }

    if (lastIndex < text.length) {
        nodes.push(createTextNode(text.slice(lastIndex)))
    }

    return nodes
}

function buildAutoLinkedNodes(text: string, autolinkContext?: AutolinkContext): Array<MarkdownTextNode | MarkdownLinkNode> {
    if (!text) return [createTextNode("")]
    if (!autolinkContext) return [createTextNode(text)]

    const processSegments = (
        segments: Array<MarkdownTextNode | MarkdownLinkNode>,
        pattern: RegExp,
        replacer: (...args: string[]) => Array<MarkdownTextNode | MarkdownLinkNode>
    ): Array<MarkdownTextNode | MarkdownLinkNode> => {
        const next: Array<MarkdownTextNode | MarkdownLinkNode> = []

        for (const segment of segments) {
            if (segment.type !== 'text') {
                next.push(segment)
                continue
            }

            let lastIndex = 0
            let matched = false

            for (const match of segment.value.matchAll(pattern)) {
                const matchIndex = match.index ?? 0
                const fullMatch = match[0]

                if (matchIndex > lastIndex) {
                    next.push(...buildSimpleAutolinkNodes(segment.value.slice(lastIndex, matchIndex), autolinkContext))
                }

                next.push(...replacer(...match))
                lastIndex = matchIndex + fullMatch.length
                matched = true
            }

            if (!matched) {
                next.push(...buildSimpleAutolinkNodes(segment.value, autolinkContext))
                continue
            }

            if (lastIndex < segment.value.length) {
                next.push(...buildSimpleAutolinkNodes(segment.value.slice(lastIndex), autolinkContext))
            }
        }

        return next
    }

    let segments: Array<MarkdownTextNode | MarkdownLinkNode> = [createTextNode(text)]

    if (autolinkContext.platform === 'github') {
        segments = processSegments(
            segments,
            GITHUB_BACKPORT_PREFIX_PATTERN,
            (_fullMatch, sourcePr, author, backporter) => [
                createTextNode('('),
                createLinkNode(`#${sourcePr}`, `${autolinkContext.repoBase}/pull/${sourcePr}`),
                createTextNode(' by '),
                createLinkNode(author, `${autolinkContext.origin}/${author.slice(1)}`),
                createTextNode(') backported by '),
                createLinkNode(backporter, `${autolinkContext.origin}/${backporter.slice(1)}`),
                createTextNode(' in '),
            ]
        )
    }

    if (autolinkContext.platform === 'gitlab' || autolinkContext.platform === 'gitea') {
        segments = processSegments(
            segments,
            GIT_FORGE_ASSIGNEE_PATTERN,
            (_fullMatch, prefixText, assignee) => [
                createTextNode(prefixText),
                createLinkNode(assignee, `${autolinkContext.origin}/${assignee.slice(1)}`),
            ]
        )
    }

    return segments
}

function transformPlatformNodeSequences(node: MarkdownNode, autolinkContext?: AutolinkContext) {
    if (!node.children || !autolinkContext) {
        return
    }

    for (let index = 0; index < node.children.length - 1; index += 1) {
        const current = node.children[index]
        const next = node.children[index + 1]

        if (!current || !next || typeof current !== 'object' || typeof next !== 'object') {
            continue
        }

        if (autolinkContext.platform === 'github' && current.type === 'text' && next.type === 'link') {
            const match = current.value?.match(/^(.*)\(#(\d+)\s+by\s+(@[a-zA-Z0-9-]+)\)\s+backported\s+by\s+(@[a-zA-Z0-9-]+)\s+in\s*$/)
            const url = typeof next.url === 'string' ? next.url : ''
            const backportMatch = url.match(/^https:\/\/github\.com\/[^/]+\/[^/]+\/(?:pull|issues)\/(\d+)$/)

            if (match && backportMatch) {
                const [, beforeText, sourcePr, author, backporter] = match
                const replacement: MarkdownNode[] = []
                if (beforeText) replacement.push(createTextNode(beforeText))
                replacement.push(createTextNode('('))
                replacement.push(createLinkNode(`#${sourcePr}`, `${autolinkContext.repoBase}/pull/${sourcePr}`))
                replacement.push(createTextNode(' by '))
                replacement.push(createLinkNode(author, `${autolinkContext.origin}/${author.slice(1)}`))
                replacement.push(createTextNode(') backported by '))
                replacement.push(createLinkNode(backporter, `${autolinkContext.origin}/${backporter.slice(1)}`))
                replacement.push(createTextNode(' in '))
                replacement.push(createLinkNode(`#${backportMatch[1]}`, url))
                node.children.splice(index, 2, ...replacement)
                index += replacement.length - 2
                continue
            }
        }

        if ((autolinkContext.platform === 'gitlab' || autolinkContext.platform === 'gitea') && current.type === 'link' && next.type === 'text') {
            const match = next.value?.match(/^(\s*assign(?:ed|eed) to )(@[a-zA-Z0-9._-]+)(.*)$/)
            if (match) {
                const [, prefix, assignee, suffix] = match
                const replacement: MarkdownNode[] = [current, createTextNode(prefix), createLinkNode(assignee, `${autolinkContext.origin}/${assignee.slice(1)}`)]
                if (suffix) replacement.push(createTextNode(suffix))
                node.children.splice(index, 2, ...replacement)
                index += replacement.length - 2
            }
        }
    }
}

function transformMarkdownTextNodes(node: MarkdownNode, autolinkContext?: AutolinkContext) {
    if (!node || typeof node !== "object") {
        return
    }

    if (TRANSFORM_EXCLUDED_NODE_TYPES.has(node.type ?? "")) {
        return
    }

    if (!node.children) {
        return
    }

    transformPlatformNodeSequences(node, autolinkContext)

    for (let index = 0; index < node.children.length; index += 1) {
        const child = node.children[index]

        if (!child || typeof child !== "object") {
            continue
        }

        if (child.type === "text" && typeof child.value === "string") {
            const replacementNodes = buildAutoLinkedNodes(child.value, autolinkContext)
            const isUnchangedTextNode = replacementNodes.length === 1
                && replacementNodes[0].type === "text"
                && replacementNodes[0].value === child.value

            if (!isUnchangedTextNode) {
                node.children.splice(index, 1, ...replacementNodes)
                index += replacementNodes.length - 1
                continue
            }
        }

        transformMarkdownTextNodes(child, autolinkContext)
    }
}

function remarkPlatformAutolinks(autolinkContext?: AutolinkContext) {
    return (tree: MarkdownNode) => {
        if (!autolinkContext) return
        transformMarkdownTextNodes(tree, autolinkContext)
    }
}

function getAutolinkContext(releaseUrl: string, trackerType?: string | null): AutolinkContext | undefined {
    try {
        const url = new URL(releaseUrl)
        const origin = url.origin
        const hostname = url.hostname.toLowerCase()

        if (trackerType === 'github' && isGitHubHost(hostname)) {
            const match = url.pathname.match(/^\/([^/]+)\/([^/]+)\/releases\//)
            if (!match) {
                return undefined
            }

            return {
                platform: "github",
                repoBase: `${origin}/${match[1]}/${match[2]}`,
                origin,
            }
        }

        if (trackerType === 'gitlab') {
            const gitLabMatch = url.pathname.match(/^\/(.+?)\/([^/]+)\/-(?:\/releases\/|\/tags\/)/)
            if (gitLabMatch && isGitLabHost(hostname)) {
                return {
                    platform: "gitlab",
                    repoBase: `${origin}/${gitLabMatch[1]}/${gitLabMatch[2]}`,
                    origin,
                }
            }
            return undefined
        }

        if (trackerType === 'gitea') {
            const giteaMatch = url.pathname.match(/^\/([^/]+)\/([^/]+)\/releases\/tag\//)
            if (giteaMatch) {
                return {
                    platform: "gitea",
                    repoBase: `${origin}/${giteaMatch[1]}/${giteaMatch[2]}`,
                    origin,
                }
            }
            return undefined
        }

        if (isGitHubHost(hostname)) {
            const match = url.pathname.match(/^\/([^/]+)\/([^/]+)\/releases\//)
            if (!match) {
                return undefined
            }

            return {
                platform: "github",
                repoBase: `${origin}/${match[1]}/${match[2]}`,
                origin,
            }
        }

        const gitLabMatch = url.pathname.match(/^\/(.+?)\/([^/]+)\/-\/releases\//)
        if (gitLabMatch && isGitLabHost(hostname)) {
            return {
                platform: "gitlab",
                repoBase: `${origin}/${gitLabMatch[1]}/${gitLabMatch[2]}`,
                origin,
            }
        }

        return undefined
    } catch {
        return undefined
    }
}

function getLinkText(children: ReactNode): string | null {
    const textParts = Children.toArray(children)
    if (textParts.length === 0) return null
    if (!textParts.every((part) => typeof part === "string")) return null
    return textParts.join("")
}

function normalizeGitHubLinkLabel(children: ReactNode, href?: string): ReactNode {
    if (!href) return children
    const text = getLinkText(children)
    if (!text || text !== href) return children

    try {
        const url = new URL(href)
        if (!isGitHubHost(url.hostname.toLowerCase())) return children
        const match = url.pathname.match(/^\/[^/]+\/[^/]+\/(pull|issues)\/(\d+)$/)
        if (!match) return children
        return `#${match[2]}`
    } catch {
        return children
    }
}

export function ReleaseNotesModal({ release, open, onOpenChange }: ReleaseNotesModalProps) {
    const { t } = useTranslation()
    const formatDate = useDateFormatter()

    if (!release) return null

    const autolinkContext = getAutolinkContext(release.url, release.tracker_type)
    const releaseChannelLabel = getReleaseChannelBadgeText(release, t)
    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent showCloseButton={false} className="max-w-4xl lg:max-w-6xl max-h-[85vh] flex flex-col">
                <DialogHeader>
                    <div className="center flex items-center gap-2 mb-3">
                        <Badge variant="outline" className="text-sm px-2.5 py-0.5">
                            {release.tracker_name}
                        </Badge>
                    </div>
                    <DialogTitle className="text-2xl">
                        <div className="relative inline-flex items-center">
                            {release.name || release.tag_name}
                            {releaseChannelLabel ? (
                                <Badge
                                    variant="outline"
                                    className="absolute left-full -top-2 ml-1 h-5 rounded-full bg-transparent px-1.5 text-[11px] font-medium leading-none transition-colors hover:bg-transparent"
                                >
                                    {releaseChannelLabel}
                                </Badge>
                            ) : null}
                        </div>
                    </DialogTitle>
                    <DialogDescription>
                        {t('dashboard.releaseNotes.releasedAt', { date: formatDate(release.published_at) })}
                    </DialogDescription>
                </DialogHeader>

                <div className="flex-1 min-h-0 mt-4 border rounded-md bg-muted/10 flex flex-col overflow-hidden">
                    <style dangerouslySetInnerHTML={{
                        __html: `
                        .markdown-content ul { list-style: disc !important; padding-left: 1.5rem !important; margin: 1rem 0 !important; display: block !important; }
                        .markdown-content ol { list-style: decimal !important; padding-left: 1.5rem !important; margin: 1rem 0 !important; display: block !important; }
                        .markdown-content li { display: list-item !important; margin-bottom: 0.5rem !important; color: inherit !important; }
                        .markdown-content h1, .markdown-content h2, .markdown-content h3, .markdown-content h4 { display: block !important; font-weight: bold !important; margin-top: 1.5rem !important; margin-bottom: 1rem !important; line-height: 1.3 !important; }
                        .markdown-content h1 { font-size: 1.5rem !important; border-bottom: 1px solid var(--border) !important; padding-bottom: 0.5rem !important; }
                        .markdown-content h2 { font-size: 1.25rem !important; border-bottom: 1px solid var(--border) !important; padding-bottom: 0.3rem !important; }
                        .markdown-content h3 { font-size: 1.1rem !important; }
                        .markdown-content p { display: block !important; margin-bottom: 1rem !important; line-height: 1.6 !important; }
                        .markdown-content pre { display: block !important; background: var(--muted) !important; padding: 1rem !important; border-radius: 0.5rem !important; overflow-x: auto !important; margin: 1rem 0 !important; border: 1px solid var(--border)/30 !important; }
                        .markdown-content code { font-family: var(--font-mono) !important; font-size: 0.9em !important; }
                        /* Remove backticks added by prose by default */
                        .markdown-content code::before, .markdown-content code::after { content: none !important; }
                        .markdown-content :not(pre) > code { background: var(--muted) !important; padding: 0.2rem 0.4rem !important; border-radius: 0.25rem !important; border: 1px solid var(--border)/50 !important; }
                        .markdown-content a { color: #3b82f6 !important; text-decoration: none !important; font-weight: 500 !important; }
                        .markdown-content a:hover { text-decoration: underline !important; }
                        .markdown-content blockquote { border-left: 4px solid var(--border) !important; padding-left: 1rem !important; font-style: italic !important; color: var(--muted-foreground) !important; margin: 1rem 0 !important; }
                        .markdown-content img { max-width: 100% !important; border-radius: 0.375rem !important; margin: 1rem 0 !important; }
                        .markdown-content table { width: 100% !important; border-collapse: collapse !important; margin: 1rem 0 !important; font-size: 0.875rem !important; }
                        .markdown-content th, .markdown-content td { border: 1px solid var(--border) !important; padding: 0.5rem !important; text-align: left !important; }
                        .markdown-content th { background: var(--muted) !important; font-weight: 600 !important; }
                    `}} />
                    <div className="flex-1 overflow-y-auto p-4">
                        <div className="markdown-content text-sm leading-relaxed prose prose-sm dark:prose-invert max-w-none">
                            <ReactMarkdown
                                remarkPlugins={[remarkGfm, remarkEmoji, [remarkPlatformAutolinks, autolinkContext]]}
                                rehypePlugins={[rehypeRaw, [rehypeSanitize, releaseNotesSanitizeSchema]]}
                                components={{
                                    a: ({ node, children, ...props }) => {
                                        void node
                                        return (
                                            <a target="_blank" rel="noopener noreferrer" className="not-prose" {...props}>
                                                {normalizeGitHubLinkLabel(children, props.href)}
                                            </a>
                                        )
                                    },
                                    pre: ({ children, ...props }) => {
                                        return <pre className="not-prose" {...props}>{children}</pre>
                                    },
                                    code: ({ node, className, children, ...props }: MarkdownCodeProps) => {
                                        void node
                                        return <code className={className} {...props}>{children}</code>
                                    }
                                }}
                            >
                                {release.body || t('dashboard.releaseNotes.noNotes')}
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
        </Dialog>
    )
}
