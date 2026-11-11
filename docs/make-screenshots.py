#!/usr/bin/python3
"""Renders the README screenshots into docs/screenshots/.

    python3 docs/make-screenshots.py FOLDER

FOLDER should hold a few sample JPEGs (IMG_4021.JPG ...) and banner.png.
Nothing is renamed or written there; the windows are only filled in and
drawn to PNG files.
"""
import glob
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import GLib, Graphene, Gtk  # noqa: E402

from spruce.app import SpruceApp  # noqa: E402
from spruce.image_window import ConvertWindow, ResizeWindow  # noqa: E402
from spruce.rename_window import RenameWindow  # noqa: E402

OUT = os.path.join(ROOT, 'docs', 'screenshots')
FOLDER = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.getcwd()


def pump(sec=0.5):
    end = time.time() + sec
    while time.time() < end:
        GLib.MainContext.default().iteration(False)
        time.sleep(0.01)


def shot(win, name):
    pump(0.8)
    w, h = win.get_width(), win.get_height()
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(win).snapshot(snap, w, h)
    tex = win.get_native().get_renderer().render_texture(snap.to_node(), Graphene.Rect().init(0, 0, w, h))
    tex.save_to_png(os.path.join(OUT, name))
    print('wrote', name)


def run(app):
    os.makedirs(OUT, exist_ok=True)
    files = sorted(glob.glob(os.path.join(FOLDER, '*')))
    photos = [f for f in files if f.lower().endswith('.jpg')]

    win = RenameWindow(app, files)
    win.set_default_size(1100, 640)
    win.present()
    win.find.set_text('IMG_')
    win.replace.set_text('Iceland ')
    win.suffix.set_text(' {taken}')
    win.find.set_text('IMG_\\d+')
    win.regex.set_active(True)
    win.replace.set_text('Iceland')
    win.number.set_enable_expansion(True)
    win.ext_mode.set_selected(1)
    for item in win.items:
        if item.name == 'banner.png':
            item.enabled = False
    win._recompute()
    shot(win, 'rename.png')
    win.close()

    win = ResizeWindow(app, photos)
    win.set_default_size(520, 700)
    win.present()
    win.preset.set_selected(1)
    shot(win, 'resize.png')
    win.close()

    win = ConvertWindow(app, photos)
    win.set_default_size(520, 620)
    win.present()
    win.format.set_selected(win.formats.index('WebP'))
    win.quality.set_value(80)
    win.strip.set_active(True)
    shot(win, 'convert.png')
    win.close()
    app.quit()


SpruceApp.do_activate = lambda self: None
app = SpruceApp(None, [])
app.connect('activate', lambda a: GLib.idle_add(lambda: run(a) and False))
app.run([])
