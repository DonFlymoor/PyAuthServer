from .decorators import simulated
from .descriptors import Attribute
from .enums import Roles, Netmodes
from .replicable import Replicable
from .signals import (ReplicableRegisteredSignal, ReplicableUnregisteredSignal)

__all__ = ['_WorldInfo', 'WorldInfo']


class _WorldInfo(Replicable):
    """Holds info about game state"""

    _MAXIMUM_TICK = (2 ** 32 - 1)
    _ID = 255

    roles = Attribute(
                      Roles(
                            Roles.authority,  # @UndefinedVariable
                            Roles.simulated_proxy  # @UndefinedVariable
                            )
                      )

    elapsed = Attribute(0.0, complain=False)
    tick_rate = Attribute(1000, complain=True, notify=True)

    netmode = Netmodes.server
    rules = None
    clock_correction = 0.0

    def on_initialised(self):
        self._cache = {}

        self.always_relevant = True

    @ReplicableRegisteredSignal.global_listener
    @simulated
    def cache_replicable(self, target):
        """Stores replicable instance for fast lookup by type

        :param target: Replicable instance"""
        for cls_type, values in self._cache.items():
            if isinstance(target, cls_type):
                values.append(target)

    @ReplicableUnregisteredSignal.global_listener
    @simulated
    def uncache_replicable(self, target):
        """Removes stored replicable instance for fast lookup by type

        :param target: Replicable instance"""
        for values in self._cache.values():
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
        return self.to_ticks(self.elapsed + self.clock_correction)

    @simulated
    def to_ticks(self, delta_time):
        """Converts delta time into approximate number of ticks

        :param delta_time: time in seconds
        :returns: ticks according to current tick rate"""
        return round(delta_time * self.tick_rate)

    @simulated
    def subclass_of(self, actor_type):
        """Find registered actors that are subclasses of a given type

        :param actor_type: type to compare against
        :returns: list of subclass instances
        """
        try:
            return self._cache[actor_type]

        except KeyError:
            values = self._cache[actor_type] = [a for a in Replicable if
                                                isinstance(a, actor_type)]
            return values

    @simulated
    def update_clock(self, delta_time):
        """Update internal clock

        :param delta_time: delta time since last simulation tick"""
        self.elapsed += delta_time

    @simulated
    def type_is(self, name):
        """Find Replicable instances with provided type

        :param name: name of class type
        :returns: list of sibling instances derived from provided type"""
        return Replicable._by_types.get(name)

    replicables = property(Replicable.get_graph_instances)
    get_replicable = simulated(Replicable.get_from_graph)
    has_replicable = simulated(Replicable.graph_has_instance)


WorldInfo = _WorldInfo(_WorldInfo._ID, register=True)
