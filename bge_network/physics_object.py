from bge import logic
from mathutils import Vector
from network.decorators import simulated
from network.signals import SignalListener
from . import enums, signals, bge_data, timer


class PhysicsObject:
    entity_name = ""
    entity_class = bge_data.GameObject

    def on_initialised(self):
        self.object = self.entity_class(self.entity_name)

        self._parent = None
        self.children = set()
        self.child_entities = set()

        self._new_colliders = set()
        self._old_colliders = set()
        self._registered = set()

        self._register_callback()
        self._establish_relationships()

    @staticmethod
    def from_object(obj):
        '''Attempt to find the associated instance that owns an object
        :param obj: instance of :py:class:`GameObject`
        :returns: instance of :py:class:`PhysicsObject`'''
        try:
            return obj.mapped_instance
        except AttributeError:
            return None

    @simulated
    def _establish_relationships(self):
        self.object.mapped_instance = self

        # Setup relationships in sockets
        for socket_name, socket in self.sockets.items():
            socket = bge_data.SocketWrapper(socket)
            socket.mapped_instance = self

    @property
    def lifespan(self):
        '''The time before the object is destroyed'''
        if hasattr(self, "_timer"):
            return self._timer.remaining
        return 0

    @lifespan.setter
    def lifespan(self, value):
        if hasattr(self, "_timer"):
            self._timer.delete()
            del self._timer
        if value > 0:
            self._timer = timer.Timer(value,
                            on_target=self.request_unregistration)

    @property
    def suspended(self):
        '''The Physics state of the object'''
        if self.physics in (enums.PhysicsType.navigation_mesh,
                            enums.PhysicsType.no_collision):
            return True
        return not self.object.useDynamics

    @suspended.setter
    def suspended(self, value):
        if self.physics in (enums.PhysicsType.navigation_mesh,
                            enums.PhysicsType.no_collision):
            return

        if self.object.parent:
            return

        self.object.useDynamics = not value

    @property
    def colliding(self):
        '''The collision status of the object'''
        return bool(self._registered)

    @simulated
    def colliding_with(self, other):
        '''Determines if the object is colliding with another object

        :param other: object to evaluate
        :returns: result of condition'''
        return other in self._registered

    @simulated
    def _register_callback(self):
        if self.physics in (enums.PhysicsType.navigation_mesh,
                            enums.PhysicsType.no_collision):
            return

        callbacks = self.object.collisionCallbacks
        callbacks.append(self._on_collide)

    @simulated
    def _on_collide(self, other, data):
        if not self or self.suspended:
            return

        # If we haven't already stored the collision
        self._new_colliders.add(other)

        if not other in self._registered:
            self._registered.add(other)
            signals.CollisionSignal.invoke(other, True, data, target=self)

    @signals.UpdateCollidersSignal.global_listener
    @simulated
    def _update_colliders(self):
        if self.suspended:
            return

        assert self

        # If we have a stored collision
        difference = self._old_colliders.difference(self._new_colliders)
        self._old_colliders, self._new_colliders = self._new_colliders, set()

        if not difference:
            return

        callback = signals.CollisionSignal.invoke
        for obj in difference:
            self._registered.remove(obj)
            if not obj.invalid:
                callback(obj, False, None, target=self)

    def on_unregistered(self):
        # Unregister from parent
        if self.parent:
            self.parent.remove_child(self)

        if hasattr(self, "_timer"):
            self._timer.delete()

        for child in self.children.copy():
            self.remove_child(child)

        self.object.endObject()

    @simulated
    def add_child(self, instance):
        '''Adds a child to this object

        :param instance: instance to add
        :requires: instance must subclass :py:class:`PhysicsObject`'''
        self.children.add(instance)
        self.child_entities.add(instance.object)

    @simulated
    def remove_child(self, instance):
        '''Removes a child from this object

        :param instance: instance to remove
        :requires: instance must subclass :py:class:`PhysicsObject`'''
        self.children.remove(instance)
        self.child_entities.remove(instance.object)

    @property
    def parent(self):
        '''Relational parent of object

        :returns: parent of object
        :requires: instance must subclass :py:class:`PhysicsObject`'''
        return self._parent

    def set_parent(self, parent, socket_name=None):
        if parent is None:
            self._parent.remove_child(self)
            self.object.setParent(None)
            self._parent = None

        elif isinstance(parent, PhysicsObject):
            parent.add_child(self)
            if socket_name is not None:
                physics_obj = parent.sockets[socket_name]
            else:
                physics_obj = parent.object

            self.object.setParent(physics_obj)
            self._parent = parent

        else:
            raise TypeError("Could not set parent\
                with type {}".format(type(parent)))

    @property
    def collision_group(self):
        '''Physics collision group

        :returns: physics bitmask of collision group
        :requires: must be within -1 and 256'''
        return self.object.collisionGroup

    @collision_group.setter
    def collision_group(self, group):
        if self.object.collisionGroup == group:
            return

        assert -1 < group < 256
        self.object.collisionGroup = group

    @property
    def collision_mask(self):
        '''Physics collision mask

        :returns: physics bitmask of collision mask
        :requires: must be within -1 and 256'''
        return self.object.collisionMask

    @collision_mask.setter
    def collision_mask(self, mask):
        if self.object.collisionMask == mask:
            return

        assert -1 < mask < 256
        self.object.collisionMask = mask

    @property
    def visible(self):
        ''':returns: the visible state of this object'''
        obj = self.object
        return (obj.visible and obj.meshes) or any(o.visible and o.meshes
                for o in obj.childrenRecursive)

    @property
    def mass(self):
        ''':returns: the mass of this object'''
        return self.object.mass

    @property
    def physics(self):
        '''The physics type of this object

        :returns: physics type of object, see :py:class:`bge_network.enums.PhysicsType`'''
        physics_type = self.object.physicsType
        if not getattr(self.object, "meshes", []):
            return logic.KX_PHYSICS_NO_COLLISION
        return physics_type

    @property
    def sockets(self):
        return {s['socket']: s for s in self.object.childrenRecursive if "socket" in s}

    @property
    def has_dynamics(self):
        return self.physics in (enums.PhysicsType.rigid_body, enums.PhysicsType.dynamic)

    @property
    def transform(self):
        return self.object.worldTransform

    @transform.setter
    def transform(self, val):
        self.object.worldTransform = val

    @property
    def rotation(self):
        return self.object.worldOrientation.to_euler()

    @rotation.setter
    def rotation(self, rot):
        self.object.worldOrientation = rot

    @property
    def position(self):
        return self.object.worldPosition

    @position.setter
    def position(self, pos):
        self.object.worldPosition = pos

    @property
    def local_position(self):
        return self.object.localPosition

    @local_position.setter
    def local_position(self, pos):
        self.object.localPosition = pos

    @property
    def local_rotation(self):
        return self.object.localOrientation.to_euler()

    @local_rotation.setter
    def local_rotation(self, ori):
        self.object.localOrientation = ori

    @property
    def velocity(self):
        if not self.has_dynamics:
            return Vector()

        return self.object.localLinearVelocity

    @velocity.setter
    def velocity(self, vel):
        if not self.has_dynamics:
            return

        self.object.localLinearVelocity = vel

    @property
    def angular(self):
        if not self.has_dynamics:
            return Vector()

        return self.object.localAngularVelocity

    @angular.setter
    def angular(self, vel):
        if not self.has_dynamics:
            return

        self.object.localAngularVelocity = vel

