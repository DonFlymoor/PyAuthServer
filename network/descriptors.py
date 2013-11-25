from copy import deepcopy
from .handler_interfaces import static_description


class StaticValue:
    '''Container for static-type values
    holds type for value and additional keyword arguments
    Pretty printable'''
    __slots__ = 'type', 'data'

    def __init__(self, type_, **kwargs):
        self.type = type_
        self.data = kwargs

    def __str__(self):
        return "Static Typed value: {}".format(self.type)


class Attribute(StaticValue):
    __slots__ = "notify", "complain", "name", "_data", "_value"

    def __init__(self, value=None, type_of=None,
                 notify=False, complain=False, **kwargs):

        super().__init__(type_of or type(value), **kwargs)

        self.notify = notify
        self.complain = complain

        self.name = None

        self._data = {}
        self._value = value

    def __get__(self, instance, base):
        # Try and get value, or register to instance
        try:
            storage_interface = self._data[instance]
            return storage_interface.value

        except AttributeError:
            return self

    def __set__(self, instance, value):
        storage_interface = self._data[instance]

        # Get the last value
        last_value = storage_interface.value

        # Avoid executing unnecessary logic
        if last_value == value:
            return

        # If the attribute should complain
        if self.complain:
            # Register a complain with value description
            storage_interface.set_complaint(static_description(value))

        # Force type check
        if value is not None and not isinstance(value, self.type):
            raise TypeError("Cannot set {.__name__} value to " \
                            "{.__name__} value".format(self.type, type(value)))

        # Store value
        storage_interface.value = value

    def __str__(self):
        return "[Attribute] name: {}, type: {.__name__}".format(self.name,
                                                       self.type)

    @property
    def value(self):
        return deepcopy(self._value)

    def register(self, instance, storage_interface):
        '''Registers attribute for instance
        Stores name of attribute through search'''
        self._data[instance] = storage_interface
        storage_interface.value = self.value

    __repr__ = __str__
