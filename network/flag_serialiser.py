from .bitfield import BitField
from .handler_interfaces import get_handler
from .descriptors import TypeFlag

__all__ = ["FlagSerialiser"]


class FlagSerialiser:
    """Interface class for parsing/dumping data to bytes
    Packed structure:
    [Contents, Data, Booleans, Nones]"""

    # The last two entries of the contents mask 
    NONE_CONTENT_INDEX = -1
    BOOL_CONTENT_INDEX = -2

    def __init__(self, arguments):
        '''Accepts ordered dict as argument'''
        self.bool_args = [(key, value) for key, value in arguments.items()
                          if value.type is bool]
        self.non_bool_args = [(key, value) for key, value in arguments.items()
                              if value.type is not bool]
        self.non_bool_handlers = [(key, get_handler(value))
                               for key, value in self.non_bool_args]

        # Maintain count of data types
        self.total_none_booleans = len(self.non_bool_args)
        self.total_booleans = len(self.bool_args)
        self.total_contents = self.total_none_booleans + self.total_booleans

        # BitFields used for packing
        self.bool_bits = BitField(self.total_booleans)
        self.none_bits = BitField(self.total_contents)

        # Additional two bits when including NoneType and Boolean values
        self.content_bits = BitField(self.total_contents + 2)

        self.boolean_packer = get_handler(TypeFlag(BitField,
                                                   fields=self.total_booleans))
        self.total_packer = get_handler(TypeFlag(BitField,
                                                fields=self.total_contents))

    def report_information(self, bytes_):
        bitfield_packer = self.bitfield_packer
        # Get header of packed data
        content_bits = bitfield_packer.unpack_from(bytes_)[:]
        content_data = bytes_[:bitfield_packer.size(bytes_)]
        bytes_ = bytes_[bitfield_packer.size(bytes_):]
        print("Header Data: ", content_data)
        entry_names, entry_handlers = zip(*(self.non_bool_args + self.bool_args))

        # If there are NoneType values they will be first
        if content_bits[self.NONE_CONTENT_INDEX]:
            none_bits = bitfield_packer.unpack_from(bytes_)
            none_data = bytes_[:bitfield_packer.size(bytes_)]
            print("NoneType Values Data: ", none_data)
            bytes_ = bytes_[bitfield_packer.size(bytes_):]

        else:
            none_bits = [False] * self.total_contents
            none_data = None

        print()
        for name, included, is_none, handler in zip(entry_names, content_bits,
                                                    none_bits, entry_handlers):
            if not included:
                print
                continue

            print("{} : {}".format(name, "None" if is_none else
                                   handler.type.__name__))

        print()

    def read_contents(self, bytes_):
        """Determine the included entries of the packed data

        :param bytes_: packed data"""
        total_packer = self.total_packer
        total_packer.unpack_merge(self.content_bits, bytes_)
        return bytes_[total_packer.size(bytes_):]

    def read_nonetype_values(self, bytes_):
        """Determine the NoneType entries of the packed data

        :param bytes_: packed data"""
        total_packer = self.total_packer
        total_packer.unpack_merge(self.none_bits, bytes_)
        return bytes_[total_packer.size(bytes_):]

    def unpack(self, bytes_, previous_values={}):
        """Unpack bytes into Python objects

        :param bytes_: packed data
        :param previous_values: previous packed values (optional)"""
        # Get the contents header
        bytes_ = self.read_contents(bytes_)
        content_values = list(self.content_bits)

        has_none_types = content_values[self.NONE_CONTENT_INDEX]
        has_booleans = self.total_booleans and \
                        content_values[self.BOOL_CONTENT_INDEX]

        # If there are NoneType values they will be first
        if has_none_types:
            bytes_ = self.read_nonetype_values(bytes_)

        # Ensure that the NoneType values are cleared
        else:
            self.none_bits.clear()

        # Create list for faster successive iterations
        none_values = list(self.none_bits)

        # All values have an entry in the contents bitfield
        for included, value_none, (key, handler) in zip(content_values,
                                        none_values, self.non_bool_handlers):
            if not included:
                continue

            # If this is a NONE value
            if value_none:
                value = None

            else:
                # Merge with existing Python object
                previous_value = previous_values.get(key)

                if previous_value is not None and \
                    hasattr(handler, "unpack_merge"):
                    # If we can't merge use default unpack
                    handler.unpack_merge(value, bytes_)

                # Otherwise ask for a new value
                else:
                    value = handler.unpack_from(bytes_)

                # We have unpacked a value, so shift by its size
                bytes_ = bytes_[handler.size(bytes_):]

            yield (key, value)

        # If there are Boolean values included in the data
        if has_booleans:
            # Read data from Boolean bitfields
            self.boolean_packer.unpack_merge(self.bool_bits, bytes_)

            found_booleans = content_values[self.total_none_booleans:]
            none_booleans = self.none_bits[self.total_none_booleans:]

            boolean_info = zip(self.bool_bits, self.bool_args, found_booleans,
                               none_booleans)

            # Yield included boolean values
            for (value, (key, _), found, none_value) in boolean_info:

                if found:
                    yield (key, None if none_value else value)

    def pack(self, data):
        content_bits = self.content_bits
        none_bits = self.none_bits

        # Reset NoneType and contents Bitmasks
        none_bits.clear()
        content_bits.clear()

        # Create data_values list
        data_values = []
        append_value = data_values.append

        # Iterate over non booleans
        for index, (key, handler) in enumerate(self.non_bool_handlers):
            if not key in data:
                continue

            value = data[key]

            if value is None:
                none_bits[index] = True

            else:
                append_value(handler.pack(value))

            # Mark attribute as included
            content_bits[index] = True

        # Any remaining data will be Boolean values
        total_none_booleans = self.total_none_booleans
        has_booleans = len(data) > total_none_booleans

        if has_booleans:
            # Reset booleans bitmask
            bools = self.bool_bits
            bools.clear()

            index_shift = total_none_booleans
            for index, (key, _) in enumerate(self.bool_args):
                if not key in data:
                    continue

                # Account for shift due to previous data
                content_index = index_shift + index

                # Register as included
                value = data[key]

                # Either save None value
                if value is None:
                    none_bits[content_index] = True

                # Or save a boolean value
                else:
                    bools[index] = value

                content_bits[content_index] = True

            # Mark Boolean values as included
            append_value(self.boolean_packer.pack(bools))
            content_bits[self.BOOL_CONTENT_INDEX] = True

        # If NoneType values have been set, mark them as included
        if none_bits:
            none_value_bytes = self.total_packer.pack(none_bits)

            data_values.insert(0, none_value_bytes)
            content_bits[self.NONE_CONTENT_INDEX] = True

        return self.total_packer.pack(content_bits) + b''.join(data_values)