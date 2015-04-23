from .decorators import simulated
from .descriptors import Attribute
from .enums import Roles, Netmodes
from .replicable import Replicable
from .signals import (ReplicableRegisteredSignal, ReplicableUnregisteredSignal)

__all__ = ['_WorldInfo', 'WorldInfo']


class _WorldInfo(Replicable):
    """Holds info about game state"""

    MAXIMUM_TICK = (2 ** 32 - 1)
    _ID = 255

    roles = Attribute(Roles(Roles.authority, Roles.simulated_proxy))

    elapsed = Attribute(0.0, complain=False)
    tick_rate = Attribute(60, complain=True, notify=True)

    netmode = Netmodes.server
    rules = None

    def on_initialised(self):
        self._replicable_lookup_cache = {}

        self.always_relevant = True

    @ReplicableRegisteredSignal.on_global
    @simulated
    def cache_replicable(self, target):
        """Stores replicable instance for fast lookup by type

        :param target: Replicable instance
        """
        cache = self._replicable_lookup_cache

        for base_cls in target.__class__.__mro__:
            try:
                instances = cache[base_cls]
            except KeyError:
                instances = cache[base_cls] = set()

            instances.add(target)

    @ReplicableUnregisteredSignal.on_global
    @simulated
    def uncache_replicable(self, target):
        """Removes stored replicable instance for fast lookup by type

        :param target: Replicable instance
        """
        for values in self._replicable_lookup_cache.values():
            if target in values:
                values.remove(target)

    def conditions(self, is_owner, is_complain, is_initial):
        yield from super().conditions(is_owner, is_complain, is_initial)

        if is_initial:
            yield "elapsed"

        if is_complain:
            yield "tick_rate"

    @property
    def tick(self):
        """:returns: current simulation tick"""
        return self.to_ticks(self.elapsed)

    @simulated
    def to_ticks(self, delta_time):
        """Converts delta time into approximate number of ticks

        :param delta_time: time in seconds
        :returns: ticks according to current tick rate
        """
        return round(delta_time * self.tick_rate)

    @simulated
    def subclass_of(self, actor_type):
        """Find registered actors that are subclasses of a given type

        :param actor_type: type to compare against
        :returns: list of subclass instances
        """
        try:
            return self._replicable_lookup_cache[actor_type]

        except KeyError:
            return set()

    @simulated
    def update_clock(self, delta_time):
        """Update internal clock

        :param delta_time: delta time since last simulation tick
        """
        self.elapsed += delta_time

    @simulated
    def type_is(self, name):
        """Find Replicable instances with provided type

        :param name: name of class type
        :returns: list of sibling instances derived from provided type
        """
        return Replicable._by_types.get(name)

    @property
    def replicables(self):
        return Replicable._instances.values()


WorldInfo = _WorldInfo(_WorldInfo._ID, register_immediately=True)
