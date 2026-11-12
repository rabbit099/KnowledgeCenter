import tomllib
from pathlib import Path

import check_release


def make_repo(tmp_path: Path, version: str, sections: tuple[str, str]) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "x"\nversion = "{version}"\n', encoding="utf-8"
    )
    for lang, section in zip(("en", "pl"), sections, strict=True):
        path = tmp_path / "docs" / lang / "changelog.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# Changelog\n\n## [Unreleased]\n\n{section}\n", encoding="utf-8")
    return tmp_path


def test_matching_release_passes(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, "1.2.3", ("## [1.2.3] - 2026-12-01", "## [1.2.3] - 2026-12-01"))
    assert check_release.check("v1.2.3", repo) == []


def test_version_mismatch_is_refused(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, "1.2.2", ("## [1.2.3]", "## [1.2.3]"))
    assert check_release.check("v1.2.3", repo) == [
        "pyproject.toml has version '1.2.2', the tag says '1.2.3'"
    ]


def test_missing_changelog_section_is_refused(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, "1.2.3", ("## [1.2.3]", "## [1.2.2]"))
    assert check_release.check("v1.2.3", repo) == [
        "docs/pl/changelog.md has no section '## [1.2.3]'"
    ]


def test_malformed_tag_is_refused(tmp_path: Path) -> None:
    assert check_release.check("1.2.3", tmp_path) == ["tag '1.2.3' is not of the form vX.Y.Z"]
    assert check_release.check("v1.2", tmp_path) == ["tag 'v1.2' is not of the form vX.Y.Z"]


def test_repository_version_has_changelog_sections() -> None:
    # The version in pyproject.toml always matches a released section in both changelogs:
    # it is only changed in a release commit, together with the changelogs.
    root = Path(check_release.__file__).resolve().parent.parent
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    assert check_release.check(f"v{version}", root) == []
