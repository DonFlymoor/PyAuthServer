import bge
import bgui

from os import path, listdir

from datetime import datetime
from collections import OrderedDict

from matchmaker import Matchmaker
from bge_network import (ConnectionErrorSignal, ConnectionSuccessSignal,
                     SignalListener, WorldInfo, ManualTimer)
from signals import ConsoleMessage

CENTERY = bgui.BGUI_DEFAULT|bgui.BGUI_CENTERY
CENTERX = bgui.BGUI_DEFAULT|bgui.BGUI_CENTERX
CENTERED = bgui.BGUI_DEFAULT|bgui.BGUI_CENTERED


class ConsoleRenderer(bgui.ListBoxRenderer):
    def __init__(self, listbox):
        super().__init__(listbox)

        self.label.color = 0, 0, 0, 1


class TableRenderer(bgui.ListBoxRenderer):

    def __init__(self, listbox, labels=[],
                 theming={}, pt_size=None):
        super().__init__(listbox)

        self.listbox = listbox

        self.frame = bgui.Frame(listbox, "frame", size=[1, 1], pos=[0, 0])

        self.labels = OrderedDict()
        self.frames = OrderedDict()

        total = len(labels)

        for index, name in enumerate(labels):
            frame_pos = [(index / total), 0.0]
            label_pos = [0.0, 0.3]

            frame = bgui.Frame(self.frame, "{}_frame".format(name),
                                size=[1 / total, 1],
                                pos=list(frame_pos),
                                sub_theme="TableEntryFrame")
            label = bgui.Label(parent=frame, name="{}_label".format(name),
                               pos=list(label_pos), pt_size=pt_size,
                               sub_theme="TableEntryLabel",
                               options=bgui.BGUI_DEFAULT | bgui.BGUI_CENTERED,
                               font=theming.get("Font"))

            self.frames[name] = frame
            self.labels[name] = label

        self.theming = theming

    def render_item(self, collection):

        if collection == self.listbox.selected:
            self.frame.colors = [self.theming.get("Selected", (0, 0, 0, 0.5))
                                 for i in range(4)]

        else:
            self.frame.colors = [self.theming.get("Deselected", (0, 0, 0, 0))
                                 for i in range(4)]

        for (name, item) in collection:

            if name in self.labels:
                self.labels[name].text = str(item)

        return self.frame


class System(bgui.System):

    def __init__(self):
        theme_path = bge.logic.expandPath("//themes")

        super().__init__(theme_path)

        self.scene = bge.logic.getCurrentScene()

        self._subscribers = []
        self._keymap = {getattr(bge.events, val): getattr(bgui, val)
                        for val in dir(bge.events) if (val.endswith('KEY') or \
                               val.startswith('PAD')) and hasattr(bgui, val)}

        self.scene.post_draw.append(self.render)

    def update(self, delta_time):
        # Handle the mouse
        mouse = bge.logic.mouse

        pos = list(mouse.position)
        pos[0] *= bge.render.getWindowWidth()
        pos[1] = bge.render.getWindowHeight() - \
                (bge.render.getWindowHeight() * pos[1])

        mouse_state = bgui.BGUI_MOUSE_NONE
        mouse_events = mouse.events

        if mouse_events[bge.events.LEFTMOUSE] == \
            bge.logic.KX_INPUT_JUST_ACTIVATED:
            mouse_state = bgui.BGUI_MOUSE_CLICK
        elif mouse_events[bge.events.LEFTMOUSE] == \
            bge.logic.KX_INPUT_JUST_RELEASED:
            mouse_state = bgui.BGUI_MOUSE_RELEASE
        elif mouse_events[bge.events.LEFTMOUSE] == \
            bge.logic.KX_INPUT_ACTIVE:
            mouse_state = bgui.BGUI_MOUSE_ACTIVE

        self.update_mouse(pos, mouse_state)

        # Handle the keyboard
        keyboard = bge.logic.keyboard

        key_events = keyboard.events
        is_shifted = key_events[bge.events.LEFTSHIFTKEY] == \
            bge.logic.KX_INPUT_ACTIVE or \
            key_events[bge.events.RIGHTSHIFTKEY] == \
            bge.logic.KX_INPUT_ACTIVE

        for key, state in keyboard.events.items():
            if state == bge.logic.KX_INPUT_JUST_ACTIVATED:
                self.update_keyboard(self._keymap[key], is_shifted)

        visible_panel = False

        for panel in self.children.values():

            if panel.visible:
                panel.update(delta_time)
                if panel.uses_mouse:
                    visible_panel = True

        bge.logic.mouse.visible = visible_panel


