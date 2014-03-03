from .flag_serialiser import FlagSerialiser
from .descriptors import TypeFlag

from collections import OrderedDict, namedtuple
from functools import update_wrapper
from inspect import signature, Parameter

__all__ = ['MarkAttribute', 'RPCInterfaceFactory', 'RPCInterface']


MarkAttribute = namedtuple("MarkAttribute", "name")


class RPCInterfaceFactory:
    '''Manages instances of an RPC function for each object'''

    def __init__(self, function):
        # Information about RPC
        update_wrapper(self, function)

        self._function = function
        self._signature = signature(function)
        self._by_instance = {}

        self.verify_function(function)

    def __get__(self, instance, base):
        if instance is None:
            return self

        try:
            return self._by_instance[instance]

        except KeyError:
            return self._function.__get__(instance)

    def create_rpc_interface(self, instance):
        bound_function = self._function.__get__(instance)

        self.update_class_arguments(bound_function, instance)
        self._by_instance[instance] = RPCInterface(bound_function)

        return self._by_instance[instance]

    def update_class_arguments(self, function, instance):
        function_arguments = RPCInterface.order_arguments(self._signature)
        lookup_type = MarkAttribute
        for argument in function_arguments.values():
            data = argument.data
            for arg_name, arg_value in data.items():
                if not isinstance(arg_value, lookup_type):
                    continue
                data[arg_name] = getattr(instance, arg_value.name)

    def verify_function(self, function):
        parameters = iter(self._signature.parameters.items())
        next(parameters)
        for parameter_name, parameter in parameters:
            if parameter.annotation is Parameter.empty:
                raise ValueError("RPC call '{}' has not provided a type for "
                         "parameter '{}'".format(self._function.__qualname__,
                                               parameter_name))

    def __repr__(self):
        return "<RPC {}>".format(self._function.__qualname__)


class RPCInterface:
    """Mediates RPC calls to/from peers"""

    def __init__(self, function):

        # Used to isolate rpc_for_instance for each function for each instance
        self._function_name = function.__qualname__
        self._function_signature = signature(function)
        self._function_call = function.__call__

        # Information about RPC
        update_wrapper(self, function)

        # Get the function signature
        self.target = self._function_signature.return_annotation

        # Interface between data and bytes
        self._binder = self._function_signature.bind
        self._serialiser = FlagSerialiser(self.order_arguments(
                                             self._function_signature))

        from .replicables import WorldInfo
        self._worldinfo = WorldInfo

    def __call__(self, *args, **kwargs):
        # Determines if call should be executed or bounced
        if self.target == self._worldinfo.netmode:
            return self._function_call(*args, **kwargs)

        # Store serialised argument data for later sending
        arguments = self._binder(*args, **kwargs).arguments
        try:
            self._interface.value = self._serialiser.pack(arguments)
        except Exception as err:
            raise RuntimeError("Could not package RPC call: '{}'".format(
                                        self._function_name)) from err

    def execute(self, bytes_):
        # Unpack RPC
        try:
            unpacked_data = self._serialiser.unpack(bytes_)
            self._function_call(**dict(unpacked_data))

        except Exception as err:
            print("Could not invoke RPC call: '{}' - {}".format(self._function_name, err))

    @staticmethod
    def order_arguments(signature):
        return OrderedDict((value.name, value.annotation)
                           for value in signature.parameters.values()
                           if isinstance(value.annotation, TypeFlag))

    def register(self, interface, rpc_id):
        self.rpc_id = rpc_id
        self._interface = interface
