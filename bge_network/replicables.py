from network import *

from . import object_types
from . import structs
from . import behaviour_tree
from . import configuration
from . import enums
from . import errors
from . import signals
from . import inputs
from . import utilities
from . import timer
from . import draw_tools
from . import stream
from . import physics_object

import aud
import bge
from collections import deque, defaultdict, namedtuple, OrderedDict

import math
import mathutils
import os

from functools import partial
from contextlib import contextmanager
from bge import logic, types

Move = namedtuple("Move", ("tick", "inputs", "mouse_x", "mouse_y"))


class Controller(Replicable):

    roles = Attribute(Roles(Roles.authority, Roles.autonomous_proxy))
    pawn = Attribute(type_of=Replicable, complain=True, notify=True)
    camera = Attribute(type_of=Replicable, complain=True, notify=True)
    weapon = Attribute(type_of=Replicable, complain=True, notify=True)
    info = Attribute(type_of=Replicable, complain=True)

    replication_priority = 2.0

    def attach_camera(self, camera):
        camera.set_parent(self.pawn, "camera")
        camera.local_position = mathutils.Vector()

    def on_initialised(self):
        super().on_initialised()

        self.hear_range = 15
        self.effective_hear_range = 10

    def conditions(self, is_owner, is_complaint, is_initial):
        yield from super().conditions(is_owner, is_complaint, is_initial)

        if is_complaint:
            yield "pawn"
            yield "camera"
            yield "weapon"
            yield "info"

    def on_unregistered(self):
        if self.pawn:
            self.remove_dependencies()

        super().on_unregistered()

    def on_pawn_updated(self):
        if self.pawn:
            self.pawn.register_child(self, greedy=True)

    def on_camera_updated(self):
        if self.camera:
            self.attach_camera(self.camera)

    def remove_dependencies(self):
        self.pawn.request_unregistration()
        self.weapon.request_unregistration()
        self.camera.request_unregistration()

        self.camera.unpossessed()
        self.weapon.unpossessed()
        self.unpossess()

        self.camera = self.weapon = None

    def hear_voice(self, info, voice):
        pass

    def possess(self, replicable):
        self.pawn = replicable
        self.pawn.possessed_by(self)
        self.info.pawn = replicable

        self.on_pawn_updated()

    def server_fire(self):
        self.weapon.fire(self.camera)

        # Update flash count (for client-side fire effects)
        self.pawn.flash_count += 1
        if self.pawn.flash_count > 255:
            self.pawn.flash_count = 0

        for controller in WorldInfo.subclass_of(Controller):
            if controller == self:
                continue

            controller.hear_sound(self.weapon.shoot_sound,
                                self.pawn.position)

    def set_camera(self, camera):
        self.camera = camera
        self.camera.possessed_by(self)

        self.on_camera_updated()

    def set_weapon(self, weapon):
        self.weapon = weapon
        self.weapon.possessed_by(self)
        self.pawn.weapon_attachment_class = weapon.attachment_class

    def unpossess(self):
        self.pawn.unpossessed()
        self.info.pawn = self.pawn = None

        self.on_pawn_updated()


class ReplicableInfo(Replicable):
    roles = Attribute(Roles(Roles.authority, Roles.simulated_proxy))

    def on_initialised(self):
        super().on_initialised()

        self.always_relevant = True


class AIReplicationInfo(ReplicableInfo):

    pawn = Attribute(type_of=Replicable, complain=True)

    def conditions(self, is_owner, is_complain, is_initial):
        yield from super().conditions(is_owner, is_complain, is_initial)

        if is_complain:
            yield "pawn"


class PlayerReplicationInfo(AIReplicationInfo):

    name = Attribute("", complain=True)
    ping = Attribute(0.0)

    def conditions(self, is_owner, is_complain, is_initial):
        yield from super().conditions(is_owner, is_complain, is_initial)

        if is_complain:
            yield "name"

        yield "ping"


class AIController(Controller):

    def get_visible(self, ignore_self=True):
        if not self.camera:
            return

        sees = self.camera.sees_actor
        my_pawn = self.pawn

        for actor in WorldInfo.subclass_of(Pawn):
            if (actor == my_pawn and ignore_self):
                continue

            elif sees(actor):
                return actor

    def unpossess(self):
        self.behaviour.reset()
        self.behaviour.blackboard['controller'] = self

        super().unpossess()

    def hear_sound(self, sound_path, source):
        if not (self.pawn and self.camera):
            return
        return
        probability = utilities.falloff_fraction(self.pawn.position,
                            self.hear_range,
                            source,
                            self.effective_hear_range)

    def on_initialised(self):
        super().on_initialised()

        self.camera_mode = enums.CameraMode.first_person
        self.behaviour = behaviour_tree.BehaviourTree(self)
        self.behaviour.blackboard['controller'] = self

    @UpdateSignal.global_listener
    def update(self, delta_time):
        self.behaviour.update()


