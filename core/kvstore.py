"""Async key-value store для дуэли: Redis или in-memory fallback (локальная разработка)."""
import asyncio
import time

import redis.asyncio as aioredis
from django.conf import settings


class InMemoryKV:
    """Минимальная async-замена Redis для матчмейкинга в dev."""

    def __init__(self):
        self._kv = {}
        self._lists = {}
        self._lock = asyncio.Lock()

    async def get(self, key):
        async with self._lock:
            rec = self._kv.get(key)
            if rec is None:
                return None
            if rec["exp"] and rec["exp"] <= time.monotonic():
                del self._kv[key]
                return None
            return rec["val"]

    async def set(self, key, val, nx=False, ex=None, px=None):
        async with self._lock:
            if nx and key in self._kv and not self._expired_locked(key):
                return False
            ttl = None
            if px:
                ttl = px / 1000
            elif ex:
                ttl = ex
            self._kv[key] = {"val": val, "exp": time.monotonic() + ttl if ttl else None}
            return True

    async def delete(self, *keys):
        async with self._lock:
            n = 0
            for key in keys:
                if key in self._kv:
                    del self._kv[key]
                    n += 1
                if key in self._lists:
                    del self._lists[key]
                    n += 1
            return n

    async def mset(self, mapping):
        async with self._lock:
            for key, val in mapping.items():
                self._kv[key] = {"val": val, "exp": None}

    async def lpush(self, key, val):
        async with self._lock:
            self._lists.setdefault(key, []).insert(0, val)

    async def rpop(self, key):
        async with self._lock:
            lst = self._lists.get(key, [])
            if not lst:
                return None
            return lst.pop()

    async def lrem(self, key, count, val):
        async with self._lock:
            lst = self._lists.get(key, [])
            if not lst:
                return 0
            removed = 0
            while val in lst:
                lst.remove(val)
                removed += 1
                if count and removed >= count:
                    break
            return removed

    async def eval(self, script, numkeys, key, token):
        async with self._lock:
            rec = self._kv.get(key)
            if rec and rec["val"] == token:
                del self._kv[key]
                return 1
            return 0

    async def aclose(self):
        pass

    def _expired_locked(self, key):
        rec = self._kv.get(key)
        if not rec:
            return True
        if rec["exp"] and rec["exp"] <= time.monotonic():
            del self._kv[key]
            return True
        return False


_store = None
_store_kind = None


async def get_store():
    """Redis если доступен, иначе in-memory (только DEBUG)."""
    global _store, _store_kind
    if _store is not None:
        return _store

    if getattr(settings, "REDIS_ENABLED", True):
        try:
            client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            await client.ping()
            _store = client
            _store_kind = "redis"
            return _store
        except Exception:
            if not settings.DEBUG:
                raise ConnectionError(
                    "Redis недоступен. Запустите redis-server или задайте REDIS_URL."
                ) from None

    if not settings.DEBUG:
        raise ConnectionError(
            "Redis недоступен. Запустите redis-server или задайте REDIS_URL."
        )

    _store = InMemoryKV()
    _store_kind = "memory"
    return _store


def store_backend():
    return _store_kind or ("redis" if getattr(settings, "REDIS_ENABLED", True) else "memory")
