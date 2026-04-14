import unittest

from src.algorithms.graph_builder import GraphData, GraphEdge
from src.algorithms.static_shortest_path import StaticShortestPathSolver


class TestStaticShortestPathSolver(unittest.TestCase):
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
        self.solver = StaticShortestPathSolver()

    def test_should_find_min_time_path(self) -> None:
        result = self.solver.solve(self.graph, 1, 4, weight_mode="time")
        self.assertEqual(result.node_path, [1, 2, 4])
        self.assertEqual(result.edge_path, [1, 2])

    def test_should_find_min_distance_path(self) -> None:
        result = self.solver.solve(self.graph, 1, 4, weight_mode="distance")
        self.assertEqual(result.edge_path, [1, 2])


if __name__ == "__main__":
    unittest.main()
