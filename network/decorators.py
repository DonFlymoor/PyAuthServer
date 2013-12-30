from functools import wraps
from inspect import signature


def reliable(func):
    func.__annotations__['reliable'] = True
    return func


def simulated(func):
    func.__annotations__['simulated'] = True
    return func


def supply_data(**args):
    def wrapper(func):
        func.__annotations__['class_data'] = args
        return func
    return wrapper


def signal_listener(signal_type, global_listener):
    def wrapper(func):
        signals = func.__annotations__.setdefault('signals', [])
        signals.append((signal_type, not global_listener))
        return func
    return wrapper


def requires_netmode(netmode):
    """Decorator
    @param netmode: netmode required to execute function
    @requires: netmode context for execution of function
    @return: decorator that prohibits function execution for incorrect netmodes"""

    def wrapper(func):
        from .replicables import WorldInfo

        @wraps(func)
        def _wrapper(*args, **kwargs):
            if WorldInfo.netmode != netmode:
                return

            return func(*args, **kwargs)

        return _wrapper

    return wrapper


def for_netmode(netmode):

    def wrapper(cls):
        cls._netmode_data = cls, netmode
        return cls

    return wrapper
