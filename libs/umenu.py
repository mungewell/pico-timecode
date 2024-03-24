try:
    from display import CENTER, RIGHT
except ImportError:
    CENTER = 1
    RIGHT = 2


class MenuItem:

    def __init__(self, name: str, decorator=None, visible=None):
        self.parent = None
        self._visible = True if visible is None else visible
        self._callback = None
        self.is_active = False
        self.name = name
        self.decorator = '' if decorator is None else decorator

    @property
    def visible(self):
        return self._visible if not self._check_callable(self._visible, False) else self._call_callable(self._visible)

    def click(self):
        raise NotImplementedError()

    def get_decorator(self):
        return self.decorator if not callable(self.decorator) else self.decorator()

    @property
    def callback(self):
        return self._callback

    @callback.setter
    def callback(self, callback):
        self._check_callable(callback)
        self._callback = callback

    @staticmethod
    def _check_callable(param, raise_error=True):
        if not (callable(param) or (type(param) is tuple and callable(param[0]))):
            if raise_error:
                raise ValueError('callable param should be callable or tuple with callable on first place!')
            else:
                return False
        return True

    @staticmethod
    def _call_callable(func, *args):
        if callable(func):
            return func(*args)
        elif type(func) is tuple and callable(func[0]):
            in_args = func[1] if type(func[1]) is tuple else (func[1],)
            return func[0](*tuple(list(in_args) + list(args)))


class SubMenuItem(MenuItem):
    menu = None

    def __init__(self, name, decorator=None, visible=None):
        super().__init__(name, '>' if decorator is None else decorator, visible)
        self.menu = MenuScreen(name, None)

    def click(self):
        pass

    def add(self, item, parent=None) -> 'SubMenuItem':
        self.menu.add(item, parent)
        return self

    def reset(self):
        self.menu.reset()


class CallbackItem(MenuItem):

    def __init__(self, name, callback, decorator=None, return_parent=True, visible=None):
        super().__init__(name, decorator, visible)
        self.callback = callback
        self.return_parent = return_parent

    def click(self):
        self._call_callable(self.callback)
        if self.return_parent:
            return self.parent


class ToggleItem(CallbackItem):

    def __init__(self, name, state_callback, change_callback, visible=None):
        super().__init__(name, change_callback, visible=visible)
        self._check_callable(state_callback)
        self.state_callback = state_callback

    def check_status(self):
        return self._call_callable(self.state_callback)

    def get_decorator(self):
        return '[x]' if self.check_status() else '[ ]'


class ConfirmItem(SubMenuItem):

    def __init__(self, name, callback, question=None, answers=None, decorator='', visible=None):
        super().__init__(name, decorator, visible=visible)
        self.callback = callback
        self._question = question if question is not None else 'Are you sure?'
        self._answers = answers if answers is not None else ('yes', 'no')

    def click(self):
        self.reset()
        self.menu.title = self._question

        for pos in range(len(self._answers)):
            name = self._answers[pos]
            self.add(CallbackItem(name, self.callback if pos == 0 else lambda: True), self.parent)

        return self.menu


class EnumItem(SubMenuItem):

    def __init__(self, name, items, callback, selected=None, visible=None):
        super().__init__(name, visible=visible)
        if not isinstance(items, list):
            raise ValueError("items should be a list!")
        self.callback = callback
        self.items = items
        self.selected = 0 if selected is None else self._get_index_by_key(selected)

        self._set_decorator()

    def choose(self, selection):
        self.selected = selection
        self._set_decorator()
        self._call_callable(self.callback, self._get_element()[0])

    def click(self):
        self.reset()
        for pos in range(len(self.items)):
            if isinstance(self.items[pos], dict):
                name = self.items[pos]['name']
            else:
                name = self.items[pos]
            decorator = '<<' if pos == self.selected else ''
            self.add(CallbackItem(name, (self.choose, pos), decorator), self.parent)

        return self.menu

    def _get_index_by_key(self, key):

        if 'value' not in self.items[0]:
            if type(key) is int:
                return key

        i = 0
        for item in self.items:
            if ('value' in item and item['value'] == key) or item['name'] == key:
                return i
            i += 1
        raise ValueError('No index for key: {}!'.format(key))

    def _get_element(self):
        if isinstance(self.items[self.selected], str):
            return self.items[self.selected], self.items[self.selected]

        # noinspection PyTypeChecker
        return self.items[self.selected]['value'], self.items[self.selected]['name']

    def _set_decorator(self):
        self.decorator = self._get_element()[1]


class InfoItem(MenuItem):

    def __init__(self, name, decorator='', visible=None):
        super().__init__(name, decorator, visible=visible)

    def click(self):
        return self.parent


class CustomItem(MenuItem):

    def __init__(self, name, visible=None):
        super().__init__(name, visible=visible)
        self.display = None  # it is set after initialization via Menu._update_display()

    def click(self):
        return self

    def up(self):
        # called when menu.move(-1) is called
        pass

    def down(self):
        # called when menu.move(1) is called
        pass

    def select(self):
        # called when menu.click() is called (on button press)
        # should return Instance of MenuItem (usually self.parent - if want to go level up or self to stay in current context)
        raise NotImplementedError()

    def draw(self):
        # called when someone click on menu item
        raise NotImplementedError()


