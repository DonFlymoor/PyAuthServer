__all__ = ['ReplicableChannelBase', 'ClientChannel', 'ServerChannel']


from functools import partial
from time import clock
from operator import attrgetter

from ...annotations.conditions import is_reliable
from ...handlers import TypeFlag, get_handler, static_description, FlagSerialiser
from ...replicable import Replicable


priority_getter = attrgetter("replication_priority")


class ReplicableChannelBase:
    """Channel for replication information.

    Belongs to an instance of Replicable and a connection
    """

    id_handler = get_handler(TypeFlag(Replicable))

    def __init__(self, scene_channel, replicable):
        # Store important info
        self.replicable = replicable
        self.scene_channel = scene_channel

        # Set initial (replication status) to True
        self._last_replication_time = 0.0
        self.is_initial = True

        # Get network attributes
        self._serialisable_data = replicable.serialisable_data
        self._replicated_functions = replicable.replicated_functions
        self._replicated_function_queue = replicable.replicated_function_queue

        # Create a serialiser instance
        self.logger = scene_channel.logger.getChild("<Channel: {}>".format(repr(replicable)))
        self._serialiser = FlagSerialiser(self._serialisable_data, logger=self.logger.getChild("<FlagSerialiser>"))

        self._rpc_id_handler = get_handler(TypeFlag(int))
        self.packed_id = self.__class__.id_handler.pack(replicable)

    @property
    def is_owner(self):
        """Return True if this channel is in the ownership tree of the connection replicable"""
        parent = self.replicable.uppermost
        # TODO
        try:
            return parent == self.scene_channel.replicable

        except AttributeError:
            return False

    def dump_rpc_calls(self):
        """Return the requested RPC calls in a packaged format:

        rpc_id (bytes) + body (bytes), reliable status (bool)
        """
        replicated_function_queue = self._replicated_function_queue

        reliable_rpc_calls = []
        unreliable_rpc_calls = []

        id_packer = self._rpc_id_handler.pack
        for (index, is_reliable, data) in replicated_function_queue:
            packed_rpc_call = id_packer(index) + data

            if is_reliable:
                reliable_rpc_calls.append(packed_rpc_call)

            else:
                unreliable_rpc_calls.append(packed_rpc_call)

        replicated_function_queue.clear()

        reliable_data = b''.join(reliable_rpc_calls)
        unreliable_data = b''.join(unreliable_rpc_calls)

        return reliable_data, unreliable_data

    def process_rpc_calls(self, data, offset, allow_execute=True):
        """Invoke an RPC call from packaged format

        :param rpc_call: rpc data (see take_rpc_calls)
        """
        start_offset = offset

        if allow_execute:
            while offset < len(data):
                rpc_id, rpc_header_size = self._rpc_id_handler.unpack_from(data, offset=offset)
                offset += rpc_header_size

                try:
                    rpc_instance = self._replicated_functions[rpc_id]

                except IndexError:
                    self.logger.exception("Error invoking RPC: No RPC function with id {}".format(rpc_id))
                    break

                else:
                    arguments, bytes_read = rpc_instance.deserialise(data, offset)
                    offset += bytes_read

                    # Call RPC
                    rpc_instance.function(**arguments)

        # We don't have permission to execute this!
        else:
            while offset < len(data):
                rpc_id, rpc_header_size = self._rpc_id_handler.unpack_from(data, offset=offset)
                offset += rpc_header_size

                try:
                    rpc_instance = self._replicated_functions[rpc_id]

                except IndexError:
                    self.logger.exception("Error invoking RPC: No RPC function with id {}".format(rpc_id))
                    break

                else:
                    arguments, bytes_read = rpc_instance.deserialise(data, offset)
                    offset += bytes_read

        unpacked_bytes = offset - start_offset
        return unpacked_bytes


class ClientReplicableChannel(ReplicableChannelBase):

    def notify_callback(self, notifications):
        invoke_notify = self.replicable.on_notify
        for attribute_name in notifications:
            invoke_notify(attribute_name)

    @property
    def replication_priority(self):
        """Get the replication priority for a replicable.
        Utilises replication interval to increment priority of neglected replicables.

        :returns: replication priority
        """
        return self.replicable.replication_priority

    def read_attributes(self, bytes_string, offset=0):
        """Unpack byte stream and updates attributes

        :param bytes\_: byte stream of attribute
        """
        # Create local references outside loop
        replicable_data = self._serialisable_data

        notifications = []
        queue_notification = notifications.append

        # Notify after all values are set
        notifier_callback = partial(self.notify_callback, notifications)

        unpacked_items, read_bytes = self._serialiser.unpack(bytes_string, replicable_data, offset=offset)
        for serialisable, value in unpacked_items:
            # Store new value
            replicable_data[serialisable] = value

            # Check if needs notification
            if serialisable.notify:
                queue_notification(serialisable.name)

        return notifier_callback, read_bytes