class SpriteSequence(bgui.Image):

    def __init__(self, *args, length, loop=False, frame_index=0, **kwargs):
        # Create internal image
        super().__init__(*args, **kwargs)

        self.length = length
        self.loop = loop
        self.frame = frame_index

    @property
    def frame(self):
        return self._index

    @frame.setter
    def frame(self, index):
        x_size = 1 / self.length

        if not 0 < index < self.length:
            raise IndexError("Frame not found")

        self._index = index
        self.texco = [[x_size * index, 0], [x_size * (index + 1), 0],
                      [x_size * (index + 1), 1], [x_size * index, 1]]

    def next_frame(self):
        try:
            self.frame += 1

        except IndexError:
            self.frame = 0 if self.loop else -1

    def previous_frame(self):
        try:
            self.frame -= 1

        except IndexError:
            self.frame = -1 if self.loop else 0


class ImageSequence(SpriteSequence):

    def __init__(self, parent, name, source, *args, **kwargs):
        self._source = source
        self._images = []

        self.update_images()

        # Create internal image
        super().__init__(parent, name, "", *args, **kwargs)

    def update_images(self):
        tail, head = path.split(self._source)
        self._images = sorted(n for n in listdir(tail)
                           if n.startswith(head)
                           )

    @property
    def frame(self):
        return super().frame

    @frame.setter
    def frame(self, index):
        try:
            source = self._images[index]

        except IndexError as err:
            raise IndexError("Could not find image with this index") from err

        self.update_image(source)


class Panel(bgui.Frame):

    def __init__(self, system, name):
        super().__init__(parent=system, name=name, size=[1, 1], options=CENTERED)

        self.uses_mouse = False

    def update(self, delta_time):
        pass


class ConsolePanel(Panel, SignalListener):

    def __init__(self, system):
        super().__init__(system, "Console")

        self.messages = []
        self.message_box = bgui.ListBox(parent=self, name="messages",
                                        items=self.messages, pos=[0.1, 0.05])
        self.message_box.renderer = ConsoleRenderer(self.message_box)

        self.register_signals()

    @ConsoleMessage.global_listener
    def receive_message(self, message):
        timestamp = datetime.today().strftime("%H : %M : %S || ")
        separator = ' '
        self.messages.append(timestamp + separator + "'{}'".format(message))


