from collections import defaultdict


class MessagePasser:

    def __init__(self):
        self._subscribers = defaultdict(list)

    def add_subscriber(self, message_id, callback):
        self._subscribers[message_id].append(callback)

    def remove_subscriber(self, message_id, callback):
        self._subscribers[message_id].pop(callback)

    def send(self, identifier, message):
        try:
            callbacks = self._subscribers[identifier]

        except KeyError:
            return

        for callbacks in callbacks:
            callbacks(message)


