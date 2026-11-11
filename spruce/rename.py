"""Bulk rename: working out new names, spotting conflicts, renaming safely
and undoing the last batch. No GTK in here."""
import datetime
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field

from . import images

STATE_DIR = os.path.join(os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state'), 'spruce')
UNDO_FILE = os.path.join(STATE_DIR, 'last-rename.json')

TOKENS = [
    ('{n}', 'Number, using the numbering settings'),
    ('{name}', 'Original name, without extension'),
    ('{ext}', 'Original extension, without the dot'),
    ('{date}', 'Date modified'),
    ('{time}', 'Time modified (HH-MM-SS)'),
    ('{taken}', 'Date taken, from the photo (else date modified)'),
    ('{camera}', 'Camera model, from the photo'),
    ('{width}', 'Image width in pixels'),
    ('{height}', 'Image height in pixels'),
    ('{parent}', 'Name of the folder it is in'),
]
TOKEN_RE = re.compile(r'\{(n|name|ext|date|time|taken|camera|width|height|parent)\}')

CASES = ['keep', 'lower', 'upper', 'title', 'sentence']
EXT_MODES = ['keep', 'lower', 'upper', 'change', 'remove']
APPLY_TO = ['name', 'ext', 'both']
SORTS = ['name', 'modified', 'taken', 'given']


@dataclass
class Options:
    title: str = ''        # replaces the whole name (not the extension)
    find: str = ''
    replace: str = ''
    regex: bool = False
    match_case: bool = False
    replace_all: bool = True
    apply_to: str = 'name'
    prefix: str = ''
    suffix: str = ''
    case: str = 'keep'
    number: bool = False
    number_position: str = 'after'   # 'before' or 'after' the name
    number_start: int = 1
    number_step: int = 1
    number_padding: int = 2
    number_separator: str = ' '
    ext_mode: str = 'keep'
    new_ext: str = ''
    date_format: str = '%Y-%m-%d'
    sort: str = 'name'


class Item:
    """One file or folder in the batch."""

    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.folder, self.name = os.path.split(self.path)
        self.is_dir = os.path.isdir(self.path)
        if self.is_dir:
            self.stem, self.ext = self.name, ''
        else:
            self.stem, self.ext = os.path.splitext(self.name)
            if not self.stem:  # ".bashrc"
                self.stem, self.ext = self.name, ''
        try:
            self.mtime = os.lstat(self.path).st_mtime
        except OSError:
            self.mtime = 0
        self._photo = None
        # results of the last compute()
        self.new_name = self.name
        self.status = 'same'   # same, ok, conflict, exists, invalid, error
        self.message = ''
        self.enabled = True

    def photo(self):
        """(date taken as datetime or None, camera, width, height), read once."""
        if self._photo is None:
            taken = camera = width = height = None
            if not self.is_dir and images.format_of(self.path):
                try:
                    from PIL import Image
                    with Image.open(self.path) as im:
                        width, height = im.size
                        exif = im.getexif()
                        if exif.get(0x0112) in (5, 6, 7, 8):
                            width, height = height, width
                        camera = (exif.get(0x0110) or '').strip('\x00 ') or None
                        sub = exif.get_ifd(0x8769)
                        raw = sub.get(0x9003) or sub.get(0x9004) or exif.get(0x0132)
                        if raw:
                            taken = datetime.datetime.strptime(str(raw).strip('\x00 ')[:19], '%Y:%m:%d %H:%M:%S')
                except Exception:
                    pass
            self._photo = (taken, camera, width, height)
        return self._photo

    @property
    def target(self):
        return os.path.join(self.folder, self.new_name)


def needs_photo(opts):
    text = opts.title + opts.find + opts.replace + opts.prefix + opts.suffix + opts.new_ext
    return opts.sort == 'taken' or any(t in text for t in ('{taken}', '{camera}', '{width}', '{height}'))


def sort_items(items, how):
    if how == 'name':
        return sorted(items, key=lambda i: _natural(i.name))
    if how == 'modified':
        return sorted(items, key=lambda i: (i.mtime, _natural(i.name)))
    if how == 'taken':
        return sorted(items, key=lambda i: (i.photo()[0] or datetime.datetime.fromtimestamp(i.mtime), _natural(i.name)))
    return list(items)


def _natural(s):
    """Sort key so that "img2" comes before "img10"."""
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r'(\d+)', s)]


