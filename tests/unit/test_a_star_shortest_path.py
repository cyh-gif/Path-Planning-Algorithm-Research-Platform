import unittest

from src.algorithms.a_star_shortest_path import AStarShortestPathSolver
from src.algorithms.graph_builder import GraphData, GraphEdge


class TestAStarShortestPathSolver(unittest.TestCase):
    def setUp(self) -> None:
        nodes = {
            1: (116.0000, 39.0000),
            2: (116.1000, 39.0500),
            3: (116.2200, 39.0800),
            4: (116.3500, 39.1200),
        }
        edges = [
            GraphEdge(1, 1, 2, 1000, 10, [nodes[1], nodes[2]]),
            GraphEdge(2, 2, 4, 1000, 10, [nodes[2], nodes[4]]),
            GraphEdge(3, 1, 3, 1400, 16, [nodes[1], nodes[3]]),
            GraphEdge(4, 3, 4, 1200, 12, [nodes[3], nodes[4]]),
        ]
        self.graph = GraphData.build(nodes=nodes, edges=edges)
        self.solver = AStarShortestPathSolver()

    def test_should_find_min_time_path(self) -> None:
        result = self.solver.solve(self.graph, 1, 4, weight_mode="time")
        self.assertEqual(result.node_path, [1, 2, 4])
        self.assertEqual(result.edge_path, [1, 2])

    def test_should_find_min_distance_path(self) -> None:
        result = self.solver.solve(self.graph, 1, 4, weight_mode="distance")
        self.assertEqual(result.edge_path, [1, 2])


if __name__ == "__main__":
    unittest.main()