class PlayerController(Controller):
    '''Player pawn controller network object'''

    input_fields = []
    config_filepath = "inputs.conf"

    max_position_difference_squared = 1
    max_rotation_difference = (2 * math.pi) / 60

    @property
    def mouse_delta(self):
        '''Returns the mouse movement since the last tick'''
        mouse = logic.mouse

        # The first tick the mouse won't be centred
        screen_center = (0.5, 0.5)
        mouse_position, mouse.position = mouse.position, screen_center
        epsilon = self._mouse_epsilon
        smooth_factor = self.mouse_smoothing

        # If we have already initialised the mouse
        if self._mouse_delta is not None:
            mouse_diff_x = screen_center[0] - mouse_position[0]
            mouse_diff_y = screen_center[1] - mouse_position[1]

            smooth_x = utilities.lerp(self._mouse_delta[0],
                                    mouse_diff_x, smooth_factor)
            smooth_y = utilities.lerp(self._mouse_delta[1],
                                    mouse_diff_y, smooth_factor)
        else:
            smooth_x = smooth_y = 0.0

        # Handle near zero values (must be set to a number above zero)
        if abs(smooth_x) < epsilon:
            smooth_x = epsilon / 1000
        if abs(smooth_y) < epsilon:
            smooth_y = epsilon / 1000

        self._mouse_delta = smooth_x, smooth_y
        return smooth_x, smooth_y

    def apply_move(self, inputs, mouse_diff_x, mouse_diff_y):
        blackboard = self.behaviour.blackboard

        blackboard['inputs'] = inputs
        blackboard['mouse'] = mouse_diff_x, mouse_diff_y

        self.behaviour.update()

    def broadcast_voice(self):
        '''Dump voice information and encode it for the server'''
        data = self.microphone.encode()
        if data:
            self.send_voice_server(data)

    @requires_netmode(Netmodes.server)
    def calculate_ping(self):
        if not self.is_locked("ping"):
            self.client_reply_ping(WorldInfo.tick)
            self.server_add_lock("ping")

    def client_adjust_tick(self) -> Netmodes.client:
        self.server_remove_lock("clock")
        self.client_request_time(WorldInfo.elapsed)

    def client_acknowledge_move(self, move_tick: TypeFlag(int,
                                max_value=WorldInfo._MAXIMUM_TICK)) -> Netmodes.client:
        if not self.pawn:
            print("Could not find Pawn for {}".format(self))
            return

        try:
            self.pending_moves.pop(move_tick)

        except KeyError:
            print("Couldn't find move to acknowledge for move {}"
                .format(move_tick))
            return

        additional_keys = [k for k in self.pending_moves if k < move_tick]

        for key in additional_keys:
            self.pending_moves.pop(key)

        return True

    def client_apply_correction(self, correction_tick: TypeFlag(int,
                               max_value=WorldInfo._MAXIMUM_TICK),
                                correction: TypeFlag(structs.RigidBodyState)) -> Netmodes.client:
        # Remove the lock at this network tick on server
        self.server_remove_buffered_lock(WorldInfo.tick, "correction")

        if not self.pawn:
            print("Could not find Pawn for {}".format(self))
            return

        if not self.client_acknowledge_move(correction_tick):
            print("No move found")
            return

        signals.PhysicsCopyState.invoke(correction, self.pawn)
        print("{}: Correcting prediction for move {}".format(self,
                                                             correction_tick))

        # Input Interface
        lookup_dict = {}

        # Input data
        input_manager = self.inputs

        # Get ordered event codes
        keybindings = input_manager._keybindings_to_events
        keybinding_codes = [keybindings[name] for name in sorted(keybindings)]

        # Ask input manager to lookup from dict
        get_input = lookup_dict.__getitem__

        # State call-backs
        apply_move = self.apply_move
        update_physics = partial(signals.PhysicsSingleUpdateSignal.invoke,
                                 1 / WorldInfo.tick_rate, target=self.pawn)

        # Re-apply later moves
        with input_manager.using_interface(get_input):
            # Iterate over all later moves
            for move in self.pending_moves.values():
                # Place inputs into input dict {code: status}
                lookup_dict.update(zip(sorted(keybinding_codes), move.inputs))
                # Apply move inputs
                apply_move(input_manager, move.mouse_x, move.mouse_y)
                # Update Physics world
                update_physics()

    @requires_netmode(Netmodes.client)
    def client_fire(self):
        self.pawn.weapon_attachment.play_fire_effects()
        self.hear_sound(self.weapon.shoot_sound, self.pawn.position)
        self.weapon.fire(self.camera)

    def client_nudge_clock(self, difference:TypeFlag(int, max_value=WorldInfo._MAXIMUM_TICK),
                           forward: TypeFlag(bool)) -> Netmodes.client:
        # Update clock
        WorldInfo.elapsed += (difference if forward else -difference) / WorldInfo.tick_rate

        # Reply received correction
        self.server_remove_buffered_lock(WorldInfo.tick, "clock_synch")

    def client_reply_ping(self, tick: TypeFlag(int, max_value=WorldInfo._MAXIMUM_TICK)) -> Netmodes.client:
        self.server_deduce_ping(tick)

    @requires_netmode(Netmodes.client)
    def client_send_move(self):
        # Get move information
        current_tick = WorldInfo.tick
        try:
            move = self.pending_moves[current_tick]
        except KeyError:
            return

        self.server_store_move(current_tick, self.inputs,
                               move.mouse_x,
                               move.mouse_y,
                               self.pawn.position,
                               self.pawn.rotation)

    @requires_netmode(Netmodes.client)
    def destroy_microphone(self):
        del self.microphone
        for key in list(self.sound_channels):
            del self.sound_channels[key]

    def get_clock_correction(self, current_tick, command_tick):
        return int((current_tick - command_tick) * self.clock_convergence_factor)

    def get_corrected_state(self, position, rotation):
        '''Finds difference between local state and remote state

        :param position: position of state
        :param rotation: rotation of state
        :returns: None if state is within safe limits else correction'''
        pos_difference = self.pawn.position - position

        if pos_difference.length_squared <= self.max_position_difference_squared:
            return

        # Create correction if neccessary
        correction = structs.RigidBodyState()
        signals.PhysicsCopyState.invoke(self.pawn, correction)

        return correction

    def hear_sound(self, sound_path: TypeFlag(str),
                   source: TypeFlag(mathutils.Vector)) -> Netmodes.client:
        if not (self.pawn and self.camera):
            return

        probability = utilities.falloff_fraction(self.pawn.position,
                                                self.hear_range, source,
                                                self.effective_hear_range)
        return
        factory = aud.Factory.file(sound_path)
        return aud.device().play(factory)

    def hear_voice(self, info: TypeFlag(Replicable),
                        data: TypeFlag(bytes, max_length=2**32 - 1)) -> Netmodes.client:
        player = self.sound_channels[info]
        player.decode(data)

    def is_locked(self, name):
        return name in self.locks

    def load_keybindings(self):
        '''Read config file for keyboard inputs
        Looks for config file with "ClassName.conf" in config filepath

        :returns: keybindings'''
        bindings = configuration.load_configuration(self.config_filepath,
                                    self.__class__.__name__,
                                    self.input_fields)
        print("Loaded {} keybindings".format(len(bindings)))
        return bindings

    def on_initialised(self):
        super().on_initialised()

        self.pending_moves = OrderedDict()

        self.mouse_smoothing = 0.6
        self._mouse_delta = None
        self._mouse_epsilon = 0.001

        self.behaviour = behaviour_tree.BehaviourTree(self,
                              default={"controller": self})

        self.locks = set()
        self.buffered_locks = FactoryDict(dict,
                                          dict_type=OrderedDict,
                                          provide_key=False)

        self.buffer = deque()

        self.clock_convergence_factor = 1.0
        self.maximum_clock_ahead = int(0.05 * WorldInfo.tick_rate)

        self.ping_influence_factor = 0.8
        self.ping_timer = timer.Timer(1.0, on_target=self.calculate_ping,
                                    repeat=True)

        self.setup_input()
        self.setup_microphone()

    def on_notify(self, name):
        if name == "pawn":
            # Register as child for signals
            self.on_pawn_updated()

        elif name == "camera":
            self.on_camera_updated()
            self.camera.active = True

        else:
            super().on_notify(name)

    def on_pawn_updated(self):
        super().on_pawn_updated()

        self.behaviour.reset()

    def on_unregistered(self):
        super().on_unregistered()
        self.destroy_microphone()

    @signals.PlayerInputSignal.global_listener
    def player_update(self, delta_time):
        '''Update function for client instance'''
        if not (self.pawn and self.camera):
            return

        # Control Mouse data
        mouse_diff_x, mouse_diff_y = self.mouse_delta
        current_tick = WorldInfo.tick

        # Apply move inputs
        self.apply_move(self.inputs, mouse_diff_x, mouse_diff_y)

        # Remember move for corrections
        self.pending_moves[current_tick] = Move(current_tick,
                 self.inputs.to_tuple(), mouse_diff_x, mouse_diff_y)
        self.broadcast_voice()

    @signals.PostPhysicsSignal.global_listener
    def post_physics(self):
        '''Post move to server and receive corrections'''
        self.client_send_move()
        self.server_check_move()

    def receive_broadcast(self, message_string: TypeFlag(str)) -> Netmodes.client:
        signals.ReceiveMessage.invoke(message_string)

    def send_voice_server(self, data: TypeFlag(bytes,
                                            max_length=2**32 - 1)) -> Netmodes.server:
        info = self.info
        for controller in WorldInfo.subclass_of(Controller):
            if controller is self:
                continue

            controller.hear_voice(info, data)

    def server_add_buffered_lock(self, tick: TypeFlag(int, max_value=WorldInfo._MAXIMUM_TICK),
                                    name: TypeFlag(str)) -> Netmodes.server:
        '''Add a server lock with respect for the dejittering latency'''
        self.buffered_locks[tick][name] = True

    def server_add_lock(self, name: TypeFlag(str)) -> Netmodes.server:
        '''Flag a variable as locked on the server'''
        self.locks.add(name)

    @requires_netmode(Netmodes.server)
    def server_check_move(self):
        """Check result of movement operation following Physics update"""
        # Get move information
        current_tick = WorldInfo.tick

        # We are forced to acknowledge moves whose base we've already corrected
        if self.is_locked("correction"):
            self.client_acknowledge_move(current_tick)
            return

        # Validate move
        try:
            position, rotation = self.pending_moves[current_tick]

        except KeyError:
            return

        correction = self.get_corrected_state(position, rotation)

        # It was a valid move
        if correction is None:
            self.client_acknowledge_move(current_tick)

        # Send the correction
        else:
            self.server_add_lock("correction")
            self.client_apply_correction(current_tick, correction)

    def server_deduce_ping(self, tick: TypeFlag(int, max_value=WorldInfo._MAXIMUM_TICK)) -> Netmodes.server:
        '''Callback to determine ping for a client
        Called by client_reply_ping(tick)
        Unlocks the ping synchronisation lock

        :param tick: tick from client reply replicated function'''
        tick_delta = (WorldInfo.tick - tick)
        round_trip_time = tick_delta / WorldInfo.tick_rate

        self.info.ping = utilities.approach(self.info.ping, round_trip_time,
                                            self.ping_influence_factor)
        self.server_remove_lock("ping")

    @requires_netmode(Netmodes.server)
    def server_fire(self):
        print("Rolling back by {:.3f} seconds".format(self.info.ping))
        if 0:
            latency_ticks = WorldInfo.to_ticks(self.info.ping) + 1
            signals.PhysicsRewindSignal.invoke(WorldInfo.tick - latency_ticks)

        super().server_fire()

        if 0:
            signals.PhysicsRewindSignal.invoke()

    def server_remove_buffered_lock(self, tick: TypeFlag(int, max_value=WorldInfo._MAXIMUM_TICK),
                                    name: TypeFlag(str)) -> Netmodes.server:
        '''Remove a server lock with respect for the dejittering latency'''
        self.buffered_locks[tick][name] = False

    def server_remove_lock(self, name: TypeFlag(str)) -> Netmodes.server:
        '''Flag a variable as unlocked on the server'''
        try:
            self.locks.remove(name)

        except KeyError as err:
            raise errors.FlagLockingError("{} was not locked".format(name))\
                 from err

    def server_store_move(self, tick: TypeFlag(int, max_value=WorldInfo._MAXIMUM_TICK),
                                inputs: TypeFlag(inputs.InputManager,
                                input_fields=MarkAttribute("input_fields")),
                                mouse_diff_x: TypeFlag(float),
                                mouse_diff_y: TypeFlag(float),
                                position: TypeFlag(mathutils.Vector),
                                rotation: TypeFlag(mathutils.Euler)) -> Netmodes.server:
        '''Store a client move for later processing and clock validation'''

        current_tick = WorldInfo.tick
        target_tick = self.maximum_clock_ahead + current_tick

        # If the move is too early, correct clock
        if tick > target_tick:
            self.update_buffered_locks(tick)
            self.start_clock_correction(target_tick, tick)
            return

        data = (inputs, mouse_diff_x, mouse_diff_y, position, rotation)
        self.buffer.append((tick, data))

    @requires_netmode(Netmodes.client)
    def setup_input(self):
        '''Create the input manager for the client'''
        keybindings = self.load_keybindings()

        self.inputs = inputs.InputManager(keybindings,
                                          inputs.BGEInputStatusLookup())
        print("Created input manager")

    @requires_netmode(Netmodes.client)
    def setup_microphone(self):
        '''Create the microphone for the client'''
        self.microphone = stream.MicrophoneStream()
        self.sound_channels = defaultdict(stream.SpeakerStream)

    def set_name(self, name: TypeFlag(str)) -> Netmodes.server:
        self.info.name = name

    def start_clock_correction(self, current_tick, command_tick):
        '''Initiate client clock correction'''
        if not self.is_locked("clock_synch"):
            tick_difference = self.get_clock_correction(current_tick,
                                                        command_tick)
            self.client_nudge_clock(abs(tick_difference),
                                    forward=current_tick > command_tick)
            self.server_add_lock("clock_synch")

    def start_fire(self):
        if not self.weapon:
            return

        if not self.weapon.can_fire or not self.camera:
            return

        self.server_fire()
        self.client_fire()

    @requires_netmode(Netmodes.server)
    @UpdateSignal.global_listener
    def update(self, delta_time):
        '''Validate client clock and apply moves'''
        # Aim ahead by the jitter buffer size
        current_tick = WorldInfo.tick
        target_tick = self.maximum_clock_ahead + current_tick
        consume_move = self.buffer.popleft

        try:
            tick, (inputs, mouse_diff_x, mouse_diff_y,
                   position, rotation) = self.buffer[0]

        except IndexError:
            return

        # Process any buffered locks
        self.update_buffered_locks(tick)

        # The tick is late, try and run a newer command
        if tick < current_tick:
            # Ensure we check through to the latest tick
            consume_move()

            if self.buffer:
                self.update(delta_time)

            else:
                self.start_clock_correction(target_tick, tick)

        # If the tick is early, wait for it to become valid
        elif tick > current_tick:
            return

        # Else run the move at the present time (it's valid)
        else:
            consume_move()

            if not (self.pawn and self.camera):
                return

            # Apply move inputs
            self.apply_move(inputs, mouse_diff_x, mouse_diff_y)
            # Save expected move results
            self.pending_moves[current_tick] = position, rotation

    def update_buffered_locks(self, tick):
        '''Apply server lock changes for the jitter buffer tick'''
        removed_keys = []
        for tick_, locks in self.buffered_locks.items():
            if tick_ > tick:
                break

            for lock_name, add_lock in locks.items():
                if add_lock:
                    self.server_add_lock(lock_name)
                else:
                    self.server_remove_lock(lock_name)

            removed_keys.append(tick_)

        for key in removed_keys:
            self.buffered_locks.pop(key)


