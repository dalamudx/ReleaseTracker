from datetime import datetime, timezone

import pytest

from releasetracker.models import Release, TrackerReleaseNotesConfig
from releasetracker.services.changelog import extract_changelog_content, render_changelog_template


def _release(tag_name="v1.2.3", version="1.2.3"):
    return Release(
        tracker_name="tracker",
        tracker_type="github",
        name=tag_name,
        tag_name=tag_name,
        version=version,
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        url="https://example.test/release",
    )


def test_render_changelog_template_uses_version_placeholders():
    release = _release("v1.2.3", "1.2.3")

    assert (
        render_changelog_template("CHANGELOG/CHANGELOG-{major}.{minor}.md", release)
        == "CHANGELOG/CHANGELOG-1.2.md"
    )
    assert render_changelog_template("docs/releases/{version}.md", release) == "docs/releases/1.2.3.md"
    assert render_changelog_template("changelog/{tag}.md", release) == "changelog/v1.2.3.md"


def test_extract_common_single_file_heading_section():
    content = """# Changelog

## [1.2.3] - 2026-01-01

### Added
- New feature

## [1.2.2]
- Previous
"""

    extracted = extract_changelog_content(
        content,
        _release(),
        TrackerReleaseNotesConfig(source="custom_changelog", changelog_source_key="source-1"),
    )

    assert "## [1.2.3]" in extracted
    assert "New feature" in extracted
    assert "1.2.2" not in extracted


def test_extract_whole_file_mode():
    config = TrackerReleaseNotesConfig(
        source="custom_changelog",
        changelog_source_key="source-1",
        extraction_mode="whole_file",
    )

    assert extract_changelog_content("# v1.2.3\n\nAll notes", _release(), config) == "# v1.2.3\n\nAll notes"


def test_extract_kubernetes_style_subheading():
    content = """# v1.2.3

## Downloads for v1.2.3
Ignore me

## Changelog since v1.2.2
- Important fix

# v1.2.2
- Previous
"""
    config = TrackerReleaseNotesConfig(
        source="custom_changelog",
        changelog_source_key="source-1",
        path_template="CHANGELOG/CHANGELOG-{major}.{minor}.md",
        extraction_mode="version_section_from_subheading",
        version_heading_template="# {tag}",
        subheading_prefix="Changelog since",
    )

    extracted = extract_changelog_content(content, _release(), config)

    assert extracted.startswith("## Changelog since")
    assert "Important fix" in extracted
    assert "Downloads" not in extracted
    assert "Previous" not in extracted


def test_extract_unmatched_heading_raises():
    config = TrackerReleaseNotesConfig(source="custom_changelog", changelog_source_key="source-1")

    with pytest.raises(ValueError, match="No changelog section matched"):
        extract_changelog_content("## 2.0.0\n- Other", _release(), config)


_KUBERNETES_STYLE_CONTENT = """\
# v1.36.1

## Downloads for v1.36.1
Binary downloads here

## Changelog since v1.36.0
- feature A
- fix B

## Changes by Kind

### Bug or Regression
- item

## Dependencies

### Changed
- dep update

# v1.36.0

## Downloads for v1.36.0
Old binary downloads

## Changelog since v1.35.0
- old feature
"""


def _release_136_1():
    return Release(
        tracker_name="tracker",
        tracker_type="github",
        name="v1.36.1",
        tag_name="v1.36.1",
        version="1.36.1",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        url="https://example.test/release",
    )


def test_kubernetes_style_version_section_does_not_include_next_version():
    """Extracting v1.36.1 from a file that also contains v1.36.0 must not bleed into v1.36.0."""
    config = TrackerReleaseNotesConfig(
        source="custom_changelog",
        changelog_source_key="source-1",
        path_template="CHANGELOG/CHANGELOG-{major}.{minor}.md",
        extraction_mode="version_section",
        version_heading_template="# {tag}",
    )

    extracted = extract_changelog_content(_KUBERNETES_STYLE_CONTENT, _release_136_1(), config)

    assert "v1.36.1" in extracted
    assert "feature A" in extracted
    assert "fix B" in extracted
    assert "dep update" in extracted
    # "v1.36.0" appears legitimately in "## Changelog since v1.36.0" within the v1.36.1 section;
    # assert on body content that only exists in the v1.36.0 section.
    assert "old feature" not in extracted
    assert "Old binary downloads" not in extracted
    # The next version heading itself must not appear in the extracted content
    assert "# v1.36.0\n" not in extracted


def test_kubernetes_style_version_section_from_subheading_does_not_include_next_version():
    """version_section_from_subheading must also stop before the next top-level version heading."""
    config = TrackerReleaseNotesConfig(
        source="custom_changelog",
        changelog_source_key="source-1",
        path_template="CHANGELOG/CHANGELOG-{major}.{minor}.md",
        extraction_mode="version_section_from_subheading",
        version_heading_template="# {tag}",
        subheading_prefix="Changelog since",
    )

    extracted = extract_changelog_content(_KUBERNETES_STYLE_CONTENT, _release_136_1(), config)

    assert extracted.startswith("## Changelog since v1.36.0")
    assert "feature A" in extracted
    assert "fix B" in extracted
    # Must not bleed into the next version section
    assert "old feature" not in extracted
    assert "Old binary downloads" not in extracted


