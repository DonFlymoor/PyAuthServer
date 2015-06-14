try:
    import bge

except ImportError:
    from panda_game_system.game_loop import Client, Server
    from .ui import UI; UI()

else:
    from bge_game_system.game_loop import Client, Server

from network.connection import Connection
from network.world_info import WorldInfo
from network.rules import ReplicationRulesBase
from network.enums import Netmodes

from game_system.controllers import PawnController, AIPawnController
from game_system.clock import Clock
from game_system.entities import Actor
from game_system.ai.sensors import ViewSensor
from game_system.replication_info import ReplicationInfo

from .actors import *
from .controllers import TestPandaPlayerController


classes = dict(server=Server, client=Client)


class Rules(ReplicationRulesBase):

    def pre_initialise(self, addr, netmode):
        return

    def post_disconnect(self, conn, replicable):
        replicable.deregister()

    def post_initialise(self, replication_stream):
        cont = TestPandaPlayerController()
        cont.possess(TestActor())
        return cont

    def is_relevant(self, connection, replicable):
        if isinstance(replicable, PawnController):
            return False

        elif isinstance(replicable, (Actor, ReplicationInfo, Clock)):
            return True

        elif replicable.always_relevant:
            return True


from .planner import *


class ZombCont(AIPawnController):
    actions = [GetNearestAmmoPickup()]
    goals = [FindAmmoGoal()]

    def on_initialised(self):
        super().on_initialised()

        self.blackboard['has_ammo'] = False
        self.blackboard['ammo'] = 0

        view_sensor = ViewSensor()
        self.sensor_manager.add_sensor(view_sensor)


def init_game():
    if WorldInfo.netmode == Netmodes.server:
        base.cam.set_pos((0, -60, 0))

        floor = TestActor()
        floor.transform.world_position = [0, 0, -11]
        floor.transform._nodepath.set_color(0.3, 0.3, 0.0)
        floor.transform._nodepath.set_scale(10)
        floor.physics.mass = 0.0
        floor.mass = 0.0

        pickup = AmmoPickup()
        pickup.transform.world_position = [5, 12, 1]
        pickup.physics.mass = 0.0
        floor.transform._nodepath.set_color(1, 0.0, 0.0)
        #
        cont = ZombCont()

        omb = TestActor()
        omb.transform.world_position = [0, 0, 1]
        cont.possess(omb)

        pass

    else:
        Connection.create_connection("localhost", 1200)


def run(mode):
    try:
        cls = classes[mode]

    except KeyError:
        print("Unable to start {}".format(mode))
        return

    if mode == "server":
        WorldInfo.rules = Rules()

    else:
        WorldInfo.netmode = Netmodes.client

    game_loop = cls()
    init_game()

    # model = loader.loadModel(f)
    # model.reparentTo(base.render)
    #
    # from panda3d.core import PointLight
    # plight = PointLight('plight')
    # plight.setColor((1, 1, 1, 1))
    # plnp = render.attachNewNode(plight)
    # plnp.setPos(10, 20, 0)
    # render.setLight(plnp)

    game_loop.delegate()
    del game_loop