def _expand(text, item, number, opts, escape=False):
    if '{' not in text:
        return text

    def value(m):
        key = m.group(1)
        modified = datetime.datetime.fromtimestamp(item.mtime)
        if key == 'n':
            v = str(number).zfill(opts.number_padding) if number >= 0 else '-' + str(-number).zfill(opts.number_padding)
        elif key == 'name':
            v = item.stem
        elif key == 'ext':
            v = item.ext.lstrip('.')
        elif key == 'date':
            v = _strftime(modified, opts.date_format)
        elif key == 'time':
            v = modified.strftime('%H-%M-%S')
        elif key == 'taken':
            v = _strftime(item.photo()[0] or modified, opts.date_format)
        elif key == 'camera':
            v = item.photo()[1] or ''
        elif key == 'width':
            v = str(item.photo()[2] or '')
        elif key == 'height':
            v = str(item.photo()[3] or '')
        else:  # parent
            v = os.path.basename(item.folder)
        v = v.replace('/', '-')
        return v.replace('\\', '\\\\') if escape else v

    return TOKEN_RE.sub(value, text)


def _strftime(dt, fmt):
    try:
        return dt.strftime(fmt or '%Y-%m-%d').replace('/', '-').replace(':', '-')
    except ValueError:
        return dt.strftime('%Y-%m-%d')


def _change_case(s, case):
    if case == 'lower':
        return s.lower()
    if case == 'upper':
        return s.upper()
    if case == 'title':
        # str.title() would turn "it's" into "It'S"; capitalise words only.
        return re.sub(r"[^\W_]+('[^\W_]+)?", lambda m: m.group(0)[:1].upper() + m.group(0)[1:].lower(), s)
    if case == 'sentence':
        s = s.lower()
        m = re.search(r'[^\W\d_]', s)
        return s[:m.start()] + s[m.start()].upper() + s[m.start() + 1:] if m else s
    return s


def new_name(item, number, opts, pattern):
    """The new name for one item. pattern is the compiled find pattern or
    None. Raises ValueError for a bad regular expression replacement."""
    stem, ext = item.stem, item.ext
    if opts.title:
        stem = _expand(opts.title, item, number, opts)
    if pattern is not None:
        replacement = _expand(opts.replace, item, number, opts, escape=True)
        if not opts.regex:
            replacement = replacement.replace('\\\\', '\\')
            repl = lambda m: replacement  # noqa: E731
        else:
            repl = lambda m: m.expand(replacement)  # noqa: E731
        count = 0 if opts.replace_all else 1
        if opts.apply_to == 'name':
            stem = pattern.sub(repl, stem, count=count)
        elif opts.apply_to == 'ext':
            ext = '.' + pattern.sub(repl, ext[1:], count=count) if ext else ext
            if ext == '.':
                ext = ''
        else:
            whole = pattern.sub(repl, stem + ext, count=count)
            if item.is_dir:
                stem, ext = whole, ''
            else:
                stem, ext = os.path.splitext(whole)
                if not stem:
                    stem, ext = whole, ''

    stem = _change_case(stem, opts.case)
    stem = _expand(opts.prefix, item, number, opts) + stem + _expand(opts.suffix, item, number, opts)

    if opts.number:
        n = _expand('{n}', item, number, opts)
        sep = opts.number_separator
        if not stem:
            stem = n
        elif opts.number_position == 'before':
            stem = n + sep + stem
        else:
            stem = stem + sep + n

    if not item.is_dir:
        if opts.ext_mode == 'lower':
            ext = ext.lower()
        elif opts.ext_mode == 'upper':
            ext = ext.upper()
        elif opts.ext_mode == 'remove':
            ext = ''
        elif opts.ext_mode == 'change':
            e = _expand(opts.new_ext, item, number, opts).strip().lstrip('.')
            ext = '.' + e if e else ''
    return stem + ext


def compile_find(opts):
    """Returns (pattern or None, error message or '')."""
    if not opts.find:
        return None, ''
    flags = 0 if opts.match_case else re.IGNORECASE
    try:
        return re.compile(opts.find if opts.regex else re.escape(opts.find), flags), ''
    except re.error as e:
        return None, f'Regular expression: {e}'


