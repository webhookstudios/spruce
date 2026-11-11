"""The Bulk Rename window: options on the left, live preview on the right."""
import os

from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango

from . import rename
from .rename import Item, Options

CASE_LABELS = ['Keep as is', 'lowercase', 'UPPERCASE', 'Title Case', 'Sentence case']
EXT_LABELS = ['Keep as is', 'lowercase', 'UPPERCASE', 'Change to…', 'Remove']
APPLY_LABELS = ['Name only', 'Extension only', 'Name and extension']
SORT_LABELS = ['Name', 'Date modified', 'Date taken', 'Order selected']
STATUS_TEXT = {
    'same': 'Unchanged',
    'ok': 'Will be renamed',
}


class Row(GObject.Object):
    __gtype_name__ = 'SpruceRenameRow'

    def __init__(self, item):
        super().__init__()
        self.item = item


def _combo(title, labels, subtitle=None):
    row = Adw.ComboRow(title=title, model=Gtk.StringList.new(labels))
    if subtitle:
        row.set_subtitle(subtitle)
    return row


def _spin(title, lower, upper, value):
    row = Adw.SpinRow.new_with_range(lower, upper, 1)
    row.set_title(title)
    row.set_value(value)
    return row


class RenameWindow(Adw.ApplicationWindow):
    def __init__(self, app, paths):
        super().__init__(application=app, default_width=1180, default_height=720,
                         title='Bulk Rename')
        self.items = [Item(p) for p in dict.fromkeys(paths) if os.path.lexists(p)]
        self.given = list(self.items)
        self._pending = 0
        self._auto_numbered = False

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        header = Adw.HeaderBar()
        self.title = Adw.WindowTitle(title='Bulk Rename')
        header.set_title_widget(self.title)
        self.apply_button = Gtk.Button(label='Rename')
        self.apply_button.add_css_class('suggested-action')
        self.apply_button.connect('clicked', self._on_apply)
        header.pack_end(self.apply_button)
        self.undo_button = Gtk.Button(icon_name='edit-undo-symbolic', tooltip_text='Undo the last rename')
        self.undo_button.connect('clicked', self._on_undo)
        header.pack_start(self.undo_button)
        reset = Gtk.Button(icon_name='view-refresh-symbolic', tooltip_text='Clear all options')
        reset.connect('clicked', lambda *_: self._reset())
        header.pack_start(reset)
        view.add_top_bar(header)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, position=430,
                          shrink_start_child=False, shrink_end_child=False)
        paned.set_start_child(self._build_options())
        paned.set_end_child(self._build_preview())
        view.set_content(paned)

        self._refresh_undo()
        self._recompute()

    # ----- options -------------------------------------------------------

    def _build_options(self):
        page = Adw.PreferencesPage()
        page.set_size_request(380, -1)

        g = Adw.PreferencesGroup(title='New name', description='Replaces the whole name and keeps the '
                                 'extension. Leave empty to keep each name.')
        self.title_entry = Adw.EntryRow(title='Rename all to')
        self.title_entry.connect('changed', self._on_title)
        g.add(self.title_entry)
        page.add(g)

        g = Adw.PreferencesGroup(title='Find and replace')
        self.find = Adw.EntryRow(title='Find')
        self.replace = Adw.EntryRow(title='Replace with')
        self.regex = Adw.SwitchRow(title='Regular expression', subtitle='Use \\1, \\2 for groups')
        self.match_case = Adw.SwitchRow(title='Match case')
        self.replace_all = Adw.SwitchRow(title='Replace every match', active=True)
        self.apply_to = _combo('Look in', APPLY_LABELS)
        for w in (self.find, self.replace, self.regex, self.match_case, self.replace_all, self.apply_to):
            g.add(w)
        page.add(g)

        g = Adw.PreferencesGroup(title='Text')
        self.prefix = Adw.EntryRow(title='Add before')
        self.suffix = Adw.EntryRow(title='Add after')
        self.case = _combo('Letter case', CASE_LABELS)
        for w in (self.prefix, self.suffix, self.case):
            g.add(w)
        page.add(g)

        g = Adw.PreferencesGroup(title='Numbering', description='Numbers follow the order in the preview. '
                                 'Use {n} anywhere to place the number yourself.')
        self.number = Adw.ExpanderRow(title='Add a number', show_enable_switch=True, enable_expansion=False)
        self.number_position = _combo('Position', ['After the name', 'Before the name'])
        self.number_start = _spin('Start at', -999999, 999999, 1)
        self.number_step = _spin('Step', 1, 1000, 1)
        self.number_padding = _spin('Digits', 1, 10, 2)
        self.number_padding.set_subtitle('Pads with zeros: 01, 02 …')
        self.number_separator = Adw.EntryRow(title='Separator', text=' ')
        for w in (self.number_position, self.number_start, self.number_step, self.number_padding,
                  self.number_separator):
            self.number.add_row(w)
        self.sort = _combo('Order', SORT_LABELS)
        g.add(self.number)
        g.add(self.sort)
        page.add(g)

        g = Adw.PreferencesGroup(title='Extension')
        self.ext_mode = _combo('Extension', EXT_LABELS)
        self.new_ext = Adw.EntryRow(title='New extension', visible=False)
        g.add(self.ext_mode)
        g.add(self.new_ext)
        page.add(g)

        g = Adw.PreferencesGroup(title='Tokens', description='Type these into any text box above.')
        self.date_format = Adw.EntryRow(title='Date format', text='%Y-%m-%d')
        g.add(self.date_format)
        tokens = Adw.ExpanderRow(title='Available tokens')
        for token, text in rename.TOKENS:
            r = Adw.ActionRow(title=token, subtitle=text, activatable=True)
            r.add_css_class('property')
            r.set_tooltip_text('Click to copy')
            r.connect('activated', self._copy_token, token)
            tokens.add_row(r)
        g.add(tokens)
        page.add(g)

        for w in (self.title_entry, self.find, self.replace, self.prefix, self.suffix, self.number_separator,
                  self.new_ext,
                  self.date_format):
            w.connect('changed', self._changed)
        for w in (self.regex, self.match_case, self.replace_all):
            w.connect('notify::active', self._changed)
        for w in (self.apply_to, self.case, self.number_position, self.sort, self.ext_mode):
            w.connect('notify::selected', self._changed)
        for w in (self.number_start, self.number_step, self.number_padding):
            w.connect('notify::value', self._changed)
        self.number.connect('notify::enable-expansion', self._changed)
        return page

    def _on_title(self, entry):
        """Typing a new name for several items turns numbering on, once, so
        the names stay different. Switched off again, it stays off."""
        text = entry.get_text()
        if text and not self._auto_numbered and '{n}' not in text \
                and sum(i.enabled for i in self.items) > 1:
            self._auto_numbered = True
            self.number.set_enable_expansion(True)

    def _copy_token(self, _row, token):
        self.get_clipboard().set(token)
        self.toasts.add_toast(Adw.Toast(title=f'Copied {token}', timeout=1))

    def _reset(self):
        self._auto_numbered = False
        for w in (self.title_entry, self.find, self.replace, self.prefix, self.suffix, self.new_ext):
            w.set_text('')
        self.number_separator.set_text(' ')
        self.date_format.set_text('%Y-%m-%d')
        self.regex.set_active(False)
        self.match_case.set_active(False)
        self.replace_all.set_active(True)
        for w in (self.apply_to, self.case, self.number_position, self.sort, self.ext_mode):
            w.set_selected(0)
        self.number.set_enable_expansion(False)
        self.number_start.set_value(1)
        self.number_step.set_value(1)
        self.number_padding.set_value(2)
        for item in self.items:
            item.enabled = True
        self._recompute()

    def options(self):
        return Options(
            title=self.title_entry.get_text(),
            find=self.find.get_text(), replace=self.replace.get_text(),
            regex=self.regex.get_active(), match_case=self.match_case.get_active(),
            replace_all=self.replace_all.get_active(),
            apply_to=rename.APPLY_TO[self.apply_to.get_selected()],
            prefix=self.prefix.get_text(), suffix=self.suffix.get_text(),
            case=rename.CASES[self.case.get_selected()],
            number=self.number.get_enable_expansion(),
            number_position=('after', 'before')[self.number_position.get_selected()],
            number_start=int(self.number_start.get_value()), number_step=int(self.number_step.get_value()),
            number_padding=int(self.number_padding.get_value()),
            number_separator=self.number_separator.get_text(),
            ext_mode=rename.EXT_MODES[self.ext_mode.get_selected()], new_ext=self.new_ext.get_text(),
            date_format=self.date_format.get_text(), sort=rename.SORTS[self.sort.get_selected()],
        )

    def _changed(self, *_):
        self.new_ext.set_visible(self.ext_mode.get_selected() == 3)
        # Wait a moment after typing, so a long list is not redone per key.
        self._pending += 1
        n = self._pending
        GLib.timeout_add(120, lambda: n == self._pending and self._recompute() and False)

    # ----- preview -------------------------------------------------------

    def _build_preview(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.store = Gio.ListStore(item_type=Row)
        self.columns = Gtk.ColumnView(model=Gtk.NoSelection(model=self.store), show_row_separators=True,
                                      reorderable=False)
        self.columns.add_css_class('data-table')

        def column(title, setup, bind, expand=False, width=-1):
            f = Gtk.SignalListItemFactory()
            f.connect('setup', lambda _f, li: li.set_child(setup()))
            f.connect('bind', lambda _f, li: bind(li.get_child(), li.get_item().item))
            c = Gtk.ColumnViewColumn(title=title, factory=f, expand=expand, resizable=True)
            if width > 0:
                c.set_fixed_width(width)
            self.columns.append_column(c)

        def check():
            b = Gtk.CheckButton()
            b.connect('toggled', self._on_toggle)
            return b

        def bind_check(b, item):
            b.item = item
            b.handler_block_by_func(self._on_toggle)
            b.set_active(item.enabled)
            b.handler_unblock_by_func(self._on_toggle)

        def label():
            lbl = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
            return lbl

        def bind_old(lbl, item):
            lbl.set_text(item.name + ('/' if item.is_dir else ''))
            lbl.set_tooltip_text(item.path)
            lbl.set_sensitive(item.enabled)

        def bind_new(lbl, item):
            lbl.set_text(item.new_name)
            lbl.set_tooltip_text(item.new_name)
            for c in ('dim-label', 'error', 'accent'):
                lbl.remove_css_class(c)
            if item.status == 'same':
                lbl.add_css_class('dim-label')
            elif item.status == 'ok':
                lbl.add_css_class('accent')
            else:
                lbl.add_css_class('error')

        def status():
            b = Gtk.Box(spacing=6)
            b.icon = Gtk.Image()
            b.text = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
            b.append(b.icon)
            b.append(b.text)
            return b

        def bind_status(b, item):
            text = item.message or STATUS_TEXT.get(item.status, '')
            b.text.set_text(text)
            b.set_tooltip_text(text)
            icon = {'ok': 'object-select-symbolic', 'same': ''}.get(item.status, 'dialog-warning-symbolic')
            b.icon.set_from_icon_name(icon or None)
            b.icon.set_visible(bool(icon))
            for c in ('error', 'success', 'dim-label'):
                b.remove_css_class(c)
            b.add_css_class({'ok': 'success', 'same': 'dim-label'}.get(item.status, 'error'))

        column('', check, bind_check, width=44)
        column('Original', label, bind_old, expand=True)
        column('New name', label, bind_new, expand=True)
        column('Status', status, bind_status, width=230)

        scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True, child=self.columns)
        box.append(scroll)

        bar = Gtk.Box(spacing=12, margin_start=12, margin_end=12, margin_top=8, margin_bottom=8)
        self.summary = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        self.only_changed = Gtk.CheckButton(label='Show only changes')
        self.only_changed.connect('toggled', lambda *_: self._fill())
        bar.append(self.summary)
        bar.append(self.only_changed)
        box.append(Gtk.Separator())
        box.append(bar)
        return box

    def _on_toggle(self, button):
        button.item.enabled = button.get_active()
        self._changed()

    def _recompute(self):
        opts = self.options()
        if opts.sort == 'given':
            ordered = list(self.given)
        else:
            ordered = rename.sort_items(self.items, opts.sort)
        self.items = ordered
        rename.compute(self.items, opts)
        self._fill()
        return False

    def _fill(self):
        only = self.only_changed.get_active()
        rows = [Row(i) for i in self.items if not only or i.status != 'same']
        self.store.splice(0, self.store.get_n_items(), rows)

        ok = sum(i.status == 'ok' for i in self.items)
        bad = [i for i in self.items if i.status not in ('ok', 'same')]
        n = len(self.items)
        self.title.set_subtitle(f'{n} item{"s" if n != 1 else ""}')
        parts = [f'{ok} of {n} will be renamed']
        if bad:
            parts.append(f'{len(bad)} cannot be, see Status')
        self.summary.set_text('. '.join(parts) + '.')
        self.summary.remove_css_class('error')
        if bad:
            self.summary.add_css_class('error')
        self.apply_button.set_sensitive(ok > 0)
        self.apply_button.set_label(f'Rename {ok}' if ok else 'Rename')

    # ----- apply and undo ------------------------------------------------

    def _on_apply(self, _button):
        done, errors = rename.apply(self.items)
        # Point every item at its new name, so the batch can be renamed again.
        for item in self.items:
            if item.status == 'ok' and not any(src == item.path for src, _ in errors):
                fresh = Item(item.target)
                item.__dict__.update(fresh.__dict__)
        self._refresh_undo()
        # The old find text usually no longer matches; start from a clean
        # slate so the preview shows the files as they now are.
        self._reset()
        self._report(f'Renamed {done} item{"s" if done != 1 else ""}', errors, undo=done > 0)

    def _on_undo(self, *_):
        data = rename.load_undo()
        if not data:
            return
        back = {renamed: original for original, renamed in data['moves']}
        done, errors = rename.undo()
        for item in self.items:
            if item.path in back and os.path.lexists(back[item.path]):
                item.__dict__.update(Item(back[item.path]).__dict__)
        self._refresh_undo()
        self._recompute()
        self._report(f'Put back {done} name{"s" if done != 1 else ""}', errors)

    def _report(self, title, errors, undo=False):
        if errors:
            body = '\n'.join(f'{os.path.basename(p)}: {m}' for p, m in errors[:12])
            if len(errors) > 12:
                body += f'\n… and {len(errors) - 12} more'
            d = Adw.AlertDialog(heading=title, body=f'Some items could not be changed:\n\n{body}')
            d.add_response('ok', 'OK')
            d.present(self)
            return
        toast = Adw.Toast(title=title, timeout=8)
        if undo:
            toast.set_button_label('Undo')
            toast.connect('button-clicked', self._on_undo)
        self.toasts.add_toast(toast)

    def _refresh_undo(self):
        data = rename.load_undo()
        self.undo_button.set_sensitive(bool(data))
        if data:
            n = len(data['moves'])
            self.undo_button.set_tooltip_text(f'Undo the last rename ({n} item{"s" if n != 1 else ""})')
        else:
            self.undo_button.set_tooltip_text('Nothing to undo')
