"""Spruce for Files (Nautilus): adds "Bulk Rename…", "Resize Images…" and
"Convert Images…" to the right-click menu of selected files.

Each item starts the Spruce app with the selected paths. The image items
appear only when the selection holds images Spruce can open.

Needs python3-nautilus. Installed to /usr/share/nautilus-python/extensions/
(deb) or ~/.local/share/nautilus-python/extensions/ (install.sh).
"""
import os
import shutil
import subprocess

import gi

# Ubuntu 26.04 ships the bindings as Nautilus 4.1; older releases call it 4.0.
for _version in ('4.1', '4.0'):
    try:
        gi.require_version('Nautilus', _version)
        break
    except ValueError:
        continue
from gi.repository import GObject, Nautilus  # noqa: E402

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.jpe', '.jfif', '.webp', '.avif', '.heic', '.heif',
                    '.gif', '.bmp', '.tif', '.tiff'}


def spruce_command():
    """The spruce launcher. Files may be started without ~/.local/bin on
    its PATH, so that is checked by hand."""
    found = shutil.which('spruce')
    if found:
        return found
    for path in (os.path.expanduser('~/.local/bin/spruce'), '/usr/bin/spruce'):
        if os.access(path, os.X_OK):
            return path
    return None


def local_path(file_info):
    if file_info.get_uri_scheme() != 'file':
        return None
    location = file_info.get_location()
    return location.get_path() if location else None


def is_image(path):
    return os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS and os.path.isfile(path)


def run(tool, paths):
    command = spruce_command()
    if not command:
        subprocess.run(['notify-send', '--app-name=Spruce', 'Spruce is not installed',
                        'The spruce command could not be found.'], check=False)
        return
    # start_new_session keeps the window open if Files is closed.
    subprocess.Popen([command, tool, *paths],  # paths are absolute, never options
                     start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class SpruceExtension(GObject.GObject, Nautilus.MenuProvider):
    def get_file_items(self, *args):
        files = args[-1]
        paths = [local_path(f) for f in files]
        paths = [p for p in paths if p]
        if not paths:
            return []
        items = []
        rename = Nautilus.MenuItem(name='Spruce::BulkRename', label='Bulk Rename…',
                                   tip='Rename these items together, with a preview')
        rename.connect('activate', lambda _i: run('rename', paths))
        items.append(rename)

        pictures = [p for p in paths if is_image(p)]
        if pictures:
            count = f' ({len(pictures)})' if len(pictures) != len(paths) else ''
            resize = Nautilus.MenuItem(name='Spruce::ResizeImages', label=f'Resize Images…{count}',
                                       tip='Make smaller copies, or resize in place')
            resize.connect('activate', lambda _i: run('resize', pictures))
            convert = Nautilus.MenuItem(name='Spruce::ConvertImages', label=f'Convert Images…{count}',
                                        tip='Save as PNG, JPEG, WebP or AVIF')
            convert.connect('activate', lambda _i: run('convert', pictures))
            items += [resize, convert]
        return items

    def get_background_items(self, *args):
        return []
