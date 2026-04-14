from datetime import datetime
import unittest
import time

from src.models.route_request import RouteRequest
from src.services.amap_web_service import AMapServiceError, PlannedRoute
from src.services.route_planning_service import RoutePlanningService


class _FakeAmapClient:
    def __init__(self) -> None:
        self.driving_call_count = 0
        self.candidate_call_count = 0
        self.raise_geocode_error = False
        self.return_empty_candidates = False
        self.fail_candidate_strategies: set[int] = set()
        self.candidate_requested_strategies: list[int] = []
        self.sleep_per_candidate_s = 0.0

    def geocode(self, address: str) -> tuple[float, float]:
        if self.raise_geocode_error:
            raise AMapServiceError("高德地理编码失败")
        if "北京" in address:
            return (116.39747, 39.908823)
        return (121.473701, 31.230416)

    def plan_driving_route(
        self,
        origin_gcj02: tuple[float, float],
        destination_gcj02: tuple[float, float],
        strategy: int,
        waypoints_gcj02: list[tuple[float, float]] | None = None,
    ) -> PlannedRoute:
        self.driving_call_count += 1

        middle_map: dict[int, list[float]] = {
            0: [117.200000, 38.800000],
            1: [118.000000, 37.800000],
            2: [118.300000, 37.200000],
            3: [116.900000, 39.100000],
            4: [117.500000, 38.500000],
        }
        middle = middle_map.get(int(strategy), [117.200000, 38.800000])

        return PlannedRoute(
            points_gcj02=[
                [origin_gcj02[0], origin_gcj02[1]],
                middle,
                [destination_gcj02[0], destination_gcj02[1]],
            ],
            total_distance_km=1200.0 + int(strategy) * 10,
            total_time_h=12.0 + int(strategy) * 0.2,
            segment_count=2,
            tmcs_status_ratio={"unknown": 1.0},
            curvature_multiplier=1.0,
        )

    def plan_driving_route_candidates(
        self,
        origin_gcj02: tuple[float, float],
        destination_gcj02: tuple[float, float],
        strategies: list[int],
        max_paths_per_strategy: int = 2,
        use_tmcs: bool = True,
        densify_max_segment_m: float = 80.0,
    ) -> dict[int, list[PlannedRoute]]:
        self.candidate_call_count += 1
        if self.return_empty_candidates:
            return {}

        result: dict[int, list[PlannedRoute]] = {}
        for raw_code in strategies:
            code = int(raw_code)
            self.candidate_requested_strategies.append(code)
            if self.sleep_per_candidate_s > 0:
                time.sleep(self.sleep_per_candidate_s)
            if code in self.fail_candidate_strategies:
                if len(strategies) == 1:
                    raise AMapServiceError(f"策略 {code} 请求失败")
                continue
            if code in result:
                continue
            route = self.plan_driving_route(origin_gcj02, destination_gcj02, code)
            result[code] = [route]
            if len(result) >= 3:
                break
        return result