class Actor(Replicable, physics_object.PhysicsObject):
    '''Physics enabled network object'''

    rigid_body_state = Attribute(structs.RigidBodyState(), notify=True)
    roles = Attribute(Roles(Roles.authority, Roles.simulated_proxy),
                    notify=True)

    def conditions(self, is_owner, is_complaint, is_initial):
        yield from super().conditions(is_owner, is_complaint, is_initial)

        remote_role = self.roles.remote

        # If simulated, send rigid body state
        valid_role = ((remote_role == Roles.simulated_proxy) or
                     (remote_role == Roles.autonomous_proxy and not is_owner))
        allowed_physics = ((self.replicate_simulated_physics or is_initial)
                        and (self.replicate_physics_to_owner or not is_owner))

        if valid_role and allowed_physics:
            yield "rigid_body_state"

    def on_initialised(self):
        super().on_initialised()

        self.camera_radius = 1

        self.always_relevant = False
        self.replicate_physics_to_owner = True
        self.replicate_simulated_physics = True

    def on_unregistered(self):
        # Unregister any actor children
        for child in self.children:
            if isinstance(child, ResourceActor):
                continue

            child.request_unregistration()

        super().on_unregistered()

    def on_notify(self, name):
        if name == "rigid_body_state":
            signals.PhysicsReplicatedSignal.invoke(self.rigid_body_state, target=self)
        else:
            super().on_notify(name)

    @simulated
    def trace_ray(self, local_vector):
        target = self.transform * local_vector

        return self.object.rayCast(self.object, target)

    @simulated
    def align_to(self, vector, time=1, axis=enums.Axis.y):
        if not vector.length:
            return
        self.object.alignAxisToVect(vector, axis, time)


