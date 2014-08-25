from .type_register import TypeRegister
from .world_info import WorldInfo

__all__ = ['DelegateByNetmode', 'DelegateByTag', 'FindByTag']


class FindByTag(metaclass=TypeRegister):
    """Provides an interface to select a subclass by a tag value"""

    _cache = {}

    @classmethod
    def update_cache(cls):
        try:
            cache = {getattr(c, "_tag", None): c for c in cls.subclasses.values()}

        except AttributeError:
            raise TypeError("Subclass dictionary was not implemented by {}".format(cls.name))

        cls._cache.update(cache)

    @classmethod
    def find_subclass_for(cls, tag_value):
        """Find subclass with a tag value

        :param tag_value: value of tag to isolate
        """

        try:
            return cls._cache[tag_value]

        except KeyError:
            raise TypeError("Tag: {} is not supported by {}".format(tag_value, cls.__name__))


class DelegateByTag(FindByTag):

    def __new__(cls, *args, **kwargs):
        tag = cls.get_current_tag()
        delegated_class = cls.find_subclass_for(tag)

        return super().__new__(delegated_class)

    @staticmethod
    def get_current_tag():
        raise NotImplementedError()


class DelegateByNetmode(DelegateByTag):

    @staticmethod
    def get_current_tag():
        return WorldInfo.netmode

# TODO make this more generic
# Delegate actor definition for env
# Create from file