from .replicable import Replicable
from .network import Network
from .connection import Connection
from .world_info import WorldInfo
from .signals import Signal

from time import clock

__all__ = ["SimpleNetwork", "respect_interval"]


class SimpleNetwork(Network):

    """Simple network update loop"""

    def on_finished(self):
        Connection.clear_graph()
        Replicable.clear_graph()
        Signal.clear_graph()

        WorldInfo.register(instance_id=WorldInfo.instance_id)

    def on_update(self):
        return True

    def stop(self):
        self.on_finished()
        super().stop()

    def step(self):
        self.receive()
        full_update = self.on_update()
        self.send(full_update)

    def run(self, timeout=None, update_rate=1/60):
        started = clock()
        last_time = started

        while True:
            current_time = clock()
            if (current_time - last_time) < update_rate:
                continue

            last_time = current_time

            any_connections = bool(Connection)

            if timeout is None:
                timed_out = False

            else:
                timed_out = (current_time - started) > timeout

            if not any_connections and timed_out:
                break

            self.step()

        self.stop()


def respect_interval(interval, function):
    """Decorator to ensure function is only called after a minimum interval

    :param interval: minimum interval between successive calls
    :param function: function to call
    """
    def wrapper():
        last_called = clock()

        while True:
            now = clock()
            dt = now - last_called

            if dt >= interval:
                function()
                last_called = now

            yield

    return wrapper().__next__
