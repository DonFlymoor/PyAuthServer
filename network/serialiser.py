from math import ceil
from struct import Struct

from .handler_interfaces import register_handler

__all__ = ['UInt16', 'UInt32', 'UInt64', 'UInt8', 'Float32', 'Float64', 'bits_to_bytes', 'handler_from_bit_length',
           'handler_from_int', 'handler_from_byte_length', 'StringHandler', 'BytesHandler', 'int_selector',
           'next_or_equal_power_of_two', 'BoolHandler']


def build_handler(name, fmt):
    """Create handler for data with struct formatting

    :param name: name of handler class
    :param fmt: format string of handler
    """
    cls_dict = {}

    struct_obj = Struct(fmt)
    fmt_size = struct_obj.size

    methods = ("""def unpack_from(bytes_string, offset=0, unpacker=struct_obj.unpack_from):\n\t
               return unpacker(bytes_string, offset)[0], {size}""",
               """def size(bytes_string=None):\n\treturn {size}""",
               """pack=struct_obj.pack""")

    register_string = """local_dict = dict();local_dict=locals().copy()\n{}\nfunc_name = next(iter(set(locals())
                         .difference(local_dict)));cls_dict[func_name] = locals()[func_name]"""

    for method_string in methods:
        func_string = method_string.format(fmt=fmt, size=fmt_size)
        wrapped_string = register_string.format(func_string)
        exec(wrapped_string)

    return type(name, (), cls_dict)


UInt32 = build_handler("UInt32", "!I")
UInt16 = build_handler("UInt16", "!H")
UInt64 = build_handler("UInt64", "!Q")
UInt8 = build_handler("UInt8", "!B")
Float32 = build_handler("Float32", "!f")
Float64 = build_handler("Float64", "!d")


int_packers = [UInt8, UInt16, UInt32, UInt64]
int_sized = {x.size(): x for x in int_packers}


def bits_to_bytes(bits):
    """Determines how many bytes are required to pack a number of bits

    :param bits: number of bits required
    """
    return ceil(bits / 8)


def next_or_equal_power_of_two(value):
    """Return next power of 2 greater than or equal to value

    :param value: value to round
    """
    if value > 0:
        value -= 1
    else:
        value = 1

    shift = 1

    while (value + 1) & value:
        value |= value >> shift
        shift *= 2

    return value + 1


def float_selector(type_flag):
    """Return the correct float handler using meta information from a given type_flag
    :param type_flag: type flag for float value
    """
    return Float64 if type_flag.data.get("max_precision") else Float32


def handler_from_bit_length(total_bits):
    """Return the correct integer handler for a given number of bits
    :param total_bits: total number of bits required
    """
    total_bytes = bits_to_bytes(total_bits)
    return handler_from_byte_length(total_bytes)


def handler_from_byte_length(total_bytes):
    """Return the smallest handler needed to pack a number of bytes

    :param total_bytes: number of bytes needed to pack
    :rtype: :py:class:`network.serialiser.IDataHandler`"""
    rounded_bytes = next_or_equal_power_of_two(total_bytes)

    try:
        return int_sized[rounded_bytes]

    except KeyError as err:
        raise ValueError("Integer too large to pack: {} bytes".format(total_bytes)) from err

    return packer


def handler_from_int(value):
    """Return the smallest integer packer capable of packing a given integer
    :param value: value to test for
    """
    return handler_from_bit_length(value.bit_length())


def int_selector(type_flag):
    """Return the correct integer handler using meta information from a given type_flag
    :param type_flag: type flag for integer value
    """
    if "max_value" in type_flag.data:
        return handler_from_int(type_flag.data["max_value"])

    return handler_from_bit_length(type_flag.data.get('max_bits', 8))


class BoolHandler:
    """Handler for boolean type"""
    unpacker = UInt8.unpack_from

    @classmethod
    def unpack_from(cls, bytes_string, offset=0):
        value, size = cls.unpacker(bytes_string, offset)
        return bool(value), size

    size = UInt8.size
    pack = UInt8.pack


class BytesHandler:
    """Handler for bytes type"""

    def __init__(self, type_flag):
        header_max_value = type_flag.data.get("max_length", 255)
        self.packer = handler_from_int(header_max_value)

    def pack(self, bytes_string):
        return self.packer.pack(len(bytes_string)) + bytes_string

    def size(self, bytes_string):
        length, length_size = self.packer.unpack_from(bytes_string)
        return length + length_size

    def unpack_from(self, bytes_string, offset=0):
        length, length_size = self.packer.unpack_from(bytes_string, offset)
        end_index = length + length_size
        value = bytes_string[length_size + offset: end_index + offset]

        return value, end_index


class StringHandler(BytesHandler):
    """Handler for string type"""

    def pack(self, str_):
        return super().pack(str_.encode())

    def unpack_from(self, bytes_string, offset=0):
        encoded_string, size = super().unpack_from(bytes_string, offset)

        return bytes(encoded_string).decode(), size

# Register handlers for native types
register_handler(bool, BoolHandler)
register_handler(str, StringHandler, is_callable=True)
register_handler(bytes, BytesHandler, is_callable=True)
register_handler(int, int_selector, is_callable=True)
register_handler(float, float_selector, is_callable=True)
