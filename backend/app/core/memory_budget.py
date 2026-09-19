"""Worker-wide memory admission budget for dataset-materialising runs (#1998)."""

from __future__ import annotations

import os
import socket
import time
from dataclasses import dataclass
from typing import Any, Final, Protocol

from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger

log = get_logger(__name__)

#: Bounded like every other broker client here (#854): a budget check that can block forever
#: would stall the very worker it is sizing.
_TIMEOUT_S: Final = 2.0
#: Slack on the key TTL so the pair outlives the longest lease it holds.
_KEY_TTL_SLACK_S: Final = 60

_KEY_PREFIX: Final = "dataq:runmem"

#: Reserve under one round trip so two prefork children cannot both read "there is room"
#: and both write. Expired leases are evicted first — an OOM-SIGKILLed child never runs its
#: own release, so the lease, not the process, is what frees the budget.
#:
#: The `used > 0` clause is the admit-alone rule: a run whose estimate exceeds the whole
#: budget can never fit beside anything, so it runs alone rather than waiting forever.
_RESERVE_LUA: Final = """
local zkey, hkey = KEYS[1], KEYS[2]
local now = tonumber(ARGV[1])
local member = ARGV[2]
local amount = tonumber(ARGV[3])
local budget = tonumber(ARGV[4])
local lease = tonumber(ARGV[5])
local key_ttl = tonumber(ARGV[6])
local expired = redis.call('ZRANGEBYSCORE', zkey, '-inf', now)
for i = 1, #expired do
  redis.call('ZREM', zkey, expired[i])
  redis.call('HDEL', hkey, expired[i])
end
redis.call('ZREM', zkey, member)
redis.call('HDEL', hkey, member)
local used = 0
local vals = redis.call('HVALS', hkey)
for i = 1, #vals do used = used + tonumber(vals[i]) end
if used > 0 and used + amount > budget then
  return {0, used}
end
redis.call('ZADD', zkey, now + lease, member)
redis.call('HSET', hkey, member, amount)
redis.call('EXPIRE', zkey, key_ttl)
redis.call('EXPIRE', hkey, key_ttl)
return {1, used + amount}
"""


@dataclass(frozen=True)
class Admission:
    """The verdict on one reservation attempt.

    ``degraded`` is true when the store could not be reached — the request was admitted
    fail-open (ADR 0035's stance: a budget outage must not stop DataQ running checks), and
    ``used_bytes`` is then unknown, not zero.
    """

    admitted: bool
    used_bytes: int | None
    degraded: bool = False


class BudgetClient(Protocol):
    """The three Redis calls this module needs (narrow so tests can substitute)."""

    def eval(self, script: str, numkeys: int, *args: Any) -> Any: ...

    def zrem(self, name: str, *values: Any) -> Any: ...

    def hdel(self, name: str, *keys: Any) -> Any: ...


class MemoryBudget:
    """A leased, worker-scoped byte budget shared by one container's prefork children."""

    def __init__(
        self,
        client: BudgetClient,
        *,
        budget_bytes: int,
        lease_seconds: int,
        scope: str,
        clock: Any = time.time,
    ) -> None:
        self._client = client
        self._budget = budget_bytes
        self._lease = lease_seconds
        self._zkey = f"{_KEY_PREFIX}:{scope}:leases"
        self._hkey = f"{_KEY_PREFIX}:{scope}:amounts"
        self._clock = clock

    @property
    def budget_bytes(self) -> int:
        return self._budget

    def reserve(self, key: str, amount: int) -> Admission:
        """Claim ``amount`` bytes for ``key``, or report that there is no room."""
        try:
            raw = self._client.eval(
                _RESERVE_LUA,
                2,
                self._zkey,
                self._hkey,
                int(self._clock()),
                key,
                max(int(amount), 0),
                self._budget,
                self._lease,
                self._lease + _KEY_TTL_SLACK_S,
            )
            granted, used = int(raw[0]), int(raw[1])
        except Exception:
            log.warning("run_admission_store_unavailable", exc_info=True)
            return Admission(admitted=True, used_bytes=None, degraded=True)
        return Admission(admitted=bool(granted), used_bytes=used)

    def release(self, key: str) -> None:
        """Hand ``key``'s bytes back. A missed release expires with the lease."""
        try:
            self._client.zrem(self._zkey, key)
            self._client.hdel(self._hkey, key)
        except Exception:
            log.warning("run_admission_release_failed", exc_info=True)


_budget: MemoryBudget | None = None


def worker_scope() -> str:
    """The budget's scope key: one budget per worker CONTAINER, shared by its children."""
    return os.environ.get("HOSTNAME") or socket.gethostname()


def get_memory_budget() -> MemoryBudget | None:
    """The process-wide budget, or ``None`` when admission control is switched off."""
    global _budget
    settings = get_settings()
    if settings.run_admission_budget_bytes <= 0:
        return None
    if _budget is None:
        import redis

        client: BudgetClient = redis.from_url(
            settings.redis_url,
            socket_connect_timeout=_TIMEOUT_S,
            socket_timeout=_TIMEOUT_S,
        )
        _budget = MemoryBudget(
            client,
            budget_bytes=settings.run_admission_budget_bytes,
            lease_seconds=settings.run_admission_lease_seconds,
            scope=worker_scope(),
        )
    return _budget


def set_memory_budget_for_testing(budget: MemoryBudget | None) -> None:
    global _budget
    _budget = budget


def reset_memory_budget_state() -> None:
    """Test hook: drop the cached budget so settings changes rebuild it."""
    global _budget
    _budget = None