def compute(items, opts):
    """Fills in new_name, status and message on every item, in place.
    items must already be in the order numbers are given out. Returns an
    error message for the whole batch (a bad regex), or ''."""
    pattern, error = compile_find(opts)
    number = opts.number_start
    for item in items:
        item.message = ''
        if error:
            item.new_name, item.status, item.message = item.name, 'error', error
            continue
        try:
            item.new_name = new_name(item, number, opts, pattern)
        except (re.error, IndexError, ValueError) as e:
            item.new_name, item.status, item.message = item.name, 'error', f'Replacement: {e}'
            error = item.message
            continue
        if item.enabled:
            number += opts.number_step
        if not item.enabled or item.new_name == item.name:
            item.status = 'same'
            item.new_name = item.new_name if item.enabled else item.name
            if not item.enabled:
                item.message = 'Skipped'
            continue
        bad = invalid_reason(item.new_name)
        if not bad and item.new_name.startswith('.') and not item.name.startswith('.'):
            bad = 'Would become a hidden file'
        if bad:
            item.status, item.message = 'invalid', bad
        else:
            item.status = 'ok'

    # Two items ending up with the same name in one folder. Compared without
    # case too, because USB drives (FAT, exFAT) ignore case.
    moving = [i for i in items if i.status == 'ok']
    seen = {}
    for item in items:
        if item.status in ('ok', 'same'):
            key = (item.folder, item.new_name.casefold())
            seen.setdefault(key, []).append(item)
    for group in seen.values():
        if len(group) > 1:
            for item in group:
                if item.status == 'ok':
                    item.status, item.message = 'conflict', 'Same new name as another item'

    # The new name is taken on disk by something that is not moving away.
    leaving = {i.path for i in moving}
    for item in items:
        if item.status != 'ok':
            continue
        target = item.target
        if os.path.lexists(target):
            if _same_file(target, item.path):
                continue  # only the case changes
            if target in leaving and _item_by_path(items, target).status == 'ok':
                continue  # swapped with another item in the batch
            item.status, item.message = 'exists', 'A file with this name already exists'
    # A clash can make a swap partner stay put; repeat until stable.
    changed = True
    while changed:
        changed = False
        staying = {i.path for i in items if i.status != 'ok'}
        for item in items:
            if item.status == 'ok' and item.target in staying and not _same_file(item.target, item.path):
                item.status, item.message = 'exists', 'A file with this name already exists'
                changed = True
    return error


def _item_by_path(items, path):
    return next(i for i in items if i.path == path)


def _same_file(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def invalid_reason(name):
    if not name or not name.strip():
        return 'The name is empty'
    if name in ('.', '..'):
        return 'Not a valid name'
    if '/' in name or '\0' in name:
        return 'Names cannot contain /'
    if len(name.encode()) > 255:
        return 'The name is too long'
    return ''


def _two_phase(moves):
    """Renames [(src, dst), ...] where a dst may be another item's src (a
    swap, or a change of case only). Every item first gets a unique
    temporary name, then its final one. Returns (done, errors), with done
    the moves that completed."""
    tag = uuid.uuid4().hex[:8]
    staged, errors, done = [], [], []
    for i, (src, dst) in enumerate(moves):
        tmp = os.path.join(os.path.dirname(src), f'.spruce-{tag}-{i}')
        try:
            os.rename(src, tmp)
            staged.append((src, tmp, dst))
        except OSError as e:
            errors.append((src, e.strerror or str(e)))
    for src, tmp, dst in staged:
        try:
            if os.path.lexists(dst):
                raise FileExistsError(17, 'A file with this name already exists')
            os.rename(tmp, dst)
            done.append((src, dst))
        except OSError as e:
            errors.append((src, e.strerror or str(e)))
            try:
                os.rename(tmp, src)
            except OSError:
                errors.append((src, f'left as {os.path.basename(tmp)}'))
    return done, errors


def apply(items):
    """Renames every item with status 'ok' and saves the undo record.
    Returns (number renamed, errors as [(path, message)])."""
    moves = [(i.path, i.target) for i in items if i.status == 'ok']
    done, errors = _two_phase(moves)
    if done:
        save_undo(done)
    return len(done), errors


def save_undo(done):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = UNDO_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'time': time.time(), 'moves': [[a, b] for a, b in done]}, f, indent=1)
    os.replace(tmp, UNDO_FILE)


def load_undo():
    try:
        with open(UNDO_FILE, encoding='utf-8') as f:
            data = json.load(f)
        return data if data.get('moves') else None
    except (OSError, ValueError):
        return None


def undo():
    """Puts the last batch back. Items that were moved or deleted since are
    left alone. Returns (number restored, errors)."""
    data = load_undo()
    if not data:
        return 0, []
    moves, errors = [], []
    renamed_to = {b for a, b in data['moves']}
    for original, renamed in data['moves']:
        if not os.path.lexists(renamed):
            errors.append((renamed, 'No longer there'))
        elif os.path.lexists(original) and original not in renamed_to \
                and not _same_file(original, renamed):
            errors.append((renamed, f'{os.path.basename(original)} exists again'))
        else:
            moves.append((renamed, original))
    done, more = _two_phase(moves)
    errors += more
    if not errors:
        try:
            os.unlink(UNDO_FILE)
        except OSError:
            pass
    else:
        # Keep what could not be undone, so a second try can finish it.
        left = [[o, r] for o, r in data['moves'] if (r, o) not in done]
        if left:
            save_undo([tuple(x) for x in left])
        else:
            os.unlink(UNDO_FILE)
    return len(done), errors
