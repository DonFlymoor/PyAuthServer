from .bases import Attribute, TypeRegister, InstanceRegister
from .enums import Roles
from .modifiers import simulated

from random import choice
from inspect import getmembers
from collections import defaultdict, deque

class Replicable(InstanceRegister, metaclass=TypeRegister):
    '''Replicable base class
    Holds record of instantiated replicables and replicable types
    Default method for notification and generator for conditions.
    Additional attributes for attribute values (from descriptors) and complaining attributes'''
    _by_types = defaultdict(list)
    _types = {}
    
    def __init__(self, instance_id=None, register=False):
        # Create a flag that is set when attributes change (if permitted)
        super().__init__(instance_id, register, allow_random_key=True)
        
        self._data = {}
        self._calls = deque()
        self._complain = {}    
        self._subscribers = []  
        
        # If this is a local replicable
        self._local = False  
        
        # Invoke descriptors to register values
        for name, value in getmembers(self):
            getattr(self, name)
               
    @classmethod
    def _create_or_return(cls, base_cls, instance_id, register=False):
        try:
            existing = cls.get_from_graph(instance_id)
        except LookupError:
            return base_cls(instance_id, register)
        
        else:
            if existing._local:
                return base_cls(instance_id, register)
            
            return existing
    
    def request_registration(self, instance_id, verbose=False):
        # This is static or replicated 
        if instance_id is None: 
            self._local = True
            
        # Therefore we will have authority to change things
        if instance_id in self.get_entire_graph_ids():
            instance = self.remove_from_entire_graph(instance_id)
            
            assert instance._local, "Authority over instance id {} is unresolveable".format(instance_id)
            
            # Possess the instance id
            super().request_registration(instance_id)
            
            if verbose:
                print("Transferring authority of id {} from {} to {}".format(instance_id, instance, self))
            
            # Forces reassignment of instance id
            instance.request_transform(None)
        
        if verbose:
            print("Create {} with id {}".format(self.__class__.__name__, instance_id))
            
        # Possess the instance id
        super().request_registration(instance_id)
    
    def register_to_graph(self):
        super().register_to_graph()
        self.__class__._by_types[type(self)].append(self)
    
    def unregister_from_graph(self):
        super().unregister_from_graph()
        self.__class__._by_types[type(self)].pop(self)
      
    def possessed_by(self, other):
        self.owner = other
    
    def unpossessed(self):
        self.owner = None
    
    def subscribe(self, subscriber):
        self._subscribers.append(subscriber)
    
    def unsubscribe(self, subscriber):
        self._subscribers.remove(subscriber)
        
    def on_delete(self):
        self.__del__()
        
        self._to_unregister.add(self)
        
        for subscriber in self._subscribers:
            subscriber(self)
    
    def on_notify(self, name):
        print("{} was changed by the network".format(name))
        
    def conditions(self, is_owner, is_complaint, is_initial):
        if False:
            yield
        
    def update(self, elapsed):
        pass
    
    def __description__(self):
        return hash(self.instance_id)
    
class BaseWorldInfo(Replicable):
    '''Holds info about game'''
    netmode = None
    rules = None
    
    local_role = Roles.authority
    remote_role = Roles.simulated_proxy
    
    elapsed = Attribute(0.0, complain=False)
    
    @property
    def actors(self):
        return Replicable.get_graph_instances()
    
    def subclass_of(self, actor_type):
        return (a for a in self.actors if isinstance(a, actor_type))
    
    def conditions(self, is_owner, is_complain, is_initial):
        if is_initial:
            yield "elapsed"

    @simulated
    def update(self, delta):
        self.elapsed += delta
    
    type_is = Replicable._by_types.get
    get_actor = Replicable.get_from_graph
                
class Controller(Replicable):
    
    local_role = Roles.authority
    remote_role = Roles.autonomous_proxy
    
    pawn = Attribute(type_of=Replicable, complain=True)
    
    input_class = None
    owner = None
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.saved_moves = []
    
    def possess(self, replicable):
        self.pawn = replicable
        self.pawn.possessed_by(self)
    
    def unpossess(self):
        self.pawn.unpossessed()
        self.pawn = None
    
    def on_delete(self):
        super().on_delete()        
        self.pawn.on_delete()
                
    def conditions(self, is_owner, is_complaint, is_initial):
        
        if is_complaint:
            yield "pawn"  
        
    def create_player_input(self):
        if callable(self.input_class):
            self.player_input = self.input_class(self)
            
    def player_update(self, elapsed):
        pass