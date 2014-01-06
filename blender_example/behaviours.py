from bge_network import *
from mathutils import Vector
from random import random, randrange
from functools import partial
from time import monotonic
import bge


def dead_animation():
    play_state = SequenceNode(
                              IsDead(),
                              PlayAnimation("Death", 1, 44,
                                            layer=0, blend=1),
                              Delay(5),
                              )

    return play_state


def walk_animation():
    play_state = SequenceNode(
                              IsWalking(),
                              Inverter(IsDead()),
                              PlayAnimation("Walk2", 15, 45, blend=1.0),
                              )
    play_state.should_restart = True
    return play_state


def idle_animation():
    play_state = SequenceNode(
                              Inverter(IsWalking()),
                              Inverter(IsDead()),
                              PlayAnimation("Idle", 0, 60, blend=1.0),
                              )
    play_state.should_restart = True
    return play_state


def dying_behaviour():
    group = SequenceNode(
                         GetPawn(),
                         IsDead(),
                         GetAttacker(),
                         SetCollisionFlags(mask=CollisionGroups.geometry),
                         Delay(5),
                         Signal(ActorKilledSignal,
                               from_blackboard={"target": "pawn",
                                                "attacker": "attacker"})
                         )
    group.name = "DyingBehaviour"

    return group


def idle_behaviour():
    group = SequenceNode(
                         GetPawn(),
                         Inverter(IsDead()),
                         FindRandomPoint(),
                         HasPointTarget(),
                         GetNavmesh(),
                         MoveToPoint(),
                         ConsumePoint(),
                         RandomDelay(2, 5),
                         )
    group.name = "IdleBehaviour"
    return group


def attack_behaviour():
    can_hit_target = SelectorNode(
                                    WithinAttackRange(),
                                    SequenceNode(
                                        GetNavmesh(),
                                        MoveToActor(),
                                        ),
                                  )

    can_hit_target.should_restart = True

    engage_target = SequenceNode(
                                 can_hit_target,
                                 SelectorNode(
                                              HasAmmo(),
                                              ReloadWeapon()
                                              ),
                                 ConvertState(EvaluationState.failure,
                                              EvaluationState.running,

                                              CheckTimer()
                                              ),
                                 #Alert("{pawn} Attacking {actor}"),
                                 AimAtActor(),
                                 FireWeapon(),
                                 SetTimer()
                                 )

    group = SequenceNode(
                         GetPawn(),
                         Inverter(IsDead()),
                         GetCamera(),
                         GetWeapon(),
                         SelectorNode(
                                      SequenceNode(
                                                   HasActorTarget(),
                                                   TargetIsAlive()
                                                   ),
                                      FindVisibleActor(),
                                      ),

                         engage_target,
                         )
    group.should_restart = True
    group.name = "AttackBehaviour"
    return group


def climb_behaviour():

    root = SequenceNode(
                        GetPawn(),
                        FindObstacle(),
                        )
    return root


def fire_behind_shelter():
    return """SequenceNode(
                                IsInShelter(),
                                Stand(),
                                )"""


class DebugState(SignalLeafNode):

    def __init__(self, child):
        super().__init__()
        self.child = child

    def evaluate(self, bb):
        st = self.child.evaluate(bb)
        x = self.child.children[0]._message
        if st == EvaluationState.running and 0:
            print("Running", self.child.children[self.child.resume_index])
        #print("'", x, "'", EvaluationState[st])
        return st


class StateModifier(SequenceNode, SignalInnerNode):

    def transform(self, old_state):
        return old_state

    def evaluate(self, blackboard):
        state = super().evaluate(blackboard)
        return self.transform(state)


class BlackboardModifier(SequenceNode, SignalInnerNode):

    def evaluate(self, blackboard):
        state = super().evaluate(blackboard.__class__())
        return self.transform(state)


class IntervalDecorator(SequenceNode, SignalInnerNode):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.last_time = 0.0

    @property
    def interval(self):
        return 0.0

    def evaluate(self, blackboard):
        if (monotonic() - self.last_time) > self.interval:
            self.last_time = monotonic()
            return super().evaluate(blackboard)

        return EvaluationState.failure