class TestRoutePlanningService(unittest.TestCase):
    def test_should_update_custom_candidate_options_with_clamp(self) -> None:
        amap_client = _FakeAmapClient()
        service = RoutePlanningService(amap_client=amap_client)

        service.set_custom_candidate_options(
            max_paths_per_strategy=99,
            use_tmcs=False,
            densify_max_segment_m=-10,
            enable_divergence=True,
            divergence_anchor_ratios=[0.01, 0.88, 0.95],
            divergence_offsets_m=[10, 4000, 500],
        )
        options = service.get_custom_candidate_options()

        self.assertEqual(options["max_paths_per_strategy"], 3)
        self.assertFalse(options["use_tmcs"])
        self.assertEqual(options["densify_max_segment_m"], 0.0)
        self.assertTrue(options["enable_divergence"])
        self.assertEqual(options["divergence_anchor_ratios"], [0.05, 0.88])
        self.assertEqual(options["divergence_offsets_m"], [50.0, 3000.0])

    def test_should_limit_custom_candidate_pool_to_five(self) -> None:
        amap_client = _FakeAmapClient()
        service = RoutePlanningService(
            amap_client=amap_client,
            amap_strategy_map={"速度优先": 0, "费用优先": 1, "常规最快": 2, "躲避拥堵": 12},
            custom_candidate_strategy_codes=[0, 12, 13, 14, 19, 20, 18],
        )

        self.assertEqual(service._candidate_strategy_codes(), [0, 12, 13, 14, 19])

    def test_should_use_custom_algorithm_with_amap_candidates(self) -> None:
        amap_client = _FakeAmapClient()
        service = RoutePlanningService(
            amap_client=amap_client,
            custom_algorithm_map={"自研-Dijkstra": "static_dijkstra"},
            amap_strategy_map={"速度优先": 0},
            default_strategy="速度优先",
            peak_hours=[7, 8, 9],
            peak_multiplier=1.6,
        )

        request = RouteRequest(
            start_text="北京",
            end_text="上海",
            algorithm="自研-Dijkstra",
            fruit_type="苹果",
            transport_mode="公路冷链",
            depart_at=datetime(2026, 4, 11, 8, 0, 0),
            load_ton=5.0,
        )
        result = service.plan_route(request)

        self.assertEqual(result.status, "ok")
        self.assertIn("引擎: 自研", result.message)
        self.assertIn("数据源: 高德候选路径", result.message)
        self.assertEqual(amap_client.candidate_call_count, 1)
        self.assertGreaterEqual(amap_client.driving_call_count, 1)
        self.assertGreater(result.total_distance_km, 0)
        self.assertGreater(result.total_time_h, 0)

    def test_should_expand_candidates_when_divergence_enabled(self) -> None:
        amap_client = _FakeAmapClient()
        service = RoutePlanningService(
            amap_client=amap_client,
            custom_algorithm_map={"自研-Dijkstra": "static_dijkstra"},
            amap_strategy_map={"速度优先": 0},
            custom_candidate_strategy_codes=[0],
            custom_candidate_enable_divergence=True,
            custom_candidate_divergence_anchor_ratios=[0.35, 0.65],
            custom_candidate_divergence_offsets_m=[300, 600],
        )

        request = RouteRequest(
            start_text="北京",
            end_text="上海",
            algorithm="自研-Dijkstra",
            fruit_type="苹果",
            transport_mode="公路冷链",
            depart_at=datetime(2026, 4, 11, 8, 0, 0),
            load_ton=5.0,
        )
        result = service.plan_route(request)

        self.assertEqual(result.status, "ok")
        self.assertEqual(amap_client.candidate_call_count, 1)
        self.assertGreater(amap_client.driving_call_count, 1)

    def test_should_use_a_star_algorithm_with_amap_candidates(self) -> None:
        amap_client = _FakeAmapClient()
        service = RoutePlanningService(
            amap_client=amap_client,
            custom_algorithm_map={"自研-A*": "a_star"},
            amap_strategy_map={"速度优先": 0},
        )

        request = RouteRequest(
            start_text="北京",
            end_text="上海",
            algorithm="自研-A*",
            fruit_type="苹果",
            transport_mode="公路冷链",
            depart_at=datetime(2026, 4, 11, 9, 0, 0),
            load_ton=5.0,
        )
        result = service.plan_route(request)

        self.assertEqual(result.status, "ok")
        self.assertIn("引擎: 自研", result.message)
        self.assertIn("自研-A*", result.message)
        self.assertIn("数据源: 高德候选路径", result.message)
        self.assertEqual(amap_client.candidate_call_count, 1)

    def test_should_use_greedy_algorithm_with_amap_candidates(self) -> None:
        amap_client = _FakeAmapClient()
        service = RoutePlanningService(
            amap_client=amap_client,
            custom_algorithm_map={"贪心算法": "greedy_best_first"},
            amap_strategy_map={"速度优先": 0},
        )

        request = RouteRequest(
            start_text="北京",
            end_text="上海",
            algorithm="贪心算法",
            fruit_type="苹果",
            transport_mode="公路冷链",
            depart_at=datetime(2026, 4, 11, 9, 0, 0),
            load_ton=5.0,
        )
        result = service.plan_route(request)

        self.assertEqual(result.status, "ok")
        self.assertIn("引擎: 自研", result.message)
        self.assertIn("贪心算法", result.message)
        self.assertGreater(result.total_distance_km, 0)
        self.assertEqual(amap_client.candidate_call_count, 1)

    def test_should_use_tycoon_algorithm_select_longest_candidate(self) -> None:
        amap_client = _FakeAmapClient()
        service = RoutePlanningService(
            amap_client=amap_client,
            custom_algorithm_map={"土豪算法": "tycoon_longest_route"},
            amap_strategy_map={"速度优先": 0},
            custom_candidate_strategy_codes=[0, 12, 13],
        )

        request = RouteRequest(
            start_text="北京",
            end_text="上海",
            algorithm="土豪算法",
            fruit_type="苹果",
            transport_mode="公路冷链",
            depart_at=datetime(2026, 4, 11, 9, 0, 0),
            load_ton=5.0,
        )
        result = service.plan_route(request)

        self.assertEqual(result.status, "ok")
        self.assertIn("最远路径", result.message)
        self.assertGreaterEqual(result.total_distance_km, 1330.0)
        self.assertEqual(amap_client.candidate_call_count, 1)

    def test_should_use_freshness_first_with_best_delta_in_amap_pool(self) -> None:
        amap_client = _FakeAmapClient()
        service = RoutePlanningService(
            amap_client=amap_client,
            custom_algorithm_map={"保鲜优先算法": "freshness_first"},
            amap_strategy_map={"速度优先": 0, "躲避拥堵": 12, "不走高速": 13},
            freshness_amap_compare_strategy_codes=[0, 12, 13],
        )
        service.static_solver.solve = lambda **_: (_ for _ in ()).throw(AssertionError("不应调用自研图搜索"))  # type: ignore[assignment]

        request = RouteRequest(
            start_text="北京",
            end_text="上海",
            algorithm="保鲜优先算法",
            fruit_type="香蕉",
            transport_mode="公路冷链",
            depart_at=datetime(2026, 4, 12, 9, 0, 0),
            load_ton=5.0,
        )
        result = service.plan_route(request)

        self.assertEqual(result.status, "ok")
        self.assertIn("高德策略仲裁胜出", result.message)
        self.assertEqual(amap_client.candidate_call_count, 3)
        self.assertEqual(amap_client.candidate_requested_strategies, [0, 12, 13])
        self.assertEqual(result.total_distance_km, 1320.0)
        self.assertIsNotNone(result.freshness_at_arrival)
        self.assertIsNotNone(result.freshness_delta_to_100)

    def test_should_only_compare_configured_amap_subset_for_freshness(self) -> None:
        amap_client = _FakeAmapClient()
        service = RoutePlanningService(
            amap_client=amap_client,
            custom_algorithm_map={"保鲜优先算法": "freshness_first"},
            amap_strategy_map={"速度优先": 0, "躲避拥堵": 12, "避免收费": 14, "高速优先": 19},
            freshness_amap_compare_strategy_codes=[12, 19],
        )

        request = RouteRequest(
            start_text="北京",
            end_text="上海",
            algorithm="保鲜优先算法",
            fruit_type="香蕉",
            transport_mode="公路冷链",
            depart_at=datetime(2026, 4, 12, 9, 0, 0),
            load_ton=5.0,
        )
        result = service.plan_route(request)

        self.assertEqual(result.status, "ok")
        self.assertEqual(amap_client.candidate_requested_strategies, [12, 19])

    def test_should_mark_budget_cutoff_when_freshness_budget_reached(self) -> None:
        amap_client = _FakeAmapClient()
        amap_client.sleep_per_candidate_s = 0.09
        service = RoutePlanningService(
            amap_client=amap_client,
            custom_algorithm_map={"保鲜优先算法": "freshness_first"},
            amap_strategy_map={"速度优先": 0, "常规最快": 2, "躲避拥堵": 12, "避免收费": 14, "高速优先": 19},
            freshness_arbitration_time_budget_s=1.0,
        )

        request = RouteRequest(
            start_text="北京",
            end_text="上海",
            algorithm="保鲜优先算法",
            fruit_type="香蕉",
            transport_mode="公路冷链",
            depart_at=datetime(2026, 4, 12, 9, 0, 0),
            load_ton=5.0,
        )
        result = service.plan_route(request)

        self.assertEqual(result.status, "ok")
        self.assertIn("budget_cutoff=true", result.message)

    def test_should_continue_when_part_of_amap_strategies_fail(self) -> None:
        amap_client = _FakeAmapClient()
        amap_client.fail_candidate_strategies = {12, 14}
        service = RoutePlanningService(
            amap_client=amap_client,
            custom_algorithm_map={"保鲜优先算法": "freshness_first"},
            amap_strategy_map={"速度优先": 0, "躲避拥堵": 12, "避免收费": 14},
        )

        request = RouteRequest(
            start_text="北京",
            end_text="上海",
            algorithm="保鲜优先算法",
            fruit_type="香蕉",
            transport_mode="公路冷链",
            depart_at=datetime(2026, 4, 12, 9, 0, 0),
            load_ton=5.0,
        )
        result = service.plan_route(request)

        self.assertEqual(result.status, "ok")
        self.assertIn("高德策略仲裁胜出", result.message)

    def test_should_fallback_to_apple_profile_when_fruit_not_found(self) -> None:
        amap_client = _FakeAmapClient()
        service = RoutePlanningService(
            amap_client=amap_client,
            custom_algorithm_map={"保鲜优先算法": "freshness_first"},
            amap_strategy_map={"速度优先": 0},
            custom_candidate_strategy_codes=[0, 12, 13],
        )

        unknown_fruit_request = RouteRequest(
            start_text="北京",
            end_text="上海",
            algorithm="保鲜优先算法",
            fruit_type="未知水果",
            transport_mode="公路冷链",
            depart_at=datetime(2026, 4, 12, 9, 0, 0),
            load_ton=5.0,
        )
        apple_request = RouteRequest(
            start_text="北京",
            end_text="上海",
            algorithm="保鲜优先算法",
            fruit_type="苹果",
            transport_mode="公路冷链",
            depart_at=datetime(2026, 4, 12, 9, 0, 0),
            load_ton=5.0,
        )

        unknown_result = service.plan_route(unknown_fruit_request)
        apple_result = service.plan_route(apple_request)

        self.assertEqual(unknown_result.status, "ok")
        self.assertEqual(apple_result.status, "ok")
        self.assertAlmostEqual(
            float(unknown_result.freshness_at_arrival),
            float(apple_result.freshness_at_arrival),
            places=3,
        )

    def test_should_keep_lychee_freshness_lower_than_apple_on_same_route(self) -> None:
        amap_client = _FakeAmapClient()
        service = RoutePlanningService(
            amap_client=amap_client,
            custom_algorithm_map={"保鲜优先算法": "freshness_first"},
            amap_strategy_map={"速度优先": 0},
            custom_candidate_strategy_codes=[0, 12, 13],
        )

        apple_request = RouteRequest(
            start_text="北京",
            end_text="上海",
            algorithm="保鲜优先算法",
            fruit_type="苹果",
            transport_mode="公路冷链",
            depart_at=datetime(2026, 4, 12, 9, 0, 0),
            load_ton=5.0,
        )
        lychee_request = RouteRequest(
            start_text="北京",
            end_text="上海",
            algorithm="保鲜优先算法",
            fruit_type="荔枝",
            transport_mode="公路冷链",
            depart_at=datetime(2026, 4, 12, 9, 0, 0),
            load_ton=5.0,
        )

        apple_result = service.plan_route(apple_request)
        lychee_result = service.plan_route(lychee_request)

        self.assertEqual(apple_result.status, "ok")
        self.assertEqual(lychee_result.status, "ok")
        self.assertLess(
            float(lychee_result.freshness_at_arrival),
            float(apple_result.freshness_at_arrival),
        )

    def test_should_keep_cold_chain_fresher_than_normal_transport(self) -> None:
        amap_client = _FakeAmapClient()
        service = RoutePlanningService(
            amap_client=amap_client,
            custom_algorithm_map={"保鲜优先算法": "freshness_first"},
            amap_strategy_map={"速度优先": 0},
            custom_candidate_strategy_codes=[0, 12, 13],
        )

        cold_chain_request = RouteRequest(
            start_text="北京",
            end_text="上海",
            algorithm="保鲜优先算法",
            fruit_type="苹果",
            transport_mode="公路冷链",
            depart_at=datetime(2026, 4, 12, 9, 0, 0),
            load_ton=5.0,
        )
        normal_request = RouteRequest(
            start_text="北京",
            end_text="上海",
            algorithm="保鲜优先算法",
            fruit_type="苹果",
            transport_mode="公路常温",
            depart_at=datetime(2026, 4, 12, 9, 0, 0),
            load_ton=5.0,
        )

        cold_result = service.plan_route(cold_chain_request)
        normal_result = service.plan_route(normal_request)

        self.assertEqual(cold_result.status, "ok")
        self.assertEqual(normal_result.status, "ok")
        self.assertGreater(
            float(cold_result.freshness_at_arrival),
            float(normal_result.freshness_at_arrival),
        )

    def test_banana_should_be_closer_to_target_at_medium_duration_than_short_duration(self) -> None:
        amap_client = _FakeAmapClient()
        service = RoutePlanningService(amap_client=amap_client)

        short_route = PlannedRoute(
            points_gcj02=[[116.40, 39.90], [116.80, 39.20]],
            total_distance_km=50.0,
            total_time_h=2.0,
            segment_count=1,
            tmcs_status_ratio={"smooth": 1.0},
            curvature_multiplier=1.0,
        )
        medium_route = PlannedRoute(
            points_gcj02=[[116.40, 39.90], [117.60, 37.80]],
            total_distance_km=220.0,
            total_time_h=8.0,
            segment_count=1,
            tmcs_status_ratio={"smooth": 1.0},
            curvature_multiplier=1.0,
        )

        short_freshness, short_delta = service._estimate_freshness_for_planned_route(
            route=short_route,
            road_class="normal",
            fruit_type="香蕉",
            transport_mode="公路冷链",
        )
        medium_freshness, medium_delta = service._estimate_freshness_for_planned_route(
            route=medium_route,
            road_class="normal",
            fruit_type="香蕉",
            transport_mode="公路冷链",
        )

        self.assertGreater(short_freshness, medium_freshness)
        self.assertLess(medium_delta, short_delta)

    def test_should_use_dynamic_detour_ratio_by_fruit_type(self) -> None:
        amap_client = _FakeAmapClient()
        service = RoutePlanningService(
            amap_client=amap_client,
            custom_algorithm_map={"保鲜优先算法": "freshness_first"},
        )

        banana_ratio = service._resolve_freshness_detour_ratio("香蕉")
        mango_ratio = service._resolve_freshness_detour_ratio("芒果")
        apple_ratio = service._resolve_freshness_detour_ratio("苹果")
        lychee_ratio = service._resolve_freshness_detour_ratio("荔枝")

        self.assertGreater(banana_ratio, apple_ratio)
        self.assertGreater(mango_ratio, apple_ratio)
        self.assertLess(lychee_ratio, apple_ratio)

    def test_should_use_amap_engine_when_strategy_is_amap(self) -> None:
        amap_client = _FakeAmapClient()
        service = RoutePlanningService(
            amap_client=amap_client,
            custom_algorithm_map={"自研-Dijkstra": "static_dijkstra"},
            amap_strategy_map={"速度优先": 0, "距离优先": 2},
            default_strategy="速度优先",
        )

        request = RouteRequest(
            start_text="北京",
            end_text="上海",
            algorithm="距离优先",
            fruit_type="苹果",
            transport_mode="公路冷链",
            depart_at=datetime.now(),
            load_ton=5.0,
        )
        result = service.plan_route(request)

        self.assertEqual(result.status, "ok")
        self.assertIn("引擎: 高德", result.message)
        self.assertEqual(amap_client.driving_call_count, 1)
        self.assertEqual(amap_client.candidate_call_count, 0)

    def test_should_return_error_when_geocode_failed(self) -> None:
        amap_client = _FakeAmapClient()
        amap_client.raise_geocode_error = True
        service = RoutePlanningService(amap_client=amap_client)

        request = RouteRequest(
            start_text="北京",
            end_text="上海",
            algorithm="自研-Dijkstra",
            fruit_type="苹果",
            transport_mode="公路冷链",
            depart_at=datetime.now(),
            load_ton=5.0,
        )
        result = service.plan_route(request)

        self.assertEqual(result.status, "error")
        self.assertIn("失败", result.message)

    def test_should_return_error_when_candidates_empty(self) -> None:
        amap_client = _FakeAmapClient()
        amap_client.return_empty_candidates = True
        service = RoutePlanningService(
            amap_client=amap_client,
            custom_algorithm_map={"自研-Dijkstra": "static_dijkstra"},
            amap_strategy_map={"速度优先": 0},
        )

        request = RouteRequest(
            start_text="北京",
            end_text="上海",
            algorithm="自研-Dijkstra",
            fruit_type="苹果",
            transport_mode="公路冷链",
            depart_at=datetime.now(),
            load_ton=5.0,
        )
        result = service.plan_route(request)

        self.assertEqual(result.status, "error")

    def test_time_dependent_should_show_dynamic_hit_count_when_peak(self) -> None:
        amap_client = _FakeAmapClient()
        service = RoutePlanningService(
            amap_client=amap_client,
            custom_algorithm_map={"自研-时变Dijkstra": "time_dependent_dijkstra"},
            amap_strategy_map={"速度优先": 0},
            peak_hours=[8],
            peak_multiplier=1.6,
        )

        request = RouteRequest(
            start_text="北京",
            end_text="上海",
            algorithm="自研-时变Dijkstra",
            fruit_type="苹果",
            transport_mode="公路冷链",
            depart_at=datetime(2026, 4, 11, 8, 0, 0),
            load_ton=5.0,
        )
        result = service.plan_route(request)

        self.assertEqual(result.status, "ok")
        self.assertIn("动态边权命中", result.message)

    def test_should_use_different_objective_labels_for_dijkstra_and_a_star(self) -> None:
        amap_client = _FakeAmapClient()
        service = RoutePlanningService(
            amap_client=amap_client,
            custom_algorithm_map={"自研-Dijkstra": "static_dijkstra", "自研-A*": "a_star"},
            amap_strategy_map={"速度优先": 0},
        )

        request_dijkstra = RouteRequest(
            start_text="北京",
            end_text="上海",
            algorithm="自研-Dijkstra",
            fruit_type="苹果",
            transport_mode="公路冷链",
            depart_at=datetime(2026, 4, 11, 9, 0, 0),
            load_ton=5.0,
        )
        request_a_star = RouteRequest(
            start_text="北京",
            end_text="上海",
            algorithm="自研-A*",
            fruit_type="苹果",
            transport_mode="公路冷链",
            depart_at=datetime(2026, 4, 11, 9, 0, 0),
            load_ton=5.0,
        )

        result_dijkstra = service.plan_route(request_dijkstra)
        result_a_star = service.plan_route(request_a_star)

        self.assertEqual(result_dijkstra.status, "ok")
        self.assertEqual(result_a_star.status, "ok")
        self.assertIn("目标: 距离代价", result_dijkstra.message)
        self.assertIn("目标: 时间代价(A*)", result_a_star.message)

    def test_should_use_improved_freshness_dijkstra_algorithm(self) -> None:
        amap_client = _FakeAmapClient()
        service = RoutePlanningService(
            amap_client=amap_client,
            custom_algorithm_map={"保鲜优先算法-迪杰斯特拉算法改进版": "freshness_dijkstra_improved"},
            amap_strategy_map={"速度优先": 0, "躲避拥堵": 12, "不走高速": 13},
            custom_candidate_strategy_codes=[0, 12, 13],
        )

        request = RouteRequest(
            start_text="北京",
            end_text="上海",
            algorithm="保鲜优先算法-迪杰斯特拉算法改进版",
            fruit_type="香蕉",
            transport_mode="公路冷链",
            depart_at=datetime(2026, 4, 11, 9, 0, 0),
            load_ton=5.0,
        )
        result = service.plan_route(request)

        self.assertEqual(result.status, "ok")
        self.assertIn("最小化|保鲜度-100|(改进Dijkstra)", result.message)
        self.assertIn("引擎: 自研", result.message)
        self.assertGreaterEqual(amap_client.candidate_call_count, 1)
        self.assertIsNotNone(result.freshness_at_arrival)
        self.assertIsNotNone(result.freshness_delta_to_100)


if __name__ == "__main__":
    unittest.main()
