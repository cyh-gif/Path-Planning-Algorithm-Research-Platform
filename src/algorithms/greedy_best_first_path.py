from __future__ import annotations

import heapq

from src.core.graph import GraphData, GraphEdge, haversine_km
from src.algorithms.path_result import PathSolveResult


class GreedyBestFirstPathSolver:
    """贪心最佳优先搜索：优先扩展“直线距离终点更近”的节点。"""

    def solve(
        self,
        graph: GraphData,
        start_node_id: int,
        end_node_id: int,
        weight_mode: str = "distance",
    ) -> PathSolveResult:
        if start_node_id == end_node_id:
            return PathSolveResult(node_path=[start_node_id], edge_path=[], total_cost=0.0)
        if start_node_id not in graph.nodes or end_node_id not in graph.nodes:
            raise ValueError("起终点节点不存在于图中。")
        if weight_mode not in {"distance", "time"}:
            raise ValueError(f"不支持的权重模式: {weight_mode}")

        end_lon, end_lat = graph.nodes[end_node_id]

        def edge_cost(edge: GraphEdge) -> float:
            if weight_mode == "time":
                return edge.base_travel_time_s
            return edge.length_m

        def heuristic(node_id: int) -> float:
            lon, lat = graph.nodes[node_id]
            return haversine_km(lon, lat, end_lon, end_lat)

        frontier: list[tuple[float, int]] = [(heuristic(start_node_id), start_node_id)]
        visited: set[int] = set()
        discovered: set[int] = {start_node_id}
        prev_node: dict[int, int] = {}
        prev_edge: dict[int, int] = {}

        while frontier:
            _, node_id = heapq.heappop(frontier)
            if node_id in visited:
                continue
            if node_id == end_node_id:
                break
            visited.add(node_id)

            edges = graph.edges_by_from.get(node_id, [])
            if not edges:
                continue

            # 邻居按“离终点更近”优先，同启发式下用边代价做次排序。
            ordered = sorted(
                edges,
                key=lambda edge: (heuristic(edge.to_node_id), edge_cost(edge)),
            )
            for edge in ordered:
                nxt = edge.to_node_id
                if nxt in visited or nxt in discovered:
                    continue
                discovered.add(nxt)
                prev_node[nxt] = node_id
                prev_edge[nxt] = edge.edge_id
                heapq.heappush(frontier, (heuristic(nxt), nxt))

        if end_node_id not in prev_node:
            raise ValueError("贪心算法未找到可达路径。")

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

        total_cost = 0.0
        for edge_id in edge_path:
            edge = graph.edges_by_id.get(edge_id)
            if edge is None:
                continue
            total_cost += edge_cost(edge)

        return PathSolveResult(node_path=node_path, edge_path=edge_path, total_cost=total_cost)
