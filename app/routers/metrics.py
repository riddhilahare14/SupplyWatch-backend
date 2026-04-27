"""
Health & Metrics router:
  GET /health  — check DB + Redis connectivity
  GET /metrics — aggregate platform stats
"""
import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.models import Shipment, Prediction, Reroute
from app.redis_client import get_redis
from app.schemas.schemas import HealthResponse, MetricsResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Health & Metrics"])


@router.get("/health", response_model=HealthResponse)
async def health_check(db: AsyncSession = Depends(get_db)):
    db_status = "error"
    redis_status = "error"
    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as e:
        logger.error("DB health check failed: %s", e)
    try:
        r = await get_redis()
        await r.ping()
        redis_status = "ok"
    except Exception as e:
        logger.error("Redis health check failed: %s", e)
    overall = "ok" if db_status == "ok" and redis_status == "ok" else "degraded"
    return HealthResponse(status=overall, db=db_status, redis=redis_status)


@router.get("/metrics", response_model=MetricsResponse)
async def metrics(db: AsyncSession = Depends(get_db)):
    total = (await db.execute(select(func.count()).select_from(Shipment))).scalar() or 0
    in_transit = (await db.execute(select(func.count()).select_from(Shipment).where(Shipment.status == "in_transit"))).scalar() or 0
    at_risk = (await db.execute(
        select(func.count()).select_from(Prediction)
        .where(Prediction.risk_score > 0.5)
    )).scalar() or 0
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    auto_rerouted = (await db.execute(
        select(func.count()).select_from(Reroute)
        .where(Reroute.status == "auto_executed", Reroute.created_at >= today_start)
    )).scalar() or 0
    pending = (await db.execute(
        select(func.count()).select_from(Reroute).where(Reroute.status == "pending")
    )).scalar() or 0
    avg_risk_result = await db.execute(select(func.avg(Prediction.risk_score)))
    avg_risk = avg_risk_result.scalar() or 0.0
    return MetricsResponse(
        total_shipments=total, in_transit=in_transit, at_risk=at_risk,
        auto_rerouted_today=auto_rerouted, pending_approvals=pending,
        avg_risk_score=round(float(avg_risk), 3), kafka_lag=0,
    )
