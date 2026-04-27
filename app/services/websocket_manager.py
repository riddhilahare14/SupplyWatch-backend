"""
WebSocket connection manager.
Maintains a pool of connected clients and broadcasts events.
Event types: gps_update, risk_update, disruption_new, reroute_created, reroute_decided
"""

import json
import logging
from typing import Dict, Any, List

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Manages WebSocket connections and broadcasts events to all clients."""

    def __init__(self):
        self._connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self._connections.append(websocket)
        logger.info("WebSocket client connected (%d total)", len(self._connections))

    def disconnect(self, websocket: WebSocket):
        if websocket in self._connections:
            self._connections.remove(websocket)
        logger.info("WebSocket client disconnected (%d remaining)", len(self._connections))

    async def broadcast(self, event: str, data: Dict[str, Any]):
        """Send an event to all connected clients."""
        message = json.dumps({"event": event, "data": data})
        dead = []
        for ws in self._connections:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        # Clean up dead connections
        for ws in dead:
            self.disconnect(ws)

    async def send_gps_update(self, shipment_id: str, lat: float, lng: float, timestamp: str):
        await self.broadcast("gps_update", {
            "shipment_id": shipment_id, "lat": lat, "lng": lng, "timestamp": timestamp,
        })

    async def send_risk_update(self, shipment_id: str, risk_score: float,
                                predicted_eta: str = None):
        await self.broadcast("risk_update", {
            "shipment_id": shipment_id, "risk_score": risk_score,
            "predicted_eta": predicted_eta,
        })

    async def send_disruption_new(self, disruption_id: str, name: str, polygon: Any = None):
        await self.broadcast("disruption_new", {
            "disruption_id": disruption_id, "name": name, "polygon": polygon,
        })

    async def send_reroute_created(self, reroute_id: str, shipment_id: str,
                                    status: str, reason: str = None):
        await self.broadcast("reroute_created", {
            "reroute_id": reroute_id, "shipment_id": shipment_id,
            "status": status, "reason": reason,
        })

    async def send_reroute_decided(self, reroute_id: str, status: str, decided_by: str):
        await self.broadcast("reroute_decided", {
            "reroute_id": reroute_id, "status": status, "decided_by": decided_by,
        })

    @property
    def connection_count(self) -> int:
        return len(self._connections)


# Global singleton
ws_manager = WebSocketManager()
