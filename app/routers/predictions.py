"""
Predictions router:
  GET  /predictions/at-risk  — shipments with risk > 0.5
  POST /predictions          — ML writes scores; triggers decision engine
"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.models import Prediction, Shipment
from app.schemas.schemas import PredictionCreate, PredictionOut, AtRiskShipment
from app.redis_client import set_shipment_state, get_shipment_state
from app.services.decision_engine import evaluate_shipment
from app.services.websocket_manager import ws_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/predictions", tags=["Predictions"])


@router.get("/at-risk", response_model=list[AtRiskShipment])
async def at_risk_shipments(db: AsyncSession = Depends(get_db)):
    latest_pred = (
        select(Prediction.shipment_id, func.max(Prediction.scored_at).label("max_scored_at"))
        .group_by(Prediction.shipment_id).subquery()
    )
    result = await db.execute(
        select(Prediction, Shipment)
        .join(Shipment, Prediction.shipment_id == Shipment.id)
        .join(latest_pred, (Prediction.shipment_id == latest_pred.c.shipment_id) & (Prediction.scored_at == latest_pred.c.max_scored_at))
        .where(Prediction.risk_score > 0.5)
        .order_by(Prediction.risk_score.desc())
    )
    return [AtRiskShipment(shipment_id=p.shipment_id, risk_score=p.risk_score, predicted_eta=p.predicted_eta, status=s.status, carrier_id=s.carrier_id) for p, s in result.all()]


@router.post("", response_model=PredictionOut, status_code=201)
async def create_prediction(body: PredictionCreate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Shipment).where(Shipment.id == body.shipment_id))
    if not result.scalar_one_or_none():
        raise HTTPException(404, "Shipment not found")
    prediction = Prediction(shipment_id=body.shipment_id, risk_score=body.risk_score, predicted_eta=body.predicted_eta, model_version=body.model_version)
    db.add(prediction)
    await db.flush()
    state = await get_shipment_state(body.shipment_id) or {}
    state["risk_score"] = body.risk_score
    if body.predicted_eta:
        state["current_eta"] = body.predicted_eta.isoformat()
    await set_shipment_state(body.shipment_id, state)
    await ws_manager.send_risk_update(body.shipment_id, body.risk_score, body.predicted_eta.isoformat() if body.predicted_eta else None)
    try:
        await evaluate_shipment(db, body.shipment_id, body.risk_score, body.predicted_eta.isoformat() if body.predicted_eta else None)
    except Exception as e:
        logger.error("Decision engine error: %s", e)
    return PredictionOut.model_validate(prediction)
