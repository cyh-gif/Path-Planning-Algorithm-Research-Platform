from __future__ import annotations

from typing import Protocol


class _CandidateRoute(Protocol):
    points_gcj02: list[list[float]]
    total_distance_km: float
    total_time_h: float


class TycoonLongestRouteSelector:
    """土豪算法：从候选路线中挑选总里程最长的一条。"""

    def select(self, candidates: dict[int, list[_CandidateRoute]]) -> tuple[int, _CandidateRoute]:
        best_code: int | None = None
        best_route: _CandidateRoute | None = None

        for strategy_code, routes in candidates.items():
            for route in routes:
                if len(route.points_gcj02) < 2:
                    continue
                if best_route is None:
                    best_code = strategy_code
                    best_route = route
                    continue

                if route.total_distance_km > best_route.total_distance_km:
                    best_code = strategy_code
                    best_route = route
                    continue

                # 里程并列时，优先选择耗时更长和路径点更多的路线。
                if (
                    route.total_distance_km == best_route.total_distance_km
                    and route.total_time_h > best_route.total_time_h
                ):
                    best_code = strategy_code
                    best_route = route
                    continue
                if (
                    route.total_distance_km == best_route.total_distance_km
                    and route.total_time_h == best_route.total_time_h
                    and len(route.points_gcj02) > len(best_route.points_gcj02)
                ):
                    best_code = strategy_code
                    best_route = route

        if best_route is None or best_code is None:
            raise ValueError("土豪算法未找到可用候选路线。")
        return best_code, best_route
