from .helpers import register_protocol_listeners, get_state_senders, on_protocol
from .replication import ClientReplicationManager, ServerReplicationManager

from ..errors import NetworkError
from ..enums import ConnectionStates, PacketProtocols, Netmodes
from ..handlers import get_handler
from ..packet import Packet
from ..signals import ConnectionErrorSignal, ConnectionSuccessSignal, ConnectionDeletedSignal, ConnectionTimeoutSignal
from ..type_flag import TypeFlag
from ..world import get_current_netmode

from time import clock

__all__ = 'ServerHandshakeManager', 'ClientHandshakeManager'


# Handshake Streams
class HandshakeManagerBase:

    def __init__(self, connection):
        self.state = ConnectionStates.init

        self.connection = connection
        self.logger = connection.logger.getChild("HandshakeManager")

        self.network_manager = connection.network_manager

        self.replication_manager = None
        self.connection_info = connection.instance_id
        self.remove_connection = None

        self.timeout_duration = 10
        self._last_received_time = clock()

        # Additional data
        self.string_packer = get_handler(TypeFlag(str))

        # Register listeners
        register_protocol_listeners(self, connection.dispatcher)
        self.senders = get_state_senders(self)

    @property
    def timed_out(self):
        """If this stream has not received anything for an interval greater or equal to the timeout duration"""
        return (clock() - self.connection.last_received_time) > self.timeout_duration

    def _cleanup(self):
        if callable(self.remove_connection):
            self.remove_connection()

        if self.replication_manager is not None:
            self.replication_manager.on_disconnected()

    def on_timeout(self):
        self._cleanup()

        self.logger.info("Timed out after {} seconds".format(self.timeout_duration))
        ConnectionTimeoutSignal.invoke(target=self)

    def pull_packets(self, network_tick, bandwidth):
        if self.timed_out:
            self.on_timeout()


class ServerHandshakeManager(HandshakeManagerBase):
    """Manages connection state for the server"""

    def __init__(self, connection):
        super().__init__(connection)

        self.handshake_error = None

        self.invoke_handshake()

    def on_ack_handshake_failed(self, packet):
        self._cleanup()

        ConnectionErrorSignal.invoke(target=self)

    @on_protocol(PacketProtocols.request_disconnect)
    def receive_disconnect_request(self, data):
        self._cleanup()

        self.state = ConnectionStates.disconnected

    @on_protocol(PacketProtocols.request_handshake)
    def receive_handshake_request(self, data):
        # Only if we're not already in some handshake process
        if self.state != ConnectionStates.awaiting_handshake:
            return

        try:
            self.network_manager.rules.pre_initialise(self.connection_info)

        except NetworkError as err:
            self.logger.error("Connection was refused: {}".format(err))
            self.handshake_error = err

        self.state = ConnectionStates.received_handshake
        self.send_handshake_result()

    def send_handshake_result(self):
        connection_failed = self.handshake_error is not None

        if connection_failed:
            pack_string = self.string_packer.pack
            error_type = type(self.handshake_error).type_name
            error_body = self.handshake_error.args[0]
            error_data = pack_string(error_type) + pack_string(error_body)

            # Set failed state
            self.state = ConnectionStates.failed
            ConnectionErrorSignal.invoke(target=self)

            # Send result
            packet = Packet(protocol=PacketProtocols.handshake_failed, payload=error_data,
                            on_success=self.on_ack_handshake_failed)

        else:
            # Set success state
            self.state = ConnectionStates.connected
            ConnectionSuccessSignal.invoke(target=self)

            self.replication_manager = ServerReplicationManager(self.connection, self.network_manager.rules)

            # Send result
            packet = Packet(protocol=PacketProtocols.handshake_success, reliable=True)

        # Add to connection queue
        self.connection.queue_packet(packet)

    def invoke_handshake(self):
        """Invoke handshake attempt on client, used for multicasting"""
        self.state = ConnectionStates.awaiting_handshake

        packet = Packet(protocol=PacketProtocols.invoke_handshake, reliable=True)
        self.connection.queue_packet(packet)


class ClientHandshakeManager(HandshakeManagerBase):

    def __init__(self, connection):
        super().__init__(connection)

        self.invoke_handshake()

    def invoke_handshake(self):
        self.state = ConnectionStates.received_handshake
        packet = Packet(protocol=PacketProtocols.request_handshake, reliable=True)
        self.connection.queue_packet(packet)

    @on_protocol(PacketProtocols.handshake_success)
    def receive_handshake_success(self, data):
        if self.state != ConnectionStates.received_handshake:
            return

        self.state = ConnectionStates.connected
        self.replication_manager = ClientReplicationManager(self.connection)

        ConnectionSuccessSignal.invoke(target=self)

    @on_protocol(PacketProtocols.invoke_handshake)
    def receive_multicast_ping(self, data):
        self.invoke_handshake()

    @on_protocol(PacketProtocols.handshake_failed)
    def receive_handshake_failed(self, data):
        error_type, type_size = self.string_packer.unpack_from(data)
        error_message, message_size = self.string_packer.unpack_from(data, type_size)

        error_class = NetworkError.from_type_name(error_type)
        raised_error = error_class(error_message)

        self.logger.error("Authentication failed: {}".format(raised_error))
        self.state = ConnectionStates.failed

        ConnectionErrorSignal.invoke(raised_error, target=self)


def create_handshake_manager(connection):
    netmode = get_current_netmode()

    if netmode == Netmodes.server:
        return ServerHandshakeManager(connection)

    else:
        return ClientHandshakeManager(connection)
