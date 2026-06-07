"""目标保鲜偏差候选图搜索算法（ATD-LS）。

这版实现不再把自己当成“全图多标签最优解器”，而是作为候选图上的精修层：
先用反向下界做可行性筛选，再用受限 frontier/beam 做 A* 式扩展，
最后在候选图里找出最接近目标保鲜度的路径。
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from math import inf

from src.algorithms.graph_builder import GraphData
from src.algorithms.dijkstra_shortest_path import PathSolveResult


@dataclass(slots=True)
class _SearchState:
    state_id: int
    node_id: int
    depth: int
    cum_loss: float
    cum_secondary: float
    cum_distance_m: float
    parent_state_id: int | None
    via_edge_id: int | None
    optimistic_delta: float


@dataclass(slots=True, frozen=True)
class _SearchBudget:
    depth_hint: int
    branching_hint: float
    base_per_node_limit: int
    max_per_node_limit: int
    base_frontier_limit: int
    max_frontier_limit: int
    base_beam_width: int
    max_beam_width: int
    expansion_limit: int


class TargetFreshnessAdaptiveLabelSearchSolver:
    """ATD-LS：面向目标保鲜度偏差的候选图束搜索 / A* 精修器。"""

    _DEFAULT_NEAR_BIN_SIZE = 0.03
    _DEFAULT_MID_BIN_SIZE = 0.08
    _DEFAULT_FAR_BIN_SIZE = 0.20
    _DEFAULT_NEAR_WINDOW = 6.0
    _DEFAULT_MID_WINDOW = 18.0
    _DEFAULT_MAX_LOSS_LIMIT = 260.0
    _DEFAULT_MAX_LABELS_PER_NODE = 8
    _DEFAULT_MAX_TOTAL_LABELS = 360
    _DEFAULT_MAX_EXPANSIONS = 2400
    _DEFAULT_MAX_ADAPTIVE_FRONTIER = 4096
    _DEFAULT_MAX_ADAPTIVE_LABELS_PER_NODE = 36
    _DEFAULT_MAX_ADAPTIVE_EXPANSIONS = 120000
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
        max_labels_per_node: int = _DEFAULT_MAX_LABELS_PER_NODE,
        max_total_labels: int = _DEFAULT_MAX_TOTAL_LABELS,
        path_depth_hint: int | None = None,
        branching_hint: float | None = None,
        baseline_result: PathSolveResult | None = None,
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
        search_budget = self._resolve_search_budget(
            graph=graph,
            max_labels_per_node=max_labels_per_node,
            max_total_labels=max_total_labels,
            path_depth_hint=path_depth_hint,
            branching_hint=branching_hint,
        )

        secondary_map = edge_secondary_cost_by_id or {}
        reverse_loss_lb = self._build_reverse_lower_bounds(
            graph=graph,
            end_node_id=end_node_id,
            edge_weight_by_id=edge_freshness_loss_by_id,
            fallback_to_base_time=False,
        )
        reverse_secondary_lb = self._build_reverse_lower_bounds(
            graph=graph,
            end_node_id=end_node_id,
            edge_weight_by_id=secondary_map,
            fallback_to_base_time=True,
        )
        if reverse_loss_lb.get(start_node_id, inf) == inf or reverse_secondary_lb.get(start_node_id, inf) == inf:
            raise ValueError("目标保鲜偏差搜索未找到可达路径。")

        normalized_baseline = self._normalize_baseline_result(
            graph=graph,
            baseline_result=baseline_result,
            edge_freshness_loss_by_id=edge_freshness_loss_by_id,
            edge_secondary_cost_by_id=secondary_map,
            target_loss=safe_target_loss,
        )
        result = self._search_candidate_graph(
            graph=graph,
            start_node_id=start_node_id,
            end_node_id=end_node_id,
            edge_freshness_loss_by_id=edge_freshness_loss_by_id,
            edge_secondary_cost_by_id=secondary_map,
            target_loss=safe_target_loss,
            max_secondary_cost=safe_max_secondary_cost,
            max_loss_limit=safe_max_loss_limit,
            reverse_loss_lb=reverse_loss_lb,
            reverse_secondary_lb=reverse_secondary_lb,
            search_budget=search_budget,
            baseline_result=normalized_baseline,
        )
        if result is None:
            raise ValueError("目标保鲜偏差搜索未找到可达路径。")
        return result

    def _search_candidate_graph(
        self,
        graph: GraphData,
        start_node_id: int,
        end_node_id: int,
        edge_freshness_loss_by_id: dict[int, float],
        edge_secondary_cost_by_id: dict[int, float],
        target_loss: float,
        max_secondary_cost: float | None,
        max_loss_limit: float,
        reverse_loss_lb: dict[int, float],
        reverse_secondary_lb: dict[int, float],
        search_budget: _SearchBudget,
        baseline_result: PathSolveResult | None,
    ) -> PathSolveResult | None:
        states_by_id: dict[int, _SearchState] = {}
        retained_state_ids: set[int] = set()
        retained_signature_ids: dict[tuple[int, int, int], list[int]] = {}
        retained_node_ids: dict[int, list[int]] = {}
        next_state_id = 1

        start_state = _SearchState(
            state_id=next_state_id,
            node_id=start_node_id,
            depth=0,
            cum_loss=0.0,
            cum_secondary=0.0,
            cum_distance_m=0.0,
            parent_state_id=None,
            via_edge_id=None,
            optimistic_delta=self._lower_bound_delta(
                current_loss=0.0,
                remaining_min_loss=reverse_loss_lb[start_node_id],
                target_loss=target_loss,
            ),
        )
        states_by_id[start_state.state_id] = start_state
        retained_state_ids.add(start_state.state_id)
        retained_node_ids[start_state.node_id] = [start_state.state_id]

        frontier_state_ids: list[int] = [start_state.state_id]
        best_terminal_state_id: int | None = None
        best_terminal_rank = None if baseline_result is None else (
            baseline_result.total_cost,
            self._sum_secondary(graph, baseline_result.edge_path, edge_secondary_cost_by_id),
            self._sum_distance(graph, baseline_result.edge_path),
        )
        expansions = 0

        while frontier_state_ids and expansions < search_budget.expansion_limit:
            current_frontier_depth = self._frontier_depth(
                frontier_state_ids=frontier_state_ids,
                states_by_id=states_by_id,
                retained_state_ids=retained_state_ids,
            )
            beam_width = self._depth_scaled_limit(
                base_limit=search_budget.base_beam_width,
                max_limit=search_budget.max_beam_width,
                current_depth=current_frontier_depth,
                depth_hint=search_budget.depth_hint,
            )
            current_frontier = self._select_frontier(
                frontier_state_ids=frontier_state_ids,
                states_by_id=states_by_id,
                retained_state_ids=retained_state_ids,
                reverse_secondary_lb=reverse_secondary_lb,
                target_loss=target_loss,
                limit=beam_width,
                depth_hint=search_budget.depth_hint,
            )
            if not current_frontier:
                break
            self._discard_unselected_states(
                candidate_state_ids=frontier_state_ids,
                kept_state_ids=current_frontier,
                states_by_id=states_by_id,
                retained_state_ids=retained_state_ids,
                retained_node_ids=retained_node_ids,
                retained_signature_ids=retained_signature_ids,
                target_loss=target_loss,
            )

            next_frontier: list[int] = []
            for state_id in current_frontier:
                if state_id not in retained_state_ids:
                    continue

                state = states_by_id.get(state_id)
                if state is None:
                    continue

                remaining_secondary_lb = reverse_secondary_lb.get(state.node_id, inf)
                if remaining_secondary_lb == inf:
                    continue
                if (
                    max_secondary_cost is not None
                    and state.cum_secondary + remaining_secondary_lb > max_secondary_cost + self._EPS
                ):
                    continue
                if best_terminal_rank is not None and state.optimistic_delta > best_terminal_rank[0] + self._EPS:
                    continue

                if state.node_id == end_node_id:
                    rank = self._terminal_rank(state, target_loss)
                    if best_terminal_rank is None or rank < best_terminal_rank:
                        best_terminal_rank = rank
                        best_terminal_state_id = state.state_id
                    continue

                expansions += 1
                if expansions > search_budget.expansion_limit:
                    break

                for edge in graph.edges_by_from.get(state.node_id, []):
                    edge_loss = max(0.0, float(edge_freshness_loss_by_id.get(edge.edge_id, 0.0)))
                    next_loss = state.cum_loss + edge_loss
                    if next_loss > max_loss_limit + self._EPS:
                        continue

                    edge_secondary = float(
                        edge_secondary_cost_by_id.get(edge.edge_id, max(float(edge.base_travel_time_s), 1e-6))
                    )
                    edge_secondary = max(edge_secondary, 1e-6)
                    next_secondary = state.cum_secondary + edge_secondary
                    next_distance_m = state.cum_distance_m + max(float(edge.length_m), 0.0)

                    remaining_loss_lb = reverse_loss_lb.get(edge.to_node_id, inf)
                    remaining_secondary_lb = reverse_secondary_lb.get(edge.to_node_id, inf)
                    if remaining_loss_lb == inf or remaining_secondary_lb == inf:
                        continue
                    if (
                        max_secondary_cost is not None
                        and next_secondary + remaining_secondary_lb > max_secondary_cost + self._EPS
                    ):
                        continue

                    optimistic_delta = self._lower_bound_delta(
                        current_loss=next_loss,
                        remaining_min_loss=remaining_loss_lb,
                        target_loss=target_loss,
                    )
                    if best_terminal_rank is not None and optimistic_delta > best_terminal_rank[0] + self._EPS:
                        continue

                    child_state = _SearchState(
                        state_id=next_state_id + 1,
                        node_id=edge.to_node_id,
                        depth=state.depth + 1,
                        cum_loss=next_loss,
                        cum_secondary=next_secondary,
                        cum_distance_m=next_distance_m,
                        parent_state_id=state.state_id,
                        via_edge_id=edge.edge_id,
                        optimistic_delta=optimistic_delta,
                    )
                    child_rank = self._state_rank(child_state, target_loss)
                    signature = self._signature(
                        node_id=child_state.node_id,
                        cum_loss=child_state.cum_loss,
                        target_loss=target_loss,
                    )

                    if self._is_dominated(
                        candidate=child_state,
                        signature=signature,
                        states_by_id=states_by_id,
                        retained_state_ids=retained_state_ids,
                        retained_signature_ids=retained_signature_ids,
                        target_loss=target_loss,
                    ):
                        continue

                    node_bucket = retained_node_ids.setdefault(child_state.node_id, [])
                    per_node_limit = self._depth_scaled_limit(
                        base_limit=search_budget.base_per_node_limit,
                        max_limit=search_budget.max_per_node_limit,
                        current_depth=child_state.depth,
                        depth_hint=search_budget.depth_hint,
                    )
                    if len(node_bucket) >= per_node_limit:
                        worst_state_id = max(
                            node_bucket,
                            key=lambda sid: self._state_rank(states_by_id[sid], target_loss),
                        )
                        worst_rank = self._state_rank(states_by_id[worst_state_id], target_loss)
                        if child_rank >= worst_rank:
                            continue
                        self._discard_state(
                            state_id=worst_state_id,
                            states_by_id=states_by_id,
                            retained_state_ids=retained_state_ids,
                            retained_node_ids=retained_node_ids,
                            retained_signature_ids=retained_signature_ids,
                            target_loss=target_loss,
                        )

                    self._prune_dominated_states(
                        candidate=child_state,
                        signature=signature,
                        states_by_id=states_by_id,
                        retained_state_ids=retained_state_ids,
                        retained_node_ids=retained_node_ids,
                        retained_signature_ids=retained_signature_ids,
                        target_loss=target_loss,
                    )

                    next_state_id += 1
                    child_state.state_id = next_state_id
                    states_by_id[child_state.state_id] = child_state
                    retained_state_ids.add(child_state.state_id)
                    retained_node_ids.setdefault(child_state.node_id, []).append(child_state.state_id)
                    retained_signature_ids.setdefault(signature, []).append(child_state.state_id)
                    next_frontier.append(child_state.state_id)

                    frontier_limit = self._depth_scaled_limit(
                        base_limit=search_budget.base_frontier_limit,
                        max_limit=search_budget.max_frontier_limit,
                        current_depth=child_state.depth,
                        depth_hint=search_budget.depth_hint,
                    )
                    if len(next_frontier) > frontier_limit:
                        kept_frontier = self._select_frontier(
                            frontier_state_ids=next_frontier,
                            states_by_id=states_by_id,
                            retained_state_ids=retained_state_ids,
                            reverse_secondary_lb=reverse_secondary_lb,
                            target_loss=target_loss,
                            limit=frontier_limit,
                            depth_hint=search_budget.depth_hint,
                        )
                        self._discard_unselected_states(
                            candidate_state_ids=next_frontier,
                            kept_state_ids=kept_frontier,
                            states_by_id=states_by_id,
                            retained_state_ids=retained_state_ids,
                            retained_node_ids=retained_node_ids,
                            retained_signature_ids=retained_signature_ids,
                            target_loss=target_loss,
                        )
                        next_frontier = kept_frontier

            if best_terminal_rank is not None:
                next_frontier = [
                    state_id
                    for state_id in next_frontier
                    if state_id in retained_state_ids
                    and self._state_rank(states_by_id[state_id], target_loss)[0] <= best_terminal_rank[0] + self._EPS
                ]
            frontier_state_ids = next_frontier

        if best_terminal_state_id is None:
            return baseline_result
        if best_terminal_rank is None:
            return None

        return self._reconstruct_result(
            states_by_id=states_by_id,
            terminal_state_id=best_terminal_state_id,
            terminal_rank=best_terminal_rank,
        )

    def _select_frontier(
        self,
        frontier_state_ids: list[int],
        states_by_id: dict[int, _SearchState],
        retained_state_ids: set[int],
        reverse_secondary_lb: dict[int, float],
        target_loss: float,
        limit: int,
        depth_hint: int,
    ) -> list[int]:
        scored: list[tuple[tuple[float, float, float, float, float, int], int]] = []
        seen: set[int] = set()
        for state_id in frontier_state_ids:
            if state_id in seen or state_id not in retained_state_ids:
                continue
            state = states_by_id.get(state_id)
            if state is None:
                continue
            seen.add(state_id)
            scored.append((self._queue_item(state, reverse_secondary_lb, target_loss, depth_hint), state_id))
        scored.sort(key=lambda item: item[0])
        return [state_id for _, state_id in scored[: max(1, limit)]]

    def _reconstruct_result(
        self,
        states_by_id: dict[int, _SearchState],
        terminal_state_id: int,
        terminal_rank: tuple[float, float, float],
    ) -> PathSolveResult:
        node_path, edge_path = self._reconstruct_path(
            states_by_id=states_by_id,
            terminal_state_id=terminal_state_id,
        )
        return PathSolveResult(
            node_path=node_path,
            edge_path=edge_path,
            total_cost=terminal_rank[0],
        )

    def _build_reverse_lower_bounds(
        self,
        graph: GraphData,
        end_node_id: int,
        edge_weight_by_id: dict[int, float],
        fallback_to_base_time: bool,
    ) -> dict[int, float]:
        reverse_edges_by_to: dict[int, list[tuple[int, float]]] = {}
        for edge in graph.edges_by_id.values():
            weight = edge_weight_by_id.get(edge.edge_id)
            if weight is None and fallback_to_base_time:
                weight = edge.base_travel_time_s
            if weight is None:
                weight = 0.0
            safe_weight = max(float(weight), 0.0)
            reverse_edges_by_to.setdefault(edge.to_node_id, []).append((edge.from_node_id, safe_weight))

        dist: dict[int, float] = {end_node_id: 0.0}
        heap: list[tuple[float, int]] = [(0.0, end_node_id)]
        while heap:
            curr_dist, node_id = heapq.heappop(heap)
            if curr_dist > dist.get(node_id, inf) + self._EPS:
                continue
            for prev_node_id, weight in reverse_edges_by_to.get(node_id, []):
                candidate = curr_dist + weight
                if candidate + self._EPS < dist.get(prev_node_id, inf):
                    dist[prev_node_id] = candidate
                    heapq.heappush(heap, (candidate, prev_node_id))
        return dist

    def _lower_bound_delta(
        self,
        current_loss: float,
        remaining_min_loss: float,
        target_loss: float,
    ) -> float:
        min_reachable_total_loss = current_loss + max(remaining_min_loss, 0.0)
        if min_reachable_total_loss <= target_loss:
            return 0.0
        return min_reachable_total_loss - target_loss

    def _signature(
        self,
        node_id: int,
        cum_loss: float,
        target_loss: float,
    ) -> tuple[int, int, int]:
        bin_size = self._adaptive_bin_size(cum_loss=cum_loss, target_loss=target_loss)
        bucket = int(cum_loss / bin_size + self._EPS)
        return node_id, int(round(bin_size * 1000.0)), bucket

    def _adaptive_bin_size(self, cum_loss: float, target_loss: float) -> float:
        delta = abs(cum_loss - target_loss)
        if delta <= self._DEFAULT_NEAR_WINDOW:
            return self._DEFAULT_NEAR_BIN_SIZE
        if delta <= self._DEFAULT_MID_WINDOW:
            return self._DEFAULT_MID_BIN_SIZE
        return self._DEFAULT_FAR_BIN_SIZE

    def _queue_item(
        self,
        state: _SearchState,
        reverse_secondary_lb: dict[int, float],
        target_loss: float,
        depth_hint: int,
    ) -> tuple[float, float, float, float, float, int]:
        return (
            state.optimistic_delta,
            abs(state.cum_loss - target_loss),
            state.cum_secondary + max(reverse_secondary_lb.get(state.node_id, 0.0), 0.0),
            state.cum_secondary,
            state.cum_distance_m,
            state.state_id,
        )

    def _state_rank(self, state: _SearchState, target_loss: float) -> tuple[float, float, float, float]:
        return (
            state.optimistic_delta,
            abs(state.cum_loss - target_loss),
            state.cum_secondary,
            state.cum_distance_m,
        )

    def _terminal_rank(self, state: _SearchState, target_loss: float) -> tuple[float, float, float]:
        return (
            abs(state.cum_loss - target_loss),
            state.cum_secondary,
            state.cum_distance_m,
        )

    def _is_dominated(
        self,
        candidate: _SearchState,
        signature: tuple[int, int, int],
        states_by_id: dict[int, _SearchState],
        retained_state_ids: set[int],
        retained_signature_ids: dict[tuple[int, int, int], list[int]],
        target_loss: float,
    ) -> bool:
        for state_id in retained_signature_ids.get(signature, []):
            if state_id not in retained_state_ids:
                continue
            existing = states_by_id.get(state_id)
            if existing is None:
                continue
            if self._dominates(existing, candidate, target_loss):
                return True
        return False

    def _prune_dominated_states(
        self,
        candidate: _SearchState,
        signature: tuple[int, int, int],
        states_by_id: dict[int, _SearchState],
        retained_state_ids: set[int],
        retained_node_ids: dict[int, list[int]],
        retained_signature_ids: dict[tuple[int, int, int], list[int]],
        target_loss: float,
    ) -> None:
        kept: list[int] = []
        for state_id in list(retained_signature_ids.get(signature, [])):
            if state_id not in retained_state_ids:
                continue
            existing = states_by_id.get(state_id)
            if existing is None:
                continue
            if self._dominates(candidate, existing, target_loss):
                self._discard_state(
                    state_id=state_id,
                    states_by_id=states_by_id,
                    retained_state_ids=retained_state_ids,
                    retained_node_ids=retained_node_ids,
                    retained_signature_ids=retained_signature_ids,
                    target_loss=target_loss,
                )
                continue
            kept.append(state_id)
        retained_signature_ids[signature] = kept

    def _discard_state(
        self,
        state_id: int,
        states_by_id: dict[int, _SearchState],
        retained_state_ids: set[int],
        retained_node_ids: dict[int, list[int]],
        retained_signature_ids: dict[tuple[int, int, int], list[int]],
        target_loss: float,
    ) -> None:
        state = states_by_id.get(state_id)
        if state is None:
            return
        retained_state_ids.discard(state_id)
        node_bucket = retained_node_ids.get(state.node_id)
        if node_bucket and state_id in node_bucket:
            node_bucket.remove(state_id)
            if not node_bucket:
                retained_node_ids.pop(state.node_id, None)
        signature = self._signature(
            node_id=state.node_id,
            cum_loss=state.cum_loss,
            target_loss=target_loss,
        )
        signature_bucket = retained_signature_ids.get(signature)
        if signature_bucket and state_id in signature_bucket:
            signature_bucket.remove(state_id)
            if not signature_bucket:
                retained_signature_ids.pop(signature, None)

    def _discard_unselected_states(
        self,
        candidate_state_ids: list[int],
        kept_state_ids: list[int],
        states_by_id: dict[int, _SearchState],
        retained_state_ids: set[int],
        retained_node_ids: dict[int, list[int]],
        retained_signature_ids: dict[tuple[int, int, int], list[int]],
        target_loss: float,
    ) -> None:
        kept = set(kept_state_ids)
        for state_id in candidate_state_ids:
            if state_id in kept:
                continue
            self._discard_state(
                state_id=state_id,
                states_by_id=states_by_id,
                retained_state_ids=retained_state_ids,
                retained_node_ids=retained_node_ids,
                retained_signature_ids=retained_signature_ids,
                target_loss=target_loss,
            )

    def _dominates(
        self,
        left: _SearchState,
        right: _SearchState,
        target_loss: float,
    ) -> bool:
        left_rank = self._state_rank(left, target_loss)
        right_rank = self._state_rank(right, target_loss)
        less_or_equal = all(l <= r + self._EPS for l, r in zip(left_rank, right_rank))
        strictly_better = any(l + self._EPS < r for l, r in zip(left_rank, right_rank))
        return less_or_equal and strictly_better

    def _reconstruct_path(
        self,
        states_by_id: dict[int, _SearchState],
        terminal_state_id: int,
    ) -> tuple[list[int], list[int]]:
        node_path: list[int] = []
        edge_path: list[int] = []
        cursor = states_by_id[terminal_state_id]
        while True:
            node_path.append(cursor.node_id)
            if cursor.via_edge_id is not None:
                edge_path.append(cursor.via_edge_id)
            if cursor.parent_state_id is None:
                break
            cursor = states_by_id[cursor.parent_state_id]
        node_path.reverse()
        edge_path.reverse()
        return node_path, edge_path

    def _normalize_baseline_result(
        self,
        graph: GraphData,
        baseline_result: PathSolveResult | None,
        edge_freshness_loss_by_id: dict[int, float],
        edge_secondary_cost_by_id: dict[int, float],
        target_loss: float,
    ) -> PathSolveResult | None:
        if baseline_result is None or not baseline_result.edge_path:
            return baseline_result

        baseline_loss = self._sum_loss(
            edge_path=baseline_result.edge_path,
            edge_freshness_loss_by_id=edge_freshness_loss_by_id,
        )
        baseline_secondary = self._sum_secondary(
            graph=graph,
            edge_path=baseline_result.edge_path,
            edge_secondary_cost_by_id=edge_secondary_cost_by_id,
        )
        baseline_distance = self._sum_distance(
            graph=graph,
            edge_path=baseline_result.edge_path,
        )
        return PathSolveResult(
            node_path=list(baseline_result.node_path),
            edge_path=list(baseline_result.edge_path),
            total_cost=abs(baseline_loss - target_loss),
        )

    def _sum_loss(
        self,
        edge_path: list[int],
        edge_freshness_loss_by_id: dict[int, float],
    ) -> float:
        total = 0.0
        for edge_id in edge_path:
            total += max(0.0, float(edge_freshness_loss_by_id.get(edge_id, 0.0)))
        return total

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

    def _sum_distance(self, graph: GraphData, edge_path: list[int]) -> float:
        total = 0.0
        for edge_id in edge_path:
            total += max(0.0, float(graph.edges_by_id[edge_id].length_m))
        return total

    def _resolve_search_budget(
        self,
        graph: GraphData,
        max_labels_per_node: int,
        max_total_labels: int,
        path_depth_hint: int | None,
        branching_hint: float | None,
    ) -> _SearchBudget:
        safe_depth_hint = self._resolve_depth_hint(graph, path_depth_hint)
        safe_branching_hint = self._resolve_branching_hint(graph, branching_hint)

        base_per_node_limit = max(2, int(max_labels_per_node))
        per_node_bonus = max(
            2,
            int(round(safe_depth_hint * (0.0015 + safe_branching_hint * 0.0025))),
        )
        max_per_node_limit = min(
            self._DEFAULT_MAX_ADAPTIVE_LABELS_PER_NODE,
            base_per_node_limit + per_node_bonus,
        )

        base_frontier_limit = max(base_per_node_limit * 3, int(max_total_labels))
        base_frontier_limit = max(24, min(base_frontier_limit, 512))
        frontier_bonus = max(
            48,
            int(round(safe_depth_hint * (0.08 + safe_branching_hint * 0.12))),
        )
        max_frontier_limit = min(
            self._DEFAULT_MAX_ADAPTIVE_FRONTIER,
            base_frontier_limit + frontier_bonus,
        )

        base_beam_width = max(4, min(base_frontier_limit, base_per_node_limit * 4))
        beam_bonus = max(
            8,
            int(round(safe_depth_hint * (0.018 + safe_branching_hint * 0.022))),
        )
        max_beam_width = min(max_frontier_limit, base_beam_width + beam_bonus)

        expansion_limit = max(
            self._DEFAULT_MAX_EXPANSIONS,
            base_frontier_limit * 8,
            int(round(safe_depth_hint * (3.0 + safe_branching_hint * 3.0))),
            int(round(len(graph.edges_by_id) * (1.1 + safe_branching_hint * 0.9))),
        )
        expansion_limit = min(expansion_limit, self._DEFAULT_MAX_ADAPTIVE_EXPANSIONS)

        return _SearchBudget(
            depth_hint=safe_depth_hint,
            branching_hint=safe_branching_hint,
            base_per_node_limit=base_per_node_limit,
            max_per_node_limit=max_per_node_limit,
            base_frontier_limit=base_frontier_limit,
            max_frontier_limit=max_frontier_limit,
            base_beam_width=base_beam_width,
            max_beam_width=max_beam_width,
            expansion_limit=expansion_limit,
        )

    def _resolve_depth_hint(self, graph: GraphData, path_depth_hint: int | None) -> int:
        if path_depth_hint is not None:
            try:
                hinted_depth = int(path_depth_hint)
            except (TypeError, ValueError):
                hinted_depth = 0
            if hinted_depth > 0:
                return hinted_depth
        fallback_depth = max(len(graph.nodes), len(graph.edges_by_id))
        return max(24, fallback_depth)

    def _resolve_branching_hint(self, graph: GraphData, branching_hint: float | None) -> float:
        if branching_hint is not None:
            try:
                hinted_ratio = float(branching_hint)
            except (TypeError, ValueError):
                hinted_ratio = 0.0
            return min(max(hinted_ratio, 0.0), 1.0)
        return self._estimate_graph_branching_hint(graph)

    def _estimate_graph_branching_hint(self, graph: GraphData) -> float:
        if not graph.edges_by_from:
            return 0.0
        active_nodes = 0
        branch_nodes = 0
        for edges in graph.edges_by_from.values():
            if not edges:
                continue
            active_nodes += 1
            if len(edges) > 1:
                branch_nodes += 1
        if active_nodes <= 0:
            return 0.0
        return min(max(branch_nodes / active_nodes, 0.0), 1.0)

    def _depth_scaled_limit(
        self,
        base_limit: int,
        max_limit: int,
        current_depth: int,
        depth_hint: int,
    ) -> int:
        if max_limit <= base_limit:
            return base_limit
        progress = min(max(float(current_depth) / max(depth_hint, 1), 0.0), 1.0)
        scaled = base_limit + int(round((max_limit - base_limit) * (progress ** 0.65)))
        return max(base_limit, min(max_limit, scaled))

    def _frontier_depth(
        self,
        frontier_state_ids: list[int],
        states_by_id: dict[int, _SearchState],
        retained_state_ids: set[int],
    ) -> int:
        deepest = 0
        for state_id in frontier_state_ids:
            if state_id not in retained_state_ids:
                continue
            state = states_by_id.get(state_id)
            if state is None:
                continue
            deepest = max(deepest, state.depth)
        return deepest
