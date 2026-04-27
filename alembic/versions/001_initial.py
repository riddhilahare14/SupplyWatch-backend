"""Initial schema — all tables

Revision ID: 001_initial
Revises: None
Create Date: 2026-04-27
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable PostGIS
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    # ── Warehouses ────────────────────────────────────────────────
    op.create_table(
        "warehouses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("city", sa.String(100), nullable=False),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("lng", sa.Float, nullable=False),
    )

    # ── Hubs ──────────────────────────────────────────────────────
    op.create_table(
        "hubs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("city", sa.String(100), nullable=False),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("lng", sa.Float, nullable=False),
    )

    # ── Carriers ──────────────────────────────────────────────────
    op.create_table(
        "carriers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("ontime_rate", sa.Float, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
    )

    # ── Lanes ─────────────────────────────────────────────────────
    op.create_table(
        "lanes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("origin_hub_id", sa.String(36), sa.ForeignKey("hubs.id"), nullable=False),
        sa.Column("dest_hub_id", sa.String(36), sa.ForeignKey("hubs.id"), nullable=False),
        sa.Column("distance_km", sa.Float, nullable=False),
        sa.Column("base_cost", sa.Float, nullable=False, server_default="0"),
    )

    # ── Shipments ─────────────────────────────────────────────────
    op.create_table(
        "shipments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("carrier_id", sa.String(36), sa.ForeignKey("carriers.id"), nullable=False),
        sa.Column("origin_hub_id", sa.String(36), sa.ForeignKey("hubs.id"), nullable=False),
        sa.Column("dest_hub_id", sa.String(36), sa.ForeignKey("hubs.id"), nullable=False),
        sa.Column("sla_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="in_transit"),
        sa.Column("contents", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ── Shipment Events ───────────────────────────────────────────
    op.create_table(
        "shipment_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("shipment_id", sa.String(36), sa.ForeignKey("shipments.id"), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("lat", sa.Float, nullable=True),
        sa.Column("lng", sa.Float, nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
    )
    op.create_index("ix_shipment_events_shipment_id", "shipment_events", ["shipment_id"])

    # ── Disruptions ───────────────────────────────────────────────
    op.create_table(
        "disruptions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("severity", sa.Integer, nullable=False, server_default="3"),
        sa.Column("active", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("polygon_geojson", sa.Text, nullable=True),
    )
    # PostGIS geometry column
    op.execute(
        "SELECT AddGeometryColumn('disruptions', 'polygon', 4326, 'POLYGON', 2)"
    )

    # ── Predictions ───────────────────────────────────────────────
    op.create_table(
        "predictions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("shipment_id", sa.String(36), sa.ForeignKey("shipments.id"), nullable=False),
        sa.Column("risk_score", sa.Float, nullable=False),
        sa.Column("predicted_eta", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("model_version", sa.String(20), nullable=True),
    )
    op.create_index("ix_predictions_shipment_id", "predictions", ["shipment_id"])

    # ── Reroutes ──────────────────────────────────────────────────
    op.create_table(
        "reroutes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("shipment_id", sa.String(36), sa.ForeignKey("shipments.id"), nullable=False),
        sa.Column("old_route", postgresql.JSONB, nullable=True),
        sa.Column("new_route", postgresql.JSONB, nullable=True),
        sa.Column("cost_delta_pct", sa.Float, nullable=True),
        sa.Column("detour_pct", sa.Float, nullable=True),
        sa.Column("new_eta", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sla_recovery_prob", sa.Float, nullable=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(100), nullable=True),
    )
    op.create_index("ix_reroutes_shipment_id", "reroutes", ["shipment_id"])

    # ── Audit Log ─────────────────────────────────────────────────
    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False, server_default="system"),
        sa.Column("shipment_id", sa.String(36), sa.ForeignKey("shipments.id"), nullable=True),
        sa.Column("reroute_id", sa.String(36), sa.ForeignKey("reroutes.id"), nullable=True),
        sa.Column("payload", postgresql.JSONB, nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("reroutes")
    op.drop_table("predictions")
    op.drop_table("disruptions")
    op.drop_table("shipment_events")
    op.drop_table("shipments")
    op.drop_table("lanes")
    op.drop_table("carriers")
    op.drop_table("hubs")
    op.drop_table("warehouses")
