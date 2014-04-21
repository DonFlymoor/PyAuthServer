from .flag_serialiser import FlagSerialiser
from .descriptors import TypeFlag, MarkAttribute

from collections import OrderedDict
from functools import update_wrapper
from inspect import signature, Parameter
from copy import deepcopy

__all__ = ['RPCInterfaceFactory', 'RPCInterface']


class RPCInterfaceFactory:
    """Manages instances of an RPC function for each object"""

    def __init__(self, function):
        # Information about RPC
        update_wrapper(self, function)

        self._by_instance = {}
        self._ordered_parameters = self.order_arguments(signature(function))
        self._serialiser_parameters = None

        self.validate_function(self._ordered_parameters, function)

        self.original_function = function
        self.has_marked_parameters = self.check_for_marked_parameters()

    def __get__(self, instance, base):
        """Get descriptor for an RPC instance
        Permits super() calls to return a generic function if a child
        redefines it

        :param instance: class instance which hosts the rpc call
        :param base: base type of class which hosts the rpc call
        """
        if instance is None:
            return self

        try:
            return self._by_instance[instance]

        # Allow subclasses to call superclass methods without invocation
        except KeyError:
            return self.original_function.__get__(instance)

    def create_rpc_interface(self, instance):
        """Handles creation of a new instance's RPC interface
        Ensures RPC interfaces exist only for classes which implement them

        :param instance: class instance which implements the RPC
        """
        bound_function = self.original_function.__get__(instance)

        # Create information for the serialiser
        if self._serialiser_parameters is None:
            self._serialiser_parameters = self.get_serialiser_parameters(instance.__class__)

        self._by_instance[instance] = RPCInterface(bound_function,
                                       self._serialiser_parameters)

        return self._by_instance[instance]

    def get_serialiser_parameters(self, cls):
        """Returns modified parameter dictionary
        Updates requests to reference class attributes with
        MarkAttribute instances

        :param cls: class reference
        """
        serialiser_info = deepcopy(self._ordered_parameters)
        lookup_type = MarkAttribute

        # Update with new values
        for argument in serialiser_info.values():
            data = argument.data

            for arg_name, arg_value in data.items():
                if not isinstance(arg_value, lookup_type):
                    continue

                data[arg_name] = getattr(cls, arg_value.name)

            # Allow types to be marked
            if isinstance(argument.type, lookup_type):
                argument.type = getattr(cls, argument.type.name)

        return serialiser_info

    def check_for_marked_parameters(self):
        """Checks for any MarkAttribute instances in parameter data"""
        lookup_type = MarkAttribute

        for argument in self._ordered_parameters.values():

            for arg_value in argument.data.values():

                if isinstance(arg_value, lookup_type):
                    return True

        return False

    @staticmethod
    def order_arguments(signature):
        parameter_values = signature.parameters.values()
        empty_parameter = Parameter.empty

        return OrderedDict((value.name, None if value.annotation is
                            empty_parameter else value.annotation)
                           for value in parameter_values
                           if isinstance(value.annotation, TypeFlag))

    @staticmethod
    def validate_function(arguments, function):
        """Validates the format of an RPC call
        Checks that all arguments have provided type annotations

        :param arguments: ordered dictionary of arguments
        :param function: function to test
        """
        # Read all arguments
        for parameter_name, parameter in arguments.items():

            if parameter is None:
                raise ValueError("RPC call '{}' has not provided a type "
                                 "annotation for parameter '{}'"
                         .format(function.__qualname__, parameter_name))

    def __repr__(self):
        return "<RPC {}>".format(self.original_function.__qualname__)


class RPCInterface:
    """Mediates RPC calls to/from peers"""

    def __init__(self, function, serialiser_info):
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
        self._serialiser = FlagSerialiser(serialiser_info)

        from .world_info import WorldInfo
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

    def register(self, interface, rpc_id):
        self.rpc_id = rpc_id
        self._interface = interface
