from dataclasses import dataclass

from src.models.route_result import RouteResult


@dataclass(slots=True)
class MapPayload:
    points: list[list[float]]
    meta: dict[str, object]

    @classmethod
    def from_route_result(
        cls,
        result: RouteResult,
        start_text: str,
        end_text: str,
        algorithm: str,
        fruit_type: str,
    ) -> "MapPayload":
        return cls(
            points=result.path_points_gcj02,
            meta={
                "start": start_text,
                "end": end_text,
                "algorithm": algorithm,
                "strategy": algorithm,
                "fruit": fruit_type,
                "distance_km": round(result.total_distance_km, 3),
                "total_time_h": round(result.total_time_h, 3),
                "status": result.status,
                "message": result.message,
                "freshness_at_arrival": (
                    round(result.freshness_at_arrival, 3)
                    if result.freshness_at_arrival is not None
                    else None
                ),
                "freshness_delta_to_100": (
                    round(result.freshness_delta_to_100, 3)
                    if result.freshness_delta_to_100 is not None
                    else None
                ),
            },
        )

    def to_dict(self) -> dict[str, object]:
        return {"points": self.points, "meta": self.meta}
