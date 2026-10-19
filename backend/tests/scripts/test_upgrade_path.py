"""`backend/scripts/upgrade_path.py` — what can be checked without a Postgres (#1704)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from backend.scripts import upgrade_path as up

_REPO_VERSIONS = up.REPO_ROOT / up.VERSIONS_REL


def _migration(path: Path, rev: str, down: str | None) -> None:
    path.write_text(
        f'"""{rev}"""\nrevision = "{rev}"\ndown_revision = {down!r}\n'
        "branch_labels = None\ndepends_on = None\n"
        "def upgrade():\n    pass\n\ndef downgrade():\n    pass\n"
    )


def _fake_git(answers: dict[str, str | Exception]) -> Callable[..., str]:
    def git(*args: str, **_: object) -> str:
        answer = answers[" ".join(args)]
        if isinstance(answer, Exception):
            raise answer
        return answer

    return git


def test_previous_release_tag_prefers_origin_main() -> None:
    git = _fake_git(
        {
            "rev-parse --verify --quiet origin/main^{commit}": "abc",
            "describe --tags --abbrev=0 --match v* origin/main": "v1.2.0",
        }
    )
    assert up.previous_release_tag(git) == "v1.2.0"


def test_previous_release_tag_falls_back_to_head_when_origin_main_is_absent() -> None:
    git = _fake_git(
        {
            "rev-parse --verify --quiet origin/main^{commit}": up.UpgradePathError("no ref"),
            "rev-parse --verify --quiet HEAD^{commit}": "abc",
            "describe --tags --abbrev=0 --match v* HEAD": "v1.1.0",
        }
    )
    assert up.previous_release_tag(git) == "v1.1.0"


def test_previous_release_tag_explains_unfetched_tags() -> None:
    git = _fake_git(
        {
            "rev-parse --verify --quiet origin/main^{commit}": "abc",
            "describe --tags --abbrev=0 --match v* origin/main": up.UpgradePathError("fatal"),
        }
    )
    with pytest.raises(up.UpgradePathError, match="fetch tags"):
        up.previous_release_tag(git)


def test_edited_or_missing_historic_migrations_are_named(tmp_path: Path) -> None:
    tag, head = tmp_path / "tag", tmp_path / "head"
    tag.mkdir()
    head.mkdir()
    _migration(tag / "a_one.py", "a", None)
    _migration(head / "a_one.py", "a", None)
    _migration(tag / "b_two.py", "b", "a")
    _migration(head / "b_two.py", "b", "a")
    (head / "b_two.py").write_text((head / "b_two.py").read_text() + "# edited\n")
    _migration(tag / "c_three.py", "c", "b")
    _migration(head / "d_four.py", "d", "c")

    assert up.changed_historic_migrations(tag, head) == ["b_two.py", "c_three.py"]


def test_identical_trees_report_nothing_changed(tmp_path: Path) -> None:
    tag = tmp_path / "tag"
    tag.mkdir()
    _migration(tag / "a_one.py", "a", None)
    assert up.changed_historic_migrations(tag, tag) == []


def test_single_head_of_a_linear_chain(tmp_path: Path) -> None:
    _migration(tmp_path / "a_one.py", "a1b2c3d4e5f6", None)
    _migration(tmp_path / "b_two.py", "b2c3d4e5f6a1", "a1b2c3d4e5f6")
    assert up.single_head(tmp_path) == "b2c3d4e5f6a1"


def test_single_head_refuses_a_fork(tmp_path: Path) -> None:
    _migration(tmp_path / "a_one.py", "a1b2c3d4e5f6", None)
    _migration(tmp_path / "b_two.py", "b2c3d4e5f6a1", "a1b2c3d4e5f6")
    _migration(tmp_path / "c_three.py", "c3d4e5f6a1b2", "a1b2c3d4e5f6")
    with pytest.raises(up.UpgradePathError, match="2 heads"):
        up.single_head(tmp_path)


def test_the_repo_tree_has_one_head() -> None:
    assert len(up.single_head(_REPO_VERSIONS)) == 12


def test_lost_rows_ignores_new_tables_and_growth() -> None:
    before = {"users": 3, "checks": 8, "runs": 2}
    after = {"users": 3, "checks": 7, "runs": 5, "coverage_settings": 0}
    assert up.lost_rows(before, after) == {"checks": (8, 7)}


def test_maintenance_url_keeps_credentials_and_swaps_the_database() -> None:
    url = up._maintenance_url("postgresql+psycopg2://u:p%40ss@db:5432/dataq_upgrade")
    assert url == "postgresql+psycopg2://u:p%40ss@db:5432/postgres"


def test_main_requires_a_database_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(up.ENV_VAR, raising=False)
    assert up.main([]) == 2
    assert up.ENV_VAR in capsys.readouterr().err


def test_main_reports_a_failed_step(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(**_: object) -> int:
        raise up.UpgradePathError("alembic upgrade head failed (exit 1)")

    monkeypatch.setattr(up, "run", boom)
    assert up.main(["--database-url", "postgresql+psycopg2://u:p@localhost/x"]) == 1
    assert "FAILED: alembic upgrade head failed" in capsys.readouterr().err
