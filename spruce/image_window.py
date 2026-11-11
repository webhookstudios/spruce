"""The Resize Images and Convert Images windows. Both share the batch
runner: a worker thread, a progress bar and a summary at the end."""
import os
import threading

from gi.repository import Adw, Gio, GLib, Gtk, Pango

from . import images

OUTPUT_LABELS = ['New copies', 'Replace originals']


def _folder_label(paths):
    folders = {os.path.dirname(p) for p in paths}
    if len(folders) != 1:
        return f'From {len(folders)} folders' if folders else ''
    return 'In ' + (os.path.basename(folders.pop()) or '/')


def _spin(title, lower, upper, value, step=1):
    row = Adw.SpinRow.new_with_range(lower, upper, step)
    row.set_title(title)
    row.set_value(value)
    return row


class ImageWindow(Adw.ApplicationWindow):
    verb = ''
    done_verb = ''

    def __init__(self, app, paths, title):
        super().__init__(application=app, default_width=560, default_height=780, title=title)
        self.paths = [p for p in dict.fromkeys(paths) if os.path.isfile(p)]
        self.cancel = threading.Event()

        self.toolbar = Adw.ToolbarView()
        self.set_content(self.toolbar)
        header = Adw.HeaderBar()
        n = len(self.paths)
        self.header_title = Adw.WindowTitle(title=title, subtitle=f'{n} image{"s" if n != 1 else ""}')
        header.set_title_widget(self.header_title)
        self.go = Gtk.Button(label=self.verb)
        self.go.add_css_class('suggested-action')
        self.go.connect('clicked', self._on_go)
        header.pack_end(self.go)
        self.toolbar.add_top_bar(header)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.page = Adw.PreferencesPage()
        self.build(self.page)
        self._add_output_group(self.page)
        self._add_files_group(self.page)
        self.stack.add_named(self.page, 'options')
        self.stack.add_named(self._build_progress(), 'progress')
        self.stack.add_named(self._build_result(), 'result')
        self.toolbar.set_content(self.stack)
        self.update()

    # subclasses fill these in
    def build(self, page):
        pass

    def update(self):
        pass

    def suffix_default(self):
        return ''

    def destination(self, path):
        """(destination path, Pillow format) for one image, or (None, reason)
        to skip it."""
        raise NotImplementedError

    def process(self, pic):
        """Changes pic in memory. Returns False to skip writing it."""
        return True

    # ----- common rows ---------------------------------------------------

    def _add_output_group(self, page):
        g = Adw.PreferencesGroup(title='Saving')
        self.quality = _spin('Quality', 1, 100, 90)
        self.quality.set_subtitle('For JPEG, WebP, AVIF and HEIC')
        self.strip = Adw.SwitchRow(title='Remove metadata',
                                   subtitle='Camera, location and date details. The colour profile is kept.')
        self.output = Adw.ComboRow(title='Save as', model=Gtk.StringList.new(OUTPUT_LABELS))
        self.suffix = Adw.EntryRow(title='Add to copy names', text=self.suffix_default())
        self._suffix_edited = False
        self.suffix.connect('changed', self._on_suffix_typed)
        self.output.connect('notify::selected', lambda *_: self.update())
        for w in (self.quality, self.strip, self.output, self.suffix):
            g.add(w)
        self.replace_note = Adw.ActionRow(
            title='Originals will be lost',
            subtitle=self.replace_subtitle(), visible=False)
        self.replace_note.add_prefix(Gtk.Image(icon_name='dialog-warning-symbolic'))
        self.replace_note.add_css_class('warning')
        g.add(self.replace_note)
        page.add(g)

    def replace_subtitle(self):
        return 'Each image is overwritten with the new version.'

    def _on_suffix_typed(self, *_):
        if not getattr(self, '_setting_suffix', False):
            self._suffix_edited = True

    def set_suffix(self, text):
        if not self._suffix_edited:
            self._setting_suffix = True
            self.suffix.set_text(text)
            self._setting_suffix = False

    @property
    def in_place(self):
        return self.output.get_selected() == 1

    def update_common(self):
        self.suffix.set_visible(not self.in_place)
        self.replace_note.set_visible(self.in_place)
        self.go.set_label(self.verb)

    def _add_files_group(self, page):
        g = Adw.PreferencesGroup(title='Images')
        files = Adw.ExpanderRow(title=f'{len(self.paths)} selected',
                                subtitle=GLib.markup_escape_text(_folder_label(self.paths)))
        for p in self.paths[:200]:
            files.add_row(Adw.ActionRow(title=GLib.markup_escape_text(os.path.basename(p))))
        if len(self.paths) > 200:
            files.add_row(Adw.ActionRow(title=f'… and {len(self.paths) - 200} more'))
        g.add(files)
        page.add(g)

    # ----- progress and result -------------------------------------------

    def _build_progress(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, valign=Gtk.Align.CENTER,
                      margin_start=36, margin_end=36)
        self.progress_title = Gtk.Label(label=f'{self.verb.rstrip("e")}ing…' if self.verb else '')
        self.progress_title.add_css_class('title-2')
        self.bar = Gtk.ProgressBar(show_text=True)
        self.progress_file = Gtk.Label(ellipsize=Pango.EllipsizeMode.MIDDLE, css_classes=['dim-label'])
        cancel = Gtk.Button(label='Cancel', halign=Gtk.Align.CENTER, css_classes=['pill'])
        cancel.connect('clicked', lambda *_: self.cancel.set())
        for w in (self.progress_title, self.bar, self.progress_file, cancel):
            box.append(w)
        return box

    def _build_result(self):
        self.result = Adw.StatusPage(icon_name='object-select-symbolic')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER)
        self.errors_list = Gtk.ListBox(css_classes=['boxed-list'], selection_mode=Gtk.SelectionMode.NONE,
                                       visible=False)
        self.errors_list.set_size_request(420, -1)
        buttons = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        self.open_button = Gtk.Button(label='Show in Files', css_classes=['pill'])
        self.open_button.connect('clicked', self._open_folder)
        close = Gtk.Button(label='Close', css_classes=['pill', 'suggested-action'])
        close.connect('clicked', lambda *_: self.close())
        buttons.append(self.open_button)
        buttons.append(close)
        box.append(self.errors_list)
        box.append(buttons)
        self.result.set_child(box)
        scroll = Gtk.ScrolledWindow(child=self.result)
        return scroll

    def _open_folder(self, *_):
        target = self.outputs[0] if self.outputs else (self.paths[0] if self.paths else None)
        if not target:
            return
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION)
            bus.call_sync('org.freedesktop.FileManager1', '/org/freedesktop/FileManager1',
                          'org.freedesktop.FileManager1', 'ShowItems',
                          GLib.Variant('(ass)', ([Gio.File.new_for_path(target).get_uri()], '')),
                          None, Gio.DBusCallFlags.NONE, 3000, None)
        except GLib.Error:
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(os.path.dirname(target)).get_uri(), None)

    # ----- running -------------------------------------------------------

    def _on_go(self, _button):
        if self.in_place:
            d = Adw.AlertDialog(heading=f'Replace {len(self.paths)} original image{"s" if len(self.paths) != 1 else ""}?',
                                body=self.replace_subtitle() + ' This cannot be undone.')
            d.add_response('cancel', 'Cancel')
            d.add_response('go', 'Replace')
            d.set_response_appearance('go', Adw.ResponseAppearance.DESTRUCTIVE)
            d.set_default_response('cancel')
            d.connect('response', lambda _d, r: r == 'go' and self._start())
            d.present(self)
        else:
            self._start()

    def settings(self):
        """A snapshot of the options, read on the GTK thread."""
        return dict(quality=int(self.quality.get_value()), strip=self.strip.get_active(),
                    in_place=self.in_place, suffix=self.suffix.get_text())

    def _start(self):
        self.go.set_sensitive(False)
        self.stack.set_visible_child_name('progress')
        self.cancel.clear()
        self.outputs, self.errors, self.skipped = [], [], []
        self.opts = self.settings()
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        total = len(self.paths)
        for n, path in enumerate(self.paths):
            if self.cancel.is_set():
                break
            GLib.idle_add(self._progress, n, total, os.path.basename(path))
            try:
                dest, fmt = self.destination(path)
                if dest is None:
                    self.skipped.append((path, fmt))
                    continue
                pic = images.Picture(path)
                if not self.process(pic):
                    self.skipped.append((path, 'Already that size or smaller'))
                    continue
                tmp = images.save(pic, dest, fmt, self.opts['quality'], self.opts['strip'])
                replace = self.opts['in_place'] and os.path.abspath(dest) == os.path.abspath(path)
                out = images.commit(tmp, dest, replace=replace)
                self.after_write(path, out)
                self.outputs.append(out)
            except Exception as e:  # one bad file must not stop the batch
                self.errors.append((path, str(e) or e.__class__.__name__))
        GLib.idle_add(self._finished)

    def after_write(self, path, out):
        pass

    def _progress(self, n, total, name):
        self.bar.set_fraction(n / total if total else 1)
        self.bar.set_text(f'{n + 1} of {total}')
        self.progress_file.set_text(name)
        return False

    def _finished(self):
        n = len(self.outputs)
        cancelled = self.cancel.is_set()
        self.result.set_title(f'{self.done_verb} {n} image{"s" if n != 1 else ""}' + (' (cancelled)' if cancelled else ''))
        lines = []
        if self.skipped:
            lines.append(f'{len(self.skipped)} skipped: {self.skipped[0][1].lower()}'
                         + (' and others' if len({r for _, r in self.skipped}) > 1 else ''))
        if self.errors:
            lines.append(f'{len(self.errors)} could not be read or saved.')
        if not self.in_place and n:
            lines.append('Your originals are unchanged.')
        self.result.set_description('\n'.join(lines))
        self.result.set_icon_name('dialog-warning-symbolic' if self.errors else 'object-select-symbolic')
        child = self.errors_list.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.errors_list.remove(child)
            child = nxt
        for path, msg in self.errors[:30]:
            self.errors_list.append(Adw.ActionRow(title=GLib.markup_escape_text(os.path.basename(path)),
                                                  subtitle=GLib.markup_escape_text(msg)))
        self.errors_list.set_visible(bool(self.errors))
        self.open_button.set_visible(bool(self.outputs))
        self.go.set_visible(False)
        self.stack.set_visible_child_name('result')
        return False


