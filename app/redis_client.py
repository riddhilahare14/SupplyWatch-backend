"""
Redis client with in-memory dict fallback.
Provides helper functions for per-shipment location, state, and features.
"""

import json
import logging
from typing import Any, Dict, Optional
from datetime import datetime

from app.config import settings

logger = logging.getLogger(__name__)

# ── In-memory fallback ────────────────────────────────────────────────────

_memory_store: Dict[str, str] = {}


class MemoryCache:
    """Dict-based Redis substitute for local dev without Redis."""

    async def get(self, key: str) -> Optional[str]:
        return _memory_store.get(key)

    async def set(self, key: str, value: str, ex: int = None) -> None:
        _memory_store[key] = value

    async def delete(self, key: str) -> None:
        _memory_store.pop(key, None)

    async def keys(self, pattern: str = "*") -> list:
        import fnmatch
        return [k for k in _memory_store if fnmatch.fnmatch(k, pattern)]

    async def lpush(self, key: str, *values: str) -> None:
        existing = json.loads(_memory_store.get(key, "[]"))
        for v in values:
            existing.insert(0, v)
        _memory_store[key] = json.dumps(existing)

    async def lrange(self, key: str, start: int, stop: int) -> list:
        existing = json.loads(_memory_store.get(key, "[]"))
        if stop == -1:
            return existing[start:]
        return existing[start : stop + 1]

    async def ltrim(self, key: str, start: int, stop: int) -> None:
        existing = json.loads(_memory_store.get(key, "[]"))
        if stop == -1:
            _memory_store[key] = json.dumps(existing[start:])
        else:
            _memory_store[key] = json.dumps(existing[start : stop + 1])

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        pass


# ── Redis connection ──────────────────────────────────────────────────────

_redis_client = None


async def get_redis():
    """Return the Redis client (or in-memory fallback)."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client

    if settings.USE_MEMORY_CACHE or settings.USE_SQLITE:
        logger.info("Using in-memory cache (no Redis)")
        _redis_client = MemoryCache()
        return _redis_client

    try:
        import redis.asyncio as aioredis
        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
        )
        await _redis_client.ping()
        logger.info("Connected to Redis at %s", settings.REDIS_URL)
        return _redis_client
    except Exception as e:
        logger.warning("Redis unavailable (%s), falling back to memory cache", e)
        _redis_client = MemoryCache()
        return _redis_client


async def close_redis():
    """Shutdown Redis connection."""
    global _redis_client
    if _redis_client:
        await _redis_client.close()
        _redis_client = None


# ── Helper functions ──────────────────────────────────────────────────────

async def set_shipment_location(shipment_id: str, lat: float, lng: float,
                                 speed_kmh: float, timestamp: str) -> None:
    r = await get_redis()
    data = json.dumps({
        "lat": lat, "lng": lng,
        "speed_kmh": speed_kmh, "timestamp": timestamp,
    })
    await r.set(f"shipment:{shipment_id}:location", data, ex=3600)


async def get_shipment_location(shipment_id: str) -> Optional[Dict]:
    r = await get_redis()
    raw = await r.get(f"shipment:{shipment_id}:location")
    return json.loads(raw) if raw else None


async def set_shipment_state(shipment_id: str, state: Dict[str, Any]) -> None:
    r = await get_redis()
    await r.set(f"shipment:{shipment_id}:state", json.dumps(state), ex=3600)


async def get_shipment_state(shipment_id: str) -> Optional[Dict]:
    r = await get_redis()
    raw = await r.get(f"shipment:{shipment_id}:state")
    return json.loads(raw) if raw else None


async def set_shipment_features(shipment_id: str, features: Dict[str, Any]) -> None:
    r = await get_redis()
    await r.set(f"shipment:{shipment_id}:features", json.dumps(features), ex=3600)


async def get_shipment_features(shipment_id: str) -> Optional[Dict]:
    r = await get_redis()
    raw = await r.get(f"shipment:{shipment_id}:features")
    return json.loads(raw) if raw else None


async def push_gps_ping(shipment_id: str, ping_data: Dict) -> None:
    """Push a GPS ping to the rolling list (keep last 60 pings = ~10 min at 10s intervals)."""
    r = await get_redis()
    key = f"shipment:{shipment_id}:pings"
    await r.lpush(key, json.dumps(ping_data))
    await r.ltrim(key, 0, 59)


async def get_recent_pings(shipment_id: str, count: int = 60) -> list:
    """Get the last N GPS pings for a shipment."""
    r = await get_redis()
    raw_list = await r.lrange(f"shipment:{shipment_id}:pings", 0, count - 1)
    return [json.loads(p) for p in raw_list]
