"""
Route optimizer using a hub graph with Dijkstra shortest paths.
Computes alternative routes and calculates detour_pct, cost_delta_pct,
new_eta, and sla_recovery_prob.
"""

import logging
import heapq
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple, Any
from math import radians, sin, cos, sqrt, atan2

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Hub, Lane, Shipment

logger = logging.getLogger(__name__)


def _haversine(lat1, lng1, lat2, lng2) -> float:
    """Distance in km between two points."""
    R = 6371
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


class HubGraph:
    """In-memory graph of hubs connected by lanes for Dijkstra routing."""

    def __init__(self):
        self._adjacency: Dict[str, List[Tuple[str, float, float]]] = {}
        # hub_id -> list of (neighbor_hub_id, distance_km, cost)
        self._hubs: Dict[str, Dict] = {}  # hub_id -> {id, name, city, lat, lng}

    async def build(self, session: AsyncSession):
        """Load hubs and lanes from DB to build the graph."""
        # Load hubs
        result = await session.execute(select(Hub))
        hubs = result.scalars().all()
        for h in hubs:
            self._hubs[h.id] = {"id": h.id, "name": h.name, "city": h.city,
                                "lat": h.lat, "lng": h.lng}
            self._adjacency.setdefault(h.id, [])

        # Load lanes
        result = await session.execute(select(Lane))
        lanes = result.scalars().all()
        for lane in lanes:
            self._adjacency.setdefault(lane.origin_hub_id, []).append(
                (lane.dest_hub_id, lane.distance_km, lane.base_cost)
            )

        logger.info("Hub graph built: %d hubs, %d lane edges",
                     len(self._hubs), len(lanes))

    def dijkstra(self, start_hub_id: str, end_hub_id: str,
                 excluded_hubs: Optional[set] = None) -> Optional[Dict]:
        """
        Find shortest path by distance from start to end hub.
        Returns: {path: [hub_ids], distance_km, total_cost} or None
        """
        if start_hub_id not in self._adjacency or end_hub_id not in self._adjacency:
            return None

        excluded = excluded_hubs or set()

        # (distance, hub_id, path, cost)
        heap = [(0.0, start_hub_id, [start_hub_id], 0.0)]
        visited = set()

        while heap:
            dist, current, path, cost = heapq.heappop(heap)

            if current in visited:
                continue
            visited.add(current)

            if current == end_hub_id:
                return {
                    "path": path,
                    "distance_km": round(dist, 1),
                    "total_cost": round(cost, 2),
                }

            for neighbor, edge_dist, edge_cost in self._adjacency.get(current, []):
                if neighbor not in visited and neighbor not in excluded:
                    heapq.heappush(heap, (
                        dist + edge_dist,
                        neighbor,
                        path + [neighbor],
                        cost + edge_cost,
                    ))

        return None  # No path found

    def path_to_geojson(self, path: List[str]) -> Dict:
        """Convert a list of hub IDs to GeoJSON LineString."""
        coords = []
        for hub_id in path:
            hub = self._hubs.get(hub_id)
            if hub:
                coords.append([hub["lng"], hub["lat"]])
        return {"type": "LineString", "coordinates": coords}

    def get_hub(self, hub_id: str) -> Optional[Dict]:
        return self._hubs.get(hub_id)


# Global singleton
hub_graph = HubGraph()


async def compute_alternative_route(
    session: AsyncSession,
    shipment: Shipment,
    current_hub_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Compute an alternative route for a shipment.
    Returns route details including detour_pct, cost_delta_pct, new_eta, sla_recovery_prob.
    """
    if not hub_graph._hubs:
        await hub_graph.build(session)

    origin_id = current_hub_id or shipment.origin_hub_id
    dest_id = shipment.dest_hub_id

    # Original route
    original = hub_graph.dijkstra(shipment.origin_hub_id, dest_id)
    if not original:
        logger.warning("No original route found for shipment %s", shipment.id)
        return None

    # Alternative: exclude the second hub in original path to force different route
    excluded = set()
    if len(original["path"]) > 2:
        excluded.add(original["path"][1])

    alternative = hub_graph.dijkstra(origin_id, dest_id, excluded_hubs=excluded)
    if not alternative:
        # Try without exclusions but from current position
        alternative = hub_graph.dijkstra(origin_id, dest_id)
    if not alternative:
        return None

    # Calculate metrics
    original_dist = original["distance_km"]
    alt_dist = alternative["distance_km"]
    detour_pct = ((alt_dist - original_dist) / max(original_dist, 1)) * 100

    original_cost = original["total_cost"]
    alt_cost = alternative["total_cost"]
    cost_delta_pct = ((alt_cost - original_cost) / max(original_cost, 1)) * 100

    # Estimate new ETA (assume avg speed 50 kmh)
    avg_speed = 50
    eta_hours = alt_dist / avg_speed
    new_eta = datetime.now(timezone.utc) + timedelta(hours=eta_hours)

    # SLA recovery probability
    sla_recovery_prob = 0.0
    if shipment.sla_deadline:
        deadline = shipment.sla_deadline
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        time_left = (deadline - now).total_seconds() / 3600
        if time_left > 0:
            sla_recovery_prob = min(1.0, max(0.0, time_left / max(eta_hours, 0.1)))

    return {
        "old_route": hub_graph.path_to_geojson(original["path"]),
        "new_route": hub_graph.path_to_geojson(alternative["path"]),
        "old_distance_km": original_dist,
        "new_distance_km": alt_dist,
        "detour_pct": round(detour_pct, 1),
        "cost_delta_pct": round(cost_delta_pct, 1),
        "new_eta": new_eta,
        "sla_recovery_prob": round(sla_recovery_prob, 2),
        "old_cost": original_cost,
        "new_cost": alt_cost,
    }
