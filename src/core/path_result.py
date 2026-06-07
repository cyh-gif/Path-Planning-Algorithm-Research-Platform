"""路径算法公共结果模型，供各类求解器复用统一的返回结构。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class PathSolveResult:
    """路径求解结果，记录节点路径、边路径以及总成本。"""

    node_path: list[int]
    edge_path: list[int]
    total_cost: float
