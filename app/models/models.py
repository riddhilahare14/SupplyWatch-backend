"""
SQLAlchemy ORM models for the Smart Supply Chain Platform.
All 10 tables: warehouses, hubs, carriers, lanes, shipments,
shipment_events, disruptions, predictions, reroutes, audit_log.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Float, Boolean, Integer, DateTime,
    ForeignKey, Text, Enum as SAEnum, JSON,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from sqlalchemy.orm import relationship
from geoalchemy2 import Geometry

from app.database import Base
from app.config import settings


def _gen_uuid():
    return str(uuid.uuid4())


def _utcnow():
    return datetime.now(timezone.utc)


# Use JSONB on Postgres, JSON on SQLite
_JsonType = JSONB if not settings.USE_SQLITE else JSON


# ── Warehouses ────────────────────────────────────────────────────────────

class Warehouse(Base):
    __tablename__ = "warehouses"

    id = Column(String(36), primary_key=True, default=_gen_uuid)
    name = Column(String(255), nullable=False)
    city = Column(String(100), nullable=False)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)


# ── Hubs ──────────────────────────────────────────────────────────────────

class Hub(Base):
    __tablename__ = "hubs"

    id = Column(String(36), primary_key=True, default=_gen_uuid)
    name = Column(String(255), nullable=False)
    city = Column(String(100), nullable=False)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)


# ── Carriers ──────────────────────────────────────────────────────────────

class Carrier(Base):
    __tablename__ = "carriers"

    id = Column(String(36), primary_key=True, default=_gen_uuid)
    name = Column(String(255), nullable=False)
    ontime_rate = Column(Float, nullable=False, default=0.85)
    active = Column(Boolean, nullable=False, default=True)


# ── Lanes ─────────────────────────────────────────────────────────────────

class Lane(Base):
    __tablename__ = "lanes"

    id = Column(String(36), primary_key=True, default=_gen_uuid)
    origin_hub_id = Column(String(36), ForeignKey("hubs.id"), nullable=False)
    dest_hub_id = Column(String(36), ForeignKey("hubs.id"), nullable=False)
    distance_km = Column(Float, nullable=False)
    base_cost = Column(Float, nullable=False, default=0.0)

    origin_hub = relationship("Hub", foreign_keys=[origin_hub_id])
    dest_hub = relationship("Hub", foreign_keys=[dest_hub_id])


# ── Shipments ─────────────────────────────────────────────────────────────

class Shipment(Base):
    __tablename__ = "shipments"

    id = Column(String(36), primary_key=True, default=_gen_uuid)
    carrier_id = Column(String(36), ForeignKey("carriers.id"), nullable=False)
    origin_hub_id = Column(String(36), ForeignKey("hubs.id"), nullable=False)
    dest_hub_id = Column(String(36), ForeignKey("hubs.id"), nullable=False)
    sla_deadline = Column(DateTime(timezone=True), nullable=False)
    status = Column(
        String(20), nullable=False, default="in_transit",
    )  # in_transit, delivered, delayed
    contents = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    carrier = relationship("Carrier")
    origin_hub = relationship("Hub", foreign_keys=[origin_hub_id])
    dest_hub = relationship("Hub", foreign_keys=[dest_hub_id])
    events = relationship("ShipmentEvent", back_populates="shipment", order_by="ShipmentEvent.timestamp.desc()")
    predictions = relationship("Prediction", back_populates="shipment", order_by="Prediction.scored_at.desc()")
    reroutes = relationship("Reroute", back_populates="shipment", order_by="Reroute.created_at.desc()")


# ── Shipment Events ──────────────────────────────────────────────────────

class ShipmentEvent(Base):
    __tablename__ = "shipment_events"

    id = Column(String(36), primary_key=True, default=_gen_uuid)
    shipment_id = Column(String(36), ForeignKey("shipments.id"), nullable=False, index=True)
    event_type = Column(String(50), nullable=False)  # gps_ping, departure, arrival, stall, etc.
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    metadata_ = Column("metadata", _JsonType, nullable=True)

    shipment = relationship("Shipment", back_populates="events")


# ── Disruptions ───────────────────────────────────────────────────────────

class Disruption(Base):
    __tablename__ = "disruptions"

    id = Column(String(36), primary_key=True, default=_gen_uuid)
    name = Column(String(255), nullable=False)
    type = Column(String(50), nullable=False)  # storm, road_closure, port_shutdown
    severity = Column(Integer, nullable=False, default=3)  # 1-5
    active = Column(Boolean, nullable=False, default=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    ends_at = Column(DateTime(timezone=True), nullable=True)

    # PostGIS geometry for spatial queries; stored as text on SQLite
    if not settings.USE_SQLITE:
        polygon = Column(Geometry("POLYGON", srid=4326), nullable=True)
    else:
        polygon = Column(Text, nullable=True)  # GeoJSON text fallback

    polygon_geojson = Column(Text, nullable=True)  # Always store the GeoJSON text for API responses


# ── Predictions ───────────────────────────────────────────────────────────

class Prediction(Base):
    __tablename__ = "predictions"

    id = Column(String(36), primary_key=True, default=_gen_uuid)
    shipment_id = Column(String(36), ForeignKey("shipments.id"), nullable=False, index=True)
    risk_score = Column(Float, nullable=False)  # 0.0 - 1.0
    predicted_eta = Column(DateTime(timezone=True), nullable=True)
    scored_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    model_version = Column(String(20), nullable=True)

    shipment = relationship("Shipment", back_populates="predictions")


# ── Reroutes ──────────────────────────────────────────────────────────────

class Reroute(Base):
    __tablename__ = "reroutes"

    id = Column(String(36), primary_key=True, default=_gen_uuid)
    shipment_id = Column(String(36), ForeignKey("shipments.id"), nullable=False, index=True)
    old_route = Column(_JsonType, nullable=True)   # GeoJSON LineString
    new_route = Column(_JsonType, nullable=True)   # GeoJSON LineString
    cost_delta_pct = Column(Float, nullable=True)
    detour_pct = Column(Float, nullable=True)
    new_eta = Column(DateTime(timezone=True), nullable=True)
    sla_recovery_prob = Column(Float, nullable=True)
    reason = Column(Text, nullable=True)
    status = Column(
        String(20), nullable=False, default="pending",
    )  # auto_executed, pending, approved, rejected
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    decided_by = Column(String(100), nullable=True)  # system or dispatcher_id

    shipment = relationship("Shipment", back_populates="reroutes")


# ── Audit Log ─────────────────────────────────────────────────────────────

class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(String(36), primary_key=True, default=_gen_uuid)
    action = Column(String(100), nullable=False)
    actor = Column(String(100), nullable=False, default="system")
    shipment_id = Column(String(36), ForeignKey("shipments.id"), nullable=True)
    reroute_id = Column(String(36), ForeignKey("reroutes.id"), nullable=True)
    payload = Column(_JsonType, nullable=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
