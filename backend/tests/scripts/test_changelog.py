"""`scripts/changelog.py` — fragments, the rendered Unreleased section, assemble and check."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "scripts" / "changelog.py"
_HOOK = _ROOT / "docs" / "hooks" / "changelog_fragments.py"


def _load_hook() -> ModuleType:
    spec = importlib.util.spec_from_file_location("dataq_changelog_hook_under_test", _HOOK)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


hook = _load_hook()
changelog = hook._changelog_module()  # the hook's loader is the one loader

_INTRO = "# Changelog\n\nIntro.\n\n"
_OLD = "## v1.2.0 — 2026-10-04\n\n### Added\n\n- **Old.** Entry.\n"


def _page(tmp_path: Path) -> Path:
    page = tmp_path / "changelog.md"
    page.write_text(_INTRO + changelog.MARKER + "\n\n" + _OLD, encoding="utf-8")
    return page


def _frag(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(exist_ok=True)
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


class TestFilenameGrammar:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("2395.changed.md", (2395, 0, "changed")),
            ("2440-2.added.md", (2440, 2, "added")),
            ("7.breaking.md", (7, 0, "breaking")),
            ("12.security.md", (12, 0, "security")),
            ("12.fixed.md", (12, 0, "fixed")),
        ],
    )
    def test_accepts_the_grammar(self, name: str, expected: tuple[int, int, str]) -> None:
        assert changelog.parse_name(name) == expected

    @pytest.mark.parametrize(
        "name",
        [
            "2395.md",
            "2395.removed.md",
            "2395.Added.md",
            "abc.added.md",
            "0.added.md",
            "2395-0.added.md",
            "2395-a.added.md",
            "2395.added.txt",
            "2395.added",
        ],
    )
    def test_rejects_anything_else(self, name: str) -> None:
        with pytest.raises(changelog.FragmentError):
            changelog.parse_name(name)


class TestFragmentBody:
    def test_reads_the_bullet_text(self, tmp_path: Path) -> None:
        path = _frag(tmp_path, "1.added.md", "**Lead.** First line\nsecond line\n")
        fragment = changelog.load_fragment(path)
        assert fragment.body == "**Lead.** First line\nsecond line"

    @pytest.mark.parametrize(
        ("body", "why"),
        [
            ("", "empty"),
            ("   \n", "empty"),
            ("- **Lead.** text", "leading '- '"),
            ("### Added\n**Lead.**", "heading"),
            ("**Lead.** fixes #2395", "issue/PR shorthand"),
            ("**Lead.** fixes #35;2395", "Mermaid-escaped"),
            ("**Lead.** see github.com/x/y/pull/12", "issue/PR link"),
            ("**Lead.** see [the rules](../../CONTRIBUTING.md)", "unpublished internal doc"),
        ],
    )
    def test_rejects_a_malformed_body(self, tmp_path: Path, body: str, why: str) -> None:
        path = _frag(tmp_path, "1.added.md", body)
        with pytest.raises(changelog.FragmentError, match=why):
            changelog.load_fragment(path)

    def test_only_fragments_and_the_readme_may_live_in_the_directory(self, tmp_path: Path) -> None:
        _frag(tmp_path, "README.md", "# how to")
        _frag(tmp_path, "1.added.md", "**A.** a")
        assert [f.number for f in changelog.load_fragments(tmp_path)] == [1]
        _frag(tmp_path, "notes.md", "x")
        with pytest.raises(changelog.FragmentError, match=r"notes\.md"):
            changelog.load_fragments(tmp_path)

    def test_a_missing_directory_is_an_error(self, tmp_path: Path) -> None:
        with pytest.raises(changelog.FragmentError, match="missing"):
            changelog.load_fragments(tmp_path / "nope")


class TestRender:
    def test_sections_come_out_in_release_order_and_newest_number_first(
        self, tmp_path: Path
    ) -> None:
        _frag(tmp_path, "10.fixed.md", "**F10.** x")
        _frag(tmp_path, "20.added.md", "**A20.** x")
        _frag(tmp_path, "20-2.added.md", "**A20b.** x")
        _frag(tmp_path, "30.added.md", "**A30.** x")
        _frag(tmp_path, "5.breaking.md", "**B5.** x")
        _frag(tmp_path, "6.security.md", "**S6.** x")
        _frag(tmp_path, "7.changed.md", "**C7.** x")
        rendered = changelog.render_unreleased(changelog.load_fragments(tmp_path))
        assert rendered == (
            "## Unreleased\n\n"
            "### Breaking\n\n- **B5.** x\n\n"
            "### Security\n\n- **S6.** x\n\n"
            "### Added\n\n- **A30.** x\n- **A20.** x\n- **A20b.** x\n\n"
            "### Changed\n\n- **C7.** x\n\n"
            "### Fixed\n\n- **F10.** x"
        )

    def test_continuation_lines_are_indented_under_the_bullet(self, tmp_path: Path) -> None:
        _frag(tmp_path, "1.added.md", "**A.** first\nsecond\n\nthird")
        rendered = changelog.render_sections(changelog.load_fragments(tmp_path))
        assert rendered == "### Added\n\n- **A.** first\n  second\n\n  third"

    def test_no_fragments_still_renders_the_heading(self, tmp_path: Path) -> None:
        tmp_path.joinpath("README.md").write_text("x")
        rendered = changelog.render_unreleased(changelog.load_fragments(tmp_path))
        assert rendered == "## Unreleased\n\n" + changelog.EMPTY_UNRELEASED

    def test_injecting_is_idempotent_and_leaves_the_rest_of_the_page_alone(
        self, tmp_path: Path
    ) -> None:
        page = _page(tmp_path)
        _frag(tmp_path / "d", "1.added.md", "**A.** a")
        fragments = changelog.load_fragments(tmp_path / "d")
        once = changelog.inject_unreleased(page.read_text(), fragments)
        assert once == (
            _INTRO + changelog.MARKER + "\n\n## Unreleased\n\n### Added\n\n- **A.** a\n\n" + _OLD
        )
        assert changelog.inject_unreleased(once, fragments) == once

    def test_a_page_without_the_marker_is_refused(self) -> None:
        with pytest.raises(changelog.FragmentError, match=r"changelog\.d:unreleased"):
            changelog.inject_unreleased("# Changelog\n", [])

    def test_the_real_fragments_render_against_the_real_page(self) -> None:
        fragments = changelog.load_fragments()
        page = (_ROOT / "docs/site/reference/changelog.md").read_text(encoding="utf-8")
        assert "## Unreleased" not in page, "the committed page carries only the marker"
        assert "## Unreleased" in changelog.inject_unreleased(page, fragments)


class TestAssemble:
    def test_moves_the_fragments_into_a_dated_section_below_the_marker(
        self, tmp_path: Path
    ) -> None:
        page = _page(tmp_path)
        d = tmp_path / "d"
        _frag(d, "1.fixed.md", "**F.** f")
        _frag(d, "2.breaking.md", "**B.** b")
        heading = changelog.assemble("1.3.0", "2026-11-29", page, d)
        assert heading == "## v1.3.0 — 2026-11-29"
        assert page.read_text() == (
            _INTRO
            + changelog.MARKER
            + "\n\n## v1.3.0 — 2026-11-29\n\n### Breaking\n\n- **B.** b\n\n"
            "### Fixed\n\n- **F.** f\n\n" + _OLD
        )
        assert sorted(p.name for p in d.iterdir()) == []

    def test_keeps_the_readme_and_accepts_a_v_prefix(self, tmp_path: Path) -> None:
        page = _page(tmp_path)
        d = tmp_path / "d"
        _frag(d, "README.md", "# x")
        _frag(d, "1.added.md", "**A.** a")
        changelog.assemble("v1.3.0", "2026-11-29", page, d)
        assert [p.name for p in d.iterdir()] == ["README.md"]

    def test_refuses_a_second_section_for_the_same_version(self, tmp_path: Path) -> None:
        page = _page(tmp_path)
        d = tmp_path / "d"
        _frag(d, "1.added.md", "**A.** a")
        with pytest.raises(changelog.FragmentError, match="already has"):
            changelog.assemble("1.2.0", "2026-10-04", page, d)
        assert d.joinpath("1.added.md").exists()

    def test_a_previewed_unreleased_block_is_replaced_not_kept(self, tmp_path: Path) -> None:
        page = _page(tmp_path)
        d = tmp_path / "d"
        _frag(d, "1.added.md", "**A.** a")
        page.write_text(changelog.inject_unreleased(page.read_text(), changelog.load_fragments(d)))
        changelog.assemble("1.3.0", "2026-11-29", page, d)
        text = page.read_text()
        assert "## Unreleased" not in text
        assert text.count("- **A.** a") == 1

    def test_refuses_with_nothing_to_assemble(self, tmp_path: Path) -> None:
        page = _page(tmp_path)
        (tmp_path / "d").mkdir()
        with pytest.raises(changelog.FragmentError, match="nothing to assemble"):
            changelog.assemble("1.3.0", "2026-11-29", page, tmp_path / "d")

    @pytest.mark.parametrize(("version", "date"), [("1.3", "2026-11-29"), ("1.3.0", "29/11/2026")])
    def test_refuses_a_malformed_version_or_date(
        self, tmp_path: Path, version: str, date: str
    ) -> None:
        page = _page(tmp_path)
        d = tmp_path / "d"
        _frag(d, "1.added.md", "**A.** a")
        with pytest.raises(changelog.FragmentError):
            changelog.assemble(version, date, page, d)


class TestPathFilter:
    @pytest.mark.parametrize(
        "path",
        [
            "backend/app/api/v1/runs.py",
            "frontend/src/pages/Runs.tsx",
            "packages/dataq-client/src/x.py",
            "integrations/airflow/dataq_airflow_callback.py",
            "deploy/terraform/aws/main.tf",
            "docs/site/guides/features.md",
            "docs/site/get-started/install.md",
            "docs/site/reference/api-compatibility.md",
        ],
    )
    def test_user_facing(self, path: str) -> None:
        assert changelog.is_user_facing(path)

    @pytest.mark.parametrize(
        "path",
        [
            "docs/site/reference/changelog.md",
            "backend/tests/api/test_runs.py",
            "frontend/src/pages/Runs.test.tsx",
            "frontend/src/__tests__/x.ts",
            "docs/site/adr/0001.md",
            "docs/progress.md",
            "scripts/changelog.py",
            ".github/workflows/ci.yml",
            "CONTRIBUTING.md",
        ],
    )
    def test_not_user_facing(self, path: str) -> None:
        assert not changelog.is_user_facing(path)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
            "HOME": str(cwd),
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        },
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "changelog.d").mkdir()
    (tmp_path / "changelog.d" / "README.md").write_text("# x")
    (tmp_path / "backend" / "app").mkdir(parents=True)
    (tmp_path / "backend" / "app" / "a.py").write_text("a = 1\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    return tmp_path


def _commit(repo: Path, path: str, text: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    _git(repo, "add", path)
    _git(repo, "commit", "-q", "-m", f"touch {path}")


class TestCheck:
    def test_a_user_facing_change_without_a_fragment_fails(self, repo: Path) -> None:
        _commit(repo, "backend/app/a.py", "a = 2\n")
        problems = changelog.check("main", False, repo)
        assert len(problems) == 1
        assert "backend/app/a.py" in problems[0]
        assert "no-changelog" in problems[0]

    def test_a_fragment_satisfies_it(self, repo: Path) -> None:
        _commit(repo, "backend/app/a.py", "a = 2\n")
        _commit(repo, "changelog.d/9.fixed.md", "**F.** f\n")
        assert changelog.check("main", False, repo) == []

    def test_editing_an_existing_fragment_counts_too(self, repo: Path) -> None:
        _git(repo, "checkout", "-q", "main")
        _commit(repo, "changelog.d/9.added.md", "**A.** a\n")
        _git(repo, "checkout", "-q", "-b", "phase-2")
        _commit(repo, "backend/app/a.py", "a = 2\n")
        _commit(repo, "changelog.d/9.added.md", "**A.** a, and more\n")
        assert changelog.check("main", False, repo) == []

    def test_against_a_merge_ref_only_the_branch_side_counts(self, repo: Path) -> None:
        """CI diffs HEAD^1...HEAD on the PR's merge commit; main's newer commits stay out."""
        _commit(repo, "docs/progress.md", "x\n")
        _git(repo, "checkout", "-q", "main")
        _commit(repo, "backend/app/a.py", "a = 3\n")
        _git(repo, "checkout", "-q", "-b", "merge-ref")
        _git(repo, "merge", "-q", "--no-ff", "-m", "merge", "feature")
        assert changelog.check("HEAD^1", False, repo) == []

    def test_the_exemption_waives_the_missing_fragment(self, repo: Path) -> None:
        _commit(repo, "backend/app/a.py", "a = 2\n")
        assert changelog.check("main", True, repo) == []

    def test_a_non_user_facing_change_needs_nothing(self, repo: Path) -> None:
        _commit(repo, "backend/tests/test_a.py", "x = 1\n")
        _commit(repo, "docs/progress.md", "x\n")
        assert changelog.check("main", False, repo) == []

    def test_a_malformed_fragment_fails_even_when_exempt(self, repo: Path) -> None:
        _commit(repo, "changelog.d/9.removed.md", "**F.** f\n")
        problems = changelog.check("main", True, repo)
        assert len(problems) == 1
        assert "9.removed.md" in problems[0]

    def test_main_moving_ahead_does_not_count_against_the_branch(self, repo: Path) -> None:
        _commit(repo, "docs/progress.md", "x\n")
        _git(repo, "checkout", "-q", "main")
        _commit(repo, "backend/app/a.py", "a = 3\n")
        _git(repo, "checkout", "-q", "feature")
        assert changelog.check("main", False, repo) == []

    def test_a_deleted_fragment_is_not_an_added_one(self, repo: Path) -> None:
        _git(repo, "checkout", "-q", "main")
        _commit(repo, "changelog.d/9.fixed.md", "**F.** f\n")
        _git(repo, "checkout", "-q", "-b", "release")
        (repo / "changelog.d" / "9.fixed.md").unlink()
        _git(repo, "add", "-A")
        _commit(repo, "backend/app/a.py", "a = 2\n")
        problems = changelog.check("main", False, repo)
        assert len(problems) == 1


class TestCli:
    def test_lint_passes_on_the_repo(self) -> None:
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "lint"], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout

    def test_render_unreleased_prints_the_section(self) -> None:
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "render-unreleased"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("## Unreleased\n")

    def test_check_against_a_nonexistent_ref_fails_loudly(self) -> None:
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "check", "--base", "no-such-ref-xyz"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 1
        assert "changelog:" in result.stderr