class ResizeWindow(ImageWindow):
    verb = 'Resize'
    done_verb = 'Resized'

    def __init__(self, app, paths):
        super().__init__(app, paths, 'Resize Images')

    def suffix_default(self):
        return ' (small)'

    def build(self, page):
        g = Adw.PreferencesGroup(title='Size')
        labels = [f'{label}  ·  {w} × {h}' for _k, label, w, h in images.PRESETS] + ['Custom']
        self.preset = Adw.ComboRow(title='Size', model=Gtk.StringList.new(labels))
        self.unit = Adw.ComboRow(title='Measure in', model=Gtk.StringList.new(['Pixels', 'Percent']))
        self.width = _spin('Width', 1, 65535, 1280)
        self.height = _spin('Height', 0, 65535, 720)
        self.height.set_subtitle('0 means any height')
        self.percent = _spin('Scale', 1, 1000, 50)
        self.percent.set_subtitle('Percent of the current size')
        for w in (self.preset, self.unit, self.width, self.height, self.percent):
            g.add(w)
        page.add(g)

        g = Adw.PreferencesGroup(title='Shape')
        self.keep_aspect = Adw.SwitchRow(title='Keep aspect ratio', active=True,
                                         subtitle='Off stretches the image to exactly this size')
        self.mode = Adw.ComboRow(title='Fit', model=Gtk.StringList.new(['Fit inside the size', 'Fill the size and crop']))
        self.shrink_only = Adw.SwitchRow(title='Only make images smaller', active=True,
                                         subtitle='Small images are left as they are')
        for w in (self.keep_aspect, self.mode, self.shrink_only):
            g.add(w)
        page.add(g)

        for w in (self.preset, self.unit, self.mode):
            w.connect('notify::selected', lambda *_: self.update())
        self.keep_aspect.connect('notify::active', lambda *_: self.update())
        self.percent.connect('notify::value', lambda *_: self.update())
        self.width.connect('notify::value', lambda *_: self.update())
        self.height.connect('notify::value', lambda *_: self.update())

    def update(self):
        custom = self.preset.get_selected() == len(images.PRESETS)
        percent = custom and self.unit.get_selected() == 1
        self.unit.set_visible(custom)
        self.width.set_visible(custom and not percent)
        self.height.set_visible(custom and not percent)
        self.percent.set_visible(percent)
        self.keep_aspect.set_sensitive(not percent)
        self.mode.set_sensitive(self.keep_aspect.get_active() and not percent)
        if custom:
            tag = f'{int(self.percent.get_value())}%' if percent else \
                f'{int(self.width.get_value())}x{int(self.height.get_value())}'
        else:
            tag = images.PRESETS[self.preset.get_selected()][1].lower()
        self.set_suffix(f' ({tag})')
        self.update_common()

    def settings(self):
        s = super().settings()
        sel = self.preset.get_selected()
        custom = sel == len(images.PRESETS)
        s['percent'] = int(self.percent.get_value()) if custom and self.unit.get_selected() == 1 else None
        s['box'] = (int(self.width.get_value()), int(self.height.get_value())) if custom else images.PRESETS[sel][2:]
        s['mode'] = ('fit', 'fill')[self.mode.get_selected()]
        s['keep_aspect'] = self.keep_aspect.get_active()
        s['shrink_only'] = self.shrink_only.get_active()
        return s

    def destination(self, path):
        fmt = images.format_of(path)
        if not fmt:
            return None, 'Not an image Spruce can write'
        if fmt in ('AVIF', 'HEIC') and not images.can_write(fmt):
            return None, f'This system cannot write {fmt}'
        if self.opts['in_place']:
            return path, fmt
        base, ext = os.path.splitext(path)
        return base + self.opts['suffix'] + ext, fmt

    def process(self, pic):
        o = self.opts
        return images.resize(pic, o['box'], o['mode'], o['keep_aspect'], o['shrink_only'], o['percent'])


