"""The Redis **server** stays on 7.x until the Redis 8 upgrade checklist is done.

Redis 8 bundles the Vector Sets module, which carries an unpatched RCE class. Dependabot does
not watch Docker images or IaC engine versions, so without this test a bump would be one quiet
edit. The checklist lives in deploy/README.md ("Upgrading the Redis server").
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]

# Every place the server version is chosen, and how to read it.
_PINS: dict[str, re.Pattern[str]] = {
    "docker-compose.yml": re.compile(r"image:\s*redis:(\d+)"),
    "docker-compose.ghcr.yml": re.compile(r"image:\s*redis:(\d+)"),
    "deploy/terraform/azure/redis.tf": re.compile(r'image\s*=\s*"redis:(\d+)'),
    "deploy/terraform/aws/elasticache.tf": re.compile(r'engine_version\s*=\s*"(\d+)'),
}


@pytest.mark.parametrize("path", sorted(_PINS))
def test_the_redis_server_is_still_on_7(path: str) -> None:
    majors = _PINS[path].findall((_REPO / path).read_text())
    assert majors, f"{path}: no Redis server version found; update this test's pattern"
    assert set(majors) == {"7"}, (
        f"{path} moves the Redis server to {sorted(set(majors))}. Redis 8 bundles the Vector Sets "
        "module (unpatched RCE class). Work through deploy/README.md 'Upgrading the Redis server' "
        "first, then update this test."
    )


def test_no_other_redis_server_image_escapes_the_pin_list() -> None:
    pinned = set(_PINS)
    candidates = [
        p
        for p in [*_REPO.glob("docker-compose*.yml"), *_REPO.glob("deploy/terraform/*/*.tf")]
        if re.search(r'redis:\d|engine\s*=\s*"redis"', p.read_text())
    ]
    unlisted = sorted(str(p.relative_to(_REPO)) for p in candidates)
    assert (
        set(unlisted) <= pinned
    ), f"a Redis server version is set outside the pin list: {unlisted}"
