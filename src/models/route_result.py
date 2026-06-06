from dataclasses import dataclass


@dataclass(slots=True)
class RouteResult:
    path_points_wgs84: list[list[float]]
    path_points_gcj02: list[list[float]]
    total_distance_km: float
    total_time_h: float
    compute_ms: float
    node_count: int
    edge_count: int
    status: str
    message: str
    freshness_at_arrival: float | None = None
    freshness_delta_to_100: float | None = None
    debug_payload: dict[str, object] | None = None
