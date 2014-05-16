from network.bitfield import BitField
from network.decorators import requires_netmode, simulated
from network.descriptors import Attribute, TypeFlag, MarkAttribute
from network.enums import Netmodes, Roles
from network.iterators import take_single
from network.network_struct import Struct
from network.replicable import Replicable
from network.signals import UpdateSignal
from network.structures import FactoryDict
from network.world_info import WorldInfo

from aud import Factory, device as AudioDevice
from collections import deque, defaultdict, OrderedDict
from functools import partial
from math import pi
from mathutils import Vector, Euler
from operator import attrgetter

from .behaviour_tree import BehaviourTree
from .configuration import load_keybindings
from .clock import PlayerClock
from .constants import MAX_32BIT_INT
from .enums import *
from .errors import FlagLockingError
from .inputs import BGEInputStatusLookup, InputManager, MouseManager
from .jitter_buffer import JitterBuffer
from .object_types import *
from .resources import ResourceManager
from .signals import *
from .stream import MicrophoneStream, SpeakerStream
from .structs import RigidBodyState
from .timer import Timer

__all__ = ['Controller', 'PlayerController', 'AIController']

TICK_FLAG = TypeFlag(int, max_value=WorldInfo._MAXIMUM_TICK)


class Controller(Replicable):

    roles = Attribute(Roles(Roles.authority, Roles.autonomous_proxy))
    pawn = Attribute(type_of=Replicable, complain=True, notify=True)
    camera = Attribute(type_of=Replicable, complain=True, notify=True)
    weapon = Attribute(type_of=Replicable, complain=True, notify=True)
    info = Attribute(type_of=Replicable, complain=True)

    def attach_camera(self, camera):
        """Connects camera to pawn

        :param camera: camera instance"""
        self._camera = camera
        camera.set_parent(self.pawn, "camera")
        camera.local_position = Vector()

    def conditions(self, is_owner, is_complaint, is_initial):
        yield from super().conditions(is_owner, is_complaint, is_initial)

        if is_complaint:
            yield "pawn"
            yield "camera"
            yield "weapon"
            yield "info"

    def hear_voice(self, info, voice):
        pass

    def on_camera_replicated(self, camera):
        """Called when camera attribute is replicated

        :param camera: camera attribute"""
        if camera:
            self.attach_camera(camera)
            camera.active = True

        else:
            self.remove_camera()

    def on_initialised(self):
        super().on_initialised()

        self.hear_range = 15
        self.effective_hear_range = 10
        self.replication_priority = 2.0

        self._camera = None
        self._pawn = None

    def on_pawn_replicated(self, pawn):
        if pawn:
            self.register_listener_to_pawn(pawn)

        else:
            self.unregister_listener_to_pawn()

    def on_unregistered(self):
        # Remove player pawn
        self.forget_pawn()

        # The player is gone, remove info
        if self.info:
            self.info.request_unregistration()

        super().on_unregistered()

    @requires_netmode(Netmodes.server)
    def possess(self, replicable):
        self.pawn = replicable
        self.pawn.possessed_by(self)

        # Setup lookups
        self.info.pawn = replicable
        self.pawn.info = self.info

        self.register_listener_to_pawn(replicable)

    def register_listener_to_pawn(self, pawn):
        """Registers as listener for pawn events

        :param pawn: pawn instance"""
        self._pawn = pawn
        pawn.register_child(self, greedy=True)

    def remove_camera(self):
        """Disconnects camera from pawn"""
        self._camera.remove_parent()
        self._camera = None

    def forget_pawn(self):
        if self.pawn:
            self.pawn.request_unregistration()
            self.unpossess()

        if self.weapon:
            self.weapon.request_unregistration()
            self.weapon.unpossessed()

        if self.camera:
            self.camera.request_unregistration()
            self.camera.unpossessed()

        self.camera = self.weapon = None

    @requires_netmode(Netmodes.server)
    def server_fire(self):
        self.weapon.fire(self.camera)

        # Update flash count (for client-side fire effects)
        self.pawn.flash_count += 1
        if self.pawn.flash_count > 255:
            self.pawn.flash_count = 0

        weapon_sound = self.weapon.resources['sounds'][self.weapon.shoot_sound]

        for controller in WorldInfo.subclass_of(Controller):
            if controller == self:
                continue

            controller.hear_sound(weapon_sound,
                                self.pawn.position,
                                self.pawn.rotation,
                                self.pawn.velocity)

    @requires_netmode(Netmodes.server)
    def set_camera(self, camera):
        self.camera = camera
        self.camera.possessed_by(self)

        self.attach_camera(camera)

    @requires_netmode(Netmodes.server)
    def set_weapon(self, weapon):
        self.weapon = weapon
        self.weapon.possessed_by(self)
        self.pawn.weapon_attachment_class = weapon.attachment_class

    @requires_netmode(Netmodes.server)
    def unpossess(self):
        self.unregister_listener_to_pawn()

        self.info.pawn = None
        self.pawn.info = None

        self.pawn.unpossessed()
        self.pawn = None

    def unregister_listener_to_pawn(self):
        """Unregisters as listener for pawn events"""
        self._pawn.unregister_child(self, greedy=True)
        self._pawn = None


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

    def hear_sound(self, sound_path, position, rotation, velocity):
        if not (self.pawn and self.camera):
            return
        return

    def on_initialised(self):
        super().on_initialised()

        self.camera_mode = CameraMode.first_person
        self.behaviour = behaviour_tree.BehaviourTree(self)
        self.behaviour.blackboard['controller'] = self

    @UpdateSignal.global_listener
    def update(self, delta_time):
        self.behaviour.update()


