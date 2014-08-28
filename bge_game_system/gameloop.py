from collections import OrderedDict, namedtuple
from contextlib import contextmanager

from network.enums import Netmodes
from network.network import Network
from network.replicable import Replicable
from network.signals import *
from network.world_info import WorldInfo

from game_system.signals import *
from game_system.timer import Timer
from game_system.entities import Camera

from .physics import PhysicsSystem

from bge import types, logic


__all__ = ['GameLoop', 'Server', 'Client', 'RewindState']


RewindState = namedtuple("RewindState", "position rotation animations")

#TODO profile server to determine why slow
#TODO consider other means of sending past moves
#TODO Move away from un handled exceptions in protected (no-return) code
#TODO implement client-side extrapolation
#TODO implement raycast weapons
#TODO rename non-actor signals to PawnSignal....


class GameLoop(types.KX_PythonLogicLoop, SignalListener):

    render = True

    def __init__(self):
        self.register_signals()

        WorldInfo.tick_rate = int(logic.getLogicTicRate())

        # Copy BGE data
        self.use_tick_rate = logic.getUseFrameRate()
        self.animation_rate = logic.getAnimationTicRate()
        self.use_animation_rate = logic.getRestrictAnimationUpdates()

        self.network_scene = next(iter(logic.getSceneList()))
        self.network_scene.post_draw = [self.render_callback]

        # Create sub systems
        self.network_system = self.create_network()
        self.physics_system = PhysicsSystem(self.physics_callback, self.scenegraph_callback)

        # Timing information
        self.current_time = 0.0
        self.last_sent_time = 0
        self.network_tick_rate = 25
        self.metric_interval = 0.10

        # Profile information
        self._state = None

        self.profile = logic.KX_ENGINE_DEBUG_SERVICES
        self.can_quit = SignalValue(self.check_quit())

        # Load world
        Signal.update_graph()
        MapLoadedSignal.invoke()

        print("Network initialised")

    @contextmanager
    def profile_as(self, context_profile):
        """Restores original profile after context collapses

        :param context_profile: profile for this context
        """
        self._state, self.profile = self.profile, context_profile

        yield self._state

        self.profile = self._state

    @property
    def profile(self):
        return self._profile

    @profile.setter
    def profile(self, value):
        self.start_profile(value)

        self._profile = value

    @ReplicableRegisteredSignal.global_listener
    def notify_registration(self, target):
        """Signal listener for replicable instantiation
        Listens for Camera creation to correct camera matrices

        :param target: replicable instance"""
        if isinstance(target, Camera):

            with target.active_context():
                self.update_render()

    def scenegraph_callback(self):
        """Callback for scenegraph update"""
        with self.profile_as(logic.KX_ENGINE_DEBUG_PHYSICS):
            self.update_scenegraph(self.current_time)

    def render_callback(self):
        """Callback for render update"""
        with self.profile_as(logic.KX_ENGINE_DEBUG_RASTERIZER):
            UIRenderSignal.invoke()

    def physics_callback(self, delta_time):
        """Callback for physics simulation

        :param delta_time: time to progress simulation"""
        with self.profile_as(logic.KX_ENGINE_DEBUG_PHYSICS):
            self.update_physics(self.current_time, delta_time)

    def update_graphs(self):
        """Update isolated resource graphs"""
        with self.profile_as(logic.KX_ENGINE_DEBUG_SCENEGRAPH):
            Replicable.update_graph()
            Signal.update_graph()

    def update(self, delta_time):
        self.update_graphs()

        self.profile = logic.KX_ENGINE_DEBUG_MESSAGES
        self.network_system.receive()
        self.update_graphs()

        # Update Timers
        self.profile = logic.KX_ENGINE_DEBUG_LOGIC
        TimerUpdateSignal.invoke(delta_time)

        # Update Player Controller inputs for client
        if WorldInfo.netmode != Netmodes.server:
            PlayerInputSignal.invoke(delta_time)
            self.update_graphs()

        # Update main logic (Replicable update)
        LogicUpdateSignal.invoke(delta_time)
        self.update_graphs()

        # Update Physics, which also handles Scene-graph
        self.profile = logic.KX_ENGINE_DEBUG_PHYSICS
        PhysicsTickSignal.invoke(delta_time)
        self.update_graphs()

        # Clean up following Physics update
        PostPhysicsSignal.invoke()
        self.update_graphs()

        # Update Animation system
        self.profile = logic.KX_ENGINE_DEBUG_ANIMATIONS
        self.update_animations(self.current_time)

        # Transmit new state to remote peer
        self.profile = logic.KX_ENGINE_DEBUG_MESSAGES
        is_full_update = ((self.current_time - self.last_sent_time) >= (1 / self.network_tick_rate))

        if is_full_update:
            self.last_sent_time = self.current_time

        self.network_system.send(is_full_update)

        network_metrics = self.network_system.metrics
        if network_metrics.sample_age >= self.metric_interval:
            network_metrics.reset_sample_window()

        # Update UI
        self.profile = logic.KX_ENGINE_DEBUG_RASTERIZER
        UIUpdateSignal.invoke(delta_time)

        self.update_graphs()

    def update_scene(self, scene, delta_time):
        self.profile = logic.KX_ENGINE_DEBUG_LOGIC
        self.update_logic_bricks(self.current_time)

        if scene == self.network_scene:
            self.update(delta_time)

        else:
            self.profile = logic.KX_ENGINE_DEBUG_PHYSICS
            self.update_physics(self.current_time, delta_time)

            self.profile = logic.KX_ENGINE_DEBUG_SCENEGRAPH
            self.update_scenegraph(self.current_time)

    def quit(self):
        self.can_quit.value = True

    def dispatch(self):
        accumulator = 0.0
        last_time = self.get_time()
        # TODO determine where logic spikes originate from

        # Fixed time-step
        while not self.can_quit.value:
            current_time = self.get_time()

            # Determine delta time
            step_time = 1 / WorldInfo.tick_rate
            delta_time = current_time - last_time
            last_time = current_time

            # Set upper bound
            if delta_time > 0.25:
                delta_time = 0.25

            accumulator += delta_time

            # Whilst we have enough time in the buffer
            while accumulator >= step_time:

                # Update IO events from Blender
                self.profile = logic.KX_ENGINE_DEBUG_SERVICES
                self.update_blender()

                if self.check_quit():
                    self.quit()

                # Handle this outside of usual update
                WorldInfo.update_clock(step_time)

                # Update all scenes
                for scene in logic.getSceneList():
                    self.set_current_scene(scene)

                    self.update_scene(scene, step_time)

                # End of frame updates
                self.profile = logic.KX_ENGINE_DEBUG_SERVICES

                self.update_keyboard()
                self.update_mouse()
                self.update_scenes()

                if self.use_tick_rate and self.render:
                    self.update_render()

                self.current_time += step_time
                accumulator -= step_time

            if not self.use_tick_rate and self.render:
                self.profile = logic.KX_ENGINE_DEBUG_RASTERIZER
                self.update_render()

            self.profile = logic.KX_ENGINE_DEBUG_OUTSIDE

    def clean_up(self):
        GameExitSignal.invoke()
        Replicable.clear_graph()
        self.network_system.stop()

    def main(self):
        self.__init__()

        try:
            self.dispatch()

        except Exception:
            raise

        finally:
            self.clean_up()


class Server(GameLoop):

    render = True

    def __init__(self):
        super().__init__()

        self._rewind_data = OrderedDict()
        self._rewind_length = 1 * WorldInfo.tick_rate

    @staticmethod
    def create_network():
        return Network("", 1200)


class Client(GameLoop):

    def quit(self):
        """Gracefully quit server"""
        quit_func = super().quit
        # Try and quit gracefully
        DisconnectSignal.invoke(quit_func)
        # Else abort
        timeout = Timer(0.6)
        timeout.on_target = quit_func

    @staticmethod
    def create_network():
        return Network("", 0)

    @ConnectToSignal.global_listener
    def new_connection(self, address, port):
        self.network_system.connect_to((address, port))