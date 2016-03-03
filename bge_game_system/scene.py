from game_system.entity import Entity, Actor
from game_system.scene import Scene as _Scene

from .entity import EntityBuilder
from .physics import PhysicsManager

from bge import logic


class Scene(_Scene):

    def __init__(self, world, name):
        try:
            scene = next(s for s in logic.getSceneList() if s.name == name)

        except StopIteration:
            scene = logic.addScene(name)

        self.bge_scene = scene

        super().__init__(world, name)

        self.physics_manager = PhysicsManager()

    def _create_entity_builder(self):
        return EntityBuilder(self.bge_scene)

    @property
    def active_camera(self):
        return self.active_camera

    @active_camera.setter
    def active_camera(self, camera):
        self.active_camera = camera

    def _on_replicable_created(self, replicable):
        super()._on_replicable_created(replicable)

        if isinstance(replicable, Entity):
            self.entity_builder.load_entity(replicable)

    def _on_replicable_destroyed(self, replicable):
        super()._on_replicable_destroyed(replicable)

        if isinstance(replicable, Entity):
            self.entity_builder.unload_entity(replicable)
