"""
Decision engine: auto-execute vs recommend reroutes.
Triggered every time a prediction is written (POST /predictions).
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.models import Shipment, Reroute, AuditLog, Disruption
from app.services.route_optimizer import compute_alternative_route
from app.services.websocket_manager import ws_manager
from app.redis_client import get_shipment_state, set_shipment_state
from app.config import settings

logger = logging.getLogger(__name__)


async def evaluate_shipment(session: AsyncSession, shipment_id: str,
                             risk_score: float, predicted_eta: str = None):
    """
    Main decision engine entry point.
    Called when a new prediction is written for a shipment.

    Logic:
    1. risk_score < 0.70 → do nothing
    2. risk_score >= 0.70 OR route intersects active disruption:
       a. Compute alternative route
       b. If detour < 15% AND cost_delta < 10%: auto-execute
       c. Else: create pending reroute recommendation
    """
    if risk_score < settings.RISK_THRESHOLD:
        logger.debug("Shipment %s risk %.2f below threshold, skipping", shipment_id, risk_score)
        return None

    # Get shipment
    result = await session.execute(
        select(Shipment).where(Shipment.id == shipment_id)
    )
    shipment = result.scalar_one_or_none()
    if not shipment:
        logger.error("Shipment %s not found for decision engine", shipment_id)
        return None

    if shipment.status != "in_transit":
        logger.debug("Shipment %s is %s, skipping reroute", shipment_id, shipment.status)
        return None

    # Check if there's already an active reroute
    existing = await session.execute(
        select(Reroute).where(
            Reroute.shipment_id == shipment_id,
            Reroute.status.in_(["pending", "auto_executed"]),
        )
    )
    if existing.scalar_one_or_none():
        logger.debug("Shipment %s already has active reroute, skipping", shipment_id)
        return None

    # Compute alternative route
    route_result = await compute_alternative_route(session, shipment)
    if not route_result:
        logger.warning("No alternative route found for shipment %s", shipment_id)
        return None

    detour_pct = route_result["detour_pct"]
    cost_delta_pct = route_result["cost_delta_pct"]

    # Decision: auto-execute or recommend
    auto_execute = (
        detour_pct < settings.AUTO_REROUTE_MAX_DETOUR_PCT
        and cost_delta_pct < settings.AUTO_REROUTE_MAX_COST_PCT
    )

    status = "auto_executed" if auto_execute else "pending"
    reason = (
        f"Risk score {risk_score:.2f} exceeds threshold. "
        f"Detour: {detour_pct:.1f}%, Cost delta: {cost_delta_pct:.1f}%."
    )
    if auto_execute:
        reason += " Auto-executed: within safe thresholds."
    else:
        reason += " Requires manual approval: exceeds auto-execute limits."

    # Create reroute record
    reroute = Reroute(
        shipment_id=shipment_id,
        old_route=route_result["old_route"],
        new_route=route_result["new_route"],
        cost_delta_pct=cost_delta_pct,
        detour_pct=detour_pct,
        new_eta=route_result["new_eta"],
        sla_recovery_prob=route_result["sla_recovery_prob"],
        reason=reason,
        status=status,
        decided_at=datetime.now(timezone.utc) if auto_execute else None,
        decided_by="system" if auto_execute else None,
    )
    session.add(reroute)

    # Audit log
    audit = AuditLog(
        action=f"reroute_{status}",
        actor="system",
        shipment_id=shipment_id,
        payload={
            "risk_score": risk_score,
            "detour_pct": detour_pct,
            "cost_delta_pct": cost_delta_pct,
            "sla_recovery_prob": route_result["sla_recovery_prob"],
        },
    )
    session.add(audit)

    await session.flush()

    # Link audit to reroute
    audit.reroute_id = reroute.id

    # If auto-executed, update Redis route
    if auto_execute:
        state = await get_shipment_state(shipment_id)
        if state:
            state["route_id"] = reroute.id
            if predicted_eta:
                state["current_eta"] = predicted_eta
            await set_shipment_state(shipment_id, state)

    await session.commit()

    # Push WebSocket event
    await ws_manager.send_reroute_created(
        reroute_id=reroute.id,
        shipment_id=shipment_id,
        status=status,
        reason=reason,
    )

    logger.info("Decision engine: shipment %s → %s (detour %.1f%%, cost %.1f%%)",
                shipment_id, status, detour_pct, cost_delta_pct)
    return reroute
