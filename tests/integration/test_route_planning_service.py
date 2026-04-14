import json
from datetime import datetime
import urllib.parse
import unittest
from unittest.mock import patch

from src.models.route_request import RouteRequest
from src.services.amap_web_service import AMapWebServiceClient
from src.services.route_planning_service import RoutePlanningService


class _FakeHttpResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class TestRoutePlanningServiceIntegration(unittest.TestCase):
    def test_should_use_custom_algorithm_with_amap_candidates(self) -> None:
        counters = {"geocode": 0, "driving": 0}

        def fake_urlopen(url: str, timeout: float):
            self.assertGreater(timeout, 0)
            if "/v3/geocode/geo" in url:
                counters["geocode"] += 1
                parsed = urllib.parse.urlparse(url)
                query = urllib.parse.parse_qs(parsed.query)
                address = (query.get("address") or [""])[0]
                if "北京" in address:
                    return _FakeHttpResponse({"status": "1", "geocodes": [{"location": "116.397470,39.908823"}]})
                return _FakeHttpResponse({"status": "1", "geocodes": [{"location": "121.473701,31.230416"}]})

            if "/v3/direction/driving" in url:
                counters["driving"] += 1
                parsed = urllib.parse.urlparse(url)
                query = urllib.parse.parse_qs(parsed.query)
                strategy = int((query.get("strategy") or ["0"])[0])

                mid = {
                    0: "117.200000,38.800000",
                    1: "118.000000,37.800000",
                    2: "118.300000,37.200000",
                    3: "116.900000,39.100000",
                    4: "117.500000,38.500000",
                }.get(strategy, "117.200000,38.800000")

                payload = {
                    "status": "1",
                    "route": {
                        "paths": [
                            {
                                "distance": str(1200000 + strategy * 10000),
                                "duration": str(43200 + strategy * 600),
                                "steps": [
                                    {"polyline": f"116.397470,39.908823;{mid}"},
                                    {"polyline": f"{mid};121.473701,31.230416"},
                                ],
                            }
                        ]
                    },
                }
                return _FakeHttpResponse(payload)

            raise AssertionError(f"未处理的 URL: {url}")

        with patch("src.services.amap_web_service.urllib.request.urlopen", side_effect=fake_urlopen):
            client = AMapWebServiceClient(web_service_key="test-key", request_timeout_s=1.0, retry=0)
            service = RoutePlanningService(
                amap_client=client,
                custom_algorithm_map={"自研-Dijkstra": "static_dijkstra"},
                amap_strategy_map={"速度优先": 0},
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
        self.assertGreaterEqual(counters["geocode"], 2)
        self.assertGreaterEqual(counters["driving"], 1)
        self.assertIsNotNone(result.freshness_at_arrival)
        self.assertIsNotNone(result.freshness_delta_to_100)

    def test_should_keep_amap_path_when_selecting_amap_strategy(self) -> None:
        counters = {"geocode": 0, "driving": 0}

        def fake_urlopen(url: str, timeout: float):
            self.assertGreater(timeout, 0)
            if "/v3/geocode/geo" in url:
                counters["geocode"] += 1
                if "北京" in url:
                    return _FakeHttpResponse({"status": "1", "geocodes": [{"location": "116.397470,39.908823"}]})
                return _FakeHttpResponse({"status": "1", "geocodes": [{"location": "121.473701,31.230416"}]})

            if "/v3/direction/driving" in url:
                counters["driving"] += 1
                return _FakeHttpResponse(
                    {
                        "status": "1",
                        "route": {
                            "paths": [
                                {
                                    "distance": "1210000",
                                    "duration": "46800",
                                    "steps": [
                                        {"polyline": "116.397470,39.908823;117.200000,38.800000"},
                                        {"polyline": "117.200000,38.800000;121.473701,31.230416"},
                                    ],
                                }
                            ]
                        },
                    }
                )

            raise AssertionError(f"未处理的 URL: {url}")

        with patch("src.services.amap_web_service.urllib.request.urlopen", side_effect=fake_urlopen):
            client = AMapWebServiceClient(web_service_key="test-key", request_timeout_s=1.0, retry=0)
            service = RoutePlanningService(
                amap_client=client,
                amap_strategy_map={"速度优先": 0},
            )

            request = RouteRequest(
                start_text="北京",
                end_text="上海",
                algorithm="速度优先",
                fruit_type="苹果",
                transport_mode="公路冷链",
                depart_at=datetime.now(),
                load_ton=5.0,
            )
            result = service.plan_route(request)

        self.assertEqual(result.status, "ok")
        self.assertIn("引擎: 高德", result.message)
        self.assertGreater(result.total_distance_km, 0)
        self.assertEqual(counters["geocode"], 2)
        self.assertEqual(counters["driving"], 1)
        self.assertIsNotNone(result.freshness_at_arrival)
        self.assertIsNotNone(result.freshness_delta_to_100)


if __name__ == "__main__":
    unittest.main()