class ResourceActor(Actor):
    pass


class Weapon(Replicable):
    roles = Attribute(Roles(Roles.authority, Roles.autonomous_proxy))
    ammo = Attribute(70, notify=True)

    @property
    def can_fire(self):
        return (bool(self.ammo) and (WorldInfo.tick - self.last_fired_tick)
                >= (self.shoot_interval * WorldInfo.tick_rate))

    @property
    def data_path(self):
        return os.path.join(self._data_path, self.__class__.__name__)

    @property
    def shoot_sound(self):
        return os.path.join(self.data_path, "sounds/shoot.wav")

    @property
    def icon_path(self):
        return os.path.join(self.data_path, "icon/icon.tga")

    def consume_ammo(self):
        self.ammo -= 1

    def fire(self, camera):
        self.consume_ammo()
        self.last_fired_tick = WorldInfo.tick

    def conditions(self, is_owner, is_complaint, is_initial):
        yield from super().conditions(is_owner, is_complaint, is_initial)

        yield "ammo"

    def on_initialised(self):
        super().on_initialised()

        self._data_path = logic.expandPath("//data")
        self.shoot_interval = 0.5
        self.last_fired_tick = 0
        self.max_ammo = 70

        self.momentum = 1
        self.maximum_range = 20
        self.effective_range = 10
        self.base_damage = 40

        self.attachment_class = None