class PlayerController(Controller):
    '''Player pawn controller network object'''

    movement_struct = None
    config_filepath = "inputs.conf"

    max_position_difference_squared = 0.5
    max_rotation_difference_squared = ((2 * pi) / 60) ** 2

    def apply_move(self, move):
        blackboard = self.behaviour.blackboard

        blackboard['inputs'] = move.inputs
        blackboard['mouse'] = move.mouse_x, move.mouse_y

        self.behaviour.update()

    def broadcast_voice(self):
        '''Dump voice information and encode it for the server'''
        data = self.microphone.encode()
        if data:
            self.send_voice_server(data)

    def client_acknowledge_move(self, move_id: TICK_FLAG) -> Netmodes.client:
        if not self.pawn:
            print("Could not find Pawn for {}".format(self))
            return

        remove_move = self.pending_moves.pop

        try:
            remove_move(move_id)

        except KeyError:
            # We don't mind if we've handled it already
            if move_id < take_single(self.pending_moves):
                return

            print("Couldn't find move to acknowledge for move {}"
                  .format(move_id))
            return

        # Remove any older moves
        older_moves = [k for k in self.pending_moves if k < move_id]

        for move_id in older_moves:
            remove_move(move_id)

        return True

    def client_apply_correction(self, correction_id: TICK_FLAG,
                    correction: TypeFlag(RigidBodyState)) -> Netmodes.client:

        # Remove the lock at this network tick on server
        self.server_remove_buffered_lock(correction_id, "correction")

        if not self.pawn:
            print("Could not find Pawn for {}".format(self))
            return

        if not self.client_acknowledge_move(correction_id):
            return

        PhysicsCopyState.invoke(correction, self.pawn)

        print("{}: Correcting prediction for move {}".format(self,
                                                             correction_id))

        # State call-backs
        apply_move = self.apply_move
        update_physics = partial(PhysicsSingleUpdateSignal.invoke,
                                 1 / WorldInfo.tick_rate, target=self.pawn)

        # Iterate over all later moves and re-apply them
        for move in self.pending_moves.values():
            # Apply move inputs
            apply_move(move)
            # Update Physics world
            update_physics()

    @requires_netmode(Netmodes.client)
    def client_fire(self):
        self.pawn.weapon_attachment.play_fire_effects()

        weapon_sounds = self.weapon.resources['sounds']
        shoot_sound = weapon_sounds[self.weapon.shoot_sound]

        self.hear_sound(shoot_sound,
                        self.pawn.position, self.pawn.rotation,
                        self.pawn.velocity)
        self.weapon.fire(self.camera)

    @requires_netmode(Netmodes.client)
    def client_setup_input(self):
        '''Create the input manager for the client'''
        keybindings = self.load_keybindings()

        self.inputs = InputManager(keybindings, BGEInputStatusLookup())
        self.mouse = MouseManager(interpolation=0.6)

        print("Created User Input Managers")

    @requires_netmode(Netmodes.client)
    def client_setup_sound(self):
        '''Create the microphone for the client'''
        self.microphone = MicrophoneStream()
        self.audio = AudioDevice()
        self.voice_channels = defaultdict(SpeakerStream)

    @requires_netmode(Netmodes.client)
    def client_send_move(self):
        """Sends a Move to the server for simulation"""

        move = self.current_move

        if move is None:
            print("No move!")
            return

        # Post physics state copying
        move.position = self.pawn.position.copy()
        move.rotation = self.pawn.rotation.copy()

        # Check move
        self.server_store_move(move, self.previous_move)

    @staticmethod
    def create_movement_struct(*fields):
        attributes = {}

        MAXIMUM_TICK = WorldInfo._MAXIMUM_TICK

        attributes['input_fields'] = fields
        attributes['inputs'] = Attribute(type_of=InputManager, fields=fields)
        attributes['mouse_x'] = Attribute(0.0)
        attributes['mouse_y'] = Attribute(0.0)
        attributes['position'] = Attribute(type_of=Vector)
        attributes['rotation'] = Attribute(type_of=Euler)
        attributes['id'] = Attribute(type_of=int, max_value=MAXIMUM_TICK)

        attributes['__slots__'] = Struct.__slots__.copy()

        return type("MovementStruct", (Struct,), attributes)

    @requires_netmode(Netmodes.client)
    def destroy_microphone(self):

        del self.microphone
        for key in list(self.voice_channels):
            del self.voice_channels[key]

    def get_corrected_state(self, move):
        """Finds difference between local state and remote state

        :param position: position of state
        :param rotation: rotation of state
        :returns: None if state is within safe limits else correction
        """
        pos_difference = self.pawn.position - move.position
        rot_difference = (self.pawn.rotation[-1] - move.rotation[-1]) ** 2
        rot_difference = min(rot_difference, (4 * (pi ** 2)) - rot_difference)

        position_invalid = (pos_difference.length_squared >
                            self.max_position_difference_squared)
        rotation_invalid = (rot_difference >
                            self.max_rotation_difference_squared)
        if not (position_invalid or rotation_invalid):
            return

        # Create correction if neccessary
        correction = RigidBodyState()
        PhysicsCopyState.invoke(self.pawn, correction)

        return correction

    @staticmethod
    @requires_netmode(Netmodes.client)
    def get_local_controller():
        return take_single(WorldInfo.subclass_of(PlayerController))

    def hear_sound(self, resource: TypeFlag(str),
                   position: TypeFlag(Vector),
                   rotation: TypeFlag(Euler),
                   velocity: TypeFlag(Vector)) -> Netmodes.client:
        if not (self.pawn and self.camera):
            return

        sound_path = ResourceManager.from_relative_path(resource)

        factory = Factory.file(sound_path)
        handle = self.audio.play(factory)

        source_to_pawn = (self.pawn.position - position).normalized()
        forward = Vector((0, 1, 0))

        handle.location = position
        handle.velocity = velocity
        handle.orientation = forward.rotation_difference(source_to_pawn)

    def hear_voice(self, info: TypeFlag(Replicable),
                   data: TypeFlag(bytes, max_length=MAX_32BIT_INT)) -> Netmodes.client:
        player = self.voice_channels[info]
        player.decode(data)

    def is_locked(self, name):
        return name in self.locks

    def load_keybindings(self):
        '''Read config file for keyboard inputs
        Looks for config file with "ClassName.conf" in config filepath

        :returns: keybindings'''
        class_name = self.__class__.__name__
        assert self.movement_struct, "Move struct was not specified for {}"\
                                    .format(self.__class__)

        bindings = load_keybindings(self.config_filepath,
                                    class_name,
                                    self.movement_struct.input_fields)

        print("Loaded {} key-bindings for {}".format(len(bindings), class_name))

        return bindings

    def on_initialised(self):
        super().on_initialised()

        self.pending_moves = OrderedDict()
        self.current_move = None
        self.previous_move = None

        self.behaviour = BehaviourTree(self,
                              default={"controller": self})

        self.locks = set()
        self.buffered_locks = FactoryDict(dict,
                                          dict_type=OrderedDict,
                                          provide_key=False)

        # Permit move recovery by sending two moves
        self._previous_moves = {}
        recover_previous_move = self._previous_moves.__getitem__

        # Number of moves to buffer
        buffer_length = WorldInfo.to_ticks(0.1)
        get_move_id = attrgetter("id")

        # Queued moves
        self.buffered_moves = JitterBuffer(buffer_length, get_move_id, recover_previous_move)

        self.client_setup_input()
        self.client_setup_sound()
        self.server_setup_clock()

    def on_notify(self, name):
        if name == "pawn":
            # Register as child for signals
            self.on_pawn_replicated(self.pawn)

        elif name == "camera":
            self.on_camera_replicated(self.camera)

        else:
            super().on_notify(name)

    def on_pawn_updated(self):
        super().on_pawn_updated()

        self.behaviour.reset()

    def on_unregistered(self):
        super().on_unregistered()

        self.destroy_microphone()

    @PlayerInputSignal.global_listener
    def player_update(self, delta_time):
        '''Update function for client instance'''
        if not (self.pawn and self.camera):
            return

        # Update audio
        self.audio.listener_location = self.pawn.position
        self.audio.listener_velocity = self.pawn.velocity
        self.audio.listener_orientation = self.pawn.rotation.to_quaternion()
        self.audio.distance_model = __import__("aud").AUD_DISTANCE_MODEL_LINEAR

        self.mouse.update()

        # Get input data
        mouse_diff_x, mouse_diff_y = self.mouse.delta_position

        # Create movement
        latest_move = self.movement_struct()

        # Populate move
        latest_move.id = (self.current_move.id + 1) if self.current_move else 0
        latest_move.inputs = self.inputs.copy()
        latest_move.mouse_x = mouse_diff_x
        latest_move.mouse_y = mouse_diff_y

        # Apply move inputs
        self.apply_move(latest_move)

        # Remember move for corrections
        self.pending_moves[latest_move.id] = latest_move

        self.previous_move = self.current_move
        self.current_move = latest_move

        self.broadcast_voice()

    @PostPhysicsSignal.global_listener
    def post_physics(self):
        '''Post move to server and receive corrections'''
        self.client_send_move()
        self.server_check_move()

    def receive_broadcast(self, message_string: TypeFlag(str)) -> Netmodes.client:
        ReceiveMessage.invoke(message_string)

    def send_voice_server(self, data: TypeFlag(bytes, max_length=MAX_32BIT_INT)) -> Netmodes.server:
        info = self.info
        for controller in WorldInfo.subclass_of(Controller):
            if controller is self:
                continue

            controller.hear_voice(info, data)

    def server_add_buffered_lock(self, tick: TICK_FLAG,
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
        current_move = self.current_move

        # We are forced to acknowledge moves whose base we've already corrected
        if self.is_locked("correction"):
            return

        # Validate move
        if current_move is None:
            return
        correction = self.get_corrected_state(current_move)

        if current_move.inputs.debug:
            correction = RigidBodyState()
            PhysicsCopyState.invoke(self.pawn, correction)

        # It was a valid move
        if correction is None:
            self.client_acknowledge_move(current_move.id)

        # Send the correction
        else:
            self.server_add_lock("correction")
            self.client_apply_correction(current_move.id, correction)

    @requires_netmode(Netmodes.server)
    def server_fire(self):
        print("Rolling back by {:.3f} seconds".format(self.info.ping))

        if True:
            latency_ticks = WorldInfo.to_ticks(self.info.ping) + 1
            physics_callback = super().server_fire
            PhysicsRewindSignal.invoke(physics_callback,
                                       WorldInfo.tick - latency_ticks)

        else:
            super().server_fire()

    def server_remove_buffered_lock(self, move_id: TICK_FLAG,
            name: TypeFlag(str)) -> Netmodes.server:
        '''Remove a server lock with respect for the dejittering latency'''
        self.buffered_locks[move_id][name] = False

    def server_remove_lock(self, name: TypeFlag(str)) -> Netmodes.server:
        '''Flag a variable as unlocked on the server'''
        try:
            self.locks.remove(name)

        except KeyError as err:
            raise FlagLockingError("{} was not locked".format(name))\
                 from err

    @requires_netmode(Netmodes.server)
    def server_setup_clock(self):
        self.clock = PlayerClock()
        self.clock.possessed_by(self)

    def server_store_move(self, move: TypeFlag(type_=MarkAttribute("movement_struct")),
                          previous_move: TypeFlag(type_=MarkAttribute("movement_struct"))) -> Netmodes.server:
        '''Store a client move for later processing and clock validation'''

        # Store move
        self.buffered_moves.append(move)
        self._previous_moves[move] = previous_move

    def set_name(self, name: TypeFlag(str)) -> Netmodes.server:
        self.info.name = name

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

        buffered_move = self.buffered_moves.pop()

        if buffered_move is None:
            print("No move!")
            return

        move_id = buffered_move.id

        # Process any buffered locks
        self.update_buffered_locks(move_id)

        if not (self.pawn and self.camera):
            return

        # Apply move inputs
        self.apply_move(buffered_move)

        # Save expected move results
        self.pending_moves[move_id] = buffered_move

        self.current_move = buffered_move

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

