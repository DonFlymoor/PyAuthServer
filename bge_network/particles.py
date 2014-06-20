from network.signals import SignalListener

from .bge_network.types.object_types import BGEBaseObject


__all__ = ['Particle']


class Particle(BGEBaseObject, SignalListener):

    entity_name = None

    def __init__(self):
        self.register_signals()
        self.register(self.__class__.entity_name)
        self.on_initialised()

    def delete(self):
        super().delete()

        self.unregister_signals()

    def on_initialised(self):
        pass