class ConditionSequence(SequenceNode, SignalInnerNode):

    def condition(self, blacboard):
        return True

    def evaluate(self, blackboard):
        if self.condition(blackboard):
            return super().evaluate(blackboard)
        return EvaluationState.failure


class ConditionNode(SignalLeafNode):

    def condition(self, blackboard):
        return True

    def evaluate(self, blackboard):
        return (EvaluationState.success if self.condition(blackboard)
                else EvaluationState.failure)


class Inverter(SequenceNode, SignalInnerNode):

    def evaluate(self, blackboard):
        state = super().evaluate(blackboard)

        if state == EvaluationState.success:
            return EvaluationState.failure
        elif state == EvaluationState.failure:
            return EvaluationState.success
        return state


class SetCollisionFlags(SignalLeafNode):

    def __init__(self, mask=None, group=None):
        super().__init__()

        self._mask = mask
        self._group = group

    def evaluate(self, blackboard):
        if self._mask is not None:
            blackboard['pawn'].collision_mask = self._mask

        if self._group is not None:
            blackboard['pawn'].collision_group = self._group

        return EvaluationState.success


class GetObstacle(ConditionNode):

    def condition(self, blackboard):
        forwards = Vector((0, 1, 0))
        hit_obj, *_ = blackboard['pawn'].trace_ray(forwards)
        return bool(hit_obj)


class Delay(SignalLeafNode):

    def __init__(self, delay=0.0):
        super().__init__()

        self._delay = delay
        self._timer = ManualTimer(target_value=self._delay)

    def on_enter(self, blackboard):
        self._timer.reset()

    def evaluate(self, blackboard):
        self._timer.update(blackboard['delta_time'])

        if self._timer.success:
            return EvaluationState.success
        return EvaluationState.running


class RandomDelay(Delay):

    def __init__(self, start, end):
        super().__init__()

        self._range = start, end

    def on_enter(self, blackboard):
        self._timer.reset()
        self._timer.target = randrange(*self._range)


class Alert(SignalLeafNode):

    def __init__(self, message, *children):
        super().__init__(*children)

        self._message = message

    def evaluate(self, blackboard):
        print(self._message.format(self=self, **blackboard))
        return EvaluationState.success


class FindCeiling(SignalLeafNode):

    def evaluate(self, blackboard):
        climbable_height = 10
        upwards = Vector((0, 0, climbable_height))
        hit_obj, hit_pos, hit_normal = blackboard['pawn'].trace_ray(upwards)
        if not hit_obj:
            return EvaluationState.failure

        blackboard['ceiling'] = hit_pos


class IsWalking(ConditionNode):

    def condition(self, blackboard):
        pawn = blackboard['pawn']
        return abs(pawn.velocity.y) >= pawn.walk_speed * 0.9


class PlayAnimation(SignalLeafNode):

    def __init__(self, name, start, end, *children, **kwargs):
        super().__init__(*children)

        self._start = start
        self._end = end
        self._name = name
        self._kwargs = kwargs

    def on_enter(self, blackboard):
        pawn = blackboard['pawn']
        pawn.play_animation(self._name, self._start,
                            self._end, **self._kwargs)

    def evaluate(self, blackboard):
        pawn = blackboard['pawn']

        if not pawn:
            return EvaluationState.failure

        layer = self._kwargs.get("layer", 0)

        if not pawn.is_playing_animation(layer):
            return EvaluationState.success
        else:
            return EvaluationState.running


class StopAnimation(SignalLeafNode):

    def __init__(self, *children, **kwargs):
        super().__init__(*children)

        self._kwargs = kwargs

    def on_enter(self, blackboard):
        pawn = blackboard['pawn']
        pawn.stop_animation(**self._kwargs)

    def evaluate(self, blackboard):
        return EvaluationState.success


