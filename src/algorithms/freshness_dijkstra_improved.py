from __future__ import annotations

import heapq

from src.algorithms.graph_builder import GraphData
from src.algorithms.static_shortest_path import PathSolveResult


class FreshnessDijkstraImprovedSolver:
    """保鲜优先的改进 Dijkstra。

    核心思想：
    1. 以“累计保鲜损耗”作为资源维度做离散化；
    2. 在 (node, loss_bin) 状态空间上，用 Dijkstra 最小化二级代价（默认时间）；
    3. 在终点所有可达状态中，选择 |累计损耗 - 目标损耗| 最小的路径。
    """

    _DEFAULT_LOSS_BIN_SIZE = 0.1
    _DEFAULT_MAX_LOSS_LIMIT = 220.0

    def solve(
        self,
        graph: GraphData,
        start_node_id: int,
        end_node_id: int,
        edge_freshness_loss_by_id: dict[int, float],
        target_loss: float,
        edge_secondary_cost_by_id: dict[int, float] | None = None,
        loss_bin_size: float = _DEFAULT_LOSS_BIN_SIZE,
        max_loss_limit: float = _DEFAULT_MAX_LOSS_LIMIT,
    ) -> PathSolveResult:
        if start_node_id == end_node_id:
            return PathSolveResult(node_path=[start_node_id], edge_path=[], total_cost=0.0)

        if start_node_id not in graph.nodes or end_node_id not in graph.nodes:
            raise ValueError("起终点节点不存在于图中。")

        safe_bin = max(0.02, float(loss_bin_size))
        # 当目标损耗较大时适度放宽上界，覆盖“接近目标值”所需路径。
        adaptive_limit = max(float(target_loss) * 2.2, float(target_loss) + 8.0, 20.0)
        safe_limit = max(8.0, min(max(float(max_loss_limit), adaptive_limit), 400.0))
        max_bin_index = int(safe_limit / safe_bin)

        secondary_map = edge_secondary_cost_by_id or {}

        def edge_loss(edge_id: int) -> float:
            return max(0.0, float(edge_freshness_loss_by_id.get(edge_id, 0.0)))

        def edge_secondary(edge_id: int) -> float:
            if edge_id in secondary_map:
                return max(1e-6, float(secondary_map[edge_id]))
            edge = graph.edges_by_id[edge_id]
            return max(1e-6, float(edge.base_travel_time_s))

        start_state = (start_node_id, 0)
        # 每个状态存最小二级代价（时间），确保 Dijkstra 最优性。
        best_secondary_cost: dict[tuple[int, int], float] = {start_state: 0.0}
        # 记录达到该状态时的真实累计损耗（非离散值）。
        cumulative_loss: dict[tuple[int, int], float] = {start_state: 0.0}
        prev_state: dict[tuple[int, int], tuple[int, int]] = {}
        prev_edge: dict[tuple[int, int], int] = {}

        heap: list[tuple[float, int, int]] = [(0.0, start_node_id, 0)]

        while heap:
            curr_secondary, node_id, loss_bin = heapq.heappop(heap)
            state = (node_id, loss_bin)
            if curr_secondary > best_secondary_cost.get(state, float("inf")) + 1e-9:
                continue

            curr_loss = cumulative_loss[state]
            for edge in graph.edges_by_from.get(node_id, []):
                e_loss = edge_loss(edge.edge_id)
                next_loss = curr_loss + e_loss
                if next_loss > safe_limit + 1e-9:
                    continue

                next_bin = min(int(next_loss / safe_bin + 1e-9), max_bin_index)
                next_state = (edge.to_node_id, next_bin)
                next_secondary = curr_secondary + edge_secondary(edge.edge_id)

                old_secondary = best_secondary_cost.get(next_state)
                if old_secondary is None or next_secondary + 1e-9 < old_secondary:
                    best_secondary_cost[next_state] = next_secondary
                    cumulative_loss[next_state] = next_loss
                    prev_state[next_state] = state
                    prev_edge[next_state] = edge.edge_id
                    heapq.heappush(heap, (next_secondary, edge.to_node_id, next_bin))
                    continue

                # 二级代价并列时，优先保留更接近目标损耗的标签。
                if abs(next_secondary - old_secondary) <= 1e-9:
                    old_loss = cumulative_loss[next_state]
                    if abs(next_loss - target_loss) + 1e-9 < abs(old_loss - target_loss):
                        cumulative_loss[next_state] = next_loss
                        prev_state[next_state] = state
                        prev_edge[next_state] = edge.edge_id

        terminal_states: list[tuple[float, float, tuple[int, int]]] = []
        for state, secondary in best_secondary_cost.items():
            node_id, _ = state
            if node_id != end_node_id:
                continue
            loss_value = cumulative_loss[state]
            delta = abs(loss_value - target_loss)
            terminal_states.append((delta, secondary, state))

        if not terminal_states:
            raise ValueError("保鲜优先Dijkstra改进版未找到可达路径。")

        terminal_states.sort(key=lambda item: (item[0], item[1]))
        best_delta, _, best_end_state = terminal_states[0]

        node_path: list[int] = [best_end_state[0]]
        edge_path: list[int] = []
        cursor = best_end_state
        while cursor != start_state:
            edge_id = prev_edge[cursor]
            edge_path.append(edge_id)
            cursor = prev_state[cursor]
            node_path.append(cursor[0])

        node_path.reverse()
        edge_path.reverse()
        return PathSolveResult(node_path=node_path, edge_path=edge_path, total_cost=best_delta)
