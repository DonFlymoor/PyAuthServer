from game_system.pathfinding.algorithm import AStarNode, AStarAlgorithm
from game_system.geometry.utilities import quad_area
from network.utilities import mean


class PandaPolygon:

    def __init__(self, vertices):
        self.neighbours = set()

        self.position = None
        self.vertices = vertices
        self.position = mean(vertices)


class PandaNodePortal:
    __slots__ = "source", "destination", "left", "right"

    def __init__(self, source, destination):
        self.source = source
        self.destination = destination

        self.left, self.right = self._generate_nodes()

    def _generate_nodes(self):
        source_position = self.source.position
        destination_position = self.destination.position
        first, second = [v for v in self.source.vertices if v in self.destination.vertices]

        side_first = quad_area(source_position, destination_position, first)
        side_second = quad_area(source_position, destination_position, second)

        mapping = {side_first: first, side_second: second}

        right = mapping[max(side_first, side_second)]
        left = mapping[min(side_first, side_second)]

        return left, right


class PandaNavmeshNode(AStarNode, PandaPolygon):

    def __init__(self, vertices):
        super().__init__(vertices)

    def get_g_score_from(self, other):
        return (other.position - self.position).length

    def get_h_score_from(self, other):
        return (other.position - self.position).length

    #@lru_cache()
    def get_portal_to(self, other):
        return PandaNodePortal(self, other)