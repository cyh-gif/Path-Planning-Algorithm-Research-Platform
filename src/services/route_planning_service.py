from __future__ import annotations

from datetime import datetime
import logging
import math
from pathlib import Path
import time

from src.algorithms.a_star_shortest_path import AStarShortestPathSolver
from src.algorithms.freshness_dijkstra_improved import FreshnessDijkstraImprovedSolver
from src.algorithms.graph_builder import GraphData, GraphEdge, haversine_km
from src.algorithms.greedy_best_first_path import GreedyBestFirstPathSolver
from src.algorithms.static_shortest_path import PathSolveResult, StaticShortestPathSolver
from src.algorithms.target_freshness_k_shortest_path import TargetFreshnessKShortestPathSolver
from src.algorithms.target_freshness_lexicographic_a_star import TargetFreshnessLexicographicAStarSolver
from src.algorithms.target_freshness_label_search import TargetFreshnessAdaptiveLabelSearchSolver
from src.algorithms.time_dependent_shortest_path import TimeDependentShortestPathSolver
from src.algorithms.tycoon_longest_route import TycoonLongestRouteSelector
from src.models.route_request import RouteRequest
from src.models.route_result import RouteResult
from src.services.amap_web_service import AMapServiceError, AMapWebServiceClient, PlannedRoute
from src.services.freshness_profile_loader import FruitProfile, FruitProfileLoadError, load_fruit_profiles
from src.utils.coord_transform import batch_gcj02_to_wgs84, batch_wgs84_to_gcj02, gcj02_to_wgs84


LOGGER = logging.getLogger(__name__)


