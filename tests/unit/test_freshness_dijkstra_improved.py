import unittest

from src.algorithms.freshness_dijkstra_improved import FreshnessDijkstraImprovedSolver
from src.algorithms.graph_builder import GraphData, GraphEdge


class TestFreshnessDijkstraImprovedSolver(unittest.TestCase):
    def setUp(self) -> None:
        nodes = {
            1: (116.0, 39.0),
            2: (116.1, 39.1),
            3: (116.2, 39.2),
            4: (116.3, 39.3),
        }
        edges = [
            GraphEdge(1, 1, 2, 1000, 10, [nodes[1], nodes[2]], road_class="normal"),
            GraphEdge(2, 2, 4, 1000, 10, [nodes[2], nodes[4]], road_class="normal"),
            GraphEdge(3, 1, 3, 1200, 12, [nodes[1], nodes[3]], road_class="normal"),
            GraphEdge(4, 3, 4, 1200, 12, [nodes[3], nodes[4]], road_class="normal"),
        ]
        self.graph = GraphData.build(nodes=nodes, edges=edges)
        self.solver = FreshnessDijkstraImprovedSolver()

    def test_should_choose_path_with_loss_closer_to_target(self) -> None:
        # 路径A(1->2->4)损耗: 1.5 + 1.5 = 3.0
        # 路径B(1->3->4)损耗: 2.2 + 2.2 = 4.4
        # 目标损耗4.0时，路径B更接近目标。
        edge_loss = {
            1: 1.5,
            2: 1.5,
            3: 2.2,
            4: 2.2,
        }
        edge_time = {
            1: 10.0,
            2: 10.0,
            3: 12.0,
            4: 12.0,
        }
        result = self.solver.solve(
            graph=self.graph,
            start_node_id=1,
            end_node_id=4,
            edge_freshness_loss_by_id=edge_loss,
            target_loss=4.0,
            edge_secondary_cost_by_id=edge_time,
            loss_bin_size=0.1,
            max_loss_limit=20.0,
        )

        self.assertEqual(result.edge_path, [3, 4])

    def test_should_raise_when_unreachable(self) -> None:
        # 移除终点相关边，形成不可达图。
        nodes = {
            1: (116.0, 39.0),
            2: (116.1, 39.1),
            4: (116.3, 39.3),
        }
        edges = [
            GraphEdge(1, 1, 2, 1000, 10, [nodes[1], nodes[2]], road_class="normal"),
        ]
        graph = GraphData.build(nodes=nodes, edges=edges)
        with self.assertRaises(ValueError):
            self.solver.solve(
                graph=graph,
                start_node_id=1,
                end_node_id=4,
                edge_freshness_loss_by_id={1: 1.0},
                target_loss=3.0,
            )


if __name__ == "__main__":
    unittest.main()
