"""
Shipments router:
  GET  /shipments        — paginated list with Redis-enriched ETA + risk
  GET  /shipments/{id}   — full detail + events + prediction + reroute
  POST /shipments        — create shipment + seed Redis state
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.models import Shipment, ShipmentEvent, Prediction, Reroute, Hub
from app.schemas.schemas import (
    ShipmentCreate, ShipmentListItem, ShipmentDetail,
    ShipmentEventOut, PredictionOut, RerouteOut, PaginatedResponse,
    CarrierOut, HubOut,
)
from app.redis_client import (
    get_shipment_state, get_shipment_location, get_shipment_features,
    set_shipment_state, set_shipment_location,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/shipments", tags=["Shipments"])


@router.get("", response_model=PaginatedResponse)
async def list_shipments(
    status: Optional[str] = Query(None),
    risk_min: Optional[float] = Query(None),
    risk_max: Optional[float] = Query(None),
    city: Optional[str] = Query(None),
    carrier_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List shipments with filtering and Redis-enriched data."""
    query = select(Shipment)
    count_query = select(func.count()).select_from(Shipment)

    # Filters
    if status:
        query = query.where(Shipment.status == status)
        count_query = count_query.where(Shipment.status == status)
    if carrier_id:
        query = query.where(Shipment.carrier_id == carrier_id)
        count_query = count_query.where(Shipment.carrier_id == carrier_id)
    if city:
        hub_ids_q = select(Hub.id).where(Hub.city == city)
        query = query.where(
            (Shipment.origin_hub_id.in_(hub_ids_q)) | (Shipment.dest_hub_id.in_(hub_ids_q))
        )
        count_query = count_query.where(
            (Shipment.origin_hub_id.in_(hub_ids_q)) | (Shipment.dest_hub_id.in_(hub_ids_q))
        )

    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(Shipment.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(query)
    shipments = result.scalars().all()

    items = []
    for s in shipments:
        item = ShipmentListItem(
            id=s.id,
            carrier_id=s.carrier_id,
            origin_hub_id=s.origin_hub_id,
            dest_hub_id=s.dest_hub_id,
            sla_deadline=s.sla_deadline,
            status=s.status,
            contents=s.contents,
            created_at=s.created_at,
        )

        # Enrich from Redis
        state = await get_shipment_state(s.id)
        if state:
            if state.get("current_eta"):
                try:
                    item.current_eta = datetime.fromisoformat(state["current_eta"])
                except (ValueError, TypeError):
                    pass
            item.risk_score = state.get("risk_score")

        loc = await get_shipment_location(s.id)
        if loc:
            item.lat = loc.get("lat")
            item.lng = loc.get("lng")

        # Filter by risk score
        if risk_min is not None and (item.risk_score is None or item.risk_score < risk_min):
            continue
        if risk_max is not None and (item.risk_score is None or item.risk_score > risk_max):
            continue

        items.append(item)

    return PaginatedResponse(
        total=total, limit=limit, offset=offset,
        items=[i.model_dump() for i in items],
    )


@router.get("/{shipment_id}", response_model=ShipmentDetail)
async def get_shipment(
    shipment_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get full shipment detail with events, prediction, and reroute."""
    result = await db.execute(
        select(Shipment)
        .options(
            selectinload(Shipment.carrier),
            selectinload(Shipment.origin_hub),
            selectinload(Shipment.dest_hub),
        )
        .where(Shipment.id == shipment_id)
    )
    shipment = result.scalar_one_or_none()
    if not shipment:
        raise HTTPException(status_code=404, detail="Shipment not found")

    # Last 20 events
    events_result = await db.execute(
        select(ShipmentEvent)
        .where(ShipmentEvent.shipment_id == shipment_id)
        .order_by(ShipmentEvent.timestamp.desc())
        .limit(20)
    )
    events = events_result.scalars().all()

    # Latest prediction
    pred_result = await db.execute(
        select(Prediction)
        .where(Prediction.shipment_id == shipment_id)
        .order_by(Prediction.scored_at.desc())
        .limit(1)
    )
    latest_pred = pred_result.scalar_one_or_none()

    # Active reroute
    reroute_result = await db.execute(
        select(Reroute)
        .where(Reroute.shipment_id == shipment_id)
        .where(Reroute.status.in_(["pending", "auto_executed"]))
        .order_by(Reroute.created_at.desc())
        .limit(1)
    )
    active_reroute = reroute_result.scalar_one_or_none()

    # Enrich from Redis
    state = await get_shipment_state(shipment_id)
    loc = await get_shipment_location(shipment_id)
    features = await get_shipment_features(shipment_id)

    detail = ShipmentDetail(
        id=shipment.id,
        carrier_id=shipment.carrier_id,
        origin_hub_id=shipment.origin_hub_id,
        dest_hub_id=shipment.dest_hub_id,
        sla_deadline=shipment.sla_deadline,
        status=shipment.status,
        contents=shipment.contents,
        created_at=shipment.created_at,
        events=[ShipmentEventOut(
            id=e.id, shipment_id=e.shipment_id, event_type=e.event_type,
            lat=e.lat, lng=e.lng, timestamp=e.timestamp, metadata=e.metadata_,
        ) for e in events],
        latest_prediction=PredictionOut.model_validate(latest_pred) if latest_pred else None,
        active_reroute=RerouteOut.model_validate(active_reroute) if active_reroute else None,
        carrier=CarrierOut.model_validate(shipment.carrier) if shipment.carrier else None,
        origin_hub=HubOut.model_validate(shipment.origin_hub) if shipment.origin_hub else None,
        dest_hub=HubOut.model_validate(shipment.dest_hub) if shipment.dest_hub else None,
        features=features,
    )

    if state:
        try:
            detail.current_eta = datetime.fromisoformat(state.get("current_eta", ""))
        except (ValueError, TypeError):
            pass
        detail.risk_score = state.get("risk_score")

    if loc:
        detail.lat = loc.get("lat")
        detail.lng = loc.get("lng")

    return detail


@router.post("", response_model=ShipmentListItem, status_code=201)
async def create_shipment(
    body: ShipmentCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new shipment and seed its Redis state."""
    # Validate references
    origin = await db.execute(select(Hub).where(Hub.id == body.origin_hub_id))
    if not origin.scalar_one_or_none():
        raise HTTPException(400, "Invalid origin_hub_id")

    dest = await db.execute(select(Hub).where(Hub.id == body.dest_hub_id))
    dest_hub = dest.scalar_one_or_none()
    if not dest_hub:
        raise HTTPException(400, "Invalid dest_hub_id")

    shipment = Shipment(
        carrier_id=body.carrier_id,
        origin_hub_id=body.origin_hub_id,
        dest_hub_id=body.dest_hub_id,
        sla_deadline=body.sla_deadline,
        contents=body.contents,
        status="in_transit",
    )
    db.add(shipment)
    await db.flush()

    # Seed Redis state
    await set_shipment_state(shipment.id, {
        "current_eta": body.sla_deadline.isoformat(),
        "risk_score": 0.0,
        "route_id": None,
        "status": "in_transit",
    })

    return ShipmentListItem(
        id=shipment.id,
        carrier_id=shipment.carrier_id,
        origin_hub_id=shipment.origin_hub_id,
        dest_hub_id=shipment.dest_hub_id,
        sla_deadline=shipment.sla_deadline,
        status=shipment.status,
        contents=shipment.contents,
        created_at=shipment.created_at,
        current_eta=body.sla_deadline,
        risk_score=0.0,
    )
