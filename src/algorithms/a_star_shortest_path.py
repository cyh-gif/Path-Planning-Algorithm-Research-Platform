from __future__ import annotations

import heapq
from math import inf

from src.core.graph import GraphData, GraphEdge, haversine_km
from src.core.path_result import PathSolveResult


class AStarShortestPathSolver:
    def solve(
        self,
        graph: GraphData,
        start_node_id: int,
        end_node_id: int,
        weight_mode: str = "time",
    ) -> PathSolveResult:
        """使用 A* 计算最短路径。"""
        if start_node_id == end_node_id:
            return PathSolveResult(node_path=[start_node_id], edge_path=[], total_cost=0.0)

        if start_node_id not in graph.nodes or end_node_id not in graph.nodes:
            raise ValueError("起终点节点不存在于图中。")

        if weight_mode not in {"time", "distance"}:
            raise ValueError(f"不支持的权重模式: {weight_mode}")

        max_speed_m_s = self._estimate_max_speed_m_s(graph)
        enable_geo_heuristic = self._can_use_geo_heuristic(graph)
        end_lon, end_lat = graph.nodes[end_node_id]

        def edge_weight(edge: GraphEdge) -> float:
            if weight_mode == "distance":
                return edge.length_m
            return edge.base_travel_time_s

        def heuristic(node_id: int) -> float:
            if not enable_geo_heuristic:
                return 0.0
            lon, lat = graph.nodes[node_id]
            distance_m = haversine_km(lon, lat, end_lon, end_lat) * 1000.0
            if weight_mode == "distance":
                return distance_m
            # 对时间代价使用“直线距离/图中最大速度”作为低估启发式，保证启发式不激进。
            return distance_m / max_speed_m_s

        g_score: dict[int, float] = {start_node_id: 0.0}
        prev_node: dict[int, int] = {}
        prev_edge: dict[int, int] = {}

        heap: list[tuple[float, float, int]] = []
        heapq.heappush(heap, (heuristic(start_node_id), 0.0, start_node_id))

        closed: set[int] = set()

        while heap:
            f_score, curr_g, node_id = heapq.heappop(heap)
            _ = f_score
            if node_id in closed:
                continue
            if curr_g > g_score.get(node_id, inf):
                continue

            if node_id == end_node_id:
                break

            closed.add(node_id)
            for edge in graph.edges_by_from.get(node_id, []):
                nxt = edge.to_node_id
                candidate_g = curr_g + edge_weight(edge)
                if candidate_g < g_score.get(nxt, inf):
                    g_score[nxt] = candidate_g
                    prev_node[nxt] = node_id
                    prev_edge[nxt] = edge.edge_id
                    candidate_f = candidate_g + heuristic(nxt)
                    heapq.heappush(heap, (candidate_f, candidate_g, nxt))

        if end_node_id not in g_score:
            raise ValueError("未找到可达路径。")

        node_path: list[int] = [end_node_id]
        edge_path: list[int] = []
        cursor = end_node_id
        while cursor != start_node_id:
            edge_id = prev_edge[cursor]
            edge_path.append(edge_id)
            cursor = prev_node[cursor]
            node_path.append(cursor)

        node_path.reverse()
        edge_path.reverse()
        return PathSolveResult(
            node_path=node_path,
            edge_path=edge_path,
            total_cost=g_score[end_node_id],
        )

    def _estimate_max_speed_m_s(self, graph: GraphData) -> float:
        best = 1.0
        for edge in graph.edges_by_id.values():
            if edge.base_travel_time_s <= 0:
                continue
            speed = edge.length_m / edge.base_travel_time_s
            if speed > best:
                best = speed
        return max(best, 1.0)

    def _can_use_geo_heuristic(self, graph: GraphData) -> bool:
        """仅在边长不小于直线距离时启用几何启发式，避免过估导致次优解。"""
        for edge in graph.edges_by_id.values():
            from_lon, from_lat = graph.nodes[edge.from_node_id]
            to_lon, to_lat = graph.nodes[edge.to_node_id]
            direct_m = haversine_km(from_lon, from_lat, to_lon, to_lat) * 1000.0
            if edge.length_m + 1e-6 < direct_m:
                return False
        return True