class ValueItem(CustomItem, CallbackItem):

    def __init__(self, name, value_reader, min_v, max_v, step, callback, visible=None):
        super().__init__(name, visible=visible)
        self._value = value_reader if not self._check_callable(value_reader, False) else 0
        self.value_reader = value_reader
        self.min_v = min_v
        self.max_v = max_v
        self.step = step
        self.precision = 0 if '.' not in str(step) else len(str(step).split('.')[1])
        self.callback = callback

    def draw(self):
        self.display.fill(0)
        if hasattr(self.display, 'rich_text'):
            self.display.text(str.upper(self.name), None, 0, 1, align=CENTER)
            self.display.rich_text(str(self.value), None, 20, 1, size=5, align=CENTER)
        else:
            x_pos = self.display.width - (len(self.name) * 8) - 1
            self.display.text(str.upper(self.name), x_pos, 0, 1)
            x_pos = self.display.width - (len(str(self.value)) * 8) - 1
            self.display.text(str(self.value), x_pos, 20, 1)

        self.display.hline(0, 10, self.display.width, 1)
        self.display.show()

    def select(self):
        return self.parent

    def up(self):
        if self.value < self.max_v:
            v = self.value + self.step
            self.value = int(v) if self.precision == 0 else round(v, self.precision)
        self.draw()

    def down(self):
        if self.value > self.min_v:
            v = self.value - self.step
            self.value = int(v) if self.precision == 0 else round(v, self.precision)
        self.draw()

    @property
    def value(self):
        return self._value \
            if not self._check_callable(self.value_reader, False) \
            else self._call_callable(self.value_reader)

    @value.setter
    def value(self, value):
        self._value = value
        self._call_callable(self.callback, self._value)

    def get_decorator(self):
        return str(self.value)


class BackItem(MenuItem):

    def __init__(self, parent, label='< BACK'):
        super().__init__(label)
        self.parent = parent

    def click(self):
        return self.parent


class MenuScreen:

    def __init__(self, title: str, parent=None):
        self._items = []
        self._visible_items = []
        self.selected = 0
        self.parent = parent
        self.title = title

    def add(self, item, parent=None):
        item.parent = self if parent is None else parent
        if type(item) is SubMenuItem:
            item.menu.parent = self if parent is None else parent
        self._items.append(item)
        return self

    def reset(self):
        self._items = []

    def count(self) -> int:
        elements = 0
        self._visible_items = []
        for item in self._items:
            if item.visible:
                elements += 1
                self._visible_items.append(item)
        return elements + (1 if self.parent is not None else 0)

    def up(self) -> None:
        if self.selected > 0:
            self.selected = self.selected - 1

    def down(self) -> None:
        if self.selected + 1 < self.count():
            self.selected = self.selected + 1

    def get(self, position):

        if position < 0 or position + 1 > self.count():
            return None

        if position + 1 == self.count() and self.parent is not None:
            item = BackItem(self.parent)
        else:
            item = self._visible_items[position]

        item.is_active = position == self.selected
        return item

    def select(self) -> None:

        item = self.get(self.selected)
        if type(item) is BackItem:
            self.selected = 0

        if type(item) is SubMenuItem:
            return item.menu
        else:
            # do action and return current menu
            return item.click()


class Menu:
    current_screen = None  # type: MenuScreen | CustomItem

    def __init__(self, display, per_page: int = 4, line_height: int = 14, font_width: int = 8, font_height: int = 8):
        # todo: replace display and specific driver to framebuf
        self.display = display
        self.per_page = per_page
        self.line_height = line_height
        self.font_height = font_height
        self.font_width = font_width
        self.main_screen = None

    def set_screen(self, screen: MenuScreen):
        self.current_screen = screen
        if self.main_screen is None:
            self.main_screen = screen
            self._update_display(screen._items)

    def move(self, direction: int = 1):
        self.current_screen.up() if direction < 0 else self.current_screen.down()
        self.draw()

    def click(self):
        self.current_screen = self.current_screen.select()
        if self.current_screen is not None:
            self.draw()

    def reset(self):
        self.current_screen = self.main_screen
        self.current_screen.selected = 0
        self.draw()

    def draw(self):

        if type(self.current_screen) is not MenuScreen:
            self.current_screen.draw()
            return

        self.display.fill(0)

        self._menu_header(self.current_screen.title)

        elements = self.current_screen.count()
        start = self.current_screen.selected - self.per_page + 1 if self.current_screen.selected + 1 > self.per_page else 0
        end = start + self.per_page

        menu_pos = 0
        for i in range(start, end if end < elements else elements):
            self._item_line(self.current_screen.get(i), menu_pos)
            menu_pos += 1

        self.display.show()

    def _item_line(self, item: MenuItem, pos):
        menu_y_end = 12
        y = menu_y_end + (pos * self.line_height)
        v_padding = int((self.line_height - self.font_height) / 2)
        background = int(item.is_active)

        self.display.fill_rect(0, y, self.display.width, self.line_height, background)

        if hasattr(self.display, 'rich_text'):
            self.display.rich_text(str.upper(item.name), 2, y + v_padding, int(not background))
            self.display.rich_text(str.upper(item.get_decorator()), None, y + v_padding, int(not background), align=RIGHT)
        else:
            self.display.text(item.name, 0, y + v_padding, int(not background))
            x_pos = self.display.width - (len(item.get_decorator()) * self.font_width) - 1
            self.display.text(item.get_decorator(), x_pos, y + v_padding, int(not background))

    def _menu_header(self, text):
        x = int((self.display.width / 2) - (len(text) * self.font_width / 2))
        self.display.text(str.upper(self.current_screen.title), x, 0, 1)
        self.display.hline(0, self.font_height + 2, self.display.width, 1)

    def _update_display(self, menu_items):
        """
        Add display object to all CustomItems, as it can be useful there to draw custom screens
        """
        for obj in menu_items:
            if isinstance(obj, CustomItem):
                obj.display = self.display
            if isinstance(obj, SubMenuItem):
                self._update_display(obj.menu._items)
