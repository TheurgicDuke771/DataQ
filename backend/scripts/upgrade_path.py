"""The version-to-version upgrade-path gate (#1704).

Brings a fresh database to the PREVIOUS release's schema head, seeds it with that release's
own demo data, upgrades it to the current head, downgrades back to the release head, and
upgrades once more — the exact sequence an operator's rollback follows. Any error fails it.

    UPGRADE_PATH_DATABASE_URL=postgresql+psycopg2://u:p@localhost:5432/dataq_upgrade \\
        python -m backend.scripts.upgrade_path [--tag v1.2.0] [--keep-db] [--skip-seed]

The previous release is `git describe --tags --abbrev=0 --match 'v*'` on `origin/main` (or
HEAD when that ref is absent), so the baseline moves forward with every release tag. The
release's migration files must still be present and byte-identical at HEAD: a deployed
database already ran them, so an edited historic migration is a failure, not a warning.
"""

from __future__ import annotations

import argparse
import filecmp
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable
from io import BytesIO
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
VERSIONS_REL = Path("backend/alembic/versions")

ENV_VAR = "UPGRADE_PATH_DATABASE_URL"

Echo = Callable[[str], None]


class UpgradePathError(RuntimeError):
    pass


def _git_executable() -> str:
    git = shutil.which("git")
    if git is None:
        raise UpgradePathError("git is not on PATH")
    return git