class LegacyConnectPanel(Panel, SignalListener):

    def __init__(self, system):
        super().__init__(system, "Connect")

        self.connecter = None
        self.aspect = bge.render.getWindowWidth() / bge.render.getWindowHeight()

        self.center_column = bgui.Frame(parent=self, name="center",
                                        size=[0.8, 0.8], options=CENTERED,
                                        sub_theme="ContentBox")

        self.connect_label = bgui.Label(parent=self.center_column,
                                        name="label", pos=[0.0, 0.20],
                                        text="Connection Wizard",
                                        options=CENTERX, sub_theme="Title")

        # IP input
        self.connection_row = bgui.Frame(parent=self.center_column,
                                         name="connection_frame",
                                         size=[0.8, 0.1], pos=[0.0, 0.5],
                                         sub_theme="ContentRow",
                                         options=CENTERX)

        self.addr_group = bgui.Frame(parent=self.connection_row,
                                     name="addr_group", size=[0.70, 1.0],
                                     pos=[0.0, 0.5], options=CENTERY,
                                     sub_theme="RowGroup")
        self.port_group = bgui.Frame(parent=self.connection_row,
                                     name="port_group", size=[0.3, 1.0],
                                     pos=[0.7, 0.5], options=CENTERY,
                                     sub_theme="RowGroup")

        self.addr_label = bgui.Label(parent=self.addr_group, name="addr_label",
                                     text="IP Address:", options=CENTERY,
                                     pos=[0.05, 0.0])
        self.port_label = bgui.Label(parent=self.port_group, name="port_label",
                                     text="Port:", options=CENTERY,
                                     pos=[0.05, 0.0])

        self.addr_field = bgui.TextInput(parent=self.addr_group,
                                         name="addr_field", size=[0.6, 1.0],
                                         pos=[0.4, 0.0], options=CENTERY,
                                         text="localhost",
                                         allow_empty=False)
        self.port_field = bgui.TextInput(parent=self.port_group,
                                         name="port_field", size=[0.6, 1.0],
                                         pos=[0.4, 0.0], options=CENTERY,
                                         type=bgui.BGUI_INPUT_INTEGER,
                                         text="1200",
                                         allow_empty=False)

        # Allows input fields to accept input when not hovered
        self.connection_row.is_listener = True

        # Data input
        self.data_row = bgui.Frame(parent=self.center_column,
                                   name="data_frame", size=[0.8, 0.1],
                                   pos=[0.0, 0.3], sub_theme="ContentRow",
                                   options=CENTERX)

        self.connect_group = bgui.Frame(parent=self.data_row,
                                     name="connect_group", size=[0.3, 1.0],
                                     pos=[0.0, 0.5], options=CENTERY,
                                     sub_theme="RowGroup")

        self.connect_button = bgui.FrameButton(parent=self.connect_group,
                                               name="connect_button",
                                               text="Connect", size=[1.0, 1.0],
                                               options=CENTERED)

        self.error_group = bgui.Frame(parent=self.data_row,
                                     name="error_group", size=[0.7, 1.0],
                                     pos=[0.3, 0.5], options=CENTERY,
                                     sub_theme="RowGroup")

        self.connect_message = bgui.Label(parent=self.error_group,
                                          name="connect_status",
                                          text="",
                                          pos=[0.0, 0.0],
                                          options=CENTERED)

        self.logo = bgui.Image(parent=self.center_column, name="logo",
                               img="legend.jpg", size=[0.3, 0.3],
                               pos=[0.5, 0.65], aspect=self.aspect,
                               options=CENTERX)

        self.connect_button.on_click = self.do_connect
        self.uses_mouse = True

        self.register_signals()

    def do_connect(self, button):
        if not callable(self.connecter):
            return

        self.connecter(self.addr_field.text, int(self.port_field.text))

    @ConnectionSuccessSignal.global_listener
    def on_connect(self, target):
        self.visible = False

    @ConnectionErrorSignal.global_listener
    def on_error(self, error, target, signal):
        self.connect_message.text = str(error)

    def update(self, delta_time):
        self.connect_button.frozen = self.port_field.invalid


