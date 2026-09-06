"""Positive and negative probes for the offline Wiki quality gate."""

import tempfile
import unittest
from pathlib import Path

from scripts.check_docs import check_site, check_sources, nav_paths


class DocsChecksTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.site = self.root / "site"
        self.write("index.html", '<h1 id="home">Home</h1>')
        self.write("en/index.html", '<h1 id="home">Home</h1>')

    def write(self, path, text):
        target = self.site / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def test_valid_relative_root_and_same_site_links(self):
        self.write("guide/index.html", '<h1 id="section">Guide</h1>')
        self.write("image.svg", '<svg xmlns="http://www.w3.org/2000/svg"/>')
        self.write(
            "index.html",
            """<h1 id="home">Home</h1><article>
            <a href="guide/#section">Relative</a>
            <a href="/wiki/guide/?q=1#section">Root</a>
            <a href="https://example.com/wiki/guide/#section">Absolute</a>
            <a href="https://other.example/missing">External</a>
            <img src="image.svg" alt="Example" /></article>""",
        )
        self.assertEqual(
            check_site(
                self.site, "https://example.com/wiki/", {"index.html": ["home"]}
            ),
            [],
        )

    def test_missing_page_image_fragment_and_legacy_anchor_fail(self):
        self.write(
            "index.html",
            """<article><a href="missing/">Page</a>
            <a href="#missing">Fragment</a><img src="missing.png" alt="Screenshot"></article>""",
        )
        errors = check_site(
            self.site, "https://example.com/wiki/", {"index.html": ["old"]}
        )
        self.assertTrue(any("missing target missing/" in error for error in errors))
        self.assertTrue(any("missing target missing.png" in error for error in errors))
        self.assertTrue(any("missing fragment" in error for error in errors))
        self.assertTrue(any("legacy anchor missing" in error for error in errors))

    def test_missing_translation_and_unlisted_page_fail(self):
        docs = self.root / "docs"
        docs.mkdir()
        (docs / "index.md").write_text("# Home")
        (docs / "extra.md").write_text("# Extra")
        errors = check_sources(docs, ["index.md"])
        self.assertTrue(any("missing translation partner" in error for error in errors))
        self.assertTrue(any("no navigation entry" in error for error in errors))
        (docs / "index.en.md").write_text("# Home")
        (docs / "extra.en.md").write_text("# Extra")
        self.assertEqual(check_sources(docs, ["index.md", "extra.md"]), [])

    def test_english_content_cannot_silently_link_to_chinese(self):
        self.write(
            "en/index.html", '<article><a href="../">Wrong language</a></article>'
        )
        self.assertTrue(
            any(
                "changes language" in e
                for e in check_site(self.site, "https://example.com/wiki/", {})
            )
        )

    def test_language_switch_outside_article_is_allowed(self):
        self.write("en/index.html", '<nav><a href="../">中文</a></nav>')
        self.assertEqual(check_site(self.site, "https://example.com/wiki/", {}), [])

    def test_blank_image_alt_and_site_escape_fail(self):
        self.write("image.svg", "<svg/>")
        self.write(
            "index.html",
            '<article><img src="image.svg"><a href="../outside">Escape</a></article>',
        )
        errors = check_site(self.site, "https://example.com/wiki/", {})
        self.assertTrue(any("lack alt" in error for error in errors))
        self.assertTrue(any("escapes" in error for error in errors))

    def test_empty_site_and_missing_legacy_page_fail(self):
        errors = check_site(
            self.root / "missing",
            "https://example.com/wiki/",
            {"gone/index.html": ["x"]},
        )
        self.assertTrue(any("homepages" in error for error in errors))
        self.assertTrue(any("legacy page missing" in error for error in errors))

    def test_nested_navigation_and_duplicates(self):
        nav = nav_paths([{"Home": "index.md"}, {"Guides": [{"One": "guide.md"}]}])
        self.assertEqual(nav, ["index.md", "guide.md"])
        self.assertTrue(
            any(
                "duplicate" in error
                for error in check_sources(self.root, ["index.md", "index.md"])
            )
        )


if __name__ == "__main__":
    unittest.main()
