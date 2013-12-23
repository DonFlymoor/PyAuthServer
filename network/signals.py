from .type_register import TypeRegister
from .conditions import is_signal_listener
from .decorators import signal_listener

from collections import defaultdict
from inspect import getmembers, signature


class SignalListener:
    """Provides interface for class based signal listeners
    Uses class instance as target for signal binding
    Optional greedy binding (binds the events supported by both classes)
    """

    @property
    def signal_callbacks(self):
        """Property
        Gets the marked signal callbacks
        @return: generator of (name, attribute) pairs"""
        for name, val in getmembers(self.__class__):

            if not hasattr(val, "__annotations__"):
                continue

            if not (callable(val) and is_signal_listener(val)):
                continue

            yield (name, getattr(self, name))

    def register_child(self, child, signal_store=None, greedy=False):
        """Subscribes child to parent for signals
        @param child: Child to subscribe for 
        @param signal_store: SignalListener subclass instance, default=None
        @param greedy: Determines if child should bind its own events, default=False
        """
        # Mirror own signals by default
        if signal_store is None:
            signal_store = self

        for name, callback in signal_store.signal_callbacks:
            for signal, *_ in Signal.get_signals(callback):
                signal.set_parent(child, self)

        if greedy:
            self.register_child(child, child)

    def unregister_child(self, child, signal_store=None, greedy=False):
        """Un-subscribes child to parent for signals
        @param child: Child to un-subscribe for
        @param signal_store: SignalListener subclass instance, default=None
        @param greedy: Determines if child should un-bind its own events, default=False
        """
        # Mirror own signals by default
        if signal_store is None:
            signal_store = self

        for name, callback in signal_store.signal_callbacks:
            for signal, *_ in Signal.get_signals(callback):
                signal.remove_parent(child, self)

        if greedy:
            self.unregister_child(child, child)

    def register_signals(self):
        """Registers signals to observer
        """
        for name, callback in self.signal_callbacks:
            Signal.subscribe(self, callback)

        Signal.update_graph()

    def unregister_signals(self):
        """Un-registers signals from observer
        """
        for name, callback in self.signal_callbacks:
            Signal.unsubscribe(self, callback)

        Signal.update_graph()


