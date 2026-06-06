"""目标保鲜字典序 A* 算法（TF-LA*）。

该算法以“到达新鲜度与目标值 100 的偏差最小”为第一目标，
以“总时间最短”为第二目标，并以总里程作为稳定的第三排序项。

实现上采用：
1. 反向最短路预处理时间/损耗/距离下界；
2. 前向字典序 A* 扩展；
3. 自适应损耗分桶 + 每节点标签上限，控制候选图上的标签数量；
4. 使用基准路径作为 incumbent，持续剪枝无法优于当前最优解的状态。
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from math import inf

from src.algorithms.graph_builder import GraphData
from src.algorithms.static_shortest_path import PathSolveResult


@dataclass(slots=True)
class _LexicoLabel:
    label_id: int
    node_id: int
    depth: int
    cum_loss: float
    cum_secondary: float
    cum_distance_m: float
    parent_label_id: int | None
    via_edge_id: int | None
    optimistic_delta: float
    optimistic_secondary: float
    optimistic_distance_m: float


@dataclass(slots=True, frozen=True)
class _SearchBudget:
    depth_hint: int
    branching_hint: float
    base_per_node_limit: int
    max_per_node_limit: int
    max_total_labels: int
    expansion_limit: int


class TargetFreshnessLexicographicAStarSolver:
    """TF-LA*: 最小化 (|freshness - target|, time, distance) 的候选图字典序 A*。"""

    _DEFAULT_NEAR_BIN_SIZE = 0.03
    _DEFAULT_MID_BIN_SIZE = 0.08
    _DEFAULT_FAR_BIN_SIZE = 0.20
    _DEFAULT_NEAR_WINDOW = 6.0
    _DEFAULT_MID_WINDOW = 18.0
    _DEFAULT_MAX_LOSS_LIMIT = 260.0
    _DEFAULT_MAX_LABELS_PER_NODE = 8
    _DEFAULT_MAX_TOTAL_LABELS = 420
    _DEFAULT_MAX_EXPANSIONS = 120000
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
        adaptive_loss_limit = max(safe_target_loss * 2.6, safe_target_loss + 14.0, 28.0)
        safe_max_loss_limit = max(
            8.0,
            min(
                max(float(max_loss_limit or self._DEFAULT_MAX_LOSS_LIMIT), adaptive_loss_limit),
                480.0,
            ),
        )
        safe_max_secondary_cost = (
            None if max_secondary_cost is None else max(float(max_secondary_cost), 1e-6)
        )
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
            use_distance=False,
        )
        reverse_secondary_lb = self._build_reverse_lower_bounds(
            graph=graph,
            end_node_id=end_node_id,
            edge_weight_by_id=secondary_map,
            fallback_to_base_time=True,
            use_distance=False,
        )
        reverse_distance_lb = self._build_reverse_lower_bounds(
            graph=graph,
            end_node_id=end_node_id,
            edge_weight_by_id={},
            fallback_to_base_time=False,
            use_distance=True,
        )

        if (
            reverse_loss_lb.get(start_node_id, inf) == inf
            or reverse_secondary_lb.get(start_node_id, inf) == inf
            or reverse_distance_lb.get(start_node_id, inf) == inf
        ):
            raise ValueError("TF-LA* 未找到可达路径。")

        normalized_baseline, best_terminal_rank = self._normalize_baseline_result(
            graph=graph,
            baseline_result=baseline_result,
            edge_freshness_loss_by_id=edge_freshness_loss_by_id,
            edge_secondary_cost_by_id=secondary_map,
            target_loss=safe_target_loss,
        )

        solved = self._search(
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
            reverse_distance_lb=reverse_distance_lb,
            search_budget=search_budget,
            incumbent_rank=best_terminal_rank,
        )
        if solved is not None:
            return solved
        if normalized_baseline is not None:
            return normalized_baseline
        raise ValueError("TF-LA* 未找到可达路径。")

    def _search(
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
        reverse_distance_lb: dict[int, float],
        search_budget: _SearchBudget,
        incumbent_rank: tuple[float, float, float] | None,
    ) -> PathSolveResult | None:
        labels_by_id: dict[int, _LexicoLabel] = {}
        active_label_ids: set[int] = set()
        labels_by_node: dict[int, list[int]] = {}
        labels_by_signature: dict[tuple[int, int, int], list[int]] = {}
        open_heap: list[tuple[tuple[float, float, float, float, float, float, int, int], int]] = []

        next_label_id = 1
        start_label = _LexicoLabel(
            label_id=next_label_id,
            node_id=start_node_id,
            depth=0,
            cum_loss=0.0,
            cum_secondary=0.0,
            cum_distance_m=0.0,
            parent_label_id=None,
            via_edge_id=None,
            optimistic_delta=self._lower_bound_delta(
                current_loss=0.0,
                remaining_min_loss=reverse_loss_lb[start_node_id],
                target_loss=target_loss,
            ),
            optimistic_secondary=reverse_secondary_lb[start_node_id],
            optimistic_distance_m=reverse_distance_lb[start_node_id],
        )
        labels_by_id[start_label.label_id] = start_label
        active_label_ids.add(start_label.label_id)
        labels_by_node[start_label.node_id] = [start_label.label_id]
        labels_by_signature[self._signature(start_label.node_id, start_label.cum_loss, target_loss)] = [
            start_label.label_id
        ]
        heapq.heappush(
            open_heap,
            (
                self._queue_item(start_label, target_loss),
                start_label.label_id,
            ),
        )

        best_terminal_rank = incumbent_rank
        best_terminal_label_id: int | None = None
        expansions = 0

        while open_heap and expansions < search_budget.expansion_limit:
            queue_item, label_id = heapq.heappop(open_heap)
            if label_id not in active_label_ids:
                continue

            if best_terminal_rank is not None and queue_item[:3] >= best_terminal_rank:
                break

            label = labels_by_id[label_id]
            if label.node_id == end_node_id:
                rank = self._terminal_rank(label, target_loss)
                if best_terminal_rank is None or rank < best_terminal_rank:
                    best_terminal_rank = rank
                    best_terminal_label_id = label.label_id
                continue

            expansions += 1
            for edge in graph.edges_by_from.get(label.node_id, []):
                edge_loss = max(0.0, float(edge_freshness_loss_by_id.get(edge.edge_id, 0.0)))
                next_loss = label.cum_loss + edge_loss
                if next_loss > max_loss_limit + self._EPS:
                    continue

                edge_secondary = max(
                    1e-6,
                    float(
                        edge_secondary_cost_by_id.get(
                            edge.edge_id,
                            max(float(edge.base_travel_time_s), 1e-6),
                        )
                    ),
                )
                next_secondary = label.cum_secondary + edge_secondary
                next_distance_m = label.cum_distance_m + max(float(edge.length_m), 0.0)

                remaining_loss_lb = reverse_loss_lb.get(edge.to_node_id, inf)
                remaining_secondary_lb = reverse_secondary_lb.get(edge.to_node_id, inf)
                remaining_distance_lb = reverse_distance_lb.get(edge.to_node_id, inf)
                if (
                    remaining_loss_lb == inf
                    or remaining_secondary_lb == inf
                    or remaining_distance_lb == inf
                ):
                    continue

                optimistic_secondary = next_secondary + remaining_secondary_lb
                if (
                    max_secondary_cost is not None
                    and optimistic_secondary > max_secondary_cost + self._EPS
                ):
                    continue

                optimistic_delta = self._lower_bound_delta(
                    current_loss=next_loss,
                    remaining_min_loss=remaining_loss_lb,
                    target_loss=target_loss,
                )
                optimistic_distance_m = next_distance_m + remaining_distance_lb
                optimistic_rank = (
                    optimistic_delta,
                    optimistic_secondary,
                    optimistic_distance_m,
                )
                if best_terminal_rank is not None and optimistic_rank >= best_terminal_rank:
                    continue

                next_label_id += 1
                child = _LexicoLabel(
                    label_id=next_label_id,
                    node_id=edge.to_node_id,
                    depth=label.depth + 1,
                    cum_loss=next_loss,
                    cum_secondary=next_secondary,
                    cum_distance_m=next_distance_m,
                    parent_label_id=label.label_id,
                    via_edge_id=edge.edge_id,
                    optimistic_delta=optimistic_delta,
                    optimistic_secondary=optimistic_secondary,
                    optimistic_distance_m=optimistic_distance_m,
                )
                signature = self._signature(child.node_id, child.cum_loss, target_loss)
                if self._is_dominated(
                    candidate=child,
                    signature=signature,
                    labels_by_id=labels_by_id,
                    active_label_ids=active_label_ids,
                    labels_by_signature=labels_by_signature,
                    target_loss=target_loss,
                ):
                    continue

                node_bucket = labels_by_node.setdefault(child.node_id, [])
                per_node_limit = self._depth_scaled_limit(
                    base_limit=search_budget.base_per_node_limit,
                    max_limit=search_budget.max_per_node_limit,
                    current_depth=child.depth,
                    depth_hint=search_budget.depth_hint,
                )
                if len(node_bucket) >= per_node_limit:
                    worst_label_id = max(
                        node_bucket,
                        key=lambda sid: self._queue_item(labels_by_id[sid], target_loss),
                    )
                    worst_queue_item = self._queue_item(labels_by_id[worst_label_id], target_loss)
                    if self._queue_item(child, target_loss) >= worst_queue_item:
                        continue
                    self._discard_label(
                        label_id=worst_label_id,
                        labels_by_id=labels_by_id,
                        active_label_ids=active_label_ids,
                        labels_by_node=labels_by_node,
                        labels_by_signature=labels_by_signature,
                        target_loss=target_loss,
                    )

                self._prune_dominated_labels(
                    candidate=child,
                    signature=signature,
                    labels_by_id=labels_by_id,
                    active_label_ids=active_label_ids,
                    labels_by_node=labels_by_node,
                    labels_by_signature=labels_by_signature,
                    target_loss=target_loss,
                )

                labels_by_id[child.label_id] = child
                active_label_ids.add(child.label_id)
                labels_by_node.setdefault(child.node_id, []).append(child.label_id)
                labels_by_signature.setdefault(signature, []).append(child.label_id)
                heapq.heappush(open_heap, (self._queue_item(child, target_loss), child.label_id))

                if len(active_label_ids) > search_budget.max_total_labels:
                    self._trim_active_labels(
                        labels_by_id=labels_by_id,
                        active_label_ids=active_label_ids,
                        labels_by_node=labels_by_node,
                        labels_by_signature=labels_by_signature,
                        target_loss=target_loss,
                        keep_limit=search_budget.max_total_labels,
                    )

        if best_terminal_label_id is None:
            return None
        return self._reconstruct_result(labels_by_id, best_terminal_label_id, best_terminal_rank, target_loss)

    def _reconstruct_result(
        self,
        labels_by_id: dict[int, _LexicoLabel],
        terminal_label_id: int,
        terminal_rank: tuple[float, float, float] | None,
        target_loss: float,
    ) -> PathSolveResult:
        node_path: list[int] = []
        edge_path: list[int] = []
        cursor = labels_by_id[terminal_label_id]
        while True:
            node_path.append(cursor.node_id)
            if cursor.via_edge_id is not None:
                edge_path.append(cursor.via_edge_id)
            if cursor.parent_label_id is None:
                break
            cursor = labels_by_id[cursor.parent_label_id]
        node_path.reverse()
        edge_path.reverse()
        exact_delta = (
            self._terminal_rank(labels_by_id[terminal_label_id], target_loss)[0]
            if terminal_rank is None
            else terminal_rank[0]
        )
        return PathSolveResult(node_path=node_path, edge_path=edge_path, total_cost=exact_delta)

    def _normalize_baseline_result(
        self,
        graph: GraphData,
        baseline_result: PathSolveResult | None,
        edge_freshness_loss_by_id: dict[int, float],
        edge_secondary_cost_by_id: dict[int, float],
        target_loss: float,
    ) -> tuple[PathSolveResult | None, tuple[float, float, float] | None]:
        if baseline_result is None or not baseline_result.edge_path:
            return baseline_result, None

        loss, secondary, distance_m = self._path_metrics(
            graph=graph,
            edge_path=baseline_result.edge_path,
            edge_freshness_loss_by_id=edge_freshness_loss_by_id,
            edge_secondary_cost_by_id=edge_secondary_cost_by_id,
        )
        rank = (abs(loss - target_loss), secondary, distance_m)
        normalized = PathSolveResult(
            node_path=list(baseline_result.node_path),
            edge_path=list(baseline_result.edge_path),
            total_cost=rank[0],
        )
        return normalized, rank

    def _path_metrics(
        self,
        graph: GraphData,
        edge_path: list[int],
        edge_freshness_loss_by_id: dict[int, float],
        edge_secondary_cost_by_id: dict[int, float],
    ) -> tuple[float, float, float]:
        total_loss = 0.0
        total_secondary = 0.0
        total_distance_m = 0.0
        for edge_id in edge_path:
            edge = graph.edges_by_id[edge_id]
            total_loss += max(0.0, float(edge_freshness_loss_by_id.get(edge_id, 0.0)))
            total_secondary += max(
                1e-6,
                float(edge_secondary_cost_by_id.get(edge_id, max(float(edge.base_travel_time_s), 1e-6))),
            )
            total_distance_m += max(float(edge.length_m), 0.0)
        return total_loss, total_secondary, total_distance_m

    def _build_reverse_lower_bounds(
        self,
        graph: GraphData,
        end_node_id: int,
        edge_weight_by_id: dict[int, float],
        fallback_to_base_time: bool,
        use_distance: bool,
    ) -> dict[int, float]:
        reverse_edges_by_to: dict[int, list[tuple[int, float]]] = {}
        for edge in graph.edges_by_id.values():
            weight = edge_weight_by_id.get(edge.edge_id)
            if weight is None:
                if use_distance:
                    weight = edge.length_m
                elif fallback_to_base_time:
                    weight = edge.base_travel_time_s
                else:
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

    def _queue_item(
        self,
        label: _LexicoLabel,
        target_loss: float,
    ) -> tuple[float, float, float, float, float, float, int, int]:
        return (
            label.optimistic_delta,
            label.optimistic_secondary,
            label.optimistic_distance_m,
            abs(label.cum_loss - target_loss),
            label.cum_secondary,
            label.cum_distance_m,
            label.depth,
            label.label_id,
        )

    def _dominance_vector(
        self,
        label: _LexicoLabel,
        target_loss: float,
    ) -> tuple[float, float, float, float, float, float]:
        return (
            label.optimistic_delta,
            label.optimistic_secondary,
            label.optimistic_distance_m,
            abs(label.cum_loss - target_loss),
            label.cum_secondary,
            label.cum_distance_m,
        )

    def _terminal_rank(
        self,
        label: _LexicoLabel,
        target_loss: float,
    ) -> tuple[float, float, float]:
        return (
            abs(label.cum_loss - target_loss),
            label.cum_secondary,
            label.cum_distance_m,
        )

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

    def _is_dominated(
        self,
        candidate: _LexicoLabel,
        signature: tuple[int, int, int],
        labels_by_id: dict[int, _LexicoLabel],
        active_label_ids: set[int],
        labels_by_signature: dict[tuple[int, int, int], list[int]],
        target_loss: float,
    ) -> bool:
        candidate_vector = self._dominance_vector(candidate, target_loss)
        for label_id in labels_by_signature.get(signature, []):
            if label_id not in active_label_ids:
                continue
            existing = labels_by_id.get(label_id)
            if existing is None:
                continue
            existing_vector = self._dominance_vector(existing, target_loss)
            if self._vector_dominates(existing_vector, candidate_vector):
                return True
        return False

    def _prune_dominated_labels(
        self,
        candidate: _LexicoLabel,
        signature: tuple[int, int, int],
        labels_by_id: dict[int, _LexicoLabel],
        active_label_ids: set[int],
        labels_by_node: dict[int, list[int]],
        labels_by_signature: dict[tuple[int, int, int], list[int]],
        target_loss: float,
    ) -> None:
        candidate_vector = self._dominance_vector(candidate, target_loss)
        for label_id in list(labels_by_signature.get(signature, [])):
            if label_id not in active_label_ids:
                continue
            existing = labels_by_id.get(label_id)
            if existing is None:
                continue
            existing_vector = self._dominance_vector(existing, target_loss)
            if self._vector_dominates(candidate_vector, existing_vector):
                self._discard_label(
                    label_id=label_id,
                    labels_by_id=labels_by_id,
                    active_label_ids=active_label_ids,
                    labels_by_node=labels_by_node,
                    labels_by_signature=labels_by_signature,
                    target_loss=target_loss,
                )

    def _vector_dominates(
        self,
        left: tuple[float, float, float, float, float, float],
        right: tuple[float, float, float, float, float, float],
    ) -> bool:
        less_or_equal = all(l <= r + self._EPS for l, r in zip(left, right))
        strictly_better = any(l + self._EPS < r for l, r in zip(left, right))
        return less_or_equal and strictly_better

    def _discard_label(
        self,
        label_id: int,
        labels_by_id: dict[int, _LexicoLabel],
        active_label_ids: set[int],
        labels_by_node: dict[int, list[int]],
        labels_by_signature: dict[tuple[int, int, int], list[int]],
        target_loss: float,
    ) -> None:
        label = labels_by_id.get(label_id)
        if label is None:
            return
        active_label_ids.discard(label_id)

        node_bucket = labels_by_node.get(label.node_id)
        if node_bucket and label_id in node_bucket:
            node_bucket.remove(label_id)
            if not node_bucket:
                labels_by_node.pop(label.node_id, None)

        signature = self._signature(label.node_id, label.cum_loss, target_loss)
        signature_bucket = labels_by_signature.get(signature)
        if signature_bucket and label_id in signature_bucket:
            signature_bucket.remove(label_id)
            if not signature_bucket:
                labels_by_signature.pop(signature, None)

    def _trim_active_labels(
        self,
        labels_by_id: dict[int, _LexicoLabel],
        active_label_ids: set[int],
        labels_by_node: dict[int, list[int]],
        labels_by_signature: dict[tuple[int, int, int], list[int]],
        target_loss: float,
        keep_limit: int,
    ) -> None:
        if len(active_label_ids) <= keep_limit:
            return
        scored = sorted(
            (
                self._queue_item(labels_by_id[label_id], target_loss),
                label_id,
            )
            for label_id in active_label_ids
            if label_id in labels_by_id
        )
        keep_ids = {label_id for _, label_id in scored[:keep_limit]}
        for label_id in list(active_label_ids):
            if label_id in keep_ids:
                continue
            self._discard_label(
                label_id=label_id,
                labels_by_id=labels_by_id,
                active_label_ids=active_label_ids,
                labels_by_node=labels_by_node,
                labels_by_signature=labels_by_signature,
                target_loss=target_loss,
            )

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
        base_per_node_limit = max(3, int(max_labels_per_node))
        per_node_bonus = max(
            2,
            int(round(safe_depth_hint * (0.0015 + safe_branching_hint * 0.0020))),
        )
        max_per_node_limit = min(32, base_per_node_limit + per_node_bonus)

        safe_total_labels = max(base_per_node_limit * 12, int(max_total_labels))
        safe_total_labels = min(max(96, safe_total_labels), 1600)

        expansion_limit = max(
            self._DEFAULT_MAX_EXPANSIONS // 8,
            safe_total_labels * 10,
            int(round(len(graph.edges_by_id) * (1.8 + safe_branching_hint * 1.4))),
            int(round(safe_depth_hint * (4.0 + safe_branching_hint * 3.0))),
        )
        expansion_limit = min(expansion_limit, self._DEFAULT_MAX_EXPANSIONS)

        return _SearchBudget(
            depth_hint=safe_depth_hint,
            branching_hint=safe_branching_hint,
            base_per_node_limit=base_per_node_limit,
            max_per_node_limit=max_per_node_limit,
            max_total_labels=safe_total_labels,
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
