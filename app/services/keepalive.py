"""
app/services/keepalive.py
─────────────────────────
Pings the Render deployment's health endpoint every KEEPALIVE_INTERVAL seconds
so the free-tier dyno never idles past the 50-second inactivity window.

All config lives in app/config.py (Settings):
    SELF_URL            — URL to ping  (default: https://supplywatch-backend.onrender.com/)
    KEEPALIVE_INTERVAL  — seconds between pings (default: 30)
    KEEPALIVE_ENABLED   — set False in local dev to silence noisy logs (default: True)
"""

import asyncio
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 10  # seconds — hard timeout per request, not configurable (intentional)


async def _ping_loop() -> None:
    """Infinite loop: GET settings.SELF_URL every settings.KEEPALIVE_INTERVAL seconds."""
    logger.info(
        "Keep-alive started → pinging %s every %ds",
        settings.SELF_URL,
        settings.KEEPALIVE_INTERVAL,
    )
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        while True:
            await asyncio.sleep(settings.KEEPALIVE_INTERVAL)
            try:
                resp = await client.get(settings.SELF_URL)
                logger.debug("Keep-alive ping → %s %s", resp.status_code, settings.SELF_URL)
            except httpx.RequestError as exc:
                # Network hiccup — log and keep retrying; don't crash the loop.
                logger.warning("Keep-alive ping failed: %s", exc)


def start_keepalive() -> asyncio.Task:
    """Schedule the ping loop and return the Task (so lifespan can cancel it on shutdown)."""
    return asyncio.create_task(_ping_loop())
