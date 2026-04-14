import json
import unittest
from unittest.mock import patch

from src.services.amap_web_service import AMapWebServiceClient


class _FakeHttpResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class TestAMapWebServiceClientCandidates(unittest.TestCase):
    def test_plan_route_should_support_waypoints_param(self) -> None:
        payload = {
            "status": "1",
            "route": {
                "paths": [
                    {
                        "distance": "8000",
                        "duration": "1000",
                        "steps": [{"polyline": "116.0,39.0;116.4,39.2"}],
                    }
                ]
            },
        }
        captured_urls: list[str] = []

        def fake_urlopen(url: str, timeout: float):
            captured_urls.append(url)
            return _FakeHttpResponse(payload)

        with patch("src.services.amap_web_service.urllib.request.urlopen", side_effect=fake_urlopen):
            client = AMapWebServiceClient(web_service_key="test-key", request_timeout_s=1.0, retry=0)
            route = client.plan_driving_route(
                origin_gcj02=(116.0, 39.0),
                destination_gcj02=(116.4, 39.2),
                strategy=0,
                waypoints_gcj02=[(116.2, 39.1)],
            )

        self.assertGreaterEqual(len(route.points_gcj02), 2)
        self.assertTrue(any("waypoints=" in url for url in captured_urls))

    def test_should_return_multiple_paths_per_strategy(self) -> None:
        payload = {
            "status": "1",
            "route": {
                "paths": [
                    {
                        "distance": "10000",
                        "duration": "1200",
                        "steps": [
                            {"polyline": "116.0,39.0;116.2,39.1"},
                            {"polyline": "116.2,39.1;116.4,39.2"},
                        ],
                    },
                    {
                        "distance": "11000",
                        "duration": "1400",
                        "steps": [
                            {"polyline": "116.0,39.0;116.1,39.15"},
                            {"polyline": "116.1,39.15;116.4,39.2"},
                        ],
                    },
                ]
            },
        }

        with patch(
            "src.services.amap_web_service.urllib.request.urlopen",
            return_value=_FakeHttpResponse(payload),
        ):
            client = AMapWebServiceClient(web_service_key="test-key", request_timeout_s=1.0, retry=0)
            result = client.plan_driving_route_candidates(
                origin_gcj02=(116.0, 39.0),
                destination_gcj02=(116.4, 39.2),
                strategies=[0],
                max_paths_per_strategy=2,
                use_tmcs=False,
                densify_max_segment_m=0.0,
            )

        self.assertIn(0, result)
        self.assertEqual(len(result[0]), 2)
        self.assertGreaterEqual(len(result[0][0].points_gcj02), 3)

    def test_should_prefer_tmcs_polyline_when_enabled(self) -> None:
        payload = {
            "status": "1",
            "route": {
                "paths": [
                    {
                        "distance": "6000",
                        "duration": "900",
                        "steps": [
                            {
                                "polyline": "116.0,39.0;116.4,39.2",
                                "tmcs": [
                                    {"polyline": "116.0,39.0;116.1,39.05;116.2,39.1"},
                                    {"polyline": "116.2,39.1;116.3,39.15;116.4,39.2"},
                                ],
                            }
                        ],
                    }
                ]
            },
        }

        with patch(
            "src.services.amap_web_service.urllib.request.urlopen",
            return_value=_FakeHttpResponse(payload),
        ):
            client = AMapWebServiceClient(web_service_key="test-key", request_timeout_s=1.0, retry=0)
            result = client.plan_driving_route_candidates(
                origin_gcj02=(116.0, 39.0),
                destination_gcj02=(116.4, 39.2),
                strategies=[12],
                max_paths_per_strategy=1,
                use_tmcs=True,
                densify_max_segment_m=0.0,
            )

        points = result[12][0].points_gcj02
        self.assertGreaterEqual(len(points), 5)
        self.assertEqual(points[0], [116.0, 39.0])
        self.assertEqual(points[-1], [116.4, 39.2])

    def test_should_densify_long_segments(self) -> None:
        payload = {
            "status": "1",
            "route": {
                "paths": [
                    {
                        "distance": "120000",
                        "duration": "5400",
                        "steps": [{"polyline": "116.0,39.0;117.0,39.0"}],
                    }
                ]
            },
        }

        with patch(
            "src.services.amap_web_service.urllib.request.urlopen",
            return_value=_FakeHttpResponse(payload),
        ):
            client = AMapWebServiceClient(web_service_key="test-key", request_timeout_s=1.0, retry=0)
            result = client.plan_driving_route_candidates(
                origin_gcj02=(116.0, 39.0),
                destination_gcj02=(117.0, 39.0),
                strategies=[19],
                max_paths_per_strategy=1,
                use_tmcs=False,
                densify_max_segment_m=30000.0,
            )

        points = result[19][0].points_gcj02
        self.assertGreater(len(points), 2)


if __name__ == "__main__":
    unittest.main()