# ---------------------------------------------------------------------------
# Heading normalization in template mode
# ---------------------------------------------------------------------------

_KUBERNETES_PLAIN_HEADINGS = """\
# v1.35.5

## Downloads for v1.35.5
Binary downloads here

## Changelog since v1.35.4
- fix A

# v1.35.4

## Changelog since v1.35.3
- fix B
"""

_KUBERNETES_LINK_HEADINGS = """\
# [v1.35.5](https://github.com/kubernetes/kubernetes/blob/master/CHANGELOG/CHANGELOG-1.35.md#v1355)

## Downloads for v1.35.5
Binary downloads here

## Changelog since v1.35.4
- fix A

# [v1.35.4](https://github.com/kubernetes/kubernetes/blob/master/CHANGELOG/CHANGELOG-1.35.md#v1354)

## Changelog since v1.35.3
- fix B
"""

_KUBERNETES_MIXED_HEADINGS = """\
# v1.35.5

## Changelog since v1.35.4
- fix A

# [v1.35.4](https://github.com/kubernetes/kubernetes/blob/master/CHANGELOG/CHANGELOG-1.35.md#v1354)

## Changelog since v1.35.3
- fix B
"""


def _release_k8s(tag: str, version: str):
    return Release(
        tracker_name="tracker",
        tracker_type="github",
        name=tag,
        tag_name=tag,
        version=version,
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        url="https://example.test/release",
    )


def test_template_hash_tag_matches_plain_heading():
    """Template '# {tag}' must match plain headings like '# v1.35.5'."""
    config = TrackerReleaseNotesConfig(
        source="custom_changelog",
        changelog_source_key="source-1",
        extraction_mode="version_section",
        version_heading_template="# {tag}",
    )
    release = _release_k8s("v1.35.5", "1.35.5")
    extracted = extract_changelog_content(_KUBERNETES_PLAIN_HEADINGS, release, config)
    assert "fix A" in extracted
    assert "fix B" not in extracted


def test_template_hash_tag_matches_link_style_heading():
    """Template '# {tag}' must match headings with markdown link syntax like '# [v1.35.5](url)'."""
    config = TrackerReleaseNotesConfig(
        source="custom_changelog",
        changelog_source_key="source-1",
        extraction_mode="version_section",
        version_heading_template="# {tag}",
    )
    release = _release_k8s("v1.35.5", "1.35.5")
    extracted = extract_changelog_content(_KUBERNETES_LINK_HEADINGS, release, config)
    assert "fix A" in extracted
    assert "fix B" not in extracted


def test_template_hash_bracket_tag_bracket_matches_plain_heading():
    """Template '# [{tag}]' must match plain headings like '# v1.35.5' after normalization."""
    config = TrackerReleaseNotesConfig(
        source="custom_changelog",
        changelog_source_key="source-1",
        extraction_mode="version_section",
        version_heading_template="# [{tag}]",
    )
    release = _release_k8s("v1.35.5", "1.35.5")
    extracted = extract_changelog_content(_KUBERNETES_PLAIN_HEADINGS, release, config)
    assert "fix A" in extracted
    assert "fix B" not in extracted


def test_template_hash_bracket_tag_bracket_matches_link_style_heading():
    """Template '# [{tag}]' must match link-style headings like '# [v1.35.5](url)'."""
    config = TrackerReleaseNotesConfig(
        source="custom_changelog",
        changelog_source_key="source-1",
        extraction_mode="version_section",
        version_heading_template="# [{tag}]",
    )
    release = _release_k8s("v1.35.5", "1.35.5")
    extracted = extract_changelog_content(_KUBERNETES_LINK_HEADINGS, release, config)
    assert "fix A" in extracted
    assert "fix B" not in extracted


def test_template_matches_all_versions_in_mixed_heading_styles():
    """Both plain and link-style headings in the same file must be matchable by template."""
    config = TrackerReleaseNotesConfig(
        source="custom_changelog",
        changelog_source_key="source-1",
        extraction_mode="version_section",
        version_heading_template="# {tag}",
    )

    # v1.35.5 has a plain heading
    release_5 = _release_k8s("v1.35.5", "1.35.5")
    extracted_5 = extract_changelog_content(_KUBERNETES_MIXED_HEADINGS, release_5, config)
    assert "fix A" in extracted_5
    assert "fix B" not in extracted_5

    # v1.35.4 has a link-style heading
    release_4 = _release_k8s("v1.35.4", "1.35.4")
    extracted_4 = extract_changelog_content(_KUBERNETES_MIXED_HEADINGS, release_4, config)
    assert "fix B" in extracted_4
    assert "fix A" not in extracted_4