class ServerReplicableChannel(ReplicableChannelBase):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._last_replicated_descriptions = {serialisable: static_description(serialisable.initial_value)
                                      for serialisable in self._serialisable_data}
        self._last_replicated_flagged_descriptions = {serialisable: description for serialisable, description
                                        in self._last_replicated_descriptions.items() if serialisable.complain}
        self._name_to_serialisable = {serialisable.name: serialisable
                                      for serialisable in self._serialisable_data}
        self._serialisable_descriptions = self.replicable.serialisable_descriptions

    @property
    def replication_priority(self):
        """Get the replication priority for a replicable
        Utilises replication interval to increment priority of neglected replicables.

        :returns: replication priority
        """
        interval = (clock() - self._last_replication_time)
        elapsed_fraction = (interval / self.replicable.replication_update_period)
        return self.replicable.replication_priority + (elapsed_fraction - 1)

    @property
    def is_awaiting_replication(self):
        """Return True if the channel is due to replicate its state"""
        interval = (clock() - self._last_replication_time)
        return (interval >= self.replicable.replication_update_period) or self.is_initial

    def get_attributes(self, is_owner):
        """Return the serialised state of the managed network object"""
        # Get Replicable and its class
        replicable = self.replicable
        name_to_serialisable = self._name_to_serialisable

        get_description = static_description
        serialisable_data = self._serialisable_data

        # Local access
        last_replicated_descriptions = self._last_replicated_descriptions
        last_replicated_flagged_descriptions = self._last_replicated_flagged_descriptions

        current_flagged_descriptions = self._serialisable_descriptions
        is_complaining = last_replicated_flagged_descriptions != current_flagged_descriptions

        # Store dict of attribute-> value
        to_serialise = {}

        # Set role context
        with replicable.roles.set_context(is_owner):
            # Get names of Replicable attributes
            can_replicate = replicable.conditions(is_owner, is_complaining, self.is_initial)

            # Iterate over attributes
            for name in can_replicate:
                serialisable = name_to_serialisable[name]
                value = serialisable_data[serialisable]

                # Check if the last hash is the same
                last_description = last_replicated_descriptions[serialisable]

                # Get value hash
                # Use the complaint hash if it is there to save computation
                flag_on_assignment = serialisable.flag_on_assignment

                if flag_on_assignment:
                    new_description = current_flagged_descriptions[serialisable]

                else:
                    new_description = get_description(value)

                # If values match, don't update
                if last_description == new_description:
                    continue

                # Add value to data dict
                to_serialise[serialisable] = value

                # Remember hash of value
                last_replicated_descriptions[serialisable] = new_description

                # Set new complaint hash if it was complaining (for above is_complaint check next replication)
                if flag_on_assignment:
                    last_replicated_flagged_descriptions[serialisable] = new_description

            # We must have now replicated
            self._last_replication_time = clock()
            self.is_initial = False

            # An output of bytes asserts we have data
            if to_serialise:
                # Returns packed data
                data = self._serialiser.pack(to_serialise)

            else:
                data = None

        return data


class SceneChannelBase:

    channel_class = None
    id_handler = get_handler(TypeFlag(int))

    def __init__(self, manager, scene, scene_id):
        self.scene = scene
        self.scene_id = scene_id
        self.manager = manager

        self.logger = manager.logger.getChild("SceneChannel")

        self.packed_id = self.__class__.id_handler.pack(scene_id)
        self.replicable_channels = {}

        # Channels may be created after replicables were instantiated
        self.register_existing_replicables()

        scene.messenger.add_subscriber("replicable_added", self.on_replicable_added)
        scene.messenger.add_subscriber("replicable_remove", self.on_replicable_removed)

    def register_existing_replicables(self):
        """Load existing registered replicables"""
        for replicable in self.scene.replicables.values():
            self.on_replicable_added(replicable)

    @property
    def prioritised_channels(self):
        return sorted(self.replicable_channels.values(), reverse=True, key=priority_getter)

    def on_replicable_added(self, target):
        self.replicable_channels[target.unique_id] = self.channel_class(self, target)

    def on_replicable_removed(self, target):
        self.replicable_channels.pop(target.unique_id)


class ServerSceneChannel(SceneChannelBase):

    channel_class = ServerReplicableChannel

    def __init__(self, manager, scene, scene_id):
        super().__init__(manager, scene, scene_id)

        self.is_initial = True
        self.deleted_channels = []

    def on_replicable_removed(self, replicable):
        channel = self.replicable_channels.pop(replicable.unique_id)
        self.deleted_channels.append(channel)


class ClientSceneChannel(SceneChannelBase):

    channel_class = ClientReplicableChannel