class IsPlayingAnimation(ConditionNode):

    def __init__(self, *children, **kwargs):
        super().__init__(*children)

        self._kwargs = kwargs

    def condition(self, blackboard):
        return blackboard['pawn'].is_playing_animation(
                                           self._kwargs.get("layer", 0)
                                           )


class HasAmmo(ConditionNode):

    def condition(self, blackboard):
        return blackboard['weapon'].ammo != 0


class IsDead(ConditionNode):

    def condition(self, blackboard):
        return not blackboard['pawn'].alive


class TargetIsAlive(ConditionNode):

    def condition(self, blackboard):
        return blackboard['actor'].health != 0


class CheckTimer(ConditionNode):

    def condition(self, blackboard):
        weapon = blackboard['weapon']
        return (WorldInfo.elapsed - weapon.last_fired_time
                ) >= weapon.shoot_interval


class SetTimer(SignalLeafNode):

    def evaluate(self, blackboard):
        blackboard['weapon'].last_fired_time = WorldInfo.elapsed
        return (EvaluationState.success)


class ConvertState(StateModifier):

    def __init__(self, st_from, st_to, *children):
        super().__init__(*children)

        self._map = {st_from: st_to}

    def transform(self, old_state):
        return self._map.get(old_state, old_state)


class Signal(SignalLeafNode):

    def __init__(self, event_cls, *args, from_blackboard={}, **kwargs):
        super().__init__()
        self._event = event_cls
        self._args = args
        self._kwargs = kwargs
        self._runtime = from_blackboard

    def evaluate(self, blackboard):
        runtime_args = {k: blackboard[v] for k, v in self._runtime.items()}
        runtime_args.update(self._kwargs)
        self._event.invoke(*self._args, **runtime_args)
        return EvaluationState.success


class AimAtActor(SignalLeafNode):

    def get_target_position(self, blackboard):
        return blackboard['actor'].position

    def evaluate(self, blackboard):
        camera = blackboard['camera']
        pawn = blackboard['pawn']

        target = self.get_target_position(blackboard)

        target_vector = (target - camera.position).normalized()
        world_z = Vector((0, 0, 1))
        camera_vector = -world_z.copy()
        camera_vector.rotate(camera.rotation)
        turn_speed = 0.1

        camera.align_to(-target_vector, axis=Axis.z, time=turn_speed)
        camera.align_to(-world_z.cross(target_vector),
                             axis=Axis.x, time=turn_speed)
        pawn.align_to(world_z.cross(-target_vector), axis=Axis.x, time=turn_speed)
        return EvaluationState.success


class AimAtPoint(AimAtActor):

    def get_target_position(self, blackboard):
        return blackboard['point']


class WithinAttackRange(ConditionNode):

    def condition(self, blackboard):

        return ((blackboard['actor'].position - blackboard['pawn'].position)
                .length <= blackboard['weapon'].maximum_range)


class CanFireWeapon(ConditionNode):

    def condition(self, blackboard):
        return blackboard['weapon'].can_fire


class ReloadWeapon(SignalLeafNode):

    def evaluate(self, blackboard):
        return EvaluationState.success


class FireWeapon(SignalLeafNode):

    def evaluate(self, blackboard):
        blackboard['controller'].start_server_fire()
        return EvaluationState.success


class FindRandomPoint(SignalLeafNode):

    @property
    def random_x(self):
        return (random() - 0.5) * 100

    @property
    def random_y(self):
        return self.random_x

    def evaluate(self, blackboard):
        point = Vector((self.random_x, self.random_y, 1))
        blackboard['point'] = point
        return EvaluationState.success


class RunOnce(ConditionSequence):

    def __init__(self, *children):
        super().__init__(*children)

        self.run = True

    def condition(self, blackboard):
        return self.run

    def on_exit(self, blackboard):
        self.run = False


class GetAttacker(SignalLeafNode):

    @ActorDamagedSignal.listener
    def save_damage(self, damage, instigator, hit_position, momentum):
        self._instigator = instigator

    def evaluate(self, blackboard):
        blackboard['attacker'] = self._instigator
        return EvaluationState.success


