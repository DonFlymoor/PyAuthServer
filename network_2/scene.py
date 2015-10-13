from collections import OrderedDict

from .factory import ProtectedInstance, UniqueIDPool, restricted_method
from .messages import MessagePasser
from .replicable import Replicable


class Scene(ProtectedInstance):

    def __init__(self, world, name):
        self.world = world
        self.name = name

        self.messenger = MessagePasser()
        self.replicables = OrderedDict()

        self._unique_ids = UniqueIDPool(255)

    @restricted_method
    def contest_id(self, unique_id, contestant, existing):
        self.replicables[unique_id] = contestant

        # Re-associate existing
        unique_id = self._unique_ids.take()
        with Replicable._grant_authority():
            existing.change_unique_id(unique_id)

        self.replicables[unique_id] = existing

    def add_replicable(self, replicable_cls, unique_id=None):
        # Check type of replicable
        if not issubclass(replicable_cls, Replicable):
            raise TypeError("Expected Replicable subclass, received '{}'".format(replicable_cls.__name__))

        is_static = unique_id is not None
        if not is_static:
            unique_id = self._unique_ids.take()

        # Just create replicable
        with Replicable._grant_authority():
            replicable = replicable_cls.__new__(replicable_cls, self, unique_id, is_static)

        self.messenger.send("replicable_created", replicable)

        # Now initialise replicable
        replicable.__init__(self, unique_id, is_static)

        # Contest id if already in use
        if unique_id in self.replicables:
            existing = self.replicables[unique_id]

            with Scene._grant_authority():
                self.contest_id(unique_id, replicable, existing)

        else:
            self.replicables[unique_id] = replicable

        self.messenger.send("replicable_added", replicable)

        return replicable

    def remove_replicable(self, replicable):
        unique_id = replicable.unique_id
        self.replicables.pop(unique_id)
        self._unique_ids.retire(unique_id)

        self.messenger.send("replicable_removed", replicable)

        replicable.on_destroyed()
        self.messenger.send("replicable_destroyed", replicable)

    @restricted_method
    def on_destroyed(self):
        # Release all replicables
        while self.replicables:
            replicable = next(iter(self.replicables.values()))
            self.remove_replicable(replicable)

    def __repr__(self):
        return "<'{}' scene>".format(self.name)

