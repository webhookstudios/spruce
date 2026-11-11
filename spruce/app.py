"""Spruce: batch rename, resize and convert, opened from the Files
right-click menu.

    spruce rename FILE...     Bulk Rename window
    spruce resize FILE...     Resize Images window
    spruce convert FILE...    Convert Images window
    spruce undo               put back the names from the last bulk rename
    spruce                    a small window to pick files and a tool
"""
import os
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import __version__, images, rename  # noqa: E402

APP_ID = 'io.github.webhookstudios.Spruce'


def _paths(args):
    out = []
    for a in args:
        if a.startswith('file://'):
            a = Gio.File.new_for_uri(a).get_path() or ''
        if a:
            out.append(os.path.abspath(a))
    return out


class HomeWindow(Adw.ApplicationWindow):
    """Shown when Spruce is opened from the apps menu."""

    def __init__(self, app):
        super().__init__(application=app, title='Spruce', default_width=480, default_height=560)
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        menu = Gio.Menu()
        menu.append('About Spruce', 'app.about')
        header.pack_end(Gtk.MenuButton(icon_name='open-menu-symbolic', menu_model=menu, tooltip_text='Main menu'))
        view.add_top_bar(header)
        self.toasts = Adw.ToastOverlay(child=view)
        self.set_content(self.toasts)

        page = Adw.PreferencesPage()
        intro = Adw.PreferencesGroup(
            title='Batch file tools',
            description='In Files, select some files and right-click them for '
                        '“Bulk Rename…”, “Resize Images…” and “Convert Images…”. '
                        'You can also pick files here.')
        page.add(intro)
        g = Adw.PreferencesGroup()
        for tool, title, sub, icon in (
            ('rename', 'Bulk Rename', 'Find and replace, numbering, dates and more', 'edit-symbolic'),
            ('resize', 'Resize Images', 'Small, medium, large, phone or your own size', 'view-fullscreen-symbolic'),
            ('convert', 'Convert Images', 'PNG, JPEG, WebP, AVIF' + (', HEIC' if images.can_write('HEIC') else ''),
             'image-x-generic-symbolic'),
        ):
            row = Adw.ActionRow(title=title, subtitle=sub, activatable=True)
            row.add_prefix(Gtk.Image(icon_name=icon))
            row.add_suffix(Gtk.Image(icon_name='go-next-symbolic'))
            row.connect('activated', self._pick, tool)
            g.add(row)
        page.add(g)

        g = Adw.PreferencesGroup()
        self.undo_row = Adw.ActionRow(title='Undo last bulk rename', activatable=True)
        self.undo_row.add_prefix(Gtk.Image(icon_name='edit-undo-symbolic'))
        self.undo_row.connect('activated', self._undo)
        g.add(self.undo_row)
        page.add(g)
        view.set_content(page)
        self._refresh()
        self.connect('notify::is-active', lambda *_: self._refresh())

    def _refresh(self):
        data = rename.load_undo()
        self.undo_row.set_sensitive(bool(data))
        if data:
            n = len(data['moves'])
            first = os.path.basename(data['moves'][0][1])
            self.undo_row.set_subtitle(f'{n} item{"s" if n != 1 else ""}, such as {first}')
        else:
            self.undo_row.set_subtitle('Nothing to undo')

    def _undo(self, *_):
        done, errors = rename.undo()
        msg = f'Put back {done} name{"s" if done != 1 else ""}'
        if errors:
            msg += f', {len(errors)} could not be'
        self.toasts.add_toast(Adw.Toast(title=msg))
        self._refresh()

    def _pick(self, _row, tool):
        dialog = Gtk.FileDialog(title={'rename': 'Choose files to rename',
                                       'resize': 'Choose images to resize',
                                       'convert': 'Choose images to convert'}[tool])
        if tool != 'rename':
            f = Gtk.FileFilter(name='Images')
            for ext in images.EXTENSIONS:
                f.add_suffix(ext.lstrip('.'))
            store = Gio.ListStore(item_type=Gtk.FileFilter)
            store.append(f)
            dialog.set_filters(store)

        def done(d, result):
            try:
                files = d.open_multiple_finish(result)
            except GLib.Error:
                return
            paths = [files.get_item(i).get_path() for i in range(files.get_n_items())]
            self.get_application().open_tool(tool, [p for p in paths if p])

        dialog.open_multiple(self, None, done)


class SpruceApp(Adw.Application):
    def __init__(self, tool, paths):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.tool, self.paths = tool, paths
        GLib.set_application_name('Spruce')

    def do_startup(self):
        Adw.Application.do_startup(self)
        from . import sedge
        sedge.apply()
        about = Gio.SimpleAction.new('about', None)
        about.connect('activate', lambda *_: Adw.AboutDialog(
            application_name='Spruce', application_icon=APP_ID, version=__version__,
            developer_name='webhookstudios', website='https://github.com/webhookstudios/spruce',
            license_type=Gtk.License.GPL_2_0,
            comments='Bulk rename, resize and convert, right from the Files app.\n\nPart of the Sedge family of apps.').present(self.get_active_window()))
        self.add_action(about)

    def do_activate(self):
        if self.tool:
            self.open_tool(self.tool, self.paths)
        else:
            HomeWindow(self).present()

    def open_tool(self, tool, paths):
        from .image_window import ConvertWindow, ResizeWindow
        from .rename_window import RenameWindow
        if tool != 'rename':
            paths = [p for p in paths if images.format_of(p)]
        if not paths:
            d = Adw.AlertDialog(heading='No files to work on',
                                body='Select some files in Files, then right-click them.'
                                if tool == 'rename' else 'None of the selected files are images Spruce can open.')
            d.add_response('ok', 'OK')
            parent = self.get_active_window()
            if parent:
                d.present(parent)
            else:
                win = Adw.ApplicationWindow(application=self, default_width=360, default_height=200)
                d.connect('response', lambda *_: win.close())
                win.present()
                d.present(win)
            return None
        cls = {'rename': RenameWindow, 'resize': ResizeWindow, 'convert': ConvertWindow}[tool]
        win = cls(self, paths)
        win.present()
        return win


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ('-v', '--version'):
        print(f'Spruce {__version__}')
        return 0
    if argv and argv[0] in ('-h', '--help', 'help'):
        print(__doc__.strip())
        return 0
    if argv and argv[0] == 'undo':
        done, errors = rename.undo()
        print(f'Put back {done} name(s).' if done or errors else 'Nothing to undo.')
        for path, msg in errors:
            print(f'  {path}: {msg}', file=sys.stderr)
        return 1 if errors else 0
    tool = None
    if argv and argv[0] in ('rename', 'resize', 'convert'):
        tool, argv = argv[0], argv[1:]
    elif argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    return SpruceApp(tool, _paths(argv)).run([sys.argv[0]])