class RoutePlanningService:
    """统一路径规划入口：仅使用高德数据，支持高德策略与自研算法。"""

    _DEFAULT_AMAP_STRATEGY_MAP: dict[str, int] = {
        "速度优先": 0,
        "费用优先": 1,
        "常规最快": 2,
        "躲避拥堵": 12,
        "不走高速": 13,
        "少走高速": 6,
        "避免收费": 14,
        "综合推荐(多路径)": 10,
        "躲避拥堵且不走高速": 15,
        "避免收费且不走高速": 16,
        "躲避拥堵且避免收费": 17,
        "躲避拥堵且避免收费且不走高速": 18,
        "高速优先": 19,
        "高速优先且躲避拥堵": 20,
    }

    _DEFAULT_CUSTOM_ALGORITHM_MAP: dict[str, str] = {
        "自研-Dijkstra": "static_dijkstra",
        "自研-时变Dijkstra": "time_dependent_dijkstra",
        "自研-A*": "a_star",
        "贪心算法": "greedy_best_first",
        "土豪算法": "tycoon_longest_route",
        "保鲜优先算法": "freshness_first",
        "保鲜优先算法-迪杰斯特拉算法改进版": "freshness_dijkstra_improved",
        "目标保鲜偏差算法(ATD-LS)": "target_freshness_atd_ls",
        "目标保鲜K最短路算法(TF-KSP)": "target_freshness_tf_ksp",
        "目标保鲜字典序A*算法(TF-LA*)": "target_freshness_tf_la_star",
    }

    _CUSTOM_ALGO_ALIAS: dict[str, str] = {
        "dijkstra": "static_dijkstra",
        "时变dijkstra": "time_dependent_dijkstra",
        "time-dependent dijkstra": "time_dependent_dijkstra",
        "a*": "a_star",
        "astar": "a_star",
        "a-star": "a_star",
        "a星": "a_star",
        "贪心": "greedy_best_first",
        "greedy": "greedy_best_first",
        "greedy best first": "greedy_best_first",
        "土豪": "tycoon_longest_route",
        "longest": "tycoon_longest_route",
        "保鲜": "freshness_first",
        "保险": "freshness_first",
        "freshness": "freshness_first",
        "freshness first": "freshness_first",
        "保鲜优先算法-迪杰斯特拉算法改进版": "freshness_dijkstra_improved",
        "freshness dijkstra improved": "freshness_dijkstra_improved",
        "freshness dijkstra": "freshness_dijkstra_improved",
        "atd-ls": "target_freshness_atd_ls",
        "target freshness": "target_freshness_atd_ls",
        "target freshness label search": "target_freshness_atd_ls",
        "目标保鲜": "target_freshness_atd_ls",
        "目标保鲜偏差": "target_freshness_atd_ls",
        "tf-ksp": "target_freshness_tf_ksp",
        "tf ksp": "target_freshness_tf_ksp",
        "target freshness ksp": "target_freshness_tf_ksp",
        "target freshness k shortest path": "target_freshness_tf_ksp",
        "target freshness k shortest paths": "target_freshness_tf_ksp",
        "目标保鲜k最短路": "target_freshness_tf_ksp",
        "目标保鲜k最短路径": "target_freshness_tf_ksp",
        "tf-la*": "target_freshness_tf_la_star",
        "tf la*": "target_freshness_tf_la_star",
        "tf la star": "target_freshness_tf_la_star",
        "tf-la-star": "target_freshness_tf_la_star",
        "target freshness la*": "target_freshness_tf_la_star",
        "target freshness lexicographic a*": "target_freshness_tf_la_star",
        "target freshness lexicographic astar": "target_freshness_tf_la_star",
        "目标保鲜字典序a*": "target_freshness_tf_la_star",
        "目标保鲜字典序astar": "target_freshness_tf_la_star",
    }

    _DEFAULT_CUSTOM_CANDIDATE_STRATEGIES: list[int] = [0, 12, 13, 14, 19]
    _DEFAULT_DIVERGENCE_ANCHOR_RATIOS: list[float] = [0.35, 0.65]
    _DEFAULT_DIVERGENCE_OFFSETS_M: list[float] = [300.0, 600.0]
    # 最大候选数配置
    _MAX_CUSTOM_CANDIDATE_COUNT: int = 5
    _MAX_CUSTOM_PATHS_PER_STRATEGY: int = 3
    _MAX_DIVERGENCE_ANCHOR_COUNT: int = 2
    _MAX_DIVERGENCE_OFFSET_COUNT: int = 2
    _MAX_DIVERGENCE_EXTRA_ROUTES_PER_STRATEGY: int = 6
    _MAX_FRESHNESS_COMPARE_STRATEGY_COUNT: int = 30
    _NO_HIGHWAY_STRATEGIES: set[int] = {3, 6, 7, 9, 13, 15, 16, 18}
    _TRAFFIC_AVOID_STRATEGIES: set[int] = {4, 8, 9, 10, 12, 15, 17, 18, 20}
    _DEFAULT_FRESHNESS_TARGET: float = 100.0
    _DEFAULT_FRESHNESS_BASE_LOSS_PER_HOUR: float = 2.0
    _DEFAULT_FRESHNESS_MAX_DETOUR_RATIO: float = 1.35
    _DEFAULT_TRANSPORT_MODE_MULTIPLIERS: dict[str, float] = {
        "公路冷链": 0.72,
        "公路常温": 1.0,
        "铁路联运": 0.88,
        "多式联运": 0.95,
    }
    _DEFAULT_ROAD_CLASS_MULTIPLIERS: dict[str, float] = {
        "normal": 1.0,
        "traffic_avoid": 0.95,
        "no_highway": 1.15,
    }
    _DEFAULT_TMCS_STATUS_MULTIPLIERS: dict[str, float] = {
        "smooth": 0.95,
        "slow": 1.05,
        "congested": 1.18,
        "severe_congested": 1.32,
        "unknown": 1.0,
    }
    _DEFAULT_FRUIT_PROFILES: list[FruitProfile] = [
        FruitProfile("apple", "苹果", 100.0, 0.85),
        FruitProfile("banana", "香蕉", 118.0, 0.95),
        FruitProfile("citrus", "柑橘", 100.0, 0.90),
        FruitProfile("grape", "葡萄", 100.0, 1.15),
        FruitProfile("strawberry", "草莓", 100.0, 1.60),
        FruitProfile("lychee", "荔枝", 100.0, 1.95),
        FruitProfile("mango", "芒果", 112.0, 1.05),
    ]

    def __init__(
        self,
        amap_client: AMapWebServiceClient,
        amap_strategy_map: dict[str, int] | None = None,
        custom_algorithm_map: dict[str, str] | None = None,
        custom_candidate_strategy_codes: list[int] | None = None,
        custom_candidate_max_paths_per_strategy: int = 2,
        custom_candidate_use_tmcs: bool = True,
        custom_candidate_densify_max_segment_m: float = 80.0,
        custom_candidate_enable_divergence: bool = False,
        custom_candidate_divergence_anchor_ratios: list[float] | None = None,
        custom_candidate_divergence_offsets_m: list[float] | None = None,
        default_strategy: str = "速度优先",
        peak_hours: list[int] | None = None,
        peak_multiplier: float = 1.35,
        freshness_target: float = 100.0,
        freshness_base_loss_per_hour: float = 2.0,
        freshness_max_detour_ratio: float = 1.35,
        freshness_arbitration_scope: str = "amap_only",
        freshness_amap_compare_strategy_codes: list[int] | None = None,
        freshness_amap_compare_max_paths_per_strategy: int = 3,
        freshness_arbitration_time_budget_s: float = 9.0,
        freshness_transport_mode_multipliers: dict[str, float] | None = None,
        freshness_road_class_multipliers: dict[str, float] | None = None,
        freshness_tmcs_status_multipliers: dict[str, float] | None = None,
        fruit_profile_json_path: str | Path | None = None,
    ) -> None:
        self.amap_client = amap_client

        self.amap_strategy_map = dict(self._DEFAULT_AMAP_STRATEGY_MAP)
        if amap_strategy_map:
            for key, value in amap_strategy_map.items():
                name = str(key).strip()
                if not name:
                    continue
                try:
                    self.amap_strategy_map[name] = int(value)
                except (TypeError, ValueError):
                    continue

        self.custom_algorithm_map = dict(self._DEFAULT_CUSTOM_ALGORITHM_MAP)
        if custom_algorithm_map:
            for key, value in custom_algorithm_map.items():
                name = str(key).strip()
                algorithm_id = str(value).strip()
                if name and algorithm_id:
                    self.custom_algorithm_map[name] = algorithm_id

        requested_default = default_strategy.strip() or "速度优先"
        if (
            requested_default not in self.amap_strategy_map
            and requested_default not in self.custom_algorithm_map
        ):
            requested_default = "速度优先"
        self.default_strategy = requested_default
        self.custom_candidate_strategy_codes = self._normalize_custom_candidate_strategy_codes(
            custom_candidate_strategy_codes
        )
        self.custom_candidate_max_paths_per_strategy = self._normalize_custom_candidate_max_paths(
            custom_candidate_max_paths_per_strategy
        )
        self.custom_candidate_use_tmcs = bool(custom_candidate_use_tmcs)
        self.custom_candidate_densify_max_segment_m = max(0.0, float(custom_candidate_densify_max_segment_m))
        self.custom_candidate_enable_divergence = bool(custom_candidate_enable_divergence)
        self.custom_candidate_divergence_anchor_ratios = self._normalize_divergence_anchor_ratios(
            custom_candidate_divergence_anchor_ratios
        )
        self.custom_candidate_divergence_offsets_m = self._normalize_divergence_offsets(
            custom_candidate_divergence_offsets_m
        )

        self.peak_hours = sorted({h for h in (peak_hours or [7, 8, 9, 17, 18, 19]) if 0 <= h <= 23})
        self.peak_multiplier = max(1.0, float(peak_multiplier))

        self.static_solver = StaticShortestPathSolver()
        self.time_dependent_solver = TimeDependentShortestPathSolver()
        self.a_star_solver = AStarShortestPathSolver()
        self.greedy_solver = GreedyBestFirstPathSolver()
        self.tycoon_selector = TycoonLongestRouteSelector()
        self.freshness_dijkstra_improved_solver = FreshnessDijkstraImprovedSolver()
        self.target_freshness_k_shortest_path_solver = TargetFreshnessKShortestPathSolver()
        self.target_freshness_la_star_solver = TargetFreshnessLexicographicAStarSolver()
        self.target_freshness_label_search_solver = TargetFreshnessAdaptiveLabelSearchSolver()
        self.freshness_target = float(freshness_target or self._DEFAULT_FRESHNESS_TARGET)
        self.freshness_base_loss_per_hour = max(
            0.01,
            float(freshness_base_loss_per_hour or self._DEFAULT_FRESHNESS_BASE_LOSS_PER_HOUR),
        )
        self.freshness_max_detour_ratio = max(
            1.0,
            float(freshness_max_detour_ratio or self._DEFAULT_FRESHNESS_MAX_DETOUR_RATIO),
        )
        self.freshness_arbitration_scope = str(freshness_arbitration_scope or "amap_only").strip().lower()
        if self.freshness_arbitration_scope != "amap_only":
            self.freshness_arbitration_scope = "amap_only"
        self.freshness_amap_compare_strategy_codes = self._normalize_strategy_codes(
            raw_codes=freshness_amap_compare_strategy_codes,
            max_count=self._MAX_FRESHNESS_COMPARE_STRATEGY_COUNT,
        )
        self.freshness_amap_compare_max_paths_per_strategy = self._normalize_custom_candidate_max_paths(
            freshness_amap_compare_max_paths_per_strategy
        )
        self.freshness_arbitration_time_budget_s = max(1.0, float(freshness_arbitration_time_budget_s))
        self.transport_mode_multipliers = self._merge_multiplier_map(
            self._DEFAULT_TRANSPORT_MODE_MULTIPLIERS,
            freshness_transport_mode_multipliers,
        )
        self.road_class_multipliers = self._merge_multiplier_map(
            self._DEFAULT_ROAD_CLASS_MULTIPLIERS,
            freshness_road_class_multipliers,
        )
        self.tmcs_status_multipliers = self._merge_multiplier_map(
            self._DEFAULT_TMCS_STATUS_MULTIPLIERS,
            freshness_tmcs_status_multipliers,
        )
        self.fruit_profiles, self.default_fruit_profile = self._load_fruit_profiles(fruit_profile_json_path)
        self._warned_unknown_fruit_keys: set[str] = set()

    def plan_route(self, request: RouteRequest) -> RouteResult:
        started = time.perf_counter()
        selected = request.algorithm.strip()

        try:
            custom_name, custom_algo_id = self._resolve_custom_algorithm(selected)
            if custom_algo_id:
                return self._plan_by_custom(
                    request=request,
                    started=started,
                    strategy_name=custom_name,
                    algorithm_id=custom_algo_id,
                )

            amap_name, amap_code = self._resolve_amap_strategy(selected)
            return self._plan_by_amap(
                request=request,
                started=started,
                strategy_name=amap_name,
                strategy_code=amap_code,
            )
        except Exception as exc:  # pragma: no cover
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            LOGGER.exception("路径规划出现未预期异常")
            return RouteResult(
                path_points_wgs84=[],
                path_points_gcj02=[],
                total_distance_km=0.0,
                total_time_h=0.0,
                compute_ms=elapsed_ms,
                node_count=0,
                edge_count=0,
                status="error",
                message=f"路径规划失败: {exc}",
            )

    def _plan_by_amap(
        self,
        request: RouteRequest,
        started: float,
        strategy_name: str,
        strategy_code: int,
    ) -> RouteResult:
        try:
            start_point = self.amap_client.geocode(request.start_text)
            end_point = self.amap_client.geocode(request.end_text)
            planned = self.amap_client.plan_driving_route(
                origin_gcj02=start_point,
                destination_gcj02=end_point,
                strategy=strategy_code,
            )

            points_gcj02 = planned.points_gcj02
            points_wgs84 = batch_gcj02_to_wgs84(points_gcj02)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            road_class = self._road_class_from_strategy(strategy_code)
            freshness_at_arrival, freshness_delta_to_100 = self._estimate_freshness_for_planned_route(
                route=planned,
                road_class=road_class,
                fruit_type=request.fruit_type,
                transport_mode=request.transport_mode,
            )

            return RouteResult(
                path_points_wgs84=points_wgs84,
                path_points_gcj02=points_gcj02,
                total_distance_km=planned.total_distance_km,
                total_time_h=planned.total_time_h,
                compute_ms=elapsed_ms,
                node_count=len(points_gcj02),
                edge_count=planned.segment_count,
                status="ok",
                message=f"规划成功（引擎: 高德，策略: {strategy_name}）",
                freshness_at_arrival=freshness_at_arrival,
                freshness_delta_to_100=freshness_delta_to_100,
            )
        except AMapServiceError as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            LOGGER.warning("高德路径规划失败: %s", exc)
            return RouteResult(
                path_points_wgs84=[],
                path_points_gcj02=[],
                total_distance_km=0.0,
                total_time_h=0.0,
                compute_ms=elapsed_ms,
                node_count=0,
                edge_count=0,
                status="error",
                message=str(exc),
            )

    def _plan_by_custom(
        self,
        request: RouteRequest,
        started: float,
        strategy_name: str,
        algorithm_id: str,
    ) -> RouteResult:
        try:
            start_gcj02 = self.amap_client.geocode(request.start_text)
            end_gcj02 = self.amap_client.geocode(request.end_text)
            normalized_algorithm = algorithm_id.strip().lower()
            is_target_freshness = normalized_algorithm in {
                "target_freshness_atd_ls",
                "target_freshness_tf_ksp",
                "target_freshness_tf_la_star",
            }
            if normalized_algorithm == "freshness_first":
                return self._plan_freshness_first_amap_only(
                    request=request,
                    started=started,
                    strategy_name=strategy_name,
                    start_gcj02=start_gcj02,
                    end_gcj02=end_gcj02,
                )

            candidates = self.amap_client.plan_driving_route_candidates(
                origin_gcj02=start_gcj02,
                destination_gcj02=end_gcj02,
                strategies=(
                    self._target_freshness_candidate_strategy_codes()
                    if is_target_freshness
                    else self._candidate_strategy_codes()
                ),
                max_paths_per_strategy=1 if is_target_freshness else self.custom_candidate_max_paths_per_strategy,
                use_tmcs=self.custom_candidate_use_tmcs,
                densify_max_segment_m=(
                    max(self.custom_candidate_densify_max_segment_m, 120.0)
                    if is_target_freshness
                    else self.custom_candidate_densify_max_segment_m
                ),
            )
            if not is_target_freshness:
                candidates, _ = self._expand_candidates_by_divergence(
                    candidates=candidates,
                    start_gcj02=start_gcj02,
                    end_gcj02=end_gcj02,
                )

            if algorithm_id.strip().lower() == "tycoon_longest_route":
                selected_code, selected_route = self.tycoon_selector.select(candidates)
                return self._build_tycoon_result(
                    started=started,
                    strategy_name=strategy_name,
                    strategy_code=selected_code,
                    selected_route=selected_route,
                    fruit_type=request.fruit_type,
                    transport_mode=request.transport_mode,
                )

            graph, start_node_id, end_node_id = self._build_graph_from_amap_candidates(
                candidates,
                start_gcj02,
                end_gcj02,
            )

            solved, is_time_dependent, dynamic_override_count, objective_label = self._solve_custom_algorithm(
                graph=graph,
                start_node_id=start_node_id,
                end_node_id=end_node_id,
                algorithm_id=algorithm_id,
                depart_at=request.depart_at,
                request_fruit_type=request.fruit_type,
                request_transport_mode=request.transport_mode,
            )

            return self._build_custom_result(
                started=started,
                strategy_name=strategy_name,
                graph=graph,
                solved=solved,
                is_time_dependent=is_time_dependent,
                depart_at=request.depart_at,
                dynamic_override_count=dynamic_override_count,
                objective_label=objective_label,
                fruit_type=request.fruit_type,
                transport_mode=request.transport_mode,
            )
        except (AMapServiceError, ValueError) as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            LOGGER.warning("自研路径规划失败: %s", exc)
            return RouteResult(
                path_points_wgs84=[],
                path_points_gcj02=[],
                total_distance_km=0.0,
                total_time_h=0.0,
                compute_ms=elapsed_ms,
                node_count=0,
                edge_count=0,
                status="error",
                message=str(exc),
            )

    def _plan_freshness_first_amap_only(
        self,
        request: RouteRequest,
        started: float,
        strategy_name: str,
        start_gcj02: tuple[float, float],
        end_gcj02: tuple[float, float],
    ) -> RouteResult:
        if self.freshness_arbitration_scope != "amap_only":
            LOGGER.info(
                "保鲜优先算法仅支持高德策略仲裁，已忽略配置值并强制使用 amap_only: %s",
                self.freshness_arbitration_scope,
            )

        compare_codes = self._freshness_compare_strategy_codes()
        if not compare_codes:
            raise ValueError("保鲜优先算法未配置可比较的高德策略编码。")

        deadline = started + self.freshness_arbitration_time_budget_s
        candidates, budget_cutoff = self._collect_freshness_amap_candidates(
            start_gcj02=start_gcj02,
            end_gcj02=end_gcj02,
            compare_codes=compare_codes,
            deadline=deadline,
        )
        if not candidates:
            raise ValueError("保鲜优先算法未获取到可用高德候选路线。")
        if budget_cutoff:
            LOGGER.info(
                "保鲜优先算法达到时间预算，采用当前最优路线返回: budget_cutoff=true, compared_strategies=%s",
                len(candidates),
            )

        selected_code, selected_route, detour_ratio, compared_route_count, total_route_count = (
            self._select_freshness_best_candidate(
                candidates=candidates,
                fruit_type=request.fruit_type,
                transport_mode=request.transport_mode,
            )
        )
        return self._build_freshness_result(
            started=started,
            strategy_name=strategy_name,
            strategy_code=selected_code,
            selected_route=selected_route,
            fruit_type=request.fruit_type,
            transport_mode=request.transport_mode,
            winner_strategy_name=self._strategy_name_by_code(selected_code),
            compared_strategy_count=len(candidates),
            compared_route_count=compared_route_count,
            total_route_count=total_route_count,
            detour_ratio=detour_ratio,
            budget_cutoff=budget_cutoff,
        )

    def _candidate_strategy_codes(self) -> list[int]:
        return list(self.custom_candidate_strategy_codes)

    def _target_freshness_candidate_strategy_codes(self) -> list[int]:
        # ATD-LS 先用更小的候选池，优先减少高德请求和图规模。
        compare_codes = self._freshness_compare_strategy_codes()
        if compare_codes:
            return compare_codes[:3]

        preferred: list[int] = []
        for code in (0, 12, 13):
            if code in self.amap_strategy_map.values() and code not in preferred:
                preferred.append(code)
        if preferred:
            return preferred

        return self._candidate_strategy_codes()[:3]

    def _freshness_compare_strategy_codes(self) -> list[int]:
        if self.freshness_amap_compare_strategy_codes:
            return list(self.freshness_amap_compare_strategy_codes)

        derived: list[int] = []
        seen: set[int] = set()
        for code in self.amap_strategy_map.values():
            try:
                normalized = int(code)
            except (TypeError, ValueError):
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            derived.append(normalized)
        return derived

    def _collect_freshness_amap_candidates(
        self,
        start_gcj02: tuple[float, float],
        end_gcj02: tuple[float, float],
        compare_codes: list[int],
        deadline: float,
    ) -> tuple[dict[int, list[PlannedRoute]], bool]:
        collected: dict[int, list[PlannedRoute]] = {}
        budget_cutoff = False
        for strategy_code in compare_codes:
            if self._is_budget_cutoff(deadline):
                budget_cutoff = True
                break
            try:
                route_map = self.amap_client.plan_driving_route_candidates(
                    origin_gcj02=start_gcj02,
                    destination_gcj02=end_gcj02,
                    strategies=[strategy_code],
                    max_paths_per_strategy=self.freshness_amap_compare_max_paths_per_strategy,
                    use_tmcs=True,
                    densify_max_segment_m=self.custom_candidate_densify_max_segment_m,
                )
            except AMapServiceError as exc:
                LOGGER.debug("保鲜仲裁策略请求失败: strategy=%s error=%s", strategy_code, exc)
                continue
            routes = route_map.get(strategy_code, [])
            if routes:
                collected[strategy_code] = routes

        if collected and self.custom_candidate_enable_divergence:
            collected, divergence_cutoff = self._expand_candidates_by_divergence(
                candidates=collected,
                start_gcj02=start_gcj02,
                end_gcj02=end_gcj02,
                deadline=deadline,
            )
            budget_cutoff = budget_cutoff or divergence_cutoff

        return collected, budget_cutoff or self._is_budget_cutoff(deadline)

    def get_custom_candidate_options(self) -> dict[str, int | bool | float | list[float]]:
        """返回当前候选拼图参数，供设置界面读取。"""
        return {
            "max_paths_per_strategy": int(self.custom_candidate_max_paths_per_strategy),
            "use_tmcs": bool(self.custom_candidate_use_tmcs),
            "densify_max_segment_m": float(self.custom_candidate_densify_max_segment_m),
            "enable_divergence": bool(self.custom_candidate_enable_divergence),
            "divergence_anchor_ratios": [float(x) for x in self.custom_candidate_divergence_anchor_ratios],
            "divergence_offsets_m": [float(x) for x in self.custom_candidate_divergence_offsets_m],
        }

    def set_custom_candidate_options(
        self,
        max_paths_per_strategy: int | None = None,
        use_tmcs: bool | None = None,
        densify_max_segment_m: float | None = None,
        enable_divergence: bool | None = None,
        divergence_anchor_ratios: list[float] | None = None,
        divergence_offsets_m: list[float] | None = None,
    ) -> None:
        """更新候选拼图参数，供界面实时调参。"""
        if max_paths_per_strategy is not None:
            self.custom_candidate_max_paths_per_strategy = self._normalize_custom_candidate_max_paths(
                max_paths_per_strategy
            )
        if use_tmcs is not None:
            self.custom_candidate_use_tmcs = bool(use_tmcs)
        if densify_max_segment_m is not None:
            try:
                value = float(densify_max_segment_m)
            except (TypeError, ValueError):
                value = self.custom_candidate_densify_max_segment_m
            self.custom_candidate_densify_max_segment_m = max(0.0, value)
        if enable_divergence is not None:
            self.custom_candidate_enable_divergence = bool(enable_divergence)
        if divergence_anchor_ratios is not None:
            self.custom_candidate_divergence_anchor_ratios = self._normalize_divergence_anchor_ratios(
                divergence_anchor_ratios
            )
        if divergence_offsets_m is not None:
            self.custom_candidate_divergence_offsets_m = self._normalize_divergence_offsets(
                divergence_offsets_m
            )

    def _normalize_custom_candidate_strategy_codes(self, raw_codes: list[int] | None) -> list[int]:
        ordered: list[int] = []
        seen: set[int] = set()
        source = raw_codes if raw_codes else self._DEFAULT_CUSTOM_CANDIDATE_STRATEGIES
        for raw in source:
            try:
                code = int(raw)
            except (TypeError, ValueError):
                continue
            if code < 0 or code > 99:
                continue
            if code in seen:
                continue
            seen.add(code)
            ordered.append(code)
            if len(ordered) >= self._MAX_CUSTOM_CANDIDATE_COUNT:
                break

        if ordered:
            return ordered
        return list(self._DEFAULT_CUSTOM_CANDIDATE_STRATEGIES)

    def _normalize_strategy_codes(self, raw_codes: list[int] | None, max_count: int) -> list[int]:
        ordered: list[int] = []
        seen: set[int] = set()
        if not raw_codes:
            return ordered
        for raw in raw_codes:
            try:
                code = int(raw)
            except (TypeError, ValueError):
                continue
            if code < 0 or code > 99:
                continue
            if code in seen:
                continue
            seen.add(code)
            ordered.append(code)
            if len(ordered) >= max_count:
                break
        return ordered

    def _normalize_custom_candidate_max_paths(self, raw_max_paths: int) -> int:
        try:
            max_paths = int(raw_max_paths)
        except (TypeError, ValueError):
            max_paths = 2
        max_paths = max(1, max_paths)
        return min(max_paths, self._MAX_CUSTOM_PATHS_PER_STRATEGY)

    def _normalize_divergence_anchor_ratios(self, raw_ratios: list[float] | None) -> list[float]:
        source = raw_ratios if raw_ratios else self._DEFAULT_DIVERGENCE_ANCHOR_RATIOS
        ordered: list[float] = []
        seen: set[float] = set()
        for raw in source:
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            value = min(max(value, 0.05), 0.95)
            value = round(value, 3)
            if value in seen:
                continue
            seen.add(value)
            ordered.append(value)
            if len(ordered) >= self._MAX_DIVERGENCE_ANCHOR_COUNT:
                break
        if not ordered:
            return list(self._DEFAULT_DIVERGENCE_ANCHOR_RATIOS)
        return sorted(ordered)

    def _normalize_divergence_offsets(self, raw_offsets: list[float] | None) -> list[float]:
        source = raw_offsets if raw_offsets else self._DEFAULT_DIVERGENCE_OFFSETS_M
        ordered: list[float] = []
        seen: set[float] = set()
        for raw in source:
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            value = min(max(value, 50.0), 3000.0)
            value = round(value, 1)
            if value in seen:
                continue
            seen.add(value)
            ordered.append(value)
            if len(ordered) >= self._MAX_DIVERGENCE_OFFSET_COUNT:
                break
        if not ordered:
            return list(self._DEFAULT_DIVERGENCE_OFFSETS_M)
        return sorted(ordered)

    def _expand_candidates_by_divergence(
        self,
        candidates: dict[int, list[PlannedRoute]],
        start_gcj02: tuple[float, float],
        end_gcj02: tuple[float, float],
        deadline: float | None = None,
    ) -> tuple[dict[int, list[PlannedRoute]], bool]:
        """对候选路径做轻量发散：锚点法线偏移 -> 经由点请求 -> 去重合并。"""
        budget_cutoff = False
        if not self.custom_candidate_enable_divergence:
            return candidates, budget_cutoff

        anchor_ratios = list(self.custom_candidate_divergence_anchor_ratios)
        offset_distances = list(self.custom_candidate_divergence_offsets_m)
        if not anchor_ratios or not offset_distances:
            return candidates, budget_cutoff

        expanded: dict[int, list[PlannedRoute]] = {}
        for strategy_code, routes in candidates.items():
            if self._is_budget_cutoff(deadline):
                budget_cutoff = True
                break
            working_routes: list[PlannedRoute] = list(routes)
            expanded[strategy_code] = working_routes
            if not working_routes:
                continue

            signatures = {
                self._build_route_signature(route)
                for route in working_routes
                if len(route.points_gcj02) >= 2
            }

            # 仅基于当前策略的首条路径做发散，避免请求爆炸。
            base_route = working_routes[0]
            anchors = self._sample_anchor_points(base_route.points_gcj02, anchor_ratios)
            if not anchors:
                continue

            added_count = 0
            for anchor_point, normal_vec in anchors:
                for offset_m in offset_distances:
                    for side in (-1.0, 1.0):
                        if self._is_budget_cutoff(deadline):
                            budget_cutoff = True
                            break
                        waypoint = self._offset_point_by_normal(
                            anchor_point=anchor_point,
                            normal_vec=normal_vec,
                            offset_m=offset_m * side,
                        )
                        try:
                            route = self.amap_client.plan_driving_route(
                                origin_gcj02=start_gcj02,
                                destination_gcj02=end_gcj02,
                                strategy=strategy_code,
                                waypoints_gcj02=[waypoint],
                            )
                        except AMapServiceError as exc:
                            LOGGER.debug(
                                "候选发散请求失败: strategy=%s waypoint=%s error=%s",
                                strategy_code,
                                waypoint,
                                exc,
                            )
                            continue

                        if len(route.points_gcj02) < 2:
                            continue

                        signature = self._build_route_signature(route)
                        if signature in signatures:
                            continue

                        signatures.add(signature)
                        working_routes.append(route)
                        added_count += 1
                        if added_count >= self._MAX_DIVERGENCE_EXTRA_ROUTES_PER_STRATEGY:
                            break
                    if budget_cutoff:
                        break
                    if added_count >= self._MAX_DIVERGENCE_EXTRA_ROUTES_PER_STRATEGY:
                        break
                if budget_cutoff:
                    break
                if added_count >= self._MAX_DIVERGENCE_EXTRA_ROUTES_PER_STRATEGY:
                    break

        return expanded, budget_cutoff

    def _sample_anchor_points(
        self,
        points_gcj02: list[list[float]],
        ratios: list[float],
    ) -> list[tuple[tuple[float, float], tuple[float, float]]]:
        """返回锚点与单位法线向量(米坐标系 x=东, y=北)。"""
        if len(points_gcj02) < 2:
            return []

        sampled: list[tuple[tuple[float, float], tuple[float, float]]] = []
        seen: set[tuple[float, float]] = set()
        for ratio in ratios:
            anchor = self._point_with_normal_at_ratio(points_gcj02, ratio)
            if anchor is None:
                continue
            anchor_point = (round(anchor[0][0], 6), round(anchor[0][1], 6))
            if anchor_point in seen:
                continue
            seen.add(anchor_point)
            sampled.append(anchor)
        return sampled

    def _point_with_normal_at_ratio(
        self,
        points_gcj02: list[list[float]],
        ratio: float,
    ) -> tuple[tuple[float, float], tuple[float, float]] | None:
        if len(points_gcj02) < 2:
            return None

        lengths: list[float] = []
        total_m = 0.0
        for idx in range(len(points_gcj02) - 1):
            p1 = points_gcj02[idx]
            p2 = points_gcj02[idx + 1]
            seg_m = max(haversine_km(p1[0], p1[1], p2[0], p2[1]) * 1000.0, 0.0)
            lengths.append(seg_m)
            total_m += seg_m

        if total_m <= 1.0:
            return None

        target_m = total_m * min(max(float(ratio), 0.05), 0.95)
        walked_m = 0.0
        for idx, seg_m in enumerate(lengths):
            p1 = points_gcj02[idx]
            p2 = points_gcj02[idx + 1]
            if seg_m <= 1e-6:
                continue
            if walked_m + seg_m < target_m:
                walked_m += seg_m
                continue

            local_t = (target_m - walked_m) / seg_m
            local_t = min(max(local_t, 0.0), 1.0)
            lon = p1[0] + (p2[0] - p1[0]) * local_t
            lat = p1[1] + (p2[1] - p1[1]) * local_t

            mean_lat = (p1[1] + p2[1]) * 0.5
            lon_meter_scale = max(111320.0 * math.cos(math.radians(mean_lat)), 1e-6)
            dx_m = (p2[0] - p1[0]) * lon_meter_scale
            dy_m = (p2[1] - p1[1]) * 111320.0
            length_m = math.hypot(dx_m, dy_m)
            if length_m <= 1e-6:
                return None

            # 法线方向：将切线 (tx,ty) 旋转 90 度得到 (-ty, tx)。
            tx = dx_m / length_m
            ty = dy_m / length_m
            normal = (-ty, tx)
            return (round(lon, 6), round(lat, 6)), normal

        return None

    def _offset_point_by_normal(
        self,
        anchor_point: tuple[float, float],
        normal_vec: tuple[float, float],
        offset_m: float,
    ) -> tuple[float, float]:
        lon, lat = anchor_point
        nx, ny = normal_vec
        lon_meter_scale = max(111320.0 * math.cos(math.radians(lat)), 1e-6)
        lat_meter_scale = 111320.0

        shifted_lon = lon + (nx * offset_m) / lon_meter_scale
        shifted_lat = lat + (ny * offset_m) / lat_meter_scale

        shifted_lon = min(max(shifted_lon, -180.0), 180.0)
        shifted_lat = min(max(shifted_lat, -85.0), 85.0)
        return round(shifted_lon, 6), round(shifted_lat, 6)

    def _build_route_signature(self, route: PlannedRoute) -> str:
        points = route.points_gcj02
        if len(points) < 2:
            return "invalid"
        step = max(1, len(points) // 24)
        sampled = [points[index] for index in range(0, len(points), step)]
        if sampled[-1] != points[-1]:
            sampled.append(points[-1])
        compact = ";".join(f"{point[0]:.4f},{point[1]:.4f}" for point in sampled[:30])
        return f"{len(points)}|{compact}"

    def _is_budget_cutoff(self, deadline: float | None) -> bool:
        if deadline is None:
            return False
        return time.perf_counter() >= deadline

    def _build_graph_from_amap_candidates(
        self,
        candidates: dict[int, list[PlannedRoute]],
        start_gcj02: tuple[float, float],
        end_gcj02: tuple[float, float],
    ) -> tuple[GraphData, int, int]:
        node_map: dict[tuple[float, float], int] = {}
        nodes: dict[int, tuple[float, float]] = {}
        edges: list[GraphEdge] = []
        edge_id = 1

        def get_node_id(point: tuple[float, float]) -> int:
            key = (round(point[0], 6), round(point[1], 6))
            if key in node_map:
                return node_map[key]
            new_id = len(node_map) + 1
            node_map[key] = new_id
            nodes[new_id] = key
            return new_id

        for strategy_code, routes in candidates.items():
            road_class = self._road_class_from_strategy(strategy_code)
            for route in routes:
                points_wgs84_list = batch_gcj02_to_wgs84(route.points_gcj02)
                if len(points_wgs84_list) < 2:
                    continue

                total_distance_m = max(route.total_distance_km * 1000.0, 1.0)
                total_duration_s = max(route.total_time_h * 3600.0, 1.0)
                avg_speed_m_s = max(total_distance_m / total_duration_s, 1.0)

                for idx in range(len(points_wgs84_list) - 1):
                    p1 = (points_wgs84_list[idx][0], points_wgs84_list[idx][1])
                    p2 = (points_wgs84_list[idx + 1][0], points_wgs84_list[idx + 1][1])
                    n1 = get_node_id(p1)
                    n2 = get_node_id(p2)
                    if n1 == n2:
                        continue

                    dist_m = max(haversine_km(p1[0], p1[1], p2[0], p2[1]) * 1000.0, 1.0)
                    time_s = max(dist_m / avg_speed_m_s, 1.0)
                    edges.append(
                        GraphEdge(
                            edge_id=edge_id,
                            from_node_id=n1,
                            to_node_id=n2,
                            length_m=dist_m,
                            base_travel_time_s=time_s,
                            geometry=[p1, p2],
                            road_class=road_class,
                        )
                    )
                    edge_id += 1

        if not nodes or not edges:
            raise ValueError("高德候选路径数据不足，无法构建自研算法图。")

        graph = GraphData.build(nodes=nodes, edges=edges)
        start_wgs84 = gcj02_to_wgs84(start_gcj02[0], start_gcj02[1])
        end_wgs84 = gcj02_to_wgs84(end_gcj02[0], end_gcj02[1])
        start_node_id = graph.nearest_node(start_wgs84[0], start_wgs84[1])
        end_node_id = graph.nearest_node(end_wgs84[0], end_wgs84[1])

        if start_node_id == end_node_id:
            raise ValueError("高德候选图构建后无法区分起终点，请更换地点后重试。")

        return graph, start_node_id, end_node_id

    def _road_class_from_strategy(self, strategy_code: int) -> str:
        if strategy_code in self._NO_HIGHWAY_STRATEGIES:
            return "no_highway"
        if strategy_code in self._TRAFFIC_AVOID_STRATEGIES:
            return "traffic_avoid"
        return "normal"

    def _build_custom_result(
        self,
        started: float,
        strategy_name: str,
        graph: GraphData,
        solved: PathSolveResult,
        is_time_dependent: bool,
        depart_at: datetime,
        dynamic_override_count: int = 0,
        objective_label: str = "",
        fruit_type: str = "",
        transport_mode: str = "",
    ) -> RouteResult:
        points_wgs84 = self._build_polyline(graph, solved)
        points_gcj02 = batch_wgs84_to_gcj02(points_wgs84)
        total_distance_km = self._sum_distance_km(graph, solved)
        total_time_h = self._sum_time_h(
            graph=graph,
            solved=solved,
            is_time_dependent=is_time_dependent,
            depart_at=depart_at,
        )
        freshness_at_arrival, freshness_delta_to_100 = self._estimate_freshness_for_graph_path(
            graph=graph,
            solved=solved,
            is_time_dependent=is_time_dependent,
            depart_at=depart_at,
            fruit_type=fruit_type,
            transport_mode=transport_mode,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        dynamic_hint = ""
        if is_time_dependent:
            dynamic_hint = f"，动态边权命中: {dynamic_override_count}"
        objective_hint = f"，目标: {objective_label}" if objective_label else ""
        return RouteResult(
            path_points_wgs84=points_wgs84,
            path_points_gcj02=points_gcj02,
            total_distance_km=total_distance_km,
            total_time_h=total_time_h,
            compute_ms=elapsed_ms,
            node_count=len(points_gcj02),
            edge_count=len(solved.edge_path),
            status="ok",
            message=f"规划成功（引擎: 自研，算法: {strategy_name}{objective_hint}，数据源: 高德候选路径{dynamic_hint}）",
            freshness_at_arrival=freshness_at_arrival,
            freshness_delta_to_100=freshness_delta_to_100,
        )

    def _solve_custom_algorithm(
        self,
        graph: GraphData,
        start_node_id: int,
        end_node_id: int,
        algorithm_id: str,
        depart_at: datetime,
        request_fruit_type: str,
        request_transport_mode: str,
    ) -> tuple[PathSolveResult, bool, int, str]:
        normalized = algorithm_id.strip().lower()
        if normalized == "static_dijkstra":
            solved = self.static_solver.solve(
                graph=graph,
                start_node_id=start_node_id,
                end_node_id=end_node_id,
                weight_mode="distance",
            )
            return solved, False, 0, "距离代价"

        if normalized == "time_dependent_dijkstra":
            overrides = self._build_peak_overrides(graph, depart_at)
            solved = self.time_dependent_solver.solve(
                graph=graph,
                start_node_id=start_node_id,
                end_node_id=end_node_id,
                edge_time_overrides_s=overrides,
            )
            return solved, True, len(overrides), "时间代价(时变)"

        if normalized == "a_star":
            solved = self.a_star_solver.solve(
                graph=graph,
                start_node_id=start_node_id,
                end_node_id=end_node_id,
                weight_mode="time",
            )
            return solved, False, 0, "时间代价(A*)"

        if normalized == "greedy_best_first":
            solved = self.greedy_solver.solve(
                graph=graph,
                start_node_id=start_node_id,
                end_node_id=end_node_id,
                weight_mode="distance",
            )
            return solved, False, 0, "贪心启发式(直线距离)"

        if normalized == "freshness_dijkstra_improved":
            solved = self._solve_freshness_dijkstra_improved(
                graph=graph,
                start_node_id=start_node_id,
                end_node_id=end_node_id,
                fruit_type=request_fruit_type,
                transport_mode=request_transport_mode,
            )
            return solved, False, 0, "最小化|保鲜度-100|(改进Dijkstra)"

        if normalized == "target_freshness_atd_ls":
            solved = self._solve_target_freshness_atd_ls(
                graph=graph,
                start_node_id=start_node_id,
                end_node_id=end_node_id,
                fruit_type=request_fruit_type,
                transport_mode=request_transport_mode,
            )
            return solved, False, 0, "最小化|保鲜度-100|(ATD-LS)"

        if normalized == "target_freshness_tf_ksp":
            solved = self._solve_target_freshness_tf_ksp(
                graph=graph,
                start_node_id=start_node_id,
                end_node_id=end_node_id,
                fruit_type=request_fruit_type,
                transport_mode=request_transport_mode,
            )
            return solved, False, 0, "最小化|保鲜度-100|(TF-KSP)"

        if normalized == "target_freshness_tf_la_star":
            solved = self._solve_target_freshness_tf_la_star(
                graph=graph,
                start_node_id=start_node_id,
                end_node_id=end_node_id,
                fruit_type=request_fruit_type,
                transport_mode=request_transport_mode,
            )
            return solved, False, 0, "最小化(|保鲜度-100|, 时间)(TF-LA*)"

        raise ValueError(f"未识别的自研算法标识: {algorithm_id}")

    def _solve_freshness_dijkstra_improved(
        self,
        graph: GraphData,
        start_node_id: int,
        end_node_id: int,
        fruit_type: str,
        transport_mode: str,
    ) -> PathSolveResult:
        target_loss, edge_freshness_loss_by_id, edge_secondary_cost_by_id = (
            self._build_target_freshness_edge_costs(
                graph=graph,
                fruit_type=fruit_type,
                transport_mode=transport_mode,
            )
        )

        return self.freshness_dijkstra_improved_solver.solve(
            graph=graph,
            start_node_id=start_node_id,
            end_node_id=end_node_id,
            edge_freshness_loss_by_id=edge_freshness_loss_by_id,
            target_loss=target_loss,
            edge_secondary_cost_by_id=edge_secondary_cost_by_id,
            loss_bin_size=0.08,
            max_loss_limit=240.0,
        )

    def _solve_target_freshness_atd_ls(
        self,
        graph: GraphData,
        start_node_id: int,
        end_node_id: int,
        fruit_type: str,
        transport_mode: str,
    ) -> PathSolveResult:
        target_loss, edge_freshness_loss_by_id, edge_secondary_cost_by_id = (
            self._build_target_freshness_edge_costs(
                graph=graph,
                fruit_type=fruit_type,
                transport_mode=transport_mode,
            )
        )
        detour_ratio = self._resolve_freshness_detour_ratio(fruit_type)
        fastest_path = self.static_solver.solve(
            graph=graph,
            start_node_id=start_node_id,
            end_node_id=end_node_id,
            weight_mode="time",
        )
        fastest_secondary_cost = fastest_path.total_cost
        path_depth_hint = len(fastest_path.edge_path)
        branching_hint = self._estimate_path_branching_hint(
            graph=graph,
            node_path=fastest_path.node_path,
        )
        max_secondary_cost = max(float(fastest_secondary_cost), 1e-6) * detour_ratio
        max_loss_limit = max(target_loss * 2.6, target_loss + 14.0, 28.0)

        return self.target_freshness_label_search_solver.solve(
            graph=graph,
            start_node_id=start_node_id,
            end_node_id=end_node_id,
            edge_freshness_loss_by_id=edge_freshness_loss_by_id,
            target_loss=target_loss,
            edge_secondary_cost_by_id=edge_secondary_cost_by_id,
            max_secondary_cost=max_secondary_cost,
            max_loss_limit=max_loss_limit,
            max_labels_per_node=self._estimate_target_freshness_label_limit(graph),
            max_total_labels=self._estimate_target_freshness_total_label_limit(graph),
            path_depth_hint=path_depth_hint,
            branching_hint=branching_hint,
            baseline_result=fastest_path,
        )

    def _solve_target_freshness_tf_ksp(
        self,
        graph: GraphData,
        start_node_id: int,
        end_node_id: int,
        fruit_type: str,
        transport_mode: str,
    ) -> PathSolveResult:
        target_loss, edge_freshness_loss_by_id, edge_secondary_cost_by_id = (
            self._build_target_freshness_edge_costs(
                graph=graph,
                fruit_type=fruit_type,
                transport_mode=transport_mode,
            )
        )
        detour_ratio = self._resolve_freshness_detour_ratio(fruit_type)
        fastest_secondary_cost = self.static_solver.solve(
            graph=graph,
            start_node_id=start_node_id,
            end_node_id=end_node_id,
            weight_mode="time",
        ).total_cost
        max_secondary_cost = max(float(fastest_secondary_cost), 1e-6) * detour_ratio
        max_loss_limit = max(target_loss * 2.6, target_loss + 14.0, 28.0)

        return self.target_freshness_k_shortest_path_solver.solve(
            graph=graph,
            start_node_id=start_node_id,
            end_node_id=end_node_id,
            edge_freshness_loss_by_id=edge_freshness_loss_by_id,
            target_loss=target_loss,
            edge_secondary_cost_by_id=edge_secondary_cost_by_id,
            max_secondary_cost=max_secondary_cost,
            max_loss_limit=max_loss_limit,
            max_candidate_paths=self._estimate_target_freshness_candidate_paths(graph),
        )

    def _solve_target_freshness_tf_la_star(
        self,
        graph: GraphData,
        start_node_id: int,
        end_node_id: int,
        fruit_type: str,
        transport_mode: str,
    ) -> PathSolveResult:
        target_loss, edge_freshness_loss_by_id, edge_secondary_cost_by_id = (
            self._build_target_freshness_edge_costs(
                graph=graph,
                fruit_type=fruit_type,
                transport_mode=transport_mode,
            )
        )
        detour_ratio = self._resolve_freshness_detour_ratio(fruit_type)
        fastest_path = self.static_solver.solve(
            graph=graph,
            start_node_id=start_node_id,
            end_node_id=end_node_id,
            weight_mode="time",
        )
        fastest_secondary_cost = fastest_path.total_cost
        path_depth_hint = len(fastest_path.edge_path)
        branching_hint = self._estimate_path_branching_hint(
            graph=graph,
            node_path=fastest_path.node_path,
        )
        max_secondary_cost = max(float(fastest_secondary_cost), 1e-6) * detour_ratio
        max_loss_limit = max(target_loss * 2.8, target_loss + 16.0, 30.0)

        return self.target_freshness_la_star_solver.solve(
            graph=graph,
            start_node_id=start_node_id,
            end_node_id=end_node_id,
            edge_freshness_loss_by_id=edge_freshness_loss_by_id,
            target_loss=target_loss,
            edge_secondary_cost_by_id=edge_secondary_cost_by_id,
            max_secondary_cost=max_secondary_cost,
            max_loss_limit=max_loss_limit,
            max_labels_per_node=max(self._estimate_target_freshness_label_limit(graph), 6),
            max_total_labels=max(self._estimate_target_freshness_total_label_limit(graph), 160),
            path_depth_hint=path_depth_hint,
            branching_hint=branching_hint,
            baseline_result=fastest_path,
        )

    def _estimate_target_freshness_label_limit(self, graph: GraphData) -> int:
        node_count = len(graph.nodes)
        limit = 4 + node_count // 220
        return max(4, min(10, limit))

    def _estimate_target_freshness_candidate_paths(self, graph: GraphData) -> int:
        node_count = len(graph.nodes)
        limit = 6 + node_count // 180
        return max(6, min(12, limit))

    def _estimate_target_freshness_total_label_limit(self, graph: GraphData) -> int:
        edge_count = len(graph.edges_by_id)
        limit = 72 + edge_count // 6
        return max(96, min(420, limit))

    def _estimate_path_branching_hint(self, graph: GraphData, node_path: list[int]) -> float:
        if len(node_path) < 2:
            return 0.0

        branching_nodes = 0
        for node_id in node_path[:-1]:
            if len(graph.edges_by_from.get(node_id, [])) > 1:
                branching_nodes += 1
        return min(max(branching_nodes / max(len(node_path) - 1, 1), 0.0), 1.0)

    def _build_target_freshness_edge_costs(
        self,
        graph: GraphData,
        fruit_type: str,
        transport_mode: str,
    ) -> tuple[float, dict[int, float], dict[int, float]]:
        profile = self._resolve_fruit_profile(fruit_type)
        transport_multiplier = self._resolve_transport_multiplier(transport_mode)
        target_loss = profile.freshness_init - self.freshness_target
        target_loss = min(max(target_loss, 0.0), 130.0)

        edge_freshness_loss_by_id: dict[int, float] = {}
        edge_secondary_cost_by_id: dict[int, float] = {}

        # 强化“路况拥堵 + 曲折路段”的损耗差异，让目标保鲜度更有判别力。
        for edge_id, edge in graph.edges_by_id.items():
            dt_h = max(float(edge.base_travel_time_s), 0.0) / 3600.0
            road_multiplier = self._resolve_road_multiplier(edge.road_class)
            curvature_multiplier = self._curvature_multiplier_from_points(edge.geometry)
            curvature_penalty = 1.0 + max(0.0, curvature_multiplier - 1.0) * 1.45

            tmcs_key = "unknown"
            if edge.road_class == "traffic_avoid":
                tmcs_key = "slow"
            elif edge.road_class == "no_highway":
                tmcs_key = "congested"
            tmcs_multiplier = self.tmcs_status_multipliers.get(
                tmcs_key,
                self.tmcs_status_multipliers.get("unknown", 1.0),
            )

            decay = (
                dt_h
                * self.freshness_base_loss_per_hour
                * profile.decay_multiplier
                * transport_multiplier
                * road_multiplier
                * tmcs_multiplier
                * curvature_penalty
            )
            edge_freshness_loss_by_id[edge_id] = max(decay, 0.0)
            edge_secondary_cost_by_id[edge_id] = max(float(edge.base_travel_time_s), 1e-6)

        return target_loss, edge_freshness_loss_by_id, edge_secondary_cost_by_id

    def _build_tycoon_result(
        self,
        started: float,
        strategy_name: str,
        strategy_code: int,
        selected_route: PlannedRoute,
        fruit_type: str,
        transport_mode: str,
    ) -> RouteResult:
        points_gcj02 = selected_route.points_gcj02
        points_wgs84 = batch_gcj02_to_wgs84(points_gcj02)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        edge_count = selected_route.segment_count
        if edge_count <= 0:
            edge_count = max(len(points_gcj02) - 1, 0)
        road_class = self._road_class_from_strategy(strategy_code)
        freshness_at_arrival, freshness_delta_to_100 = self._estimate_freshness_for_planned_route(
            route=selected_route,
            road_class=road_class,
            fruit_type=fruit_type,
            transport_mode=transport_mode,
        )
        return RouteResult(
            path_points_wgs84=points_wgs84,
            path_points_gcj02=points_gcj02,
            total_distance_km=selected_route.total_distance_km,
            total_time_h=selected_route.total_time_h,
            compute_ms=elapsed_ms,
            node_count=len(points_gcj02),
            edge_count=edge_count,
            status="ok",
            message=(
                f"规划成功（引擎: 自研，算法: {strategy_name}，目标: 最远路径，"
                f"候选策略编码: {strategy_code}）"
            ),
            freshness_at_arrival=freshness_at_arrival,
            freshness_delta_to_100=freshness_delta_to_100,
        )

    def _build_freshness_result(
        self,
        started: float,
        strategy_name: str,
        strategy_code: int,
        selected_route: PlannedRoute,
        fruit_type: str,
        transport_mode: str,
        winner_strategy_name: str | None = None,
        compared_strategy_count: int | None = None,
        compared_route_count: int | None = None,
        total_route_count: int | None = None,
        detour_ratio: float | None = None,
        budget_cutoff: bool = False,
    ) -> RouteResult:
        points_gcj02 = selected_route.points_gcj02
        points_wgs84 = batch_gcj02_to_wgs84(points_gcj02)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        edge_count = selected_route.segment_count
        if edge_count <= 0:
            edge_count = max(len(points_gcj02) - 1, 0)

        road_class = self._road_class_from_strategy(strategy_code)
        freshness_at_arrival, freshness_delta_to_100 = self._estimate_freshness_for_planned_route(
            route=selected_route,
            road_class=road_class,
            fruit_type=fruit_type,
            transport_mode=transport_mode,
        )
        return RouteResult(
            path_points_wgs84=points_wgs84,
            path_points_gcj02=points_gcj02,
            total_distance_km=selected_route.total_distance_km,
            total_time_h=selected_route.total_time_h,
            compute_ms=elapsed_ms,
            node_count=len(points_gcj02),
            edge_count=edge_count,
            status="ok",
            message=(
                f"规划成功（引擎: 自研，算法: {strategy_name}，目标: 最小化|保鲜度-{self.freshness_target:.0f}|，"
                f"高德策略仲裁胜出: {winner_strategy_name or self._strategy_name_by_code(strategy_code)}"
                f"(编码:{strategy_code})，"
                f"比较策略数: {compared_strategy_count if compared_strategy_count is not None else '-'}，"
                f"参与排序路线: {compared_route_count if compared_route_count is not None else '-'}"
                f"/总候选: {total_route_count if total_route_count is not None else '-'}，"
                f"绕路阈值: {detour_ratio if detour_ratio is not None else self.freshness_max_detour_ratio:.2f}"
                f"{'，budget_cutoff=true' if budget_cutoff else ''}）"
            ),
            freshness_at_arrival=freshness_at_arrival,
            freshness_delta_to_100=freshness_delta_to_100,
        )

    def _build_freshness_graph_result(
        self,
        started: float,
        strategy_name: str,
        graph: GraphData,
        solved: PathSolveResult,
        depart_at: datetime,
        fruit_type: str,
        transport_mode: str,
    ) -> RouteResult:
        points_wgs84 = self._build_polyline(graph, solved)
        points_gcj02 = batch_wgs84_to_gcj02(points_wgs84)
        total_distance_km = self._sum_distance_km(graph, solved)
        total_time_h = self._sum_time_h(
            graph=graph,
            solved=solved,
            is_time_dependent=False,
            depart_at=depart_at,
        )
        freshness_at_arrival, freshness_delta_to_100 = self._estimate_freshness_for_graph_path(
            graph=graph,
            solved=solved,
            is_time_dependent=False,
            depart_at=depart_at,
            fruit_type=fruit_type,
            transport_mode=transport_mode,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return RouteResult(
            path_points_wgs84=points_wgs84,
            path_points_gcj02=points_gcj02,
            total_distance_km=total_distance_km,
            total_time_h=total_time_h,
            compute_ms=elapsed_ms,
            node_count=len(points_gcj02),
            edge_count=len(solved.edge_path),
            status="ok",
            message=(
                f"规划成功（引擎: 自研，算法: {strategy_name}，目标: 最小化|保鲜度-{self.freshness_target:.0f}|，"
                "候选图搜索校正）"
            ),
            freshness_at_arrival=freshness_at_arrival,
            freshness_delta_to_100=freshness_delta_to_100,
        )

    def _select_freshness_best_candidate(
        self,
        candidates: dict[int, list[PlannedRoute]],
        fruit_type: str,
        transport_mode: str,
    ) -> tuple[int, PlannedRoute, float, int, int]:
        flattened: list[tuple[int, PlannedRoute]] = []
        for strategy_code, routes in candidates.items():
            for route in routes:
                if len(route.points_gcj02) < 2:
                    continue
                flattened.append((strategy_code, route))
        if not flattened:
            raise ValueError("保鲜优先算法未找到可用候选路径。")

        fastest_h = min(max(route.total_time_h, 0.0) for _, route in flattened)
        detour_ratio = self._resolve_freshness_detour_ratio(fruit_type)
        allowed_max_h = fastest_h * detour_ratio
        constrained = [
            (code, route)
            for code, route in flattened
            if max(route.total_time_h, 0.0) <= allowed_max_h + 1e-9
        ]
        working_set = constrained if constrained else flattened

        best_code = -1
        best_route: PlannedRoute | None = None
        best_rank: tuple[float, float, float, float] | None = None
        for strategy_code, route in working_set:
            road_class = self._road_class_from_strategy(strategy_code)
            freshness, delta = self._estimate_freshness_for_planned_route(
                route=route,
                road_class=road_class,
                fruit_type=fruit_type,
                transport_mode=transport_mode,
            )
            # 保鲜优先：先取与目标值最接近，再按保鲜度高、耗时短、里程短排序。
            rank = (
                float(delta),
                -float(freshness),
                max(route.total_time_h, 0.0),
                max(route.total_distance_km, 0.0),
            )
            if best_rank is None or rank < best_rank:
                best_rank = rank
                best_code = strategy_code
                best_route = route

        if best_route is None:
            raise ValueError("保鲜优先算法候选选择失败。")
        return best_code, best_route, detour_ratio, len(working_set), len(flattened)

    def _resolve_freshness_detour_ratio(self, fruit_type: str) -> float:
        profile = self._resolve_fruit_profile(fruit_type)
        ratio = self.freshness_max_detour_ratio

        # 后熟型水果（初始值显著高于目标）允许更宽绕路，以接近最佳食用点。
        if profile.freshness_init >= self.freshness_target + 8.0:
            ratio += 0.12

        # 易腐水果收紧绕路阈值，避免因时长增加导致保鲜快速下降。
        if profile.decay_multiplier >= 1.7:
            ratio -= 0.20
        elif profile.decay_multiplier >= 1.3:
            ratio -= 0.10

        return min(max(ratio, 1.05), 1.65)

    def _estimate_freshness_for_planned_route(
        self,
        route: PlannedRoute,
        road_class: str,
        fruit_type: str,
        transport_mode: str,
    ) -> tuple[float, float]:
        profile = self._resolve_fruit_profile(fruit_type)
        total_time_h = max(float(route.total_time_h), 0.0)
        transport_multiplier = self._resolve_transport_multiplier(transport_mode)
        road_multiplier = self._resolve_road_multiplier(road_class)
        tmcs_multiplier = self._weighted_tmcs_multiplier(route.tmcs_status_ratio)
        curvature_multiplier = self._normalize_curvature_multiplier(route.curvature_multiplier)
        loss = (
            total_time_h
            * self.freshness_base_loss_per_hour
            * profile.decay_multiplier
            * transport_multiplier
            * road_multiplier
            * tmcs_multiplier
            * curvature_multiplier
        )
        return self._to_freshness_metrics(profile.freshness_init, loss)

    def _estimate_freshness_for_graph_path(
        self,
        graph: GraphData,
        solved: PathSolveResult,
        is_time_dependent: bool,
        depart_at: datetime,
        fruit_type: str,
        transport_mode: str,
    ) -> tuple[float, float]:
        profile = self._resolve_fruit_profile(fruit_type)
        overrides = self._build_peak_overrides(graph, depart_at) if is_time_dependent else {}
        transport_multiplier = self._resolve_transport_multiplier(transport_mode)
        total_loss = 0.0
        for edge_id in solved.edge_path:
            edge = graph.edges_by_id[edge_id]
            time_s = max(overrides.get(edge_id, edge.base_travel_time_s), 0.0)
            dt_h = time_s / 3600.0
            road_multiplier = self._resolve_road_multiplier(edge.road_class)
            curvature_multiplier = self._curvature_multiplier_from_points(edge.geometry)
            tmcs_multiplier = self.tmcs_status_multipliers.get("unknown", 1.0)
            total_loss += (
                dt_h
                * self.freshness_base_loss_per_hour
                * profile.decay_multiplier
                * transport_multiplier
                * road_multiplier
                * tmcs_multiplier
                * curvature_multiplier
            )
        return self._to_freshness_metrics(profile.freshness_init, total_loss)

    def _to_freshness_metrics(self, freshness_init: float, total_loss: float) -> tuple[float, float]:
        freshness = freshness_init - total_loss
        freshness = min(max(freshness, 0.0), 130.0)
        delta = abs(freshness - self.freshness_target)
        return round(freshness, 3), round(delta, 3)

    def _weighted_tmcs_multiplier(self, tmcs_ratio: dict[str, float] | None) -> float:
        if not isinstance(tmcs_ratio, dict) or not tmcs_ratio:
            return self.tmcs_status_multipliers.get("unknown", 1.0)
        weighted_sum = 0.0
        total_ratio = 0.0
        for status, ratio in tmcs_ratio.items():
            try:
                ratio_value = float(ratio)
            except (TypeError, ValueError):
                continue
            if ratio_value <= 0:
                continue
            key = str(status).strip().lower() or "unknown"
            weighted_sum += ratio_value * self.tmcs_status_multipliers.get(
                key,
                self.tmcs_status_multipliers.get("unknown", 1.0),
            )
            total_ratio += ratio_value
        if total_ratio <= 0:
            return self.tmcs_status_multipliers.get("unknown", 1.0)
        return max(0.5, min(weighted_sum / total_ratio, 2.0))

    def _normalize_curvature_multiplier(self, raw_value: float | None) -> float:
        try:
            value = float(raw_value if raw_value is not None else 1.0)
        except (TypeError, ValueError):
            value = 1.0
        return max(1.0, min(value, 1.6))

    def _curvature_multiplier_from_points(self, points: list[tuple[float, float]] | list[list[float]]) -> float:
        if not points or len(points) < 2:
            return 1.0
        path_m = 0.0
        for idx in range(len(points) - 1):
            p1 = points[idx]
            p2 = points[idx + 1]
            path_m += haversine_km(p1[0], p1[1], p2[0], p2[1]) * 1000.0
        direct_m = haversine_km(points[0][0], points[0][1], points[-1][0], points[-1][1]) * 1000.0
        if path_m <= 0 or direct_m <= 0:
            return 1.0
        ratio = path_m / max(direct_m, 1.0)
        extra = max(0.0, ratio - 1.0)
        return max(1.0, min(1.0 + min(extra, 1.5) * 0.25, 1.45))

    def _resolve_transport_multiplier(self, transport_mode: str) -> float:
        key = str(transport_mode).strip()
        return self.transport_mode_multipliers.get(key, 1.0)

    def _resolve_road_multiplier(self, road_class: str) -> float:
        key = str(road_class).strip().lower() or "normal"
        return self.road_class_multipliers.get(key, self.road_class_multipliers.get("normal", 1.0))

    def _resolve_fruit_profile(self, fruit_type: str) -> FruitProfile:
        normalized = self._normalize_text_key(fruit_type)
        profile = self.fruit_profiles.get(normalized)
        if profile is not None:
            return profile
        if normalized not in self._warned_unknown_fruit_keys:
            LOGGER.warning("未命中水果参数: %s，回退苹果参数。", fruit_type)
            self._warned_unknown_fruit_keys.add(normalized)
        return self.default_fruit_profile

    def _normalize_text_key(self, value: str) -> str:
        return str(value).strip().lower()

    def _merge_multiplier_map(
        self,
        base: dict[str, float],
        override: dict[str, float] | None,
    ) -> dict[str, float]:
        merged = dict(base)
        if not override:
            return merged
        for key, value in override.items():
            name = str(key).strip()
            if not name:
                continue
            try:
                merged[name] = float(value)
            except (TypeError, ValueError):
                continue
        return merged

    def _load_fruit_profiles(
        self,
        profile_path: str | Path | None,
    ) -> tuple[dict[str, FruitProfile], FruitProfile]:
        profiles = list(self._DEFAULT_FRUIT_PROFILES)
        if profile_path:
            path = Path(profile_path)
            try:
                profiles = load_fruit_profiles(path)
            except FruitProfileLoadError as exc:
                LOGGER.warning("水果参数加载失败，回退默认参数: %s", exc)
        indexed: dict[str, FruitProfile] = {}
        for profile in profiles:
            indexed[self._normalize_text_key(profile.fruit_id)] = profile
            indexed[self._normalize_text_key(profile.name)] = profile
        fallback = indexed.get("apple") or indexed.get("苹果") or profiles[0]
        return indexed, fallback

    def _build_peak_overrides(self, graph: GraphData, depart_at: datetime) -> dict[int, float]:
        overrides: dict[int, float] = {}
        is_peak = depart_at.hour in self.peak_hours
        peak_gain = (self.peak_multiplier - 1.0) if is_peak else 0.0
        for edge_id, edge in graph.edges_by_id.items():
            multiplier = 1.0 + peak_gain
            if edge.road_class == "traffic_avoid":
                # 非高峰时略偏向避堵路线；高峰时上涨幅度小于普通路段。
                multiplier = 0.92 + peak_gain * 0.70
            elif edge.road_class == "no_highway":
                # 非高峰时不走高速通常略慢；高峰时仍上涨但幅度更小。
                multiplier = 1.08 + peak_gain * 0.35

            if abs(multiplier - 1.0) < 1e-6:
                continue

            overrides[edge_id] = edge.base_travel_time_s * max(0.65, multiplier)
        return overrides

    def _build_polyline(self, graph: GraphData, solved: PathSolveResult) -> list[list[float]]:
        if not solved.edge_path:
            return [[*graph.nodes[node_id]] for node_id in solved.node_path]

        polyline: list[list[float]] = []
        for edge_id in solved.edge_path:
            edge = graph.edges_by_id[edge_id]
            segment = edge.geometry or [graph.nodes[edge.from_node_id], graph.nodes[edge.to_node_id]]
            if not polyline:
                polyline.extend([[round(lon, 6), round(lat, 6)] for lon, lat in segment])
                continue
            for lon, lat in segment[1:]:
                polyline.append([round(lon, 6), round(lat, 6)])
        return polyline

    def _sum_distance_km(self, graph: GraphData, solved: PathSolveResult) -> float:
        total_m = 0.0
        for edge_id in solved.edge_path:
            total_m += graph.edges_by_id[edge_id].length_m
        return round(total_m / 1000.0, 3)

    def _sum_time_h(
        self,
        graph: GraphData,
        solved: PathSolveResult,
        is_time_dependent: bool,
        depart_at: datetime,
    ) -> float:
        total_s = 0.0
        overrides = self._build_peak_overrides(graph, depart_at) if is_time_dependent else {}
        for edge_id in solved.edge_path:
            edge = graph.edges_by_id[edge_id]
            total_s += overrides.get(edge_id, edge.base_travel_time_s)
        return round(total_s / 3600.0, 3)

    def _resolve_amap_strategy(self, strategy_text: str) -> tuple[str, int]:
        raw = strategy_text.strip()
        if raw in self.amap_strategy_map:
            return raw, int(self.amap_strategy_map[raw])

        fallback_name = next(iter(self.amap_strategy_map.keys()))
        return fallback_name, int(self.amap_strategy_map[fallback_name])

    def _strategy_name_by_code(self, strategy_code: int) -> str:
        for name, code in self.amap_strategy_map.items():
            try:
                if int(code) == int(strategy_code):
                    return name
            except (TypeError, ValueError):
                continue
        return str(strategy_code)

    def _resolve_custom_algorithm(self, strategy_text: str) -> tuple[str, str | None]:
        raw = strategy_text.strip()
        if raw in self.custom_algorithm_map:
            return raw, self.custom_algorithm_map[raw]

        normalized = raw.lower()
        if normalized in self._CUSTOM_ALGO_ALIAS:
            return raw, self._CUSTOM_ALGO_ALIAS[normalized]

        return raw, None
