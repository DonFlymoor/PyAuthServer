from network.replicable import Replicable
from network.replication import Serialisable, Pointer
from network.type_serialisers import TypeInfo
from network.annotations.decorators import reliable, requires_netmode, simulated
from network.enums import Netmodes, Roles

from .coordinates import Vector
from .entity import Actor
from .enums import InputButtons
from .latency_compensation import JitterBuffer

from collections import OrderedDict, deque
from logging import getLogger
from math import radians, pi, floor
from os import path
from time import time


class ReplicationInfo(Replicable):
    pawn = Serialisable(data_type=Replicable)
    roles = Serialisable(Roles(Roles.authority, Roles.simulated_proxy))

    def __init__(self, unique_id, scene, is_static=False):
        self.always_relevant = True

    def can_replicate(self, is_owner, is_initial):
        yield from super().can_replicate(is_owner, is_initial)

        yield "pawn"


class PlayerReplicationInfo(ReplicationInfo):
    name = Serialisable("")
    ping = Serialisable(0.0)

    def can_replicate(self, is_owner, is_initial):
        yield from super().can_replicate(is_owner, is_initial)

        yield "name"
        yield "ping"


class Clock(Replicable):

    roles = Serialisable(Roles(Roles.authority, Roles.autonomous_proxy))

    def __init__(self, scene, unique_id, is_static=False):
        if scene.world.netmode == Netmodes.server:
            self.initialise_server()
        else:
            self.initialise_client()

    def initialise_client(self):
        self.nudge_minimum = 0.05
        self.nudge_maximum = 0.4
        self.nudge_factor = 0.8

        self.estimated_elapsed_server = 0.0

    def initialise_server(self):
        self.poll_timer = self.scene.add_timer(1.0, repeat=True)
        self.poll_timer.on_elapsed = self.server_send_clock

    def destroy_client(self):
        super().on_destroyed()

    def destroy_server(self):
        self.scene.remove_timer(self.poll_timer)

        super().on_destroyed()

    def server_send_clock(self):
        self.client_update_clock(WorldInfo.elapsed)

    def client_update_clock(self, elapsed: float) -> Netmodes.client:
        controller = self.owner
        if controller is None:
            return

        info = controller.info

        # Find difference between local and remote time
        difference = self.estimated_elapsed_server - (elapsed + info.ping)
        abs_difference = abs(difference)

        if abs_difference < self.nudge_minimum:
            return

        if abs_difference > self.nudge_maximum:
            self.estimated_elapsed_server -= difference

        else:
            self.estimated_elapsed_server -= difference * self.nudge_factor

    def on_destroyed(self):
        if self.scene.world.netmode == Netmodes.server:
            self.destroy_server()

        else:
            self.destroy_client()

    @property
    def tick(self):
        return floor(self.estimated_elapsed_server * self.scene.world.tick_rate)

    @property
    def sync_interval(self):
        return self.poll_timer.delay

    @sync_interval.setter
    def sync_interval(self, delay):
        self.poll_timer.delay = delay

    @simulated
    def on_tick(self):
        self.estimated_elapsed_server += 1 / self.scene.world.tick_rate


class PawnController(Replicable):
    """Base class for Pawn controllers"""

    roles = Serialisable(Roles(Roles.authority, Roles.autonomous_proxy))
    pawn = Serialisable(data_type=Replicable, notify_on_replicated=True)
    info = Serialisable(data_type=Replicable)

    info_class = ReplicationInfo

    def __init__(self, scene, unique_id, is_static=False):
        self.logger = getLogger(repr(self))

        if scene.world.netmode == Netmodes.server:
            self.initialise_server()
        else:
            self.initialise_client()

    def initialise_server(self):
        self.info = self.scene.add_replicable(self.info_class)

        # When RTT estimate is updated
        self.messenger.add_subscriber("estimated_rtt", self.server_rtt_estimate_updated)

    def server_rtt_estimate_updated(self, rtt_estimate):
        self.info.ping = rtt_estimate / 2

    def initialise_client(self):
        pass

    def can_replicate(self, is_owner, is_initial):
        yield from super().can_replicate(is_owner, is_initial)

        yield "pawn"
        yield "info"

    def on_replicated(self, name):
        if name == "pawn":
            self.on_take_control(self.pawn)

    def on_destroyed(self):
        if self.pawn is not None:
            self.scene.remove_replicable(self.pawn)

        super().on_destroyed()

    def take_control(self, pawn):
        """Take control of pawn

        :param pawn: Pawn instance
        """
        if pawn is self.pawn:
            return

        self.pawn = pawn
        self.on_take_control(self.pawn)

    def release_control(self):
        """Release control of possessed pawn"""
        self.pawn.released_control()
        self.pawn = None

    def on_take_control(self, pawn):
        pawn.controlled_by(self)