class GetPawn(SignalLeafNode):

    def evaluate(self, blackboard):
        if not blackboard['controller'].pawn:
            return EvaluationState.failure

        blackboard['pawn'] = blackboard['controller'].pawn
        return EvaluationState.success


class GetWeapon(SignalLeafNode):

    def evaluate(self, blackboard):
        if not blackboard['controller'].weapon:
            return EvaluationState.failure

        blackboard['weapon'] = blackboard['controller'].weapon
        return EvaluationState.success


class GetCamera(SignalLeafNode):

    def evaluate(self, blackboard):
        if not blackboard['controller'].camera:
            return EvaluationState.failure

        blackboard['camera'] = blackboard['controller'].camera
        return EvaluationState.success


class GetNavmesh(SignalLeafNode):

    def evaluate(self, blackboard):
        try:
            navmesh = next(WorldInfo.subclass_of(Navmesh))
        except StopIteration:
            return EvaluationState.failure

        blackboard['navmesh'] = navmesh
        return EvaluationState.success


class FindVisibleActor(SignalLeafNode):

    def get_distance(self, pawn, actor):
        return (pawn.position - actor.position).length

    def on_enter(self, blackboard):
        found_actors = []

        camera = blackboard['camera']
        pawn = blackboard['pawn']

        is_visible = camera.sees_actor

        for actor in WorldInfo.subclass_of(Pawn):

            if actor == pawn or actor == camera or actor.health == 0:
                continue

            if not is_visible(actor):
                continue

            found_actors.append(actor)

        if found_actors:
            self.actor = min(found_actors, key=partial(self.get_distance, pawn))

        else:
            self.actor = None

    def evaluate(self, blackboard):
        if self.actor is None:
            return EvaluationState.failure

        blackboard['actor'] = self.actor
        return EvaluationState.success


class HasActorTarget(ConditionNode):

    def condition(self, blackboard):
        return bool(blackboard.get("actor"))


class HasPointTarget(ConditionNode):

    def condition(self, blackboard):
        return "point" in blackboard


class ConsumePoint(SignalLeafNode):

    def evaluate(self, blackboard):
        try:
            blackboard.pop("point")
        except KeyError:
            return EvaluationState.failure
        return EvaluationState.success


class ConsumeActor(SignalLeafNode):

    def evaluate(self, blackboard):
        try:
            blackboard.pop("actor")
        except KeyError:
            return EvaluationState.failure
        return EvaluationState.success


class MoveToActor(SignalLeafNode):

    def __init__(self):
        super().__init__()

        self.tolerance = 1.0

    def get_target(self, blackboard):
        return blackboard['actor']

    def get_target_position(self, target):
        return target.position

    def on_exit(self, blackboard):
        try:
            blackboard['pawn'].velocity.y = 0
        except KeyError:
            print(blackboard)
            bge.logic.endGame()

    def draw(self, path):
        start = path[0]
        drawLine = __import__("bge").render.drawLine
        step = 1 / len(path)
        for index, point in enumerate(path):
            drawLine(start, point, [1.0 - index * step, 0, 0])
            start = point

    def evaluate(self, blackboard):
        pawn = blackboard['pawn']
        target = self.get_target(blackboard)

        path = blackboard['navmesh'].find_path(pawn.position,
                                            self.get_target_position(target))

        if not path:
            return EvaluationState.failure

        while path:
            to_target = (path[0] - pawn.position)
            to_target.z = 0

            if to_target.magnitude < self.tolerance:
                path.pop(0)
            else:
                break

        else:
            return EvaluationState.success

        pawn.velocity.y = pawn.walk_speed
        pawn.align_to(to_target.cross(Vector((0, 0, 1))), 1, axis=Axis.x)

        return EvaluationState.running


class MoveToPoint(MoveToActor):

    def get_target(self, blackboard):
        return blackboard['point']

    def get_target_position(self, target):
        return target
