from .actors import Actor
from .enums import PhysicsType
from .events import CollisionEvent, PhysicsReplicatedEvent, PhysicsTickEvent, PhysicsSingleUpdate, PhysicsSetSimulated, PhysicsUnsetSimulated

from bge import logic, types
from collections import defaultdict
from functools import partial
from network import (WorldInfo, Netmodes, EventListener,
                     ReplicableUnregisteredEvent)
from time import monotonic


class CollisionStatus:
    """Handles collision for Actors"""
    def __init__(self, actor):

        self.register_callback(actor)

        self._new_colliders = set()
        self._old_colliders = set()
        self._registered = set()
        self._actor = actor

        self.receive_collisions = True

    @property
    def colliding(self):
        return bool(self._registered)

    def is_colliding(self, other, data):
        if not self.receive_collisions:
            return

        # If we haven't already stored the collision
        self._new_colliders.add(other)

        if not other in self._registered:
            self._registered.add(other)

            CollisionEvent.invoke(other, True, target=self._actor)

    def not_colliding(self):
        if not self.receive_collisions:
            return

        # If we have a stored collision
        difference = self._old_colliders.difference(self._new_colliders)

        self._old_colliders = self._new_colliders
        self._new_colliders = set()

        if not difference:
            return

        for obj in difference:
            self._registered.remove(obj)

            CollisionEvent.invoke(obj, False, target=self._actor)

    def register_callback(self, actor):
        actor.object.collisionCallbacks.append(self.is_colliding)


class PhysicsSystem(EventListener):

    def __new__(cls, *args, **kwargs):
        """Constructor switch depending upon netmode"""
        if cls is PhysicsSystem:
            netmode = WorldInfo.netmode

            if netmode == Netmodes.server:
                return ServerPhysics.__new__(ServerPhysics, *args, **kwargs)

            elif netmode == Netmodes.client:
                return ClientPhysics.__new__(ClientPhysics, *args, **kwargs)
        else:
            return super().__new__(cls)

    def __init__(self, update_func, apply_func):
        super().__init__()

        self._exempt_actors = []
        self._listeners = {}
        self._update_func = update_func
        self._apply_func = apply_func
        self._active_physics = [PhysicsType.dynamic, PhysicsType.rigid_body]

        self.listen_for_events()

    @ReplicableUnregisteredEvent.listener(True)
    def notify_unregistration(self, target):
        self.remove_listener(target)
        if target in self._exempt_actors:
            self.remove_exemption(target)

    @PhysicsUnsetSimulated.listener(True)
    def add_exemption(self, target):
        if not target in self._exempt_actors:
            self._exempt_actors.append(target)

    @PhysicsSetSimulated.listener(True)
    def remove_exemption(self, target):
        if target in self._exempt_actors:
            self._exempt_actors.remove(target)

    @PhysicsSingleUpdate.listener(True)
    def update_for(self, delta_time, target):
        if not target.physics in self._active_physics:
            return

        for this_target in WorldInfo.subclass_of(Actor):

            if this_target == target:
                continue

            # Callbacks freeze
            if target in self._listeners:
                self._listeners[target].receive_collisions = False

            this_target.suspend_physics()

        self._update_func(delta_time)

        for this_target in WorldInfo.subclass_of(Actor):
            if this_target == target:
                continue

            if target in self._listeners:
                self._listeners[target].receive_collisions = True

            this_target.restore_physics()

        self._apply_func()

    def restore_objects(self):
        pass

    def update(self, scene, delta_time):

        # Restore scheduled objects
        for actor in self._exempt_actors:
            actor.suspend_physics()

            if actor in self._listeners:
                self._listeners[actor].receive_collisions = False

        self._update_func(delta_time)

        # Restore scheduled objects
        for actor in self._exempt_actors:

            if actor in self._listeners:
                self._listeners[actor].receive_collisions = True

            actor.restore_physics()

        self._apply_func()

        for key, listener in self._listeners.items():
            listener.not_colliding()

    def needs_listener(self, replicable):
        return replicable.physics in self._active_physics and not \
                            replicable in self._listeners

    def create_listener(self, replicable):
        self._listeners[replicable] = CollisionStatus(replicable)

    def remove_listener(self, replicable):
        self._listeners.pop(replicable, None)


class ServerPhysics(PhysicsSystem):

    @PhysicsTickEvent.listener(True)
    def update(self, scene, delta_time):
        super().update(scene, delta_time)

        for replicable in WorldInfo.subclass_of(Actor):
            state = replicable.rigid_body_state

            # Can probably do this once then use muteable property
            state.position = replicable.position.copy()
            state.velocity = replicable.velocity.copy()
            state.rotation = replicable.rotation.copy()
            state.angular = replicable.angular.copy()

            # If we need to make a callback instance
            if self.needs_listener(replicable):
                self.create_listener(replicable)


class ClientPhysics(PhysicsSystem):

    small_correction_squared = 1

    @PhysicsTickEvent.listener(True)
    def update(self, scene, delta_time):
        super().update(scene, delta_time)

        for replicable in WorldInfo.subclass_of(Actor):
            
            # If we need to make a callback instance
            if self.needs_listener(replicable):
                self.create_listener(replicable)

    @PhysicsReplicatedEvent.listener(True)
    def actor_replicated(self, target_physics, target):
        difference = target_physics.position - target.position

        small_correction = difference.length_squared < \
                            self.small_correction_squared

        if small_correction:
            target.position += difference * 0.2
            target.velocity = target_physics.velocity + difference * 0.8

        else:
            target.position = target_physics.position.copy()
            target.velocity = target_physics.velocity.copy()

        target.rotation = target_physics.rotation.copy()
        target.angular = target_physics.angular.copy()
