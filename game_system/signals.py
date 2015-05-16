from network.signals import Signal


class CollisionSignal(Signal):
    pass


class UpdateCollidersSignal(Signal):
    pass


class SetMoveTarget(Signal):
    pass


class PhysicsReplicatedSignal(Signal):
    pass


class PhysicsSingleUpdateSignal(Signal):
    pass


class PhysicsRoleChangedSignal(Signal):
    pass


class PhysicsRewindSignal(Signal):
    pass


class ConnectToSignal(Signal):
    pass


class CopyStateToActor(Signal):
    pass


class CopyActorToState(Signal):
    pass


class ActorDamagedSignal(Signal):
    pass


class PawnKilledSignal(Signal):
    pass


class PlayerInputSignal(Signal):
    pass


class PhysicsTickSignal(Signal):
    pass


class MapLoadedSignal(Signal):
    pass


class GameExitSignal(Signal):
    pass


class MessageReceivedSignal(Signal):
    pass


class PostPhysicsSignal(Signal):
    pass


class LogicUpdateSignal(Signal):
    pass


class TimerUpdateSignal(Signal):
    pass


class UIUpdateSignal(Signal):
    pass


class UIRenderSignal(Signal):
    pass