class ConnectPanel(Panel, SignalListener):

    def __init__(self, system):
        super().__init__(system, "Connect")

        self.connecter = None
        self.aspect = bge.render.getWindowWidth() / bge.render.getWindowHeight()

        self.center_column = bgui.Frame(parent=self, name="center",
                                        size=[0.8, 0.8], options=CENTERED,
                                        sub_theme="ContentBox")

        self.connect_label = bgui.Label(parent=self.center_column,
                                        name="label", pos=[0.0, 0.025],
                                        text="Connection Wizard",
                                        options=CENTERX, sub_theme="Title")

        # IP input
        self.connection_row = bgui.Frame(parent=self.center_column,
                                         name="connection_frame",
                                         size=[0.8, 0.08], pos=[0.0, 0.85],
                                         sub_theme="ContentRow",
                                         options=CENTERX)

        self.addr_group = bgui.Frame(parent=self.connection_row,
                                     name="addr_group", size=[0.70, 1.0],
                                     pos=[0.0, 0.5], options=CENTERY,
                                     sub_theme="RowGroup")
        self.port_group = bgui.Frame(parent=self.connection_row,
                                     name="port_group", size=[0.3, 1.0],
                                     pos=[0.7, 0.5], options=CENTERY,
                                     sub_theme="RowGroup")

        self.addr_label = bgui.Label(parent=self.addr_group, name="addr_label",
                                     text="IP Address:", options=CENTERY,
                                     pos=[0.05, 0.0])
        self.port_label = bgui.Label(parent=self.port_group, name="port_label",
                                     text="Port:", options=CENTERY,
                                     pos=[0.05, 0.0])

        self.addr_field = bgui.TextInput(parent=self.addr_group,
                                         name="addr_field", size=[0.6, 1.0],
                                         pos=[0.4, 0.0], options=CENTERY,
                                         text="localhost",
                                         allow_empty=False)
        self.port_field = bgui.TextInput(parent=self.port_group,
                                         name="port_field", size=[0.6, 1.0],
                                         pos=[0.4, 0.0], options=CENTERY,
                                         type=bgui.BGUI_INPUT_INTEGER,
                                         text="1200",
                                         allow_empty=False)

        # Allows input fields to accept input when not hovered
        self.connection_row.is_listener = True

        # Data input
        self.data_row = bgui.Frame(parent=self.center_column,
                                   name="data_frame", size=[0.8, 0.08],
                                   pos=[0.0, 0.77], sub_theme="ContentRow",
                                   options=CENTERX)

        self.error_msg_group = bgui.Frame(parent=self.data_row,
                                     name="error_msg_group", size=[0.3, 1.0],
                                     pos=[0.0, 0.5], options=CENTERY,
                                     sub_theme="RowGroup")

        self.error_msg_label = bgui.Label(parent=self.error_msg_group,
                                          name="error_status",
                                          text="Connection Information:",
                                          pos=[0.0, 0.0],
                                          options=CENTERED)

        self.error_group = bgui.Frame(parent=self.data_row,
                                     name="error_group", size=[0.7, 1.0],
                                     pos=[0.3, 0.5], options=CENTERY,
                                     sub_theme="RowGroup")

        self.connect_message = bgui.Label(parent=self.error_group,
                                          name="connect_status",
                                          text="",
                                          pos=[0.0, 0.0],
                                          options=CENTERED)

        self.server_controls = bgui.Frame(parent=self.center_column,
                                   name="server_controls", size=[0.8, 0.08],
                                   pos=[0.0, 0.69], sub_theme="ContentRow",
                                   options=CENTERX)

        self.refresh_group = bgui.Frame(parent=self.server_controls,
                                     name="refresh_group", size=[0.15, 1.0],
                                     pos=[0.0, 0.5], options=CENTERY,
                                     sub_theme="RowGroup")

        self.refresh_button = bgui.FrameButton(parent=self.refresh_group,
                                               name="refresh_button",
                                               text="Update", size=[1.0, 1.0],
                                               options=CENTERED)

        self.connect_group = bgui.Frame(parent=self.server_controls,
                                     name="connect_group", size=[0.15, 1.0],
                                     pos=[0.15, 0.5], options=CENTERY,
                                     sub_theme="RowGroup")

        self.connect_button = bgui.FrameButton(parent=self.connect_group,
                                               name="connect_button",
                                               text="Connect", size=[1.0, 1.0],
                                               options=CENTERED)

        self.match_group = bgui.Frame(parent=self.server_controls,
                                     name="match_group", size=[0.7, 1.0],
                                     pos=[0.3, 0.5], options=CENTERY,
                                     sub_theme="RowGroup")
        self.match_label = bgui.Label(parent=self.match_group, name="match_label",
                                     text="Matchmaker:", options=CENTERY,
                                     pos=[0.025, 0.0])
        self.match_field = bgui.TextInput(parent=self.match_group,
                                         name="match_field", size=[0.8, 1.0],
                                         pos=[0.2, 0.0], options=CENTERY,
                                         text="http://coldcinder.co.uk/networking/matchmaker",
                                         allow_empty=False)

        self.servers_list = bgui.Frame(parent=self.center_column,
                                   name="server_list", size=[0.8, 0.6],
                                   pos=[0.0, 0.09], sub_theme="ContentRow",
                                   options=CENTERX)
        self.servers = []
        self.server_headers = ["name",
                               "map",
                               "players",
                               "max_players",
                               ]

        self.matchmaker = Matchmaker("")

        self.servers_box = bgui.ListBox(parent=self.servers_list,
                                        name="servers",
                                        items=self.servers, padding=0.0,
                                        size=[1.0, 1.0],
                                        pos=[0.0, 0.0])
        self.servers_box.renderer = TableRenderer(self.servers_box,
                                              labels=self.server_headers)

        #self.sprite = SpriteSequence(self.servers_box, "sprite", "C:/Users/Angus/workspace/ReplicationSystem/trunk/blender_example/themes/477.png",
                #                     length=20, loop=True,  size=[1, 1],  pos=[0, 0],
                 #                    relative_path=False)
        #self.sprite_timer = ManualTimer(target_value=1 / 20,
                                     #   repeat=True,
                                     #   on_target=self.sprite.next_frame)
        self.connect_button.on_click = self.do_connect
        self.refresh_button.on_click = self.do_refresh
        self.servers_box.on_select = self.on_select
        self.uses_mouse = True

        self.register_signals()

    def on_select(self, list_box, entry):
        data = dict(entry)

        self.addr_field.text = data['address']
        self.port_field.text = data['port']

    def do_refresh(self, button):
        self.matchmaker.url = self.match_field.text
        self.matchmaker.perform_query(self.evaluate_servers,
                                      self.matchmaker.server_query())

    def evaluate_servers(self, response):
        self.servers[:] = [tuple(entry.items()) for entry in response]
        self.connect_message.text = ("Refreshed Server List" if self.servers
                                    else "No Servers Found")

    def do_connect(self, button):
        if not callable(self.connecter):
            return

        self.connecter(self.addr_field.text, int(self.port_field.text))

    @ConnectionSuccessSignal.global_listener
    def on_connect(self, target):
        self.visible = False

    @ConnectionErrorSignal.global_listener
    def on_error(self, error, target, signal):
        self.connect_message.text = str(error)

    def update(self, delta_time):
        self.connect_button.frozen = self.port_field.invalid
        self.matchmaker.update()
        #self.sprite_timer.update(delta_time)


class SamanthaPanel(Panel):

    def __init__(self, system):
        super().__init__(system, "Samantha_Overlay")

        aspect = bge.render.getWindowWidth() / bge.render.getWindowHeight()
        scene = system.scene

        camera = scene.objects['Samantha_Camera']

        self.video_source = bge.texture.ImageRender(scene, camera)
        self.video_source.background = 255, 255, 255, 255

        self.video = bgui.ImageRender(parent=self, name="Samantha_Video",
                                    pos=[0, 0], size=[0.2, 0.2],
                                    aspect=aspect, source=self.video_source)


class BGESystem(System):

    def __init__(self):
        super().__init__()

        self.connect_panel = ConnectPanel(self)
        #self.console_panel = ConsolePanel(self)
        #self.samantha_panel = SamanthaPanel(self)
