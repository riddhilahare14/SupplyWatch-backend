"""
Disruptions router:
  GET    /disruptions                     — list disruptions
  GET    /disruptions/{id}/affected-shipments — shipments in disruption zone
  POST   /disruptions/simulate            — activate demo disruption
  POST   /disruptions                     — create disruption
  DELETE /disruptions/{id}                — deactivate disruption
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import Disruption, Shipment, ShipmentEvent
from app.schemas.schemas import (
    DisruptionCreate, DisruptionSimulate, DisruptionOut,
    ShipmentListItem,
)
from app.redis_client import get_shipment_state, get_shipment_location
from app.services.websocket_manager import ws_manager
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/disruptions", tags=["Disruptions"])


def _disruption_to_out(d: Disruption) -> DisruptionOut:
    polygon = None
    if d.polygon_geojson:
        try:
            polygon = json.loads(d.polygon_geojson)
        except Exception:
            pass
    return DisruptionOut(
        id=d.id, name=d.name, type=d.type, severity=d.severity,
        active=d.active, started_at=d.started_at, ends_at=d.ends_at,
        polygon=polygon,
    )


@router.get("", response_model=list[DisruptionOut])
async def list_disruptions(
    active: Optional[bool] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    query = select(Disruption)
    if active is not None:
        query = query.where(Disruption.active == active)
    result = await db.execute(query)
    return [_disruption_to_out(d) for d in result.scalars().all()]


@router.get("/{disruption_id}/affected-shipments")
async def affected_shipments(
    disruption_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Find in-transit shipments whose current position intersects this disruption."""
    result = await db.execute(
        select(Disruption).where(Disruption.id == disruption_id)
    )
    disruption = result.scalar_one_or_none()
    if not disruption:
        raise HTTPException(404, "Disruption not found")

    if not disruption.polygon_geojson:
        return []

    # Get all in-transit shipments
    ships_result = await db.execute(
        select(Shipment).where(Shipment.status == "in_transit")
    )
    shipments = ships_result.scalars().all()

    affected = []
    geo = json.loads(disruption.polygon_geojson)
    coords = geo.get("coordinates", [[]])[0]
    if not coords:
        return []

    lngs = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    bbox = (min(lngs), min(lats), max(lngs), max(lats))

    for s in shipments:
        loc = await get_shipment_location(s.id)
        if not loc:
            continue
        lat, lng = loc.get("lat", 0), loc.get("lng", 0)

        # Simple bounding box check (works for both SQLite and Postgres fallback)
        if bbox[0] <= lng <= bbox[2] and bbox[1] <= lat <= bbox[3]:
            state = await get_shipment_state(s.id)
            item = ShipmentListItem(
                id=s.id, carrier_id=s.carrier_id,
                origin_hub_id=s.origin_hub_id, dest_hub_id=s.dest_hub_id,
                sla_deadline=s.sla_deadline, status=s.status,
                contents=s.contents, created_at=s.created_at,
                lat=lat, lng=lng,
            )
            if state:
                item.risk_score = state.get("risk_score")
                try:
                    item.current_eta = datetime.fromisoformat(state.get("current_eta", ""))
                except (ValueError, TypeError):
                    pass
            affected.append(item)

    return affected


@router.post("/simulate", response_model=DisruptionOut)
async def simulate_disruption(
    body: DisruptionSimulate,
    db: AsyncSession = Depends(get_db),
):
    """Activate a pre-seeded demo disruption by name."""
    result = await db.execute(
        select(Disruption).where(Disruption.name == body.name)
    )
    disruption = result.scalar_one_or_none()
    if not disruption:
        raise HTTPException(404, f"Demo disruption '{body.name}' not found")

    disruption.active = True
    disruption.started_at = datetime.now(timezone.utc)
    await db.flush()

    # Push WebSocket event
    polygon = None
    if disruption.polygon_geojson:
        try:
            polygon = json.loads(disruption.polygon_geojson)
        except Exception:
            pass
    await ws_manager.send_disruption_new(disruption.id, disruption.name, polygon)

    return _disruption_to_out(disruption)


@router.post("", response_model=DisruptionOut, status_code=201)
async def create_disruption(
    body: DisruptionCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new disruption."""
    polygon_geojson = None
    if body.polygon:
        polygon_geojson = body.polygon.model_dump_json()

    disruption = Disruption(
        name=body.name,
        type=body.type,
        severity=body.severity,
        active=True,
        started_at=datetime.now(timezone.utc),
        ends_at=body.ends_at,
        polygon_geojson=polygon_geojson,
    )

    if polygon_geojson and not settings.USE_SQLITE:
        try:
            from geoalchemy2.elements import WKTElement
            geo = json.loads(polygon_geojson)
            coords = geo["coordinates"][0]
            wkt_coords = ", ".join(f"{c[0]} {c[1]}" for c in coords)
            wkt = f"SRID=4326;POLYGON(({wkt_coords}))"
            disruption.polygon = WKTElement(wkt, srid=4326)
        except Exception as e:
            logger.warning("Failed to create PostGIS polygon: %s", e)

    db.add(disruption)
    await db.flush()

    # Push WebSocket event
    await ws_manager.send_disruption_new(
        disruption.id, disruption.name,
        json.loads(polygon_geojson) if polygon_geojson else None,
    )

    return _disruption_to_out(disruption)


@router.delete("/{disruption_id}", status_code=200)
async def deactivate_disruption(
    disruption_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Deactivate a disruption (set active=false)."""
    result = await db.execute(
        select(Disruption).where(Disruption.id == disruption_id)
    )
    disruption = result.scalar_one_or_none()
    if not disruption:
        raise HTTPException(404, "Disruption not found")

    disruption.active = False
    return {"status": "deactivated", "id": disruption_id}
