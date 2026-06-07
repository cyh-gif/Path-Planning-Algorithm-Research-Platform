"""目标保鲜K最短路算法（TF-KSP）。

先在候选图上按时间代价枚举若干条近最短简单路径，再按“保鲜度偏差最小”
进行重排序，最终返回最接近目标保鲜度 100 的路径。
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from math import inf

from src.algorithms.graph_builder import GraphData
from src.algorithms.dijkstra_shortest_path import PathSolveResult


@dataclass(slots=True)
class _RouteCandidate:
    node_path: list[int]
    edge_path: list[int]
    total_secondary: float
    total_loss: float
    total_distance_m: float


class TargetFreshnessKShortestPathSolver:
    """TF-KSP：候选图上的 K 最短路 + 目标新鲜度重排序。"""

    _DEFAULT_MAX_CANDIDATE_PATHS = 8
    _DEFAULT_MAX_LOSS_LIMIT = 260.0
    _DEFAULT_MAX_SPUR_EXPANSIONS = 72
    _EPS = 1e-9

    def solve(
        self,
        graph: GraphData,
        start_node_id: int,
        end_node_id: int,
        edge_freshness_loss_by_id: dict[int, float],
        target_loss: float,
        edge_secondary_cost_by_id: dict[int, float] | None = None,
        max_secondary_cost: float | None = None,
        max_loss_limit: float | None = None,
        max_candidate_paths: int = _DEFAULT_MAX_CANDIDATE_PATHS,
    ) -> PathSolveResult:
        if start_node_id == end_node_id:
            return PathSolveResult(node_path=[start_node_id], edge_path=[], total_cost=0.0)

        if start_node_id not in graph.nodes or end_node_id not in graph.nodes:
            raise ValueError("起终点节点不存在于图中。")

        safe_target_loss = max(0.0, float(target_loss))
        adaptive_loss_limit = max(safe_target_loss * 2.4, safe_target_loss + 12.0, 24.0)
        safe_max_loss_limit = max(
            8.0,
            min(
                max(float(max_loss_limit or self._DEFAULT_MAX_LOSS_LIMIT), adaptive_loss_limit),
                480.0,
            ),
        )
        safe_max_secondary_cost = None if max_secondary_cost is None else max(float(max_secondary_cost), 1e-6)
        candidate_limit = max(2, int(max_candidate_paths))
        secondary_map = edge_secondary_cost_by_id or {}

        first_path = self._shortest_path(
            graph=graph,
            start_node_id=start_node_id,
            end_node_id=end_node_id,
            edge_secondary_cost_by_id=secondary_map,
            banned_nodes=set(),
            banned_edges=set(),
        )
        if first_path is None:
            raise ValueError("目标保鲜K最短路算法未找到可达路径。")
        first_path.total_loss = self._sum_loss(
            edge_path=first_path.edge_path,
            edge_freshness_loss_by_id=edge_freshness_loss_by_id,
        )

        accepted: list[_RouteCandidate] = [first_path]
        accepted_signatures: set[tuple[int, ...]] = {tuple(first_path.edge_path)}
        candidate_heap: list[tuple[float, float, float, int, _RouteCandidate]] = []
        candidate_seq = 0

        while len(accepted) < candidate_limit:
            previous = accepted[-1]
            root_secondary_prefix = self._build_prefix_sums(
                edge_path=previous.edge_path,
                edge_value_by_id=secondary_map,
                default_edge_value_by_id=graph.edges_by_id,
                default_attr="base_travel_time_s",
                minimum_value=1e-6,
            )
            root_loss_prefix = self._build_prefix_sums(
                edge_path=previous.edge_path,
                edge_value_by_id=edge_freshness_loss_by_id,
                default_edge_value_by_id=None,
                default_attr="",
                minimum_value=0.0,
            )
            root_distance_prefix = self._build_prefix_sums(
                edge_path=previous.edge_path,
                edge_value_by_id=None,
                default_edge_value_by_id=graph.edges_by_id,
                default_attr="length_m",
                minimum_value=0.0,
            )

            for spur_index in self._select_spur_indices(previous=previous, graph=graph):
                spur_node_id = previous.node_path[spur_index]
                root_node_path = previous.node_path[: spur_index + 1]
                root_edge_path = previous.edge_path[:spur_index]
                banned_nodes = set(root_node_path[:-1])
                banned_edges: set[int] = set()

                for path in accepted:
                    if len(path.edge_path) <= spur_index:
                        continue
                    if path.node_path[: spur_index + 1] == root_node_path:
                        banned_edges.add(path.edge_path[spur_index])

                spur_path = self._shortest_path(
                    graph=graph,
                    start_node_id=spur_node_id,
                    end_node_id=end_node_id,
                    edge_secondary_cost_by_id=secondary_map,
                    banned_nodes=banned_nodes,
                    banned_edges=banned_edges,
                )
                if spur_path is None:
                    continue
                spur_path.total_loss = self._sum_loss(
                    edge_path=spur_path.edge_path,
                    edge_freshness_loss_by_id=edge_freshness_loss_by_id,
                )

                merged = self._merge_paths(
                    root_node_path=root_node_path,
                    root_edge_path=root_edge_path,
                    spur_path=spur_path,
                )
                signature = tuple(merged.edge_path)
                if signature in accepted_signatures:
                    continue

                merged.total_secondary = root_secondary_prefix[spur_index] + spur_path.total_secondary
                merged.total_loss = root_loss_prefix[spur_index] + spur_path.total_loss
                merged.total_distance_m = root_distance_prefix[spur_index] + spur_path.total_distance_m
                if safe_max_secondary_cost is not None and merged.total_secondary > safe_max_secondary_cost + self._EPS:
                    continue
                if merged.total_loss > safe_max_loss_limit + self._EPS:
                    continue
                candidate_seq += 1
                heapq.heappush(
                    candidate_heap,
                    (
                        merged.total_secondary,
                        abs(merged.total_loss - safe_target_loss),
                        merged.total_distance_m,
                        candidate_seq,
                        merged,
                    ),
                )

            next_candidate: _RouteCandidate | None = None
            while candidate_heap:
                _, _, _, _, cand = heapq.heappop(candidate_heap)
                sig = tuple(cand.edge_path)
                if sig in accepted_signatures:
                    continue
                next_candidate = cand
                break

            if next_candidate is None:
                break

            accepted.append(next_candidate)
            accepted_signatures.add(tuple(next_candidate.edge_path))

        working_set = [
            path
            for path in accepted
            if path.total_loss <= safe_max_loss_limit + self._EPS
            and (safe_max_secondary_cost is None or path.total_secondary <= safe_max_secondary_cost + self._EPS)
        ]
        if not working_set:
            working_set = [
                path
                for path in accepted
                if path.total_loss <= safe_max_loss_limit + self._EPS
            ] or accepted

        best = min(
            working_set,
            key=lambda path: (
                abs(path.total_loss - safe_target_loss),
                path.total_secondary,
                path.total_distance_m,
                len(path.edge_path),
            ),
        )
        return PathSolveResult(
            node_path=best.node_path,
            edge_path=best.edge_path,
            total_cost=abs(best.total_loss - safe_target_loss),
        )

    def _shortest_path(
        self,
        graph: GraphData,
        start_node_id: int,
        end_node_id: int,
        edge_secondary_cost_by_id: dict[int, float],
        banned_nodes: set[int],
        banned_edges: set[int],
    ) -> _RouteCandidate | None:
        dist: dict[int, float] = {start_node_id: 0.0}
        prev_node: dict[int, int] = {}
        prev_edge: dict[int, int] = {}
        heap: list[tuple[float, int]] = [(0.0, start_node_id)]

        while heap:
            curr_secondary, node_id = heapq.heappop(heap)
            if curr_secondary > dist.get(node_id, inf) + self._EPS:
                continue
            if node_id == end_node_id:
                break

            for edge in graph.edges_by_from.get(node_id, []):
                if edge.edge_id in banned_edges:
                    continue
                nxt = edge.to_node_id
                if nxt in banned_nodes and nxt != end_node_id:
                    continue
                step_secondary = max(
                    1e-6,
                    float(edge_secondary_cost_by_id.get(edge.edge_id, max(float(edge.base_travel_time_s), 1e-6))),
                )
                candidate_secondary = curr_secondary + step_secondary
                if candidate_secondary + self._EPS < dist.get(nxt, inf):
                    dist[nxt] = candidate_secondary
                    prev_node[nxt] = node_id
                    prev_edge[nxt] = edge.edge_id
                    heapq.heappush(heap, (candidate_secondary, nxt))

        if end_node_id not in dist:
            return None

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

        return self._build_candidate(
            graph=graph,
            node_path=node_path,
            edge_path=edge_path,
            edge_secondary_cost_by_id=edge_secondary_cost_by_id,
        )

    def _merge_paths(
        self,
        root_node_path: list[int],
        root_edge_path: list[int],
        spur_path: _RouteCandidate,
    ) -> _RouteCandidate:
        if not root_node_path or not spur_path.node_path:
            return _RouteCandidate([], [], 0.0, 0.0, 0.0)
        node_path = root_node_path[:-1] + list(spur_path.node_path)
        edge_path = list(root_edge_path) + list(spur_path.edge_path)
        return _RouteCandidate(node_path, edge_path, 0.0, 0.0, 0.0)

    def _select_spur_indices(
        self,
        previous: _RouteCandidate,
        graph: GraphData,
    ) -> list[int]:
        path_edge_count = len(previous.edge_path)
        if path_edge_count <= 0:
            return []

        limit = min(path_edge_count, self._DEFAULT_MAX_SPUR_EXPANSIONS)
        if path_edge_count <= limit:
            return list(range(path_edge_count))

        branch_indices = [
            idx
            for idx, node_id in enumerate(previous.node_path[:-1])
            if len(graph.edges_by_from.get(node_id, [])) > 1
        ]
        if not branch_indices:
            branch_indices = list(range(path_edge_count))

        return self._sample_indices(branch_indices, limit)

    def _sample_indices(self, ordered_indices: list[int], limit: int) -> list[int]:
        if not ordered_indices:
            return []
        if len(ordered_indices) <= limit:
            return ordered_indices

        sampled: list[int] = []
        last_index = len(ordered_indices) - 1
        for step in range(limit):
            position = round(step * last_index / max(limit - 1, 1))
            index_value = ordered_indices[position]
            if sampled and sampled[-1] == index_value:
                continue
            sampled.append(index_value)

        if sampled[0] != ordered_indices[0]:
            sampled[0] = ordered_indices[0]
        if sampled[-1] != ordered_indices[-1]:
            sampled[-1] = ordered_indices[-1]
        return sampled

    def _build_candidate(
        self,
        graph: GraphData,
        node_path: list[int],
        edge_path: list[int],
        edge_secondary_cost_by_id: dict[int, float],
    ) -> _RouteCandidate:
        return _RouteCandidate(
            node_path=node_path,
            edge_path=edge_path,
            total_secondary=self._sum_secondary(
                graph=graph,
                edge_path=edge_path,
                edge_secondary_cost_by_id=edge_secondary_cost_by_id,
            ),
            total_loss=0.0,
            total_distance_m=self._sum_distance(graph=graph, edge_path=edge_path),
        )

    def _sum_secondary(
        self,
        graph: GraphData,
        edge_path: list[int],
        edge_secondary_cost_by_id: dict[int, float],
    ) -> float:
        total = 0.0
        for edge_id in edge_path:
            edge = graph.edges_by_id[edge_id]
            total += max(1e-6, float(edge_secondary_cost_by_id.get(edge_id, max(float(edge.base_travel_time_s), 1e-6))))
        return total

    def _sum_loss(
        self,
        edge_path: list[int],
        edge_freshness_loss_by_id: dict[int, float],
    ) -> float:
        total = 0.0
        for edge_id in edge_path:
            total += max(0.0, float(edge_freshness_loss_by_id.get(edge_id, 0.0)))
        return total

    def _sum_distance(self, graph: GraphData, edge_path: list[int]) -> float:
        total = 0.0
        for edge_id in edge_path:
            total += max(0.0, float(graph.edges_by_id[edge_id].length_m))
        return total

    def _build_prefix_sums(
        self,
        edge_path: list[int],
        edge_value_by_id: dict[int, float] | None,
        default_edge_value_by_id: dict[int, object] | None,
        default_attr: str,
        minimum_value: float,
    ) -> list[float]:
        prefix = [0.0]
        total = 0.0
        for edge_id in edge_path:
            if edge_value_by_id is not None and edge_id in edge_value_by_id:
                raw_value = edge_value_by_id[edge_id]
            elif default_edge_value_by_id is not None and default_attr:
                raw_value = getattr(default_edge_value_by_id[edge_id], default_attr)
            else:
                raw_value = 0.0
            total += max(minimum_value, float(raw_value))
            prefix.append(total)
        return prefix
