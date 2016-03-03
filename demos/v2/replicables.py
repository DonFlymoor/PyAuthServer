from game_system.entity import MeshComponent, PhysicsComponent, TransformComponent
from game_system.replicables import PlayerPawnController, Pawn
from game_system.input import InputContext

from network.annotations.decorators import simulated
from network.replication import Serialisable
from network.enums import Roles


class SomeEntity(Pawn):
    mesh = MeshComponent("Suzanne")
    physics = PhysicsComponent("Cube", mass=200)
    transform = TransformComponent(position=(0, 10, 0))

    roles = Serialisable(Roles(Roles.authority, Roles.autonomous_proxy))

    def __init__(self, scene, unique_id, id_is_explicit=False):
        scene.messenger.add_subscriber("tick", self.on_update)
        self.messenger.add_subscriber("collision_started", self.on_collide)

    def on_destroyed(self):
        self.scene.messenger.remove_subscriber("tick", self.on_update)
        self.messenger.remove_subscriber("collision_started", self.on_collide)

        super().on_destroyed()

    def can_replicate(self, is_owner, is_initial):
        yield from super().can_replicate(is_owner, is_initial)

    def on_collide(self, entity, contacts):
        if not self.physics.mass:
            return

        self.physics.apply_impulse((0, 0, 500), (0, 0, 0))

    @simulated
    def on_update(self):
        pass


class MyPC(PlayerPawnController):

    input_context = InputContext("left", "right", "up", "down", "debug")