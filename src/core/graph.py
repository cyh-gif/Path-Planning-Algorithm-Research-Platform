"""图结构基础模块。

本文件定义算法层通用的图数据结构，包括节点、边以及图的索引组织方式，
供 Dijkstra、A*、保鲜优化等路径算法统一使用。
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(slots=True)
class GraphEdge:
    """表示图中的一条有向边及其几何与代价信息。"""

    edge_id: int
    from_node_id: int
    to_node_id: int
    length_m: float
    base_travel_time_s: float
    geometry: list[tuple[float, float]]
    road_class: str = ""
    dynamic_travel_time_s: float | None = None


@dataclass(slots=True)
class GraphData:
    """表示算法求解使用的整张图及其快速访问索引。"""

    nodes: dict[int, tuple[float, float]]
    edges_by_from: dict[int, list[GraphEdge]]
    edges_by_id: dict[int, GraphEdge]

    @classmethod
    def build(
        cls,
        nodes: dict[int, tuple[float, float]],
        edges: list[GraphEdge],
    ) -> "GraphData":
        """根据节点和边列表构建图对象及边索引。"""

        edges_by_from: dict[int, list[GraphEdge]] = {}
        edges_by_id: dict[int, GraphEdge] = {}

        for edge in edges:
            edges_by_from.setdefault(edge.from_node_id, []).append(edge)
            edges_by_id[edge.edge_id] = edge

        return cls(nodes=nodes, edges_by_from=edges_by_from, edges_by_id=edges_by_id)

    def nearest_node(self, lon: float, lat: float) -> int:
        """根据经纬度在图中查找最近的节点。"""

        if not self.nodes:
            raise ValueError("图中没有节点，无法匹配最近节点。")

        best_node = -1
        best_dist = float("inf")
        for node_id, (node_lon, node_lat) in self.nodes.items():
            dist = haversine_km(lon, lat, node_lon, node_lat)
            if dist < best_dist:
                best_dist = dist
                best_node = node_id

        return best_node


def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """使用 Haversine 公式计算两点之间的球面距离（千米）。"""

    rad = math.pi / 180.0
    d_lat = (lat2 - lat1) * rad
    d_lon = (lon2 - lon1) * rad
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(lat1 * rad) * math.cos(lat2 * rad) * (math.sin(d_lon / 2) ** 2)
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return 6371.0 * c
