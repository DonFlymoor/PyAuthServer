from cProfile import Profile
from collections import defaultdict

from .signals import ProfileSignal, SignalListener

__all__ = ['ProfileManager', 'profiler']


class ProfileManager(SignalListener):

    def __init__(self):
        self.register_signals()

        self._profiles = defaultdict(Profile)

    @ProfileSignal.global_listener
    def update_profiler(self, profile_id, start, dump=True):
        profile = self._profiles[profile_id]
        if start:
            profile.enable()
        else:
            profile.disable()
            if dump:
                profile.dump_stats("C:/{}.results".format(profile_id))

profiler = ProfileManager()