class TraceWeapon(Weapon):

    def fire(self, camera):
        super().fire(camera)

        self.trace_shot(camera)

    @requires_netmode(Netmodes.server)
    def trace_shot(self, camera):
        hit_object, hit_position, hit_normal = camera.trace_ray(
                                                self.maximum_range)
        if not hit_object:
            return

        replicable = Actor.from_object(hit_object)

        if replicable == self.owner.pawn or not isinstance(replicable, Pawn):
            return

        hit_vector = (hit_position - camera.position)
        falloff = utilities.falloff_fraction(camera.position,
                                    self.maximum_range,
                                    hit_position, self.effective_range)
        damage = self.base_damage * falloff
        momentum = self.momentum * hit_vector.normalized() * falloff

        signals.ActorDamagedSignal.invoke(damage, self.owner, hit_position,
                                momentum, target=replicable)


class ProjectileWeapon(Weapon):

    def on_initialised(self):
        super().on_initialised()

        self.projectile_class = None
        self.projectile_velocity = mathutils.Vector()

    def fire(self, camera):
        super().fire(camera)

        self.projectile_shot(camera)

    @requires_netmode(Netmodes.server)
    def projectile_shot(self, camera):
        projectile = self.projectile_class()
        forward_vector = mathutils.Vector((0, 1, 0))
        forward_vector.rotate(camera.rotation)
        projectile.position = camera.position + forward_vector * 6.0
        projectile.rotation = camera.rotation.copy()
        projectile.velocity = self.projectile_velocity
        projectile.possessed_by(self)


