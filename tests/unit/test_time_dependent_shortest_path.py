import unittest

from src.algorithms.graph_builder import GraphData, GraphEdge
from src.algorithms.time_dependent_shortest_path import TimeDependentShortestPathSolver


class TestTimeDependentShortestPathSolver(unittest.TestCase):
    def setUp(self) -> None:
        nodes = {
            1: (116.0, 39.0),
            2: (116.1, 39.1),
            3: (116.2, 39.2),
            4: (116.3, 39.3),
        }
        edges = [
            GraphEdge(1, 1, 2, 1000, 10, [nodes[1], nodes[2]]),
            GraphEdge(2, 2, 4, 1000, 10, [nodes[2], nodes[4]]),
            GraphEdge(3, 1, 3, 1200, 12, [nodes[1], nodes[3]]),
            GraphEdge(4, 3, 4, 1200, 12, [nodes[3], nodes[4]]),
        ]
        self.graph = GraphData.build(nodes=nodes, edges=edges)
        self.solver = TimeDependentShortestPathSolver()

    def test_should_use_dynamic_weight(self) -> None:
        overrides = {
            1: 40,
            2: 40,
            3: 12,
            4: 12,
        }
        result = self.solver.solve(self.graph, 1, 4, edge_time_overrides_s=overrides)
        self.assertEqual(result.node_path, [1, 3, 4])
        self.assertEqual(result.edge_path, [3, 4])


if __name__ == "__main__":
    unittest.main()
