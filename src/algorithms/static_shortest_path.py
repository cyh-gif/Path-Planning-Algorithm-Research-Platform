from __future__ import annotations

import heapq
from dataclasses import dataclass

from src.algorithms.graph_builder import GraphData, GraphEdge


@dataclass(slots=True)
class PathSolveResult:
    node_path: list[int]
    edge_path: list[int]
    total_cost: float


class StaticShortestPathSolver:
    def solve(
        self,
        graph: GraphData,
        start_node_id: int,
        end_node_id: int,
        weight_mode: str = "time",
    ) -> PathSolveResult:
        """使用 Dijkstra 计算静态最短路径。"""
        def edge_weight(edge: GraphEdge) -> float:
            if weight_mode == "distance":
                return edge.length_m
            return edge.base_travel_time_s

        dist: dict[int, float] = {start_node_id: 0.0}
        prev_node: dict[int, int] = {}
        prev_edge: dict[int, int] = {}
        visited: set[int] = set()

        heap: list[tuple[float, int]] = [(0.0, start_node_id)]

        while heap:
            curr_dist, node_id = heapq.heappop(heap)
            if node_id in visited:
                continue
            visited.add(node_id)

            if node_id == end_node_id:
                break

            for edge in graph.edges_by_from.get(node_id, []):
                nxt = edge.to_node_id
                w = edge_weight(edge)
                candidate = curr_dist + w
                if candidate < dist.get(nxt, float("inf")):
                    dist[nxt] = candidate
                    prev_node[nxt] = node_id
                    prev_edge[nxt] = edge.edge_id
                    heapq.heappush(heap, (candidate, nxt))

        if end_node_id not in dist:
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

        return PathSolveResult(node_path=node_path, edge_path=edge_path, total_cost=dist[end_node_id])