class EmptyWeapon(Weapon):

    ammo = Attribute(0)

    def on_initialised(self):
        super().on_initialised()

        self.attachment_class = EmptyAttatchment


class WeaponAttachment(Actor):

    roles = Attribute(Roles(Roles.authority, Roles.none))

    def on_initialised(self):
        super().on_initialised()

        self.replicate_simulated_physics = False

    def play_fire_effects(self):
        pass


class EmptyAttatchment(WeaponAttachment):

    entity_name = "Empty.002"


class Camera(Actor):

    entity_class = object_types.CameraObject
    entity_name = "Camera"

    roles = Attribute(Roles(Roles.authority, Roles.autonomous_proxy),
                    notify=True)

    @property
    def active(self):
        return self.object == logic.getCurrentScene().active_camera

    @active.setter
    def active(self, status):
        if status:
            logic.getCurrentScene().active_camera = self.object

    @property
    def lens(self):
        return self.object.lens

    @lens.setter
    def lens(self, value):
        self.object.lens = value

    @property
    def fov(self):
        return self.object.fov

    @fov.setter
    def fov(self, value):
        self.object.fov = value

    @property
    def mode(self):
        return self._mode

    @mode.setter
    def mode(self, mode):
        if mode == self._mode:
            return

        if mode == enums.CameraMode.first_person:
            self.local_position = mathutils.Vector()

        else:
            self.local_position = mathutils.Vector((0, -self.gimbal_offset, 0))

        self.local_rotation = mathutils.Euler()
        self._mode = mode

    @property
    def rotation(self):
        rotation = mathutils.Euler((-math.radians(90), 0, 0))
        rotation.rotate(self.object.worldOrientation)
        return rotation

    @rotation.setter
    def rotation(self, rot):
        rotation = mathutils.Euler((math.radians(90), 0, 0))
        rotation.rotate(rot)
        self.object.worldOrientation = rotation

    @property
    def local_rotation(self):
        rotation = mathutils.Euler((-math.radians(90), 0, 0))
        rotation.rotate(self.object.localOrientation)
        return rotation

    @local_rotation.setter
    def local_rotation(self, rot):
        rotation = mathutils.Euler((math.radians(90), 0, 0))
        rotation.rotate(rot)
        self.object.localOrientation = rotation

    @contextmanager
    def active_context(self):
        cam = self.object
        scene = cam.scene

        old_camera = scene.active_camera
        scene.active_camera = cam
        yield
        if old_camera:
            scene.active_camera = old_camera

    def draw(self):
        '''Draws a colourful 3D camera object to the screen'''
        orientation = self.rotation.to_matrix()
        circle_size = 0.20
        upwards_orientation = orientation * mathutils.Matrix.Rotation(math.radians(90),
                                                            3, "X")
        upwards_vector = mathutils.Vector(upwards_orientation.col[1])

        sideways_orientation = orientation * mathutils.Matrix.Rotation(math.radians(-90),
                                                            3, "Z")
        sideways_vector = (mathutils.Vector(sideways_orientation.col[1]))
        forwards_vector = mathutils.Vector(orientation.col[1])

        draw_tools.draw_arrow(self.position, orientation, colour=[0, 1, 0])
        draw_tools.draw_arrow(self.position + upwards_vector * circle_size,
                upwards_orientation, colour=[0, 0, 1])
        draw_tools.draw_arrow(self.position + sideways_vector * circle_size,
                sideways_orientation, colour=[1, 0, 0])
        draw_tools.draw_circle(self.position, orientation, circle_size)
        draw_tools.draw_box(self.position, orientation)
        draw_tools.draw_square_pyramid(self.position + forwards_vector * 0.4, orientation,
                            colour=[1, 1, 0], angle=self.fov, incline=False)

    def on_initialised(self):
        super().on_initialised()

        self._mode = None

        self.gimbal_offset = 2.0
        self.mode = enums.CameraMode.first_person

    def sees_actor(self, actor):
        '''Determines if actor is visible to camera

        :param actor: Actor subclass
        :returns: condition result'''
        try:
            radius = actor.camera_radius
        except AttributeError:
            radius = 0.0

        if radius < 0.5:
            return self.object.pointInsideFrustum(actor.position)

        return self.object.sphereInsideFrustum(actor.position, radius) != self.object.OUTSIDE

    def trace(self, x_coord, y_coord, distance=0):
        return self.object.getScreenRay(x_coord, y_coord, distance)

    def trace_ray(self, distance=0):
        target = self.transform * mathutils.Vector((0, 0, -distance))
        return self.object.rayCast(target, self.position, distance)

    @UpdateSignal.global_listener
    @simulated
    def update(self, delta_time):
        if self.visible:
            self.draw()


