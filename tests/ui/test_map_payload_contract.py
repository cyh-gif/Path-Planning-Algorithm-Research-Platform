import unittest

from src.models.map_payload import MapPayload
from src.models.route_result import RouteResult


class TestMapPayloadContract(unittest.TestCase):
    def test_should_build_payload_with_points_and_meta(self) -> None:
        result = RouteResult(
            path_points_wgs84=[[116.4, 39.9], [121.47, 31.23]],
            path_points_gcj02=[[116.41, 39.91], [121.48, 31.24]],
            total_distance_km=1200.5,
            total_time_h=14.2,
            compute_ms=20.1,
            node_count=20,
            edge_count=19,
            status="ok",
            message="规划成功",
            freshness_at_arrival=98.3,
            freshness_delta_to_100=1.7,
        )

        payload = MapPayload.from_route_result(
            result=result,
            start_text="北京",
            end_text="上海",
            algorithm="Dijkstra",
            fruit_type="苹果",
        )

        raw = payload.to_dict()
        self.assertIn("points", raw)
        self.assertIn("meta", raw)
        self.assertEqual(raw["points"], result.path_points_gcj02)
        self.assertEqual(raw["meta"]["start"], "北京")
        self.assertEqual(raw["meta"]["end"], "上海")
        self.assertEqual(raw["meta"]["freshness_at_arrival"], 98.3)
        self.assertEqual(raw["meta"]["freshness_delta_to_100"], 1.7)


if __name__ == "__main__":
    unittest.main()
