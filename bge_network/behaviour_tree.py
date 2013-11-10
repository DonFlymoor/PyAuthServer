from operator import attrgetter
from network import Enum, Signal, SignalListener
from itertools import islice


class EvaluationState(metaclass=Enum):
    values = "success", "failure", "running", "error", "ready"


class BehaviourTree:

    def __init__(self, signaller, root=None):
        self.signaller = signaller
        self.blackboard = {}

        if root is None:
            root = SelectorNode()
        self.root = root

    @property
    def root(self):
        return self._root

    @root.setter
    def root(self, value):
        self._root = value
        self._root.change_signaller(self.signaller)

    def debug(self):
        self._root.print_tree()

    def update(self, delta_time):
        self.blackboard['delta_time'] = delta_time
        self.root.update(self.blackboard)

    def reset(self):
        self.root.reset(self.blackboard)
        self.blackboard.clear()


class LeafNode(SignalListener):

    def __init__(self):
        super().__init__()

        self.register_signals()
        self._signal_parent = self

        self.state = EvaluationState.ready
        self.name = ""

    def change_signaller(self, parent):
        parent.register_greedy_child(self)
        if self._signal_parent is not self:
            self._signal_parent.unregister_greedy_child(self)
        self._signal_parent = parent

    def evaluate(self, blackboard):
        pass

    def on_enter(self, blackboard):
        pass

    def on_exit(self, blackboard):
        pass

    def update(self, blackboard):
        if self.state != EvaluationState.running:
            self.state = EvaluationState.running
            self.on_enter(blackboard)

        new_state = self.evaluate(blackboard)

        if new_state is not None:
            self.state = new_state

        if self.state != EvaluationState.running:
            self.on_exit(blackboard)

    def reset(self):
        self.state = EvaluationState.ready
        self.on_exit(blackboard)

    def print_tree(self, index=0):
        print('   ' * index, '->', index, self)

    def __repr__(self):
        return "[{} {}] : {}".format(self.__class__.__name__,
                                     self.name,
                                     EvaluationState[self.state])


class InnerNode(LeafNode):

    def __init__(self, *children):
        super().__init__()

        self._children = []

        for child in children:
            self.add_child(child)

    @property
    def children(self):
        return self._children

    def add_child(self, child, index=None):
        if index is None:
            self._children.append(child)

        else:
            self._children.insert(index, child)

        child.change_signaller(self._signal_parent)

    def change_signaller(self, identifier):
        super().change_signaller(identifier)

        for child in self.children:
            child.change_signaller(identifier)

    def print_tree(self, index=0):
        super().print_tree(index)
        for child in self.children:

            child.print_tree(index + 1)

        if index == 0:
            print()

    def remove_child(self, child):
        self._children.remove(child)
        child.change_signaller(child)

    def reset(self):
        super().reset()

        for child in self._children:
            child.reset()


class ResumableNode(InnerNode):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._resume_index = 0
        self.should_restart = False

    @property
    def resume_index(self):
        return self._resume_index

    @resume_index.setter
    def resume_index(self, value):
        self._resume_index = value

    def on_exit(self, blackboard):
        self.resume_index = 0


class SelectorNode(ResumableNode):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def evaluate(self, blackboard):
        start = 0 if self.should_restart else self.resume_index
        remembered_resume = False

        for index, child in enumerate(islice(self.children, start, None)):
            child.update(blackboard)

            if child.state == EvaluationState.running:
                self.resume_index = index + start
                remembered_resume = True
                break

            if child.state == EvaluationState.success:
                break

        else:
            return EvaluationState.failure

        # Copy child's state
        if remembered_resume:
            child = self.children[self.resume_index]

        return child.state


class ConcurrentNode(ResumableNode):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._failure_limit = 1

    @property
    def failure_limit(self):
        return self._failure_limit

    def on_exit(self, blackboard):
        self.resume_index = 0

    def evaluate(self, blackboard):
        failed = 0
        start = 0 if self.should_restart else self.resume_index
        remembered_resume = False

        for index, child in enumerate(islice(self.children, start, None)):
            child.update(blackboard)

            # Increment failure count (anything that isn't a success)
            if child.state != EvaluationState.success:
                failed += 1

            # Remember the first child that needed completion
            if (child.state == EvaluationState.running
                            and not remembered_resume):
                remembered_resume = True
                self.resume_index = start + index

            # At the limit we then return the last/ last running child's status
            if failed == self.failure_limit:
                if remembered_resume:
                    return self.children[self.resume_index].state

                else:
                    return child.state

        return EvaluationState.success


class SequenceNode(ConcurrentNode):

    @property
    def failure_limit(self):
        return 1


class LoopNode(SequenceNode):

    def evaluate(self, blackboard):
        while self.state not in (EvaluationState.failure,
                            EvaluationState.error):
            super().evaluate(blackboard)


class SignalLeafNode(LeafNode):

    @property
    def signaller(self):
        if self._signal_parent is self:
            return None
        return self._signal_parent


class SignalInnerNode(InnerNode):

    @property
    def signaller(self):
        if self._signal_parent is self:
            return None
        return self._signal_parent