class Pawn(Actor):
    # Network Attributes
    alive = Attribute(True, notify=True, complain=True)
    flash_count = Attribute(0)
    health = Attribute(100, notify=True, complain=True)
    roles = Attribute(Roles(Roles.authority,
                             Roles.autonomous_proxy),
                      notify=True)
    view_pitch = Attribute(0.0)
    weapon_attachment_class = Attribute(type_of=type(Replicable),
                                        notify=True,
                                        complain=True)

    def conditions(self, is_owner, is_complaint, is_initial):
        yield from super().conditions(is_owner, is_complaint, is_initial)

        if not is_owner:
            yield "view_pitch"
            yield "flash_count"

        if is_complaint:
            yield "weapon_attachment_class"
            yield "alive"

            if is_owner:
                yield "health"

    @simulated
    def create_weapon_attachment(self, cls):
        self.weapon_attachment = cls()
        self.weapon_attachment.set_parent(self, "weapon")

        if self.weapon_attachment is not None:
            self.weapon_attachment.unpossessed()
        self.weapon_attachment.possessed_by(self)

        self.weapon_attachment.local_position = mathutils.Vector()
        self.weapon_attachment.local_rotation = mathutils.Euler()

    @simulated
    def get_animation_frame(self, layer=0):
        return int(self.skeleton.getActionFrame(layer))

    @simulated
    def is_playing_animation(self, layer=0):
        return self.skeleton.isPlayingAction(layer)

    @property
    def on_ground(self):
        for collider in self._registered:
            if not self.from_object(collider):
                return True
        return False

    def on_initialised(self):
        super().on_initialised()

        self.weapon_attachment = None

        # Non owner attributes
        self.last_flash_count = 0

        self.walk_speed = 4.0
        self.run_speed = 7.0
        self.turn_speed = 1.0
        self.replication_update_period = 1 / 60

        self.animations = behaviour_tree.BehaviourTree(self)
        self.animations.blackboard['pawn'] = self

    @simulated
    def on_notify(self, name):
        # play weapon effects
        if name == "weapon_attachment_class":
            self.create_weapon_attachment(self.weapon_attachment_class)

        else:
            super().on_notify(name)

    @simulated
    def play_animation(self, name, start, end, layer=0, priority=0, blend=0,
                    mode=enums.AnimationMode.play, weight=0.0, speed=1.0,
                    blend_mode=enums.AnimationBlend.interpolate):

        # Define conversions from Blender animations to Network animation enum
        ge_mode = {enums.AnimationMode.play: logic.KX_ACTION_MODE_PLAY,
                enums.AnimationMode.loop: logic.KX_ACTION_MODE_LOOP,
                enums.AnimationMode.ping_pong: logic.KX_ACTION_MODE_PING_PONG
                }[mode]
        ge_blend_mode = {enums.AnimationBlend.interpolate: logic.KX_ACTION_BLEND_BLEND,
                        enums.AnimationBlend.add: logic.KX_ACTION_BLEND_ADD}[blend_mode]

        self.skeleton.playAction(name, start, end, layer, priority, blend,
                                ge_mode, weight, speed=speed,
                                blend_mode=ge_blend_mode)

    @simulated
    def stop_animation(self, layer=0):
        self.skeleton.stopAction(layer)

    @property
    def skeleton(self):
        for child in self.object.childrenRecursive:
            if isinstance(child, types.BL_ArmatureObject):
                return child

    @signals.ActorDamagedSignal.listener
    def take_damage(self, damage, instigator, hit_position, momentum):
        self.health = int(max(self.health - damage, 0))

    @simulated
    @UpdateSignal.global_listener
    def update(self, delta_time):
        if self.weapon_attachment:
            self.update_weapon_attachment()

        # Allow remote players to determine if we are alive without seeing health
        self.update_alive_status()
        self.animations.update()

    def update_alive_status(self):
        '''Update health boolean
        Runs on authority / autonomous proxy only'''
        self.alive = self.health > 0

    @simulated
    def update_weapon_attachment(self):
        # Account for missing shots
        if self.flash_count != self.last_flash_count:
            # Protect from wrap around
            if self.last_flash_count > self.flash_count:
                self.last_flash_count = -1

            self.weapon_attachment.play_fire_effects()
            self.last_flash_count += 1

        self.weapon_attachment.local_rotation = mathutils.Euler(
                                                        (self.view_pitch, 0, 0)
                                                        )


class Lamp(Actor):
    roles = Roles(Roles.authority, Roles.simulated_proxy)

    entity_class = object_types.LampObject
    entity_name = "Lamp"

    def on_initialised(self):
        super().on_initialised()

        self._intensity = None

    @property
    def intensity(self):
        return self.object.energy

    @intensity.setter
    def intensity(self, energy):
        self.object.energy = energy

    @property
    def active(self):
        return not self.intensity

    @active.setter
    def active(self, state):
        '''Modifies the lamp state by setting the intensity to a placeholder

        :param state: enabled state'''

        if not (state != (self._intensity is None)):
            return

        if state:
            self._intensity, self.intensity = None, self._intensity
        else:
            self._intensity, self.intensity = self.intensity, None


class Navmesh(Actor):
    roles = Roles(Roles.authority, Roles.none)

    entity_class = object_types.NavmeshObject
    entity_name = "Navmesh"

    def draw(self):
        self.object.draw(logic.RM_TRIS)

    def find_path(self, from_point, to_point):
        return self.object.findPath(from_point, to_point)

    def get_wall_intersection(self, from_point, to_point):
        return self.object.raycast(from_point, to_point)
