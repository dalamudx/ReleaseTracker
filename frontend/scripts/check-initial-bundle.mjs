#!/usr/bin/env node

import { readFile, readdir, stat } from "node:fs/promises"
import { gzipSync } from "node:zlib"
import path from "node:path"

const distDir = path.resolve(process.argv[2] ?? "dist")
const indexPath = path.join(distDir, "index.html")
const MAX_INITIAL_RAW_BYTES = readBudget("MAX_INITIAL_RAW_BYTES", 900 * 1024)
const MAX_INITIAL_GZIP_BYTES = readBudget("MAX_INITIAL_GZIP_BYTES", 300 * 1024)
const MAX_ASSET_RAW_BYTES = readBudget("MAX_ASSET_RAW_BYTES", 400 * 1024)
const MAX_MARKDOWN_VENDOR_RAW_BYTES = readBudget("MAX_MARKDOWN_VENDOR_RAW_BYTES", 650 * 1024)

function readBudget(name, fallback) {
    const value = process.env[name]
    if (value === undefined) return fallback

    const parsed = Number(value)
    if (!Number.isSafeInteger(parsed) || parsed <= 0) {
        throw new Error(`${name} must be a positive integer`)
    }
    return parsed
}

function extractAttribute(tag, name) {
    const match = tag.match(new RegExp(`\\b${name}=["']([^"']+)["']`, "i"))
    return match?.[1] ?? null
}

function extractInitialJavaScriptReferences(html) {
    const references = []

    for (const match of html.matchAll(/<script\b[^>]*>/gi)) {
        const source = extractAttribute(match[0], "src")
        if (source?.includes(".js")) references.push(source)
    }

    for (const match of html.matchAll(/<link\b[^>]*>/gi)) {
        const rel = extractAttribute(match[0], "rel")
        const href = extractAttribute(match[0], "href")
        if (rel?.split(/\s+/).includes("modulepreload") && href?.includes(".js")) {
            references.push(href)
        }
    }

    return [...new Set(references)]
}

function relativeAssetPath(reference) {
    const pathname = reference.split("?", 1)[0]
    if (!pathname.startsWith("./assets/")) {
        throw new Error(`initial JavaScript asset must be relative to the application base: ${reference}`)
    }
    return pathname.slice(2)
}

async function listJavaScriptAssets(directory) {
    const entries = await readdir(directory, { withFileTypes: true })
    const nested = await Promise.all(entries.map(async (entry) => {
        const entryPath = path.join(directory, entry.name)
        if (entry.isDirectory()) return listJavaScriptAssets(entryPath)
        return entry.isFile() && entry.name.endsWith(".js") ? [entryPath] : []
    }))
    return nested.flat()
}

function formatBytes(value) {
    return `${(value / 1024).toFixed(1)} KiB`
}

async function main() {
    const html = await readFile(indexPath, "utf8")
    const failures = []
    if (!html.includes("<!-- APP_BASE_HREF -->")) {
        failures.push("index.html must preserve the APP_BASE_HREF runtime deployment marker")
    }

    const references = extractInitialJavaScriptReferences(html)
    if (references.length === 0) {
        failures.push("index.html does not reference an initial JavaScript asset")
    }
    for (const reference of references) {
        if (/(chart|markdown)-vendor/i.test(reference)) {
            failures.push(`heavy feature bundle is preloaded by index.html: ${reference}`)
        }
    }

    let initialRawBytes = 0
    let initialGzipBytes = 0
    for (const reference of references) {
        try {
            const assetPath = path.resolve(distDir, relativeAssetPath(reference))
            if (!assetPath.startsWith(`${distDir}${path.sep}`)) {
                throw new Error("resolved path escapes dist directory")
            }
            const bytes = await readFile(assetPath)
            initialRawBytes += bytes.length
            initialGzipBytes += gzipSync(bytes, { level: 9 }).length
        } catch (error) {
            failures.push(`cannot inspect initial asset ${reference}: ${error.message}`)
        }
    }

    if (initialRawBytes > MAX_INITIAL_RAW_BYTES) {
        failures.push(
            `initial JavaScript is ${formatBytes(initialRawBytes)}; budget is ${formatBytes(MAX_INITIAL_RAW_BYTES)}`,
        )
    }
    if (initialGzipBytes > MAX_INITIAL_GZIP_BYTES) {
        failures.push(
            `initial JavaScript gzip is ${formatBytes(initialGzipBytes)}; budget is ${formatBytes(MAX_INITIAL_GZIP_BYTES)}`,
        )
    }

    const markdownVendorAssets = []
    for (const assetPath of await listJavaScriptAssets(path.join(distDir, "assets"))) {
        const assetStats = await stat(assetPath)
        const relativePath = path.relative(distDir, assetPath)
        const isMarkdownVendor = /^markdown-vendor-.*\.js$/i.test(path.basename(assetPath))
        if (isMarkdownVendor) markdownVendorAssets.push(relativePath)

        // Release Notes is lazy-loaded, so its cohesive dependency graph may be
        // larger than an initial-route asset. Do not split it by size: Rolldown
        // can otherwise create circular chunks that fail during initialization.
        const rawBudget = isMarkdownVendor ? MAX_MARKDOWN_VENDOR_RAW_BYTES : MAX_ASSET_RAW_BYTES
        if (assetStats.size > rawBudget) {
            failures.push(
                `${relativePath} is ${formatBytes(assetStats.size)}; single-asset budget is ${formatBytes(rawBudget)}`,
            )
        }
    }

    if (markdownVendorAssets.length > 1) {
        failures.push(
            `Markdown vendor graph must remain cohesive; found ${markdownVendorAssets.length} chunks: ${markdownVendorAssets.join(", ")}`,
        )
    }

    if (failures.length > 0) {
        throw new Error(`Initial bundle budget failed:\n- ${failures.join("\n- ")}`)
    }

    console.log(
        `Initial bundle budget passed: ${references.length} assets, ${formatBytes(initialRawBytes)} raw, ${formatBytes(initialGzipBytes)} gzip`,
    )
}

main().catch((error) => {
    console.error(error.message)
    process.exitCode = 1
})
