"""
Reroutes router:
  GET  /reroutes            — list reroute proposals
  GET  /reroutes/{id}       — full reroute detail
  POST /reroutes/{id}/approve — approve reroute
  POST /reroutes/{id}/reject  — reject reroute
"""
import logging
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.models import Reroute, AuditLog
from app.schemas.schemas import RerouteOut, RerouteReject
from app.services.websocket_manager import ws_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/reroutes", tags=["Reroutes"])


@router.get("", response_model=list[RerouteOut])
async def list_reroutes(
    status: Optional[str] = Query(None),
    shipment_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    query = select(Reroute).order_by(Reroute.created_at.desc())
    if status:
        query = query.where(Reroute.status == status)
    if shipment_id:
        query = query.where(Reroute.shipment_id == shipment_id)
    result = await db.execute(query)
    return [RerouteOut.model_validate(r) for r in result.scalars().all()]


@router.get("/{reroute_id}", response_model=RerouteOut)
async def get_reroute(reroute_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Reroute).where(Reroute.id == reroute_id))
    reroute = result.scalar_one_or_none()
    if not reroute:
        raise HTTPException(404, "Reroute not found")
    return RerouteOut.model_validate(reroute)


@router.post("/{reroute_id}/approve", response_model=RerouteOut)
async def approve_reroute(reroute_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Reroute).where(Reroute.id == reroute_id))
    reroute = result.scalar_one_or_none()
    if not reroute:
        raise HTTPException(404, "Reroute not found")
    if reroute.status != "pending":
        raise HTTPException(400, f"Cannot approve reroute with status '{reroute.status}'")
    reroute.status = "approved"
    reroute.decided_at = datetime.now(timezone.utc)
    reroute.decided_by = "dispatcher"
    audit = AuditLog(action="reroute_approved", actor="dispatcher", shipment_id=reroute.shipment_id, reroute_id=reroute.id, payload={"detour_pct": reroute.detour_pct, "cost_delta_pct": reroute.cost_delta_pct})
    db.add(audit)
    await ws_manager.send_reroute_decided(reroute.id, "approved", "dispatcher")
    return RerouteOut.model_validate(reroute)


@router.post("/{reroute_id}/reject", response_model=RerouteOut)
async def reject_reroute(reroute_id: str, body: RerouteReject, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Reroute).where(Reroute.id == reroute_id))
    reroute = result.scalar_one_or_none()
    if not reroute:
        raise HTTPException(404, "Reroute not found")
    if reroute.status != "pending":
        raise HTTPException(400, f"Cannot reject reroute with status '{reroute.status}'")
    reroute.status = "rejected"
    reroute.decided_at = datetime.now(timezone.utc)
    reroute.decided_by = "dispatcher"
    reroute.reason = (reroute.reason or "") + f" | Rejected: {body.reason}"
    audit = AuditLog(action="reroute_rejected", actor="dispatcher", shipment_id=reroute.shipment_id, reroute_id=reroute.id, payload={"rejection_reason": body.reason})
    db.add(audit)
    await ws_manager.send_reroute_decided(reroute.id, "rejected", "dispatcher")
    return RerouteOut.model_validate(reroute)