class ConvertWindow(ImageWindow):
    verb = 'Convert'
    done_verb = 'Converted'

    def __init__(self, app, paths):
        self.formats = images.writable_formats()
        super().__init__(app, paths, 'Convert Images')

    def build(self, page):
        g = Adw.PreferencesGroup(title='Format')
        self.format = Adw.ComboRow(title='Convert to', model=Gtk.StringList.new(self.formats))
        self.format.set_selected(self.formats.index('JPEG'))
        self.format.connect('notify::selected', lambda *_: self.update())
        g.add(self.format)
        missing = [f for f in images.FORMATS if f not in self.formats]
        if missing:
            hint = Adw.ActionRow(title=f'{" and ".join(missing)} not available',
                                 subtitle='Saving HEIC needs the libheif-plugin-x265 package, '
                                 'and opening it needs libheif-plugin-libde265.')
            hint.add_prefix(Gtk.Image(icon_name='dialog-information-symbolic'))
            g.add(hint)
        page.add(g)

    def replace_subtitle(self):
        return 'The new file is saved and each original is moved to the Trash.'

    @property
    def target(self):
        return self.formats[self.format.get_selected()]

    def update(self):
        self.quality.set_sensitive(self.target != 'PNG')
        self.set_suffix('')
        self.suffix.set_title('Add to new names (optional)')
        self.update_common()
        self.go.set_label(f'Convert to {self.target}')

    def settings(self):
        s = super().settings()
        s['target'] = self.target
        return s

    def destination(self, path):
        pil, ext = images.FORMATS[self.opts['target']]
        if images.format_of(path) == pil:
            return None, f'Already {self.opts["target"]}'
        base = os.path.splitext(path)[0]
        suffix = '' if self.opts['in_place'] else self.opts['suffix']
        return base + suffix + ext, pil

    def after_write(self, path, out):
        if self.opts['in_place'] and os.path.abspath(path) != os.path.abspath(out):
            try:
                Gio.File.new_for_path(path).trash(None)
            except GLib.Error as e:
                self.errors.append((path, f'Converted, but the original could not be moved to the Trash: {e.message}'))
