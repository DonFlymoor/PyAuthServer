from network import Struct, Attribute
from mathutils import Vector, Euler


class RigidBodyState(Struct):

    position = Attribute(Vector())
    velocity = Attribute(Vector())
    angular = Attribute(Vector())
    rotation = Attribute(Euler())

    collision_group = Attribute(type_of=float)


class AnimationState(Struct):

    layer = Attribute(0)
    start = Attribute(0)
    end = Attribute(0)
    blend = Attribute(1.0)
    name = Attribute("")
    speed = Attribute(1.0)
    mode = Attribute(0)
    timestamp = Attribute(0.0)
