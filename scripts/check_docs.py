"""Validate bilingual Wiki sources and locally built links without network access."""

from __future__ import annotations

import argparse
import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
LEGACY_PAGES = {"configuration/flow.md", "limitations.md"}


class Page(HTMLParser):
    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.links: list[tuple[str, bool]] = []
        self.missing_alt = 0
        self.in_article = False
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"])
        if tag == "article":
            self.in_article = True
        if tag in {"a", "link"} and values.get("href"):
            self.links.append((values["href"], self.in_article))
        if tag in {"img", "script"} and values.get("src"):
            self.links.append((values["src"], False))
        if tag == "img" and self.in_article and not values.get("alt", "").strip():
            self.missing_alt += 1

    def handle_endtag(self, tag):
        if tag == "article":
            self.in_article = False


def nav_paths(nav) -> list[str]:
    if isinstance(nav, str):
        return [nav] if nav.endswith(".md") else []
    if isinstance(nav, dict):
        return [path for value in nav.values() for path in nav_paths(value)]
    if isinstance(nav, list):
        return [path for value in nav for path in nav_paths(value)]
    return []


def check_sources(docs: Path, nav: list[str]) -> list[str]:
    errors = []
    paths = {path.relative_to(docs).as_posix() for path in docs.rglob("*.md")}
    primary = {path for path in paths if not path.endswith(".en.md")}
    for path in sorted(paths):
        partner = path[:-6] + ".md" if path.endswith(".en.md") else path[:-3] + ".en.md"
        if partner not in paths:
            errors.append(f"{path}: missing translation partner {partner}")
    for path in nav:
        if path not in primary:
            errors.append(
                f"navigation points to a missing/default-language page: {path}"
            )
    for path in sorted(primary - set(nav) - LEGACY_PAGES):
        errors.append(f"page has no navigation entry: {path}")
    if len(nav) != len(set(nav)):
        errors.append("navigation has duplicate page entries")
    return errors


def local_target(site: Path, page: Path, href: str, site_url: str):
    """Resolve relative/root/same-site URLs; ignore other origins and schemes."""
    link = urlsplit(href)
    base = urlsplit(site_url)
    prefix = base.path.rstrip("/") + "/"
    path = unquote(link.path)
    if link.scheme or link.netloc:
        if (link.scheme, link.netloc) != (base.scheme, base.netloc):
            return None
        if not (path == prefix.rstrip("/") or path.startswith(prefix)):
            return None
    if not path:
        target = page
    elif path.startswith("/"):
        if path == prefix.rstrip("/"):
            path = prefix
        if not path.startswith(prefix):
            raise ValueError("root-relative link is outside the site base path")
        target = site / path[len(prefix) :]
    else:
        target = page.parent / path
    target = target.resolve()
    if not target.is_relative_to(site.resolve()):
        raise ValueError("link escapes the built site")
    if target.is_dir():
        target = target / "index.html"
    return target, unquote(link.fragment)


def check_site(site: Path, site_url: str, legacy: dict[str, list[str]]) -> list[str]:
    site = site.resolve()
    pages = {
        path: Page(path.read_text(encoding="utf-8")) for path in site.rglob("*.html")
    }
    errors = []
    if site / "index.html" not in pages or site / "en/index.html" not in pages:
        errors.append("built site must contain both Chinese and English homepages")
    for path, page in sorted(pages.items()):
        relative = path.relative_to(site).as_posix()
        if page.missing_alt:
            errors.append(
                f"{relative}: {page.missing_alt} content images lack alt text"
            )
        for href, in_article in page.links:
            try:
                resolved = local_target(site, path, href, site_url)
            except ValueError as exc:
                errors.append(f"{relative}: {href}: {exc}")
                continue
            if resolved is None:
                continue
            target, fragment = resolved
            if not target.is_file():
                errors.append(f"{relative}: missing target {href}")
            elif target in pages:
                if fragment and fragment not in pages[target].ids:
                    errors.append(f"{relative}: missing fragment {href}")
                if in_article:
                    target_relative = target.relative_to(site).as_posix()
                    if relative.startswith("en/") != target_relative.startswith("en/"):
                        errors.append(
                            f"{relative}: content link changes language: {href}"
                        )
    for relative, ids in legacy.items():
        path = site / relative
        if path not in pages:
            errors.append(f"legacy page missing: {relative}")
            continue
        for anchor in ids:
            if anchor not in pages[path].ids:
                errors.append(f"legacy anchor missing: {relative}#{anchor}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-dir", type=Path, default=ROOT / "site")
    args = parser.parse_args()
    # Imported only by the CLI; unit tests need no documentation dependencies.
    from mkdocs.config import load_config

    config = load_config(str(ROOT / "mkdocs.yml"))
    legacy = json.loads((ROOT / "scripts/docs-legacy-anchors.json").read_text())
    nav = nav_paths(config.nav)
    errors = check_sources(Path(config.docs_dir), nav)
    errors += check_site(args.site_dir, config.site_url, legacy)
    if errors:
        print("Wiki checks failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        f"Wiki checks passed: {len(nav)} bilingual topics; local links, images, languages, "
        f"and {sum(map(len, legacy.values()))} legacy anchors verified."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