class Signal(metaclass=TypeRegister):
    """Observer class for signal-like invocation
    """

    @classmethod
    def register_subtype(cls):
        cls.subscribers = {}
        cls.isolated_subscribers = {}

        cls.to_subscribe = {}
        cls.to_isolate = {}

        cls.to_unsubscribe = []
        cls.to_unisolate = []

        cls.children = {}
        cls.to_unchild = set()
        cls.to_child = defaultdict(set)

    @staticmethod
    def get_signals(decorated):
        return decorated.__annotations__['signals']

    @classmethod
    def register_type(cls):
        cls.register_subtype()
        cls.highest_signal = cls

    @classmethod
    def unsubscribe(cls, identifier, callback):
        signals_data = cls.get_signals(callback)

        for signal_cls, is_context in signals_data:
            remove_list = (signal_cls.to_unisolate if is_context else
                         signal_cls.to_unsubscribe)
            remove_list.append(identifier)

            signal_children = signal_cls.children

            if identifier in signal_children:
                for child in signal_children[identifier]:
                    signal_cls.remove_parent(child, identifier)

            for parent, next_children in signal_children.items():
                if identifier in next_children:
                    signal_cls.remove_parent(identifier, parent)

    @classmethod
    def set_parent(cls, identifier, parent_identifier):
        cls.to_child[parent_identifier].add(identifier)

    @classmethod
    def remove_parent(cls, identifier, parent_identifier):
        cls.to_unchild.add((identifier, parent_identifier))

    @classmethod
    def on_subscribed(cls, is_contextual, subscriber, data):
        pass

    @classmethod
    def get_total_subscribers(cls):
        return len(cls.subscribers) + len(cls.isolated_subscribers)

    @classmethod
    def subscribe(cls, identifier, callback):
        signals_data = cls.get_signals(callback)
        func_signature = signature(callback)

        accepts_signal = "signal" in func_signature.parameters
        accepts_target = "target" in func_signature.parameters

        for signal_cls, is_context in signals_data:
            data_dict = (signal_cls.to_isolate if is_context else
                         signal_cls.to_subscribe)
            data_dict[identifier] = callback, accepts_signal, accepts_target

    @classmethod
    def update_state(cls):
        if cls.to_subscribe:
            local_to_subscribe = cls.to_subscribe.copy()
            cls.subscribers.update(cls.to_subscribe)
            cls.to_subscribe.clear()
            for identifier, data in local_to_subscribe.items():
                cls.on_subscribed(False, identifier, data)

        if cls.to_isolate:
            cls.isolated_subscribers.update(cls.to_isolate)
            local_to_isolate = cls.to_isolate.copy()
            cls.to_isolate.clear()
            for identifier, data in local_to_isolate.items():
                cls.on_subscribed(True, identifier, data)

        # Run safe notifications
        if cls.to_unsubscribe:
            for key in cls.to_unsubscribe:
                cls.subscribers.pop(key, None)
            cls.to_unsubscribe.clear()

        if cls.to_unisolate:
            for key in cls.to_unisolate:
                cls.isolated_subscribers.pop(key, None)
            cls.to_unisolate.clear()

        # Add new children
        cls.children.update(cls.to_child)
        cls.to_child.clear()

        # Remove old children
        children = cls.children
        for (child, parent) in cls.to_unchild:
            parent_children_dict = children[parent]
            parent_children_dict.remove(child)

            if not parent_children_dict:
                children.pop(parent)

        cls.to_unchild.clear()

        # Catch any missed subscribers
        if cls.to_subscribe or cls.to_isolate:
            cls.update_state()

    @classmethod
    def update_graph(cls):
        for cls in cls._types:
            cls.update_state()

    @classmethod
    def invoke_signal(cls, args, target, kwargs, callback,
                            supply_signal, supply_target):
        if supply_signal:
            if supply_target:
                callback(*args, signal=cls, target=target, **kwargs)
            else:
                callback(*args, signal=cls, **kwargs)

        elif supply_target:
            callback(*args, target=target, **kwargs)

        else:
            callback(*args, **kwargs)

    @classmethod
    def invoke_targets(cls, all_targets, *args, target=None, **kwargs):
        targets = [target]

        while targets:
            try:
                target_ = targets.pop(0)

            except IndexError:
                return

            if target_ is None:
                continue

            cls.update_graph()
            # Bugfix? In future may need to do an == loop to check
            # If the child is a context listener
            if target_ in all_targets:
                callback, supply_signal, supply_target = all_targets[target_]
                # Invoke with the same target context even if this is a child
                cls.invoke_signal(args, target, kwargs, callback,
                                 supply_signal, supply_target)

            if target_ in cls.children:
                targets.extend(cls.children[target_])

    @classmethod
    def invoke_general(cls, all_subscribers, *args, target=None, **kwargs):
        for (callback, supply_signal, supply_target) in \
                                all_subscribers.values():

            cls.invoke_signal(args, target, kwargs, callback,
                             supply_signal, supply_target)

    @classmethod
    def invoke(cls, *args, target=None, **kwargs):
        cls.invoke_targets(cls.isolated_subscribers, *args,
                           target=target, **kwargs)
        cls.invoke_general(cls.subscribers, *args,
                           target=target, **kwargs)

        cls.invoke_parent(*args, target=target, **kwargs)

    @classmethod
    def invoke_parent(cls, *args, target=None, **kwargs):

        if cls.highest_signal == cls:
            return

        try:
            parent = cls.__mro__[1]

        except IndexError:
            return

        parent.invoke(*args, target=target, **kwargs)

    @classmethod
    def global_listener(cls, func):
        return signal_listener(cls, True)(func)

    @classmethod
    def listener(cls, func):
        return signal_listener(cls, False)(func)


class CachedSignal(Signal):

    @classmethod
    def register_subtype(cls):
        # Unfortunate hack to reproduce super() behaviour
        Signal.register_subtype.__func__(cls)
        cls.cache = []

    @classmethod
    def invoke(cls, *args, subscriber_data=None, target=None, **kwargs):

        # Only cache normal invocations
        if subscriber_data is None:
            cls.cache.append((args, target, kwargs))

            cls.invoke_targets(cls.isolated_subscribers, *args,
                               target=target, **kwargs)
            cls.invoke_general(cls.subscribers, *args,
                               target=target, **kwargs)

        # Otherwise run a general invocation on new subscriber
        else:
            cls.invoke_general(subscriber_data, *args,
                               target=target, **kwargs)

        cls.invoke_parent(*args, target=target, **kwargs)

    @classmethod
    def on_subscribed(cls, is_contextual, subscriber, data):
        # Only inform global listeners (wouldn't work anyway)
        if is_contextual:
            return

        subscriber_info = {subscriber: data}
        for previous_args, target, previous_kwargs in cls.cache:
            cls.invoke(*previous_args, target=target,
                       subscriber_data=subscriber_info,
                       **previous_kwargs)


class ReplicableRegisteredSignal(CachedSignal):
    pass


class ReplicableUnregisteredSignal(Signal):
    pass


class ConnectionErrorSignal(Signal):
    pass


class ConnectionSuccessSignal(Signal):
    pass


class NetworkSendSignal(Signal):
    pass


class NetworkReceiveSignal(Signal):
    pass


class UpdateSignal(Signal):
    pass


class ProfileSignal(Signal):
    pass
