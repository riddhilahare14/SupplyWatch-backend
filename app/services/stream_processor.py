"""
Stream processor: GPS ingestion background task.
Consumes GPS pings, updates Redis, computes rolling features,
detects stalls, and pushes WebSocket events.
"""

import json
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from math import radians, sin, cos, sqrt, atan2
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.models import ShipmentEvent, Hub, Disruption, Shipment
from app.redis_client import (
    set_shipment_location, get_shipment_location,
    set_shipment_features, get_shipment_features,
    push_gps_ping, get_recent_pings,
    get_shipment_state, set_shipment_state,
)
from app.services.websocket_manager import ws_manager
from app.config import settings

logger = logging.getLogger(__name__)


def _haversine(lat1, lng1, lat2, lng2) -> float:
    R = 6371
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


async def process_gps_ping(
    session: AsyncSession,
    shipment_id: str,
    lat: float,
    lng: float,
    speed_kmh: float,
    timestamp: datetime,
) -> dict:
    """
    Process a single GPS ping for a shipment.
    1. Update Redis location
    2. Insert shipment_event
    3. Recompute rolling features
    4. Check stall condition
    5. Push gps_update via WebSocket
    """
    ts_str = timestamp.isoformat() if isinstance(timestamp, datetime) else str(timestamp)

    # 1. Update Redis location
    await set_shipment_location(shipment_id, lat, lng, speed_kmh, ts_str)

    # 2. Insert shipment_event
    event = ShipmentEvent(
        shipment_id=shipment_id,
        event_type="gps_ping",
        lat=lat,
        lng=lng,
        timestamp=timestamp,
        metadata_={"speed_kmh": speed_kmh},
    )
    session.add(event)
    await session.flush()

    # 3. Push to rolling ping list
    await push_gps_ping(shipment_id, {
        "lat": lat, "lng": lng, "speed_kmh": speed_kmh, "timestamp": ts_str,
    })

    # 4. Recompute rolling features
    features = await _compute_features(session, shipment_id, lat, lng)
    await set_shipment_features(shipment_id, features)

    # 5. Update state
    state = await get_shipment_state(shipment_id)
    if state:
        # Update risk based on features
        state["status"] = "in_transit"
        await set_shipment_state(shipment_id, state)

    # 6. Check stall condition
    stall_detected = await _check_stall(shipment_id)
    result = {"event_id": event.id, "stall_detected": stall_detected, "features": features}

    if stall_detected:
        stall_event = ShipmentEvent(
            shipment_id=shipment_id,
            event_type="stall_alert",
            lat=lat,
            lng=lng,
            timestamp=timestamp,
            metadata_={"reason": "Stationary > 30 min, speed < 2 kmh"},
        )
        session.add(stall_event)
        await ws_manager.broadcast("stall_alert", {
            "shipment_id": shipment_id,
            "lat": lat, "lng": lng,
            "timestamp": ts_str,
        })

    # 7. Push GPS update via WebSocket
    await ws_manager.send_gps_update(shipment_id, lat, lng, ts_str)

    return result


async def _compute_features(
    session: AsyncSession,
    shipment_id: str,
    current_lat: float,
    current_lng: float,
) -> dict:
    """Compute rolling features for a shipment."""

    # avg_speed_10min from recent pings
    pings = await get_recent_pings(shipment_id, 60)
    if pings:
        speeds = [p["speed_kmh"] for p in pings if "speed_kmh" in p]
        avg_speed = sum(speeds) / len(speeds) if speeds else 0
    else:
        avg_speed = 0

    # distance_remaining_km: haversine to destination hub
    result = await session.execute(
        select(Shipment).where(Shipment.id == shipment_id)
    )
    shipment = result.scalar_one_or_none()
    distance_remaining = 0.0
    if shipment:
        dest_result = await session.execute(
            select(Hub).where(Hub.id == shipment.dest_hub_id)
        )
        dest_hub = dest_result.scalar_one_or_none()
        if dest_hub:
            distance_remaining = _haversine(
                current_lat, current_lng, dest_hub.lat, dest_hub.lng
            )

    # disruptions_intersected: count active disruptions near this point
    disruptions_count = 0
    if not settings.USE_SQLITE:
        try:
            q = text("""
                SELECT COUNT(*) FROM disruptions
                WHERE active = true
                AND ST_Intersects(polygon, ST_SetSRID(ST_Point(:lng, :lat), 4326))
            """)
            r = await session.execute(q, {"lat": current_lat, "lng": current_lng})
            disruptions_count = r.scalar() or 0
        except Exception as e:
            logger.debug("PostGIS query failed: %s", e)
    else:
        # Simple bounding box check for SQLite
        result = await session.execute(
            select(Disruption).where(Disruption.active == True)
        )
        for d in result.scalars().all():
            if d.polygon_geojson:
                try:
                    geo = json.loads(d.polygon_geojson)
                    coords = geo.get("coordinates", [[]])[0]
                    lngs = [c[0] for c in coords]
                    lats = [c[1] for c in coords]
                    if (min(lngs) <= current_lng <= max(lngs) and
                        min(lats) <= current_lat <= max(lats)):
                        disruptions_count += 1
                except Exception:
                    pass

    return {
        "avg_speed_10min": round(avg_speed, 1),
        "distance_remaining_km": round(distance_remaining, 1),
        "disruptions_intersected": disruptions_count,
    }


async def _check_stall(shipment_id: str) -> bool:
    """
    Check if shipment is stalled: speed < 2 kmh for > 30 minutes.
    Uses the rolling ping list in Redis.
    """
    pings = await get_recent_pings(shipment_id, 60)
    if len(pings) < 3:
        return False

    # Check if all recent pings have speed < 2
    slow_pings = [p for p in pings if p.get("speed_kmh", 999) < 2]
    if len(slow_pings) < 3:
        return False

    # Check time span of slow pings
    try:
        timestamps = [
            datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00"))
            for p in slow_pings
            if "timestamp" in p
        ]
        if timestamps:
            time_span = max(timestamps) - min(timestamps)
            return time_span >= timedelta(minutes=30)
    except Exception:
        pass

    return False


async def gps_ingestion_loop():
    """
    Background task that can consume from Kafka or run as a polling loop.
    In dev mode without Kafka, this just sleeps — pings come via POST /internal/gps-ping.
    """
    if settings.KAFKA_BROKER:
        try:
            from aiokafka import AIOKafkaConsumer
            consumer = AIOKafkaConsumer(
                settings.KAFKA_GPS_TOPIC,
                bootstrap_servers=settings.KAFKA_BROKER,
                group_id="smartroute-stream",
                value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            )
            await consumer.start()
            logger.info("Kafka consumer started on topic %s", settings.KAFKA_GPS_TOPIC)

            try:
                async for msg in consumer:
                    async with async_session() as session:
                        try:
                            data = msg.value
                            await process_gps_ping(
                                session=session,
                                shipment_id=data["shipment_id"],
                                lat=data["lat"],
                                lng=data["lng"],
                                speed_kmh=data["speed_kmh"],
                                timestamp=datetime.fromisoformat(data["timestamp"]),
                            )
                            await session.commit()
                        except Exception as e:
                            logger.error("Error processing Kafka message: %s", e)
                            await session.rollback()
            finally:
                await consumer.stop()

        except ImportError:
            logger.warning("aiokafka not available, GPS ingestion via REST only")
        except Exception as e:
            logger.error("Kafka consumer failed: %s", e)
    else:
        logger.info("No Kafka broker configured — GPS ingestion via POST /internal/gps-ping only")

    # Keep task alive
    while True:
        await asyncio.sleep(60)