def _git(*args: str, cwd: Path = REPO_ROOT) -> str:
    result = subprocess.run(  # noqa: S603
        [_git_executable(), *args], cwd=cwd, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise UpgradePathError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def previous_release_tag(git: Callable[..., str] = _git) -> str:
    """The newest `v*` tag reachable from `origin/main`, else from HEAD."""
    for ref in ("origin/main", "HEAD"):
        try:
            git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        except UpgradePathError:
            continue
        try:
            return git("describe", "--tags", "--abbrev=0", "--match", "v*", ref)
        except UpgradePathError as exc:
            raise UpgradePathError(
                f"no v* tag reachable from {ref} — fetch tags first "
                "(`git fetch --tags origin`, or checkout with fetch-depth 0)"
            ) from exc
    raise UpgradePathError("neither origin/main nor HEAD resolves to a commit")


def extract_tree(tag: str, path: str, dest: Path, git: Callable[..., str] = _git) -> Path:
    """`git archive <tag> <path>` unpacked under `dest`; returns the unpacked path."""
    result = subprocess.run(  # noqa: S603
        [_git_executable(), "archive", "--format=tar", tag, path],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise UpgradePathError(f"git archive {tag} {path} failed: {result.stderr.decode().strip()}")
    with tarfile.open(fileobj=BytesIO(result.stdout)) as tar:
        tar.extractall(dest, filter="data")
    return dest / path


def changed_historic_migrations(tag_versions: Path, head_versions: Path) -> list[str]:
    """Release migration files missing or edited at HEAD."""
    changed: list[str] = []
    for file in sorted(tag_versions.glob("*.py")):
        current = head_versions / file.name
        if not current.is_file() or not filecmp.cmp(file, current, shallow=False):
            changed.append(file.name)
    return changed


def single_head(versions_dir: Path) -> str:
    """The one head revision of a migration directory."""
    config = Config()
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("version_locations", str(versions_dir))
    config.set_main_option("path_separator", "os")
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise UpgradePathError(f"{versions_dir} has {len(heads)} heads: {heads}")
    return heads[0]


def _maintenance_url(url: str) -> str:
    return make_url(url).set(database="postgres").render_as_string(hide_password=False)


def recreate_database(url: str, *, echo: Echo) -> None:
    name = make_url(url).database
    if not name:
        raise UpgradePathError(f"{ENV_VAR} must name a database")
    engine = create_engine(_maintenance_url(url), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        engine.dispose()
    echo(f"created {name}")


def drop_database(url: str, *, echo: Echo) -> None:
    name = make_url(url).database
    engine = create_engine(_maintenance_url(url), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        engine.dispose()
    echo(f"dropped {name}")


def current_revision(url: str) -> str | None:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            value = conn.scalar(text("SELECT version_num FROM alembic_version"))
            return str(value) if value is not None else None
    finally:
        engine.dispose()


def row_counts(url: str) -> dict[str, int]:
    """Row count per public table — the data-preservation witness across the cycle."""
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            tables = conn.scalars(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                    "AND tablename <> 'alembic_version' ORDER BY tablename"
                )
            ).all()
            return {
                t: int(conn.scalar(text(f'SELECT count(*) FROM "{t}"')) or 0)  # noqa: S608
                for t in tables
            }
    finally:
        engine.dispose()


def lost_rows(before: dict[str, int], after: dict[str, int]) -> dict[str, tuple[int, int]]:
    """Tables that existed before and hold fewer rows after."""
    return {t: (n, after[t]) for t, n in before.items() if t in after and after[t] < n}


def _run(argv: list[str], *, cwd: Path, env: dict[str, str], label: str, echo: Echo) -> None:
    echo(f"-> {label}")
    result = subprocess.run(argv, cwd=cwd, env=env, check=False)  # noqa: S603
    if result.returncode != 0:
        raise UpgradePathError(f"{label} failed (exit {result.returncode})")


def _alembic(target: str, direction: str, url: str, *, echo: Echo) -> None:
    env = {**os.environ, "DATABASE_URL": url}
    _run(
        [sys.executable, "-m", "alembic", direction, target],
        cwd=BACKEND_DIR,
        env=env,
        label=f"alembic {direction} {target}",
        echo=echo,
    )


def _expect_revision(url: str, expected: str, *, step: str) -> None:
    actual = current_revision(url)
    if actual != expected:
        raise UpgradePathError(f"after {step}: alembic_version is {actual}, expected {expected}")


def seed_at_tag(tag: str, url: str, workdir: Path, *, echo: Echo) -> None:
    """Run the release's own `seed_dev` against the release-schema database."""
    tree = workdir / "tree"
    tree.mkdir()
    extract_tree(tag, "backend", tree)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DATAQ_", "AUTH_", "SECRET_"))}
    env.update(
        {
            "DATABASE_URL": url,
            "SECRET_STORE": "env",
            "PYTHONPATH": str(tree),
            "PYTHONSAFEPATH": "1",
        }
    )
    _run(
        [sys.executable, "-m", "backend.scripts.seed_dev"],
        cwd=tree,
        env=env,
        label=f"seed_dev at {tag}",
        echo=echo,
    )


def run(*, url: str, tag: str | None, keep_db: bool, skip_seed: bool, echo: Echo = print) -> int:
    tag = tag or previous_release_tag()
    echo(f"previous release: {tag}")
    with tempfile.TemporaryDirectory(prefix="dataq-upgrade-path-") as tmp:
        workdir = Path(tmp)
        tag_versions = extract_tree(tag, str(VERSIONS_REL), workdir / "tag")
        edited = changed_historic_migrations(tag_versions, REPO_ROOT / VERSIONS_REL)
        if edited:
            raise UpgradePathError(
                f"migrations shipped in {tag} are missing or edited at HEAD: {', '.join(edited)}"
            )
        tag_head = single_head(tag_versions)
        head = single_head(REPO_ROOT / VERSIONS_REL)
        echo(f"{tag} head: {tag_head}; current head: {head}")

        recreate_database(url, echo=echo)
        try:
            _alembic(tag_head, "upgrade", url, echo=echo)
            _expect_revision(url, tag_head, step=f"upgrade to {tag}")
            if not skip_seed:
                seed_at_tag(tag, url, workdir, echo=echo)
            baseline = row_counts(url)
            echo(f"seeded {sum(baseline.values())} rows across {len(baseline)} tables")

            _alembic("head", "upgrade", url, echo=echo)
            _expect_revision(url, head, step="upgrade head")
            if lost := lost_rows(baseline, row_counts(url)):
                raise UpgradePathError(f"upgrade head lost rows: {lost}")

            _alembic(tag_head, "downgrade", url, echo=echo)
            _expect_revision(url, tag_head, step=f"downgrade to {tag}")
            if lost := lost_rows(baseline, row_counts(url)):
                raise UpgradePathError(f"downgrade to {tag_head} lost rows: {lost}")

            _alembic("head", "upgrade", url, echo=echo)
            _expect_revision(url, head, step="re-upgrade head")
        finally:
            if not keep_db:
                drop_database(url, echo=echo)
    echo(f"upgrade path {tag} ({tag_head}) -> {head} -> {tag_head} -> {head}: OK")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="upgrade_path", description=__doc__)
    parser.add_argument("--tag", help="release tag to start from (default: newest v* on main)")
    parser.add_argument(
        "--database-url", default=os.environ.get(ENV_VAR), help=f"scratch DB (default: ${ENV_VAR})"
    )
    parser.add_argument("--keep-db", action="store_true", help="leave the scratch DB in place")
    parser.add_argument("--skip-seed", action="store_true", help="migrate an empty schema only")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.database_url:
        print(f"{ENV_VAR} (or --database-url) is required", file=sys.stderr)
        return 2
    try:
        return run(
            url=args.database_url, tag=args.tag, keep_db=args.keep_db, skip_seed=args.skip_seed
        )
    except UpgradePathError as exc:
        print(f"upgrade path FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
