from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import math
import threading
import time
from typing import Any
import urllib.parse
import urllib.request
from urllib.error import URLError


LOGGER = logging.getLogger(__name__)


class AMapServiceError(RuntimeError):
    """高德 Web Service 调用失败。"""


@dataclass(slots=True, frozen=True)
class PlannedRoute:
    points_gcj02: list[list[float]]
    total_distance_km: float
    total_time_h: float
    segment_count: int
    tmcs_status_ratio: dict[str, float] | None = None
    curvature_multiplier: float = 1.0


class AMapWebServiceClient:
    _GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo"
    _DRIVING_URL = "https://restapi.amap.com/v3/direction/driving"
    _MAX_CANDIDATE_PATHS_PER_STRATEGY = 3
    _MAX_DENSIFY_POINTS = 12000

    _INFOCODE_HINTS: dict[str, str] = {
        "10001": "高德 Key 无效或未生效",
        "10003": "高德访问受限，请检查白名单配置",
        "10004": "高德配额已用尽",
        "10007": "高德签名校验失败，请检查安全密钥",
        "10029": "高德请求频率过高，请稍后重试",
    }

    def __init__(
        self,
        web_service_key: str,
        request_timeout_s: float = 1.2,
        retry: int = 1,
        cache_ttl_s: int = 45,
    ) -> None:
        self.web_service_key = web_service_key.strip()
        self.request_timeout_s = max(0.3, float(request_timeout_s))
        self.retry = max(0, int(retry))
        self.cache_ttl_s = max(5, int(cache_ttl_s))

        self._cache_lock = threading.Lock()
        self._geocode_cache: dict[str, tuple[float, tuple[float, float]]] = {}
        self._route_cache: dict[str, tuple[float, list[PlannedRoute]]] = {}

    def geocode(self, address: str) -> tuple[float, float]:
        normalized = address.strip()
        if not normalized:
            raise AMapServiceError("地点不能为空。")

        cache_key = normalized.lower()
        now = time.time()
        with self._cache_lock:
            cache_hit = self._geocode_cache.get(cache_key)
            if cache_hit and (now - cache_hit[0] <= self.cache_ttl_s):
                return cache_hit[1]

        payload = self._request_json(self._GEOCODE_URL, {"address": normalized})
        geocodes = payload.get("geocodes", [])
        if not isinstance(geocodes, list) or not geocodes:
            raise AMapServiceError(f"高德未返回地点坐标: {normalized}")

        location = str(geocodes[0].get("location", "")).strip()
        lon, lat = self._parse_location(location, f"地点解析失败: {normalized}")

        with self._cache_lock:
            self._geocode_cache[cache_key] = (now, (lon, lat))
            self._prune_cache(now)

        return lon, lat

    def plan_driving_route(
        self,
        origin_gcj02: tuple[float, float],
        destination_gcj02: tuple[float, float],
        strategy: int,
        waypoints_gcj02: list[tuple[float, float]] | None = None,
    ) -> PlannedRoute:
        routes = self._plan_driving_route_list(
            origin_gcj02=origin_gcj02,
            destination_gcj02=destination_gcj02,
            strategy=strategy,
            waypoints_gcj02=waypoints_gcj02,
            max_paths_per_strategy=1,
            use_tmcs=False,
            densify_max_segment_m=0.0,
        )
        return self._clone_route(routes[0])

    def plan_driving_route_candidates(
        self,
        origin_gcj02: tuple[float, float],
        destination_gcj02: tuple[float, float],
        strategies: list[int],
        max_paths_per_strategy: int = 2,
        use_tmcs: bool = True,
        densify_max_segment_m: float = 80.0,
    ) -> dict[int, list[PlannedRoute]]:
        """按多策略拉取候选路径，每个策略可返回多条路径。"""
        result: dict[int, list[PlannedRoute]] = {}
        visited: set[int] = set()
        for raw_code in strategies:
            try:
                code = int(raw_code)
            except (TypeError, ValueError):
                continue
            if code in visited:
                continue
            visited.add(code)
            try:
                routes = self._plan_driving_route_list(
                    origin_gcj02=origin_gcj02,
                    destination_gcj02=destination_gcj02,
                    strategy=code,
                    waypoints_gcj02=None,
                    max_paths_per_strategy=max_paths_per_strategy,
                    use_tmcs=use_tmcs,
                    densify_max_segment_m=densify_max_segment_m,
                )
            except AMapServiceError as exc:
                LOGGER.debug("候选策略 %s 请求失败: %s", code, exc)
                continue
            if routes:
                result[code] = self._clone_routes(routes)

        if not result:
            raise AMapServiceError("高德候选路径获取失败，请检查网络或 Key 配置。")
        return result

    def _plan_driving_route_list(
        self,
        origin_gcj02: tuple[float, float],
        destination_gcj02: tuple[float, float],
        strategy: int,
        waypoints_gcj02: list[tuple[float, float]] | None,
        max_paths_per_strategy: int,
        use_tmcs: bool,
        densify_max_segment_m: float,
    ) -> list[PlannedRoute]:
        origin_text = f"{origin_gcj02[0]:.6f},{origin_gcj02[1]:.6f}"
        destination_text = f"{destination_gcj02[0]:.6f},{destination_gcj02[1]:.6f}"
        waypoints_text = self._build_waypoints_text(waypoints_gcj02)
        max_paths = self._normalize_max_paths(max_paths_per_strategy)
        densify_threshold_m = max(0.0, float(densify_max_segment_m))
        extensions = "all" if use_tmcs else "base"

        cache_key = (
            f"{origin_text}|{destination_text}|{int(strategy)}|{waypoints_text}|{extensions}|"
            f"{max_paths}|{densify_threshold_m:.1f}"
        )
        now = time.time()
        with self._cache_lock:
            cache_hit = self._route_cache.get(cache_key)
            if cache_hit and (now - cache_hit[0] <= self.cache_ttl_s):
                return self._clone_routes(cache_hit[1])

        request_params: dict[str, str] = {
            "origin": origin_text,
            "destination": destination_text,
            "strategy": str(int(strategy)),
            "extensions": extensions,
        }
        if waypoints_text:
            request_params["waypoints"] = waypoints_text

        payload = self._request_json(self._DRIVING_URL, request_params)

        route = payload.get("route", {})
        paths = route.get("paths", []) if isinstance(route, dict) else []
        if not isinstance(paths, list) or not paths:
            raise AMapServiceError("高德未返回可用路径，请尝试更换起终点或策略。")

        planned_routes: list[PlannedRoute] = []
        for raw_path in paths:
            if not isinstance(raw_path, dict):
                continue
            planned = self._build_planned_route_from_path(
                path=raw_path,
                origin_gcj02=origin_gcj02,
                destination_gcj02=destination_gcj02,
                use_tmcs=bool(use_tmcs),
                densify_max_segment_m=densify_threshold_m,
            )
            if len(planned.points_gcj02) < 2:
                continue
            planned_routes.append(planned)
            if len(planned_routes) >= max_paths:
                break

        if not planned_routes:
            raise AMapServiceError("高德返回了路径，但缺少可用折线数据。")

        with self._cache_lock:
            self._route_cache[cache_key] = (now, self._clone_routes(planned_routes))
            self._prune_cache(now)

        return planned_routes

    def _build_planned_route_from_path(
        self,
        path: dict[str, Any],
        origin_gcj02: tuple[float, float],
        destination_gcj02: tuple[float, float],
        use_tmcs: bool,
        densify_max_segment_m: float,
    ) -> PlannedRoute:
        points = self._extract_points_from_path(path, use_tmcs=use_tmcs)
        if len(points) < 2:
            points = [
                [round(origin_gcj02[0], 6), round(origin_gcj02[1], 6)],
                [round(destination_gcj02[0], 6), round(destination_gcj02[1], 6)],
            ]

        if densify_max_segment_m > 0:
            points = self._densify_polyline(points, densify_max_segment_m)

        distance_m = self._safe_float(path.get("distance"))
        if distance_m <= 0:
            distance_m = self._polyline_distance_m(points)
        duration_s = self._safe_float(path.get("duration"))
        if duration_s <= 0:
            duration_s = max(distance_m / 12.0, 1.0)

        steps = path.get("steps", [])
        segment_count = len(steps) if isinstance(steps, list) and steps else max(len(points) - 1, 0)
        tmcs_status_ratio = self._extract_tmcs_status_ratio(path)
        curvature_multiplier = self._estimate_curvature_multiplier(points)

        return PlannedRoute(
            points_gcj02=points,
            total_distance_km=round(distance_m / 1000.0, 3),
            total_time_h=round(duration_s / 3600.0, 3),
            segment_count=segment_count,
            tmcs_status_ratio=tmcs_status_ratio,
            curvature_multiplier=curvature_multiplier,
        )

    def _extract_points_from_path(self, path: dict[str, Any], use_tmcs: bool) -> list[list[float]]:
        steps = path.get("steps", [])
        points: list[list[float]] = []
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                step_points = self._extract_step_points(step, use_tmcs=use_tmcs)
                self._merge_segment(points, step_points)

        if len(points) >= 2:
            return points

        fallback_path_polyline = self._parse_polyline(str(path.get("polyline", "")).strip())
        if len(fallback_path_polyline) >= 2:
            return fallback_path_polyline
        return points

    def _extract_step_points(self, step: dict[str, Any], use_tmcs: bool) -> list[list[float]]:
        if use_tmcs:
            tmcs_points: list[list[float]] = []
            tmcs = step.get("tmcs", [])
            if isinstance(tmcs, list):
                for item in tmcs:
                    if not isinstance(item, dict):
                        continue
                    segment = self._parse_polyline(str(item.get("polyline", "")).strip())
                    self._merge_segment(tmcs_points, segment)
            if len(tmcs_points) >= 2:
                return tmcs_points

        return self._parse_polyline(str(step.get("polyline", "")).strip())

    def _densify_polyline(self, points: list[list[float]], max_segment_m: float) -> list[list[float]]:
        if len(points) < 2 or max_segment_m <= 0:
            return [[round(point[0], 6), round(point[1], 6)] for point in points]

        rounded_points = [[round(point[0], 6), round(point[1], 6)] for point in points]
        max_points = max(2, int(self._MAX_DENSIFY_POINTS))
        if len(rounded_points) >= max_points:
            sampled: list[list[float]] = []
            last_index = len(rounded_points) - 1
            for output_index in range(max_points):
                source_index = round(output_index * last_index / (max_points - 1))
                point = rounded_points[source_index]
                if sampled and sampled[-1] == point:
                    continue
                sampled.append(point)
            if sampled[-1] != rounded_points[-1]:
                sampled[-1] = rounded_points[-1]
            return sampled

        segment_distances: list[float] = []
        desired_extra_points: list[int] = []
        total_desired_extra = 0
        for idx in range(len(points) - 1):
            p1 = points[idx]
            p2 = points[idx + 1]
            dist_m = self._haversine_m(p1[0], p1[1], p2[0], p2[1])
            segment_distances.append(dist_m)
            extra_count = max(0, int(math.ceil(dist_m / max_segment_m)) - 1)
            desired_extra_points.append(extra_count)
            total_desired_extra += extra_count

        remaining_budget = max_points - len(rounded_points)
        if total_desired_extra <= remaining_budget:
            allocated_extra_points = desired_extra_points
        else:
            allocated_extra_points = [0] * len(desired_extra_points)
            if remaining_budget > 0 and total_desired_extra > 0:
                remainders: list[tuple[float, int]] = []
                assigned = 0
                for idx, desired in enumerate(desired_extra_points):
                    if desired <= 0:
                        continue
                    quota = desired * remaining_budget / total_desired_extra
                    base = min(desired, int(math.floor(quota)))
                    allocated_extra_points[idx] = base
                    assigned += base
                    remainders.append((quota - base, idx))

                leftover = remaining_budget - assigned
                for _, idx in sorted(remainders, key=lambda item: item[0], reverse=True):
                    if leftover <= 0:
                        break
                    if allocated_extra_points[idx] >= desired_extra_points[idx]:
                        continue
                    allocated_extra_points[idx] += 1
                    leftover -= 1

        dense: list[list[float]] = [rounded_points[0]]
        for idx in range(len(points) - 1):
            p1 = points[idx]
            p2 = points[idx + 1]
            extra_count = allocated_extra_points[idx]
            segments = extra_count + 1
            for step in range(1, segments):
                ratio = step / segments
                lon = p1[0] + (p2[0] - p1[0]) * ratio
                lat = p1[1] + (p2[1] - p1[1]) * ratio
                dense.append([round(lon, 6), round(lat, 6)])
            dense.append(rounded_points[idx + 1])

        if dense[-1] != rounded_points[-1]:
            dense.append(rounded_points[-1])
        return dense[:max_points]

    def _polyline_distance_m(self, points_gcj02: list[list[float]]) -> float:
        if len(points_gcj02) < 2:
            return 0.0
        total_m = 0.0
        for idx in range(len(points_gcj02) - 1):
            p1 = points_gcj02[idx]
            p2 = points_gcj02[idx + 1]
            total_m += self._haversine_m(p1[0], p1[1], p2[0], p2[1])
        return total_m

    def _normalize_max_paths(self, raw_value: int) -> int:
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            value = 1
        value = max(1, value)
        return min(value, self._MAX_CANDIDATE_PATHS_PER_STRATEGY)

    def _request_json(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        if not self.web_service_key:
            raise AMapServiceError("未配置高德 Web Service Key。")

        query = dict(params)
        query["key"] = self.web_service_key
        full_url = f"{url}?{urllib.parse.urlencode(query)}"

        last_exc: Exception | None = None
        attempts = self.retry + 1
        for index in range(attempts):
            try:
                with urllib.request.urlopen(full_url, timeout=self.request_timeout_s) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if str(payload.get("status")) != "1":
                    raise self._build_api_error(payload)
                return payload
            except AMapServiceError:
                raise
            except Exception as exc:
                last_exc = exc
                if index < attempts - 1:
                    LOGGER.debug(
                        "高德请求失败，准备重试(%s/%s): %s",
                        index + 1,
                        attempts,
                        exc,
                    )
                    continue

        if isinstance(last_exc, TimeoutError):
            raise AMapServiceError("高德请求超时，请检查网络后重试。") from last_exc
        if isinstance(last_exc, URLError):
            raise AMapServiceError("高德请求失败，请检查网络连接。") from last_exc
        raise AMapServiceError(f"高德请求失败: {last_exc}") from last_exc

    def _build_api_error(self, payload: dict[str, Any]) -> AMapServiceError:
        info = str(payload.get("info", "")).strip()
        infocode = str(payload.get("infocode", "")).strip()
        hint = self._INFOCODE_HINTS.get(infocode)
        if hint:
            return AMapServiceError(f"{hint}(infocode={infocode})")
        if info:
            return AMapServiceError(f"高德接口返回错误: {info}")
        return AMapServiceError("高德接口返回错误。")

    def _parse_location(self, location: str, fallback_message: str) -> tuple[float, float]:
        if "," not in location:
            raise AMapServiceError(fallback_message)
        lon_text, lat_text = location.split(",", 1)
        try:
            lon = round(float(lon_text.strip()), 6)
            lat = round(float(lat_text.strip()), 6)
        except ValueError as exc:
            raise AMapServiceError(fallback_message) from exc
        return lon, lat

    def _parse_polyline(self, polyline: str) -> list[list[float]]:
        if not polyline:
            return []
        parsed: list[list[float]] = []
        for token in polyline.split(";"):
            token = token.strip()
            if not token or "," not in token:
                continue
            lon_text, lat_text = token.split(",", 1)
            try:
                lon = round(float(lon_text.strip()), 6)
                lat = round(float(lat_text.strip()), 6)
            except ValueError:
                continue
            parsed.append([lon, lat])
        return parsed

    def _build_waypoints_text(self, waypoints_gcj02: list[tuple[float, float]] | None) -> str:
        if not waypoints_gcj02:
            return ""
        points: list[str] = []
        for waypoint in waypoints_gcj02:
            if not isinstance(waypoint, tuple) or len(waypoint) != 2:
                continue
            try:
                lon = round(float(waypoint[0]), 6)
                lat = round(float(waypoint[1]), 6)
            except (TypeError, ValueError):
                continue
            points.append(f"{lon:.6f},{lat:.6f}")
        return ";".join(points)

    def _merge_segment(self, target: list[list[float]], segment: list[list[float]]) -> None:
        if not segment:
            return
        if not target:
            target.extend(segment)
            return
        start_index = 1 if target[-1] == segment[0] else 0
        for point in segment[start_index:]:
            target.append(point)

    def _clone_route(self, route: PlannedRoute) -> PlannedRoute:
        tmcs_ratio: dict[str, float] | None = None
        if isinstance(route.tmcs_status_ratio, dict):
            tmcs_ratio = {str(k): float(v) for k, v in route.tmcs_status_ratio.items()}
        return PlannedRoute(
            points_gcj02=[[point[0], point[1]] for point in route.points_gcj02],
            total_distance_km=route.total_distance_km,
            total_time_h=route.total_time_h,
            segment_count=route.segment_count,
            tmcs_status_ratio=tmcs_ratio,
            curvature_multiplier=float(route.curvature_multiplier),
        )

    def _clone_routes(self, routes: list[PlannedRoute]) -> list[PlannedRoute]:
        return [self._clone_route(route) for route in routes]

    def _safe_float(self, value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _haversine_m(self, lon1: float, lat1: float, lon2: float, lat2: float) -> float:
        if lon1 == lon2 and lat1 == lat2:
            return 0.0

        lon1_r = math.radians(lon1)
        lat1_r = math.radians(lat1)
        lon2_r = math.radians(lon2)
        lat2_r = math.radians(lat2)

        dlon = lon2_r - lon1_r
        dlat = lat2_r - lat1_r
        a = math.sin(dlat / 2.0) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2.0) ** 2
        c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
        return 6371000.0 * c

    def _extract_tmcs_status_ratio(self, path: dict[str, Any]) -> dict[str, float]:
        steps = path.get("steps", [])
        if not isinstance(steps, list) or not steps:
            return {"unknown": 1.0}

        status_distance: dict[str, float] = {}
        total_m = 0.0
        for step in steps:
            if not isinstance(step, dict):
                continue
            tmcs_items = step.get("tmcs", [])
            if not isinstance(tmcs_items, list):
                continue
            for item in tmcs_items:
                if not isinstance(item, dict):
                    continue
                status = self._normalize_tmcs_status(item.get("status"))
                dist_m = self._safe_float(item.get("distance"))
                if dist_m <= 0:
                    segment = self._parse_polyline(str(item.get("polyline", "")).strip())
                    dist_m = self._polyline_distance_m(segment)
                if dist_m <= 0:
                    continue
                status_distance[status] = status_distance.get(status, 0.0) + dist_m
                total_m += dist_m

        if total_m <= 0:
            return {"unknown": 1.0}

        ratio = {status: round(distance / total_m, 6) for status, distance in status_distance.items()}
        ratio_sum = sum(ratio.values())
        if ratio_sum <= 0:
            return {"unknown": 1.0}
        if ratio_sum < 0.999:
            ratio["unknown"] = round(1.0 - ratio_sum, 6)
        return ratio

    def _normalize_tmcs_status(self, raw_status: Any) -> str:
        text = str(raw_status).strip().lower()
        if not text:
            return "unknown"
        if "严重拥堵" in text or "非常拥堵" in text or "severe" in text:
            return "severe_congested"
        if "拥堵" in text or "jam" in text:
            return "congested"
        if "缓行" in text or "slow" in text:
            return "slow"
        if "畅通" in text or "smooth" in text:
            return "smooth"
        return "unknown"

    def _estimate_curvature_multiplier(self, points: list[list[float]]) -> float:
        if len(points) < 2:
            return 1.0
        path_length_m = self._polyline_distance_m(points)
        direct_m = self._haversine_m(points[0][0], points[0][1], points[-1][0], points[-1][1])
        if path_length_m <= 0 or direct_m <= 0:
            return 1.0
        ratio = path_length_m / max(direct_m, 1.0)
        bend_extra = max(0.0, ratio - 1.0)
        multiplier = 1.0 + min(bend_extra, 1.5) * 0.25
        return round(min(max(multiplier, 1.0), 1.45), 4)

    def _prune_cache(self, now: float) -> None:
        geocode_expired = [
            key for key, (ts, _) in self._geocode_cache.items() if (now - ts) > self.cache_ttl_s
        ]
        route_expired = [
            key for key, (ts, _) in self._route_cache.items() if (now - ts) > self.cache_ttl_s
        ]
        for key in geocode_expired:
            self._geocode_cache.pop(key, None)
        for key in route_expired:
            self._route_cache.pop(key, None)
