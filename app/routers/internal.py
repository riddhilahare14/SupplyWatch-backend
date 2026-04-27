"""
Internal router (called by stream processor):
  POST /internal/gps-ping       — process GPS ping
  POST /internal/trigger-reroute — run route optimization
"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.models import Shipment
from app.schemas.schemas import GPSPing, TriggerReroute
from app.services.stream_processor import process_gps_ping
from app.services.decision_engine import evaluate_shipment
from app.redis_client import get_shipment_state

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/internal", tags=["Internal"])


@router.post("/gps-ping")
async def gps_ping(body: GPSPing, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Shipment).where(Shipment.id == body.shipment_id))
    if not result.scalar_one_or_none():
        raise HTTPException(404, "Shipment not found")
    ping_result = await process_gps_ping(
        session=db, shipment_id=body.shipment_id,
        lat=body.lat, lng=body.lng, speed_kmh=body.speed_kmh,
        timestamp=body.timestamp,
    )
    return {"status": "ok", **ping_result}


@router.post("/trigger-reroute")
async def trigger_reroute(body: TriggerReroute, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Shipment).where(Shipment.id == body.shipment_id))
    if not result.scalar_one_or_none():
        raise HTTPException(404, "Shipment not found")
    state = await get_shipment_state(body.shipment_id)
    risk_score = state.get("risk_score", 0.75) if state else 0.75
    reroute = await evaluate_shipment(db, body.shipment_id, risk_score)
    if reroute:
        return {"status": "reroute_created", "reroute_id": reroute.id, "reroute_status": reroute.status}
    return {"status": "no_reroute_needed"}