class PlayerPawnController(PawnController):
    """Base class for player pawn controllers"""

    clock = Serialisable(data_type=Replicable)

    MAX_POSITION_ERROR_SQUARED = 0.5
    MAX_ORIENTATION_ANGLE_ERROR_SQUARED = radians(5) ** 2

    # input_context = InputContext()
    info_class = PlayerReplicationInfo

    def can_replicate(self, is_owner, is_initial):
        yield from super().can_replicate(is_owner, is_initial)

        yield "clock"

    # info_cls = PlayerReplicationInfo TODO
    def __init__(self, scene, unique_id, is_static=False):
        """Initialisation method"""
        super().__init__(scene, unique_id, is_static)

    def can_replicate(self, is_owner, is_initial):
        yield from super().can_replicate(is_owner, is_initial)

    @reliable
    def client_correct_move(self, move_id: (int, {'max_value': 1000}), position: Vector, yaw: float, velocity: Vector,
                            angular_yaw: float) -> Netmodes.client:
        """Correct previous move which was mis-predicted

        :param move_id: ID of move to correct
        :param position: corrected position
        :param yaw: corrected yaw
        :param velocity: corrected velocity
        :param angular_yaw: corrected angular yaw
        """
        pawn = self.pawn
        if not pawn:
            return

        # Restore pawn state
        pawn.transform.world_position = position
        pawn.physics.world_velocity = velocity

        # Recreate Z rotation
        orientation = pawn.transform.world_orientation
        orientation.z = yaw
        pawn.transform.world_orientation = orientation

        # Recreate Z angular rotation
        angular = pawn.physics.world_angular
        angular.z = angular_yaw
        pawn.physics.world_angular = angular

        process_inputs = self.process_inputs
        sent_states = self.sent_states

        # Correcting move
        self.logger.info("Correcting an invalid move: {}".format(move_id))

        for move_id in range(move_id, self.move_id + 1):
            state = sent_states[move_id]
            buttons, ranges = state.read()

            process_inputs(buttons, ranges)
            # self.scene.physics_manager.update_actor(pawn)

        # Remember this correction, so that older moves are not corrected
        self.latest_correction_id = move_id

    def on_input(self, delta_time, input_manager):
        """Handle local inputs from client
        :param input_manager: input system
        """
        remapped_state = self.input_context.remap_state(input_manager, self.input_map)
        packed_state = self.input_context.network.struct_cls()
        packed_state.write(remapped_state)

        self.move_id += 1
        self.sent_states[self.move_id] = packed_state
        self.recent_states.appendleft(packed_state)

        self.process_inputs(*remapped_state)

    @requires_netmode(Netmodes.client)
    def client_send_move(self):
        """Send inputs, alongside results of applied inputs, to the server"""
        pawn = self.pawn
        if not pawn:
            return

        position = pawn.transform.world_position
        yaw = pawn.transform.world_orientation.z

        self.server_receive_move(self.move_id, self.latest_correction_id, self.recent_states, position, yaw)

    def get_input_map(self):
        """Return keybinding mapping (from actions to buttons)"""
        file_path = path.join(self.__class__.__name__, "input_map.cfg")
        defaults = {n: str(v) for n, v in InputButtons}
        configuration = self.scene.resource_manager.open_configuration(file_path, defaults=defaults)
        bindings = {n: int(v) for n, v in configuration.items() if isinstance(v, str)}
        return bindings

    def initialise_client(self):
        """Initialise client-specific player controller state"""

        self.input_map = self.get_input_map()
        self.move_id = 0
        self.latest_correction_id = 0

        self.sent_states = OrderedDict()
        self.recent_states = deque(maxlen=5)
        
    def initialise_server(self):
        """Initialise server-specific player controller state"""
        self.info = self.__class__.info()
        self.info.possessed_by(self)

        # Network clock
        self.clock = Clock()
        self.clock.possessed_by(self)

        # Network jitter compensation
        ticks = round(0.1 * self.scene.world.tick_rate)
        self.buffer = JitterBuffer(length=ticks)

        # Clienat results of simulating moves
        self.client_moves_states = {}

        # ID of move waiting to be verified
        self.pending_validation_move_id = None
        self.last_corrected_move_id = 0

        self.scene.messenger.add_subscriber("tick", self.on_tick)

    def on_destroyed(self):
        if self.scene.world.netmode == Netmodes.server:
            self.scene.messenger.remove_subscriber("tick", self.on_tick)

    def receive_message(self, message: str, player_info: Replicable) -> Netmodes.client:
        self.scene.messenger.send("message", message=message, player_info=player_info)

    def send_message(self, message: str, info: Replicable=None) -> Netmodes.server:
        self_info = self.info

        # Broadcast to all controllers
        if info is None:
            for replicable in self.scene.replicables.values():
                if not isinstance(replicable, PlayerReplicationInfo):
                    continue

                controller = replicable.owner
                controller.receive_message(message, self_info)

        else:
            controller = info.owner
            controller.receive_message(message, self_info)

    def set_name(self, name: str)->Netmodes.server:
        self.info.name = name

    def update_ping_estimate(self, rtt):
        """Update ReplicationInfo with approximation of connection ping
        :param rtt: round trip time from server to client and back
        """
        self.info.ping = rtt / 2

    def server_receive_move(self, move_id: (int, {'max_value': 1000}), latest_correction_id: (int, {'max_value': 1000}),
                            recent_states: (list, {'element_flag':
                                                       TypeInfo(Pointer("input_context.network.struct_cls"))
                            }),
                            position: Vector, yaw: float) -> Netmodes.server:
        """Handle remote client inputs

        :param move_id: unique ID of move
        :param recent_states: list of recent input states
        """
        push = self.buffer.push

        try:
            for i, state in enumerate(recent_states):
                push(state, move_id - i)

        except KeyError:
            pass

        # Save physics state for this move for later validation
        self.client_moves_states[move_id] = position, yaw, latest_correction_id

    def post_physics(self):
        self.client_send_move()
        self.server_validate_last_move()

    def process_inputs(self, buttons, ranges):
        pass

    @requires_netmode(Netmodes.server)
    def server_validate_last_move(self):
        """Validate result of applied input states.
        Send correction to client if move was invalid.
        """
        # Well, we need a Pawn!
        pawn = self.pawn
        if not pawn:
            return

        # If we don't have a move ID, bail here
        move_id = self.pending_validation_move_id
        if move_id is None:
            return

        # We've handled this
        self.pending_validation_move_id = None

        moves_states = self.client_moves_states

        # Delete old move states
        old_move_ids = [i for i in moves_states if i < move_id]
        for old_move_id in old_move_ids:
            moves_states.pop(old_move_id)

        # Get corrected state
        client_position, client_yaw, client_last_correction = moves_states.pop(move_id)

        # Don't bother checking if we're already checking invalid state
        if client_last_correction < self.last_corrected_move_id:
            return

        # Check predicted position is valid
        position = pawn.transform.world_position
        velocity = pawn.physics.world_velocity
        yaw = pawn.transform.world_orientation.z
        angular_yaw = pawn.physics.world_angular.z

        pos_err = (client_position - position).length_squared > self.__class__.MAX_POSITION_ERROR_SQUARED
        abs_yaw_diff = ((client_yaw - yaw) % pi) ** 2
        rot_err = min(abs_yaw_diff, pi - abs_yaw_diff) > self.__class__.MAX_ORIENTATION_ANGLE_ERROR_SQUARED

        if pos_err or rot_err:
            self.client_correct_move(move_id, position, yaw, velocity, angular_yaw)
            self.last_corrected_move_id = move_id

    @requires_netmode(Netmodes.server)
    def on_tick(self, delta_time):
        try:
            input_state, move_id = next(self.buffer)

        except StopIteration:
            return

        except ValueError as err:
            self.logger.error(err)
            return

        if not self.pawn:
            return

        buttons, ranges = input_state.read()
        self.process_inputs(buttons, ranges)

        self.pending_validation_move_id = move_id


class Pawn(Actor):

    def released_control(self):
        pass

    def controlled_by(self, controller):
        pass