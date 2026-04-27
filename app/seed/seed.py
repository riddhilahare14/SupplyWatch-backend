"""
Seed data for the Smart Supply Chain Platform.
Creates: 5 cities × (3 warehouses + 5 hubs), 5 carriers, lanes,
2000 historical + 500 in-flight shipments, 3 demo disruptions.
"""

import uuid
import random
import json
import logging
from datetime import datetime, timezone, timedelta
from math import radians, sin, cos, sqrt, atan2

from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    Warehouse, Hub, Carrier, Lane,
    Shipment, ShipmentEvent, Disruption,
)
from app.redis_client import set_shipment_location, set_shipment_state, set_shipment_features
from app.config import settings

logger = logging.getLogger(__name__)


# ── City coordinates ──────────────────────────────────────────────────────

CITIES = {
    "Mumbai":    {"lat": 19.0760, "lng": 72.8777},
    "Delhi":     {"lat": 28.7041, "lng": 77.1025},
    "Chennai":   {"lat": 13.0827, "lng": 80.2707},
    "Bangalore": {"lat": 12.9716, "lng": 77.5946},
    "Hyderabad": {"lat": 17.3850, "lng": 78.4867},
}

CARRIER_DATA = [
    {"name": "SpeedFreight India",   "ontime_rate": 0.95},
    {"name": "TransLogic Express",   "ontime_rate": 0.88},
    {"name": "BharatHaul Logistics", "ontime_rate": 0.82},
    {"name": "QuickShip National",   "ontime_rate": 0.78},
    {"name": "DesiRoute Carriers",   "ontime_rate": 0.72},
]

CONTENTS_OPTIONS = [
    "Electronics — smartphones & tablets",
    "Automotive parts — engine components",
    "Pharmaceuticals — temperature-sensitive",
    "Textiles — garments & fabrics",
    "FMCG — packaged food & beverages",
    "Industrial machinery",
    "Raw materials — steel coils",
    "Consumer appliances",
    "Agricultural produce — perishable",
    "Chemical compounds — hazmat class 3",
]

# ── Demo disruptions (pre-seeded, inactive) ──────────────────────────────

DEMO_DISRUPTIONS = [
    {
        "name": "storm_mumbai",
        "type": "storm",
        "severity": 4,
        "polygon_geojson": json.dumps({
            "type": "Polygon",
            "coordinates": [[[72.75, 18.95], [72.75, 19.20], [73.00, 19.20],
                             [73.00, 18.95], [72.75, 18.95]]]
        }),
        "ends_at": datetime.now(timezone.utc) + timedelta(days=2),
    },
    {
        "name": "close_nh48",
        "type": "road_closure",
        "severity": 3,
        "polygon_geojson": json.dumps({
            "type": "Polygon",
            "coordinates": [[[72.85, 19.00], [72.85, 19.45], [72.90, 19.45],
                             [72.90, 19.00], [72.85, 19.00]]]
        }),
        "ends_at": datetime.now(timezone.utc) + timedelta(hours=18),
    },
    {
        "name": "shutdown_chennai_port",
        "type": "port_shutdown",
        "severity": 5,
        "polygon_geojson": json.dumps({
            "type": "Polygon",
            "coordinates": [[[80.25, 13.05], [80.25, 13.15], [80.35, 13.15],
                             [80.35, 13.05], [80.25, 13.05]]]
        }),
        "ends_at": datetime.now(timezone.utc) + timedelta(days=3),
    },
]


def _haversine(lat1, lng1, lat2, lng2):
    """Distance in km between two points."""
    R = 6371
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def _jitter(val, amount=0.05):
    """Add small random offset to a coordinate."""
    return val + random.uniform(-amount, amount)


def _gen_id():
    return str(uuid.uuid4())


def _interpolate(lat1, lng1, lat2, lng2, t):
    """Linear interpolation between two points, t in [0, 1]."""
    return lat1 + t * (lat2 - lat1), lng1 + t * (lng2 - lng1)


async def seed_database(session: AsyncSession):
    """Main seed function — idempotent (skips if data exists)."""

    # Check if already seeded
    result = await session.execute(select(func.count()).select_from(Hub))
    count = result.scalar()
    if count and count > 0:
        logger.info("Database already seeded (%d hubs found), skipping.", count)
        return

    logger.info("Seeding database...")

    # ── 1. Warehouses & Hubs ──────────────────────────────────────
    all_hubs = []
    hub_by_city = {}

    for city_name, coords in CITIES.items():
        hub_by_city[city_name] = []

        # 3 warehouses per city
        for i in range(3):
            wh = Warehouse(
                id=_gen_id(),
                name=f"{city_name} Warehouse {i + 1}",
                city=city_name,
                lat=_jitter(coords["lat"]),
                lng=_jitter(coords["lng"]),
            )
            session.add(wh)

        # 5 hubs per city
        for i in range(5):
            hub = Hub(
                id=_gen_id(),
                name=f"{city_name} Hub {i + 1}",
                city=city_name,
                lat=_jitter(coords["lat"], 0.08),
                lng=_jitter(coords["lng"], 0.08),
            )
            session.add(hub)
            all_hubs.append(hub)
            hub_by_city[city_name].append(hub)

    # ── 2. Carriers ───────────────────────────────────────────────
    carriers = []
    for cd in CARRIER_DATA:
        carrier = Carrier(id=_gen_id(), name=cd["name"], ontime_rate=cd["ontime_rate"])
        session.add(carrier)
        carriers.append(carrier)

    # ── 3. Lanes (connect hubs between all city pairs) ────────────
    lanes = []
    city_names = list(CITIES.keys())
    for i, c1 in enumerate(city_names):
        for c2 in city_names[i + 1:]:
            # Connect first hub of each city pair (and one more for redundancy)
            for h1 in hub_by_city[c1][:2]:
                for h2 in hub_by_city[c2][:2]:
                    dist = _haversine(h1.lat, h1.lng, h2.lat, h2.lng)
                    lane = Lane(
                        id=_gen_id(),
                        origin_hub_id=h1.id,
                        dest_hub_id=h2.id,
                        distance_km=round(dist, 1),
                        base_cost=round(dist * random.uniform(1.5, 3.0), 2),
                    )
                    session.add(lane)
                    lanes.append(lane)
                    # Reverse lane
                    rev = Lane(
                        id=_gen_id(),
                        origin_hub_id=h2.id,
                        dest_hub_id=h1.id,
                        distance_km=round(dist, 1),
                        base_cost=round(dist * random.uniform(1.5, 3.0), 2),
                    )
                    session.add(rev)
                    lanes.append(rev)

        # Intra-city lanes
        for j, h1 in enumerate(hub_by_city[c1]):
            for h2 in hub_by_city[c1][j + 1:]:
                dist = _haversine(h1.lat, h1.lng, h2.lat, h2.lng)
                lane = Lane(
                    id=_gen_id(),
                    origin_hub_id=h1.id,
                    dest_hub_id=h2.id,
                    distance_km=round(dist, 1),
                    base_cost=round(dist * random.uniform(1.0, 2.0), 2),
                )
                session.add(lane)

    await session.flush()

    # ── 4. Historical shipments (2000, completed) ─────────────────
    logger.info("Creating 2000 historical shipments...")
    statuses = ["delivered", "delayed"]
    for _ in range(2000):
        origin_city = random.choice(city_names)
        dest_city = random.choice([c for c in city_names if c != origin_city])
        origin_hub = random.choice(hub_by_city[origin_city])
        dest_hub = random.choice(hub_by_city[dest_city])
        carrier = random.choice(carriers)
        created = datetime.now(timezone.utc) - timedelta(days=random.randint(1, 90))
        sla = created + timedelta(hours=random.randint(24, 96))
        status = random.choices(statuses, weights=[0.75, 0.25])[0]

        shipment = Shipment(
            id=_gen_id(),
            carrier_id=carrier.id,
            origin_hub_id=origin_hub.id,
            dest_hub_id=dest_hub.id,
            sla_deadline=sla,
            status=status,
            contents=random.choice(CONTENTS_OPTIONS),
            created_at=created,
        )
        session.add(shipment)

    await session.flush()

    # ── 5. In-flight shipments (500) with GPS positions ───────────
    logger.info("Creating 500 in-flight shipments with GPS traces...")
    in_flight_shipments = []

    for _ in range(500):
        origin_city = random.choice(city_names)
        dest_city = random.choice([c for c in city_names if c != origin_city])
        origin_hub = random.choice(hub_by_city[origin_city])
        dest_hub = random.choice(hub_by_city[dest_city])
        carrier = random.choice(carriers)
        created = datetime.now(timezone.utc) - timedelta(hours=random.randint(2, 48))
        sla = created + timedelta(hours=random.randint(24, 72))

        shipment = Shipment(
            id=_gen_id(),
            carrier_id=carrier.id,
            origin_hub_id=origin_hub.id,
            dest_hub_id=dest_hub.id,
            sla_deadline=sla,
            status="in_transit",
            contents=random.choice(CONTENTS_OPTIONS),
            created_at=created,
        )
        session.add(shipment)
        in_flight_shipments.append((shipment, origin_hub, dest_hub))

    await session.flush()

    # Generate GPS events for in-flight shipments
    for shipment, origin_hub, dest_hub in in_flight_shipments:
        # Simulate progress along route (10-80% complete)
        progress = random.uniform(0.10, 0.80)
        num_pings = random.randint(5, 15)

        for i in range(num_pings):
            t = (progress * i) / num_pings
            lat, lng = _interpolate(
                origin_hub.lat, origin_hub.lng,
                dest_hub.lat, dest_hub.lng, t
            )
            lat = _jitter(lat, 0.02)
            lng = _jitter(lng, 0.02)
            ts = shipment.created_at + timedelta(
                minutes=int(i * (progress * 60 * 24) / num_pings)
            )
            event = ShipmentEvent(
                id=_gen_id(),
                shipment_id=shipment.id,
                event_type="gps_ping",
                lat=round(lat, 6),
                lng=round(lng, 6),
                timestamp=ts,
                metadata_={"speed_kmh": round(random.uniform(30, 80), 1)},
            )
            session.add(event)

        # Set current position in Redis
        current_lat, current_lng = _interpolate(
            origin_hub.lat, origin_hub.lng,
            dest_hub.lat, dest_hub.lng, progress
        )
        speed = round(random.uniform(40, 75), 1)
        now_str = datetime.now(timezone.utc).isoformat()

        await set_shipment_location(
            shipment.id, round(current_lat, 6), round(current_lng, 6),
            speed, now_str,
        )

        dist_remaining = _haversine(current_lat, current_lng, dest_hub.lat, dest_hub.lng)
        eta_hours = dist_remaining / max(speed, 1)
        current_eta = datetime.now(timezone.utc) + timedelta(hours=eta_hours)

        await set_shipment_state(shipment.id, {
            "current_eta": current_eta.isoformat(),
            "risk_score": round(random.uniform(0.1, 0.6), 2),
            "route_id": None,
            "status": "in_transit",
        })

        await set_shipment_features(shipment.id, {
            "avg_speed_10min": speed,
            "distance_remaining_km": round(dist_remaining, 1),
            "disruptions_intersected": 0,
        })

    # ── 6. Demo disruptions ───────────────────────────────────────
    logger.info("Creating demo disruptions...")
    for dd in DEMO_DISRUPTIONS:
        # Build WKT for PostGIS from GeoJSON
        geojson = json.loads(dd["polygon_geojson"])
        coords = geojson["coordinates"][0]
        wkt_coords = ", ".join(f"{c[0]} {c[1]}" for c in coords)
        wkt = f"SRID=4326;POLYGON(({wkt_coords}))"

        disruption = Disruption(
            id=_gen_id(),
            name=dd["name"],
            type=dd["type"],
            severity=dd["severity"],
            active=False,
            started_at=None,
            ends_at=dd["ends_at"],
            polygon_geojson=dd["polygon_geojson"],
        )

        # Set PostGIS polygon if not SQLite
        if not settings.USE_SQLITE:
            from geoalchemy2.elements import WKTElement
            disruption.polygon = WKTElement(wkt, srid=4326)

        session.add(disruption)

    await session.commit()
    logger.info("✅ Seed complete: %d hubs, %d carriers, 2500 shipments, %d disruptions",
                len(all_hubs), len(carriers), len(DEMO_DISRUPTIONS))
