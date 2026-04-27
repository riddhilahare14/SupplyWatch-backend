"""
Pydantic request / response schemas for the Smart Supply Chain API.
All timestamps are ISO 8601 UTC. Route geometry is GeoJSON LineString.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ── Pagination ────────────────────────────────────────────────────────────

class PaginationParams(BaseModel):
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class PaginatedResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: List[Any]


# ── GeoJSON helpers ───────────────────────────────────────────────────────

class GeoJSONPolygon(BaseModel):
    type: str = "Polygon"
    coordinates: List[List[List[float]]]


class GeoJSONLineString(BaseModel):
    type: str = "LineString"
    coordinates: List[List[float]]


# ── Warehouse ─────────────────────────────────────────────────────────────

class WarehouseOut(BaseModel):
    id: str
    name: str
    city: str
    lat: float
    lng: float

    model_config = {"from_attributes": True}


# ── Hub ───────────────────────────────────────────────────────────────────

class HubOut(BaseModel):
    id: str
    name: str
    city: str
    lat: float
    lng: float

    model_config = {"from_attributes": True}


# ── Carrier ───────────────────────────────────────────────────────────────

class CarrierOut(BaseModel):
    id: str
    name: str
    ontime_rate: float
    active: bool

    model_config = {"from_attributes": True}


# ── Lane ──────────────────────────────────────────────────────────────────

class LaneOut(BaseModel):
    id: str
    origin_hub_id: str
    dest_hub_id: str
    distance_km: float
    base_cost: float

    model_config = {"from_attributes": True}


# ── Shipment ──────────────────────────────────────────────────────────────

class ShipmentCreate(BaseModel):
    carrier_id: str
    origin_hub_id: str
    dest_hub_id: str
    sla_deadline: datetime
    contents: Optional[str] = None


class ShipmentEventOut(BaseModel):
    id: str
    shipment_id: str
    event_type: str
    lat: Optional[float] = None
    lng: Optional[float] = None
    timestamp: datetime
    metadata: Optional[Dict[str, Any]] = None

    model_config = {"from_attributes": True}


class PredictionOut(BaseModel):
    id: str
    shipment_id: str
    risk_score: float
    predicted_eta: Optional[datetime] = None
    scored_at: datetime
    model_version: Optional[str] = None

    model_config = {"from_attributes": True}


class RerouteOut(BaseModel):
    id: str
    shipment_id: str
    old_route: Optional[Any] = None
    new_route: Optional[Any] = None
    cost_delta_pct: Optional[float] = None
    detour_pct: Optional[float] = None
    new_eta: Optional[datetime] = None
    sla_recovery_prob: Optional[float] = None
    reason: Optional[str] = None
    status: str
    created_at: datetime
    decided_at: Optional[datetime] = None
    decided_by: Optional[str] = None

    model_config = {"from_attributes": True}


class ShipmentListItem(BaseModel):
    id: str
    carrier_id: str
    origin_hub_id: str
    dest_hub_id: str
    sla_deadline: datetime
    status: str
    contents: Optional[str] = None
    created_at: datetime
    # Enriched from Redis
    current_eta: Optional[datetime] = None
    risk_score: Optional[float] = None
    lat: Optional[float] = None
    lng: Optional[float] = None

    model_config = {"from_attributes": True}


class ShipmentDetail(BaseModel):
    id: str
    carrier_id: str
    origin_hub_id: str
    dest_hub_id: str
    sla_deadline: datetime
    status: str
    contents: Optional[str] = None
    created_at: datetime
    # Enriched from Redis
    current_eta: Optional[datetime] = None
    risk_score: Optional[float] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    features: Optional[Dict[str, Any]] = None
    # Related data
    events: List[ShipmentEventOut] = []
    latest_prediction: Optional[PredictionOut] = None
    active_reroute: Optional[RerouteOut] = None
    carrier: Optional[CarrierOut] = None
    origin_hub: Optional[HubOut] = None
    dest_hub: Optional[HubOut] = None

    model_config = {"from_attributes": True}


# ── Disruption ────────────────────────────────────────────────────────────

class DisruptionCreate(BaseModel):
    name: str
    type: str
    polygon: Optional[GeoJSONPolygon] = None
    severity: int = Field(default=3, ge=1, le=5)
    ends_at: Optional[datetime] = None


class DisruptionSimulate(BaseModel):
    name: str  # storm_mumbai, close_nh48, shutdown_chennai_port


class DisruptionOut(BaseModel):
    id: str
    name: str
    type: str
    severity: int
    active: bool
    started_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    polygon: Optional[Any] = None  # GeoJSON

    model_config = {"from_attributes": True}


# ── Prediction ────────────────────────────────────────────────────────────

class PredictionCreate(BaseModel):
    shipment_id: str
    risk_score: float = Field(ge=0.0, le=1.0)
    predicted_eta: Optional[datetime] = None
    model_version: Optional[str] = None


class AtRiskShipment(BaseModel):
    shipment_id: str
    risk_score: float
    predicted_eta: Optional[datetime] = None
    status: str
    carrier_id: str

    model_config = {"from_attributes": True}


# ── Reroute ───────────────────────────────────────────────────────────────

class RerouteReject(BaseModel):
    reason: str


class TriggerReroute(BaseModel):
    shipment_id: str


# ── GPS Ping (internal) ──────────────────────────────────────────────────

class GPSPing(BaseModel):
    shipment_id: str
    lat: float
    lng: float
    speed_kmh: float
    timestamp: datetime


# ── Health / Metrics ──────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    db: str
    redis: str
    kafka: str = "not_configured"


class MetricsResponse(BaseModel):
    total_shipments: int
    in_transit: int
    at_risk: int
    auto_rerouted_today: int
    pending_approvals: int
    avg_risk_score: float
    kafka_lag: int = 0


# ── WebSocket events ─────────────────────────────────────────────────────

class WSEvent(BaseModel):
    event: str
    data: Dict[str, Any]


# ── Audit Log ─────────────────────────────────────────────────────────────

class AuditLogOut(BaseModel):
    id: str
    action: str
    actor: str
    shipment_id: Optional[str] = None
    reroute_id: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    timestamp: datetime

    model_config = {"from_attributes": True}
