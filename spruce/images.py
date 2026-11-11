"""Loading, resizing and saving images. No GTK in here, so it can be tested
on its own."""
import os
import tempfile

from PIL import Image, ImageOps, ImageSequence, features

from . import heif

Image.MAX_IMAGE_PIXELS = 400_000_000  # big panoramas are fine, bombs are not

# name shown in the UI -> (Pillow format or heif name, file extension)
FORMATS = {
    'PNG': ('PNG', '.png'),
    'JPEG': ('JPEG', '.jpg'),
    'WebP': ('WEBP', '.webp'),
    'AVIF': ('AVIF', '.avif'),
    'HEIC': ('HEIC', '.heic'),
}
EXTENSIONS = {
    '.png': 'PNG', '.jpg': 'JPEG', '.jpeg': 'JPEG', '.jpe': 'JPEG', '.jfif': 'JPEG',
    '.webp': 'WEBP', '.avif': 'AVIF', '.heic': 'HEIC', '.heif': 'HEIC',
    '.gif': 'GIF', '.bmp': 'BMP', '.tif': 'TIFF', '.tiff': 'TIFF',
}
LOSSY = {'JPEG', 'WEBP', 'AVIF', 'HEIC'}
ANIMATED = {'GIF', 'WEBP', 'PNG'}

PRESETS = [
    # key, label, width, height (PowerToys' defaults)
    ('small', 'Small', 854, 480),
    ('medium', 'Medium', 1366, 768),
    ('large', 'Large', 1920, 1080),
    ('phone', 'Phone', 320, 568),
]


def can_write(fmt):
    """fmt is a FORMATS key."""
    pil = FORMATS[fmt][0]
    if pil in ('AVIF', 'HEIC'):
        return heif.can_write(pil) or (pil == 'AVIF' and features.check('avif'))
    return True


def writable_formats():
    return [f for f in FORMATS if can_write(f)]


def format_of(path):
    return EXTENSIONS.get(os.path.splitext(path)[1].lower())


class Picture:
    """A decoded image plus what is needed to write it back out."""

    def __init__(self, path):
        self.path = path
        fmt = format_of(path)
        if fmt in ('AVIF', 'HEIC') and not features.check('avif'):
            self.image, self.exif, self.icc = heif.read(path)
            self.format = fmt
            self.frames = None
        else:
            image = Image.open(path)
            self.format = image.format or fmt
            self.exif = image.info.get('exif')
            self.icc = image.info.get('icc_profile')
            n = getattr(image, 'n_frames', 1)
            if n > 1:
                self.frames = [f.copy() for f in ImageSequence.Iterator(image)]
                self.durations = [f.info.get('duration', 100) for f in ImageSequence.Iterator(image)]
                self.loop = image.info.get('loop', 0)
                image.seek(0)
            else:
                self.frames = None
            image.load()
            self.image = image
        # Turn the pixels upright and record that in the EXIF, so viewers
        # that honour the orientation tag do not rotate it a second time.
        exif = self.image.getexif()
        if self.exif and not exif:
            exif.load(self.exif)
        if exif.get(0x0112, 1) != 1:
            self.image = ImageOps.exif_transpose(self.image)
            if self.frames:
                self.frames = [ImageOps.exif_transpose(f) for f in self.frames]
            exif[0x0112] = 1
            self.exif = exif.tobytes()

    @property
    def size(self):
        return self.image.size


def target_size(size, box, mode='fit', keep_aspect=True, shrink_only=True):
    """The size an image of `size` ends up at for a `box` of (w, h).

    mode 'fit' keeps the whole picture inside the box, 'fill' covers the box
    and is then cropped to it. Without keep_aspect the image is stretched to
    the box. A 0 in the box means "any" for that side. Returns (resize_to,
    crop_box or None)."""
    w, h = size
    bw, bh = box
    if not keep_aspect and bw and bh:
        new = (bw, bh)
        if shrink_only and (bw > w or bh > h):
            return size, None
        return new, None
    sx = bw / w if bw else None
    sy = bh / h if bh else None
    scales = [s for s in (sx, sy) if s is not None]
    if not scales:
        return size, None
    scale = (max if mode == 'fill' and len(scales) == 2 else min)(scales)
    if shrink_only and scale >= 1:
        return size, None
    new = (max(1, round(w * scale)), max(1, round(h * scale)))
    if mode == 'fill' and bw and bh and new != (bw, bh):
        left = (new[0] - bw) // 2
        top = (new[1] - bh) // 2
        return new, (left, top, left + bw, top + bh)
    return new, None


def _resized(img, new, crop):
    if img.size != new:
        if img.mode == 'P':
            img = img.convert('RGBA')
        img = img.resize(new, Image.LANCZOS, reducing_gap=3.0)
    if crop:
        img = img.crop(crop)
    return img


def resize(pic, box, mode='fit', keep_aspect=True, shrink_only=True, percent=None):
    """Resizes pic in memory. Returns True if anything changed."""
    if percent:
        box = (max(1, round(pic.size[0] * percent / 100)), max(1, round(pic.size[1] * percent / 100)))
        keep_aspect, mode = False, 'fit'
    new, crop = target_size(pic.size, box, mode, keep_aspect, shrink_only)
    if new == pic.size and not crop:
        return False
    pic.image = _resized(pic.image, new, crop)
    if pic.frames:
        pic.frames = [_resized(f, new, crop) for f in pic.frames]
    return True


def _flatten(img, mode='RGB'):
    """Drops transparency onto white, for formats without an alpha channel."""
    if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
        img = img.convert('RGBA')
        bg = Image.new('RGB', img.size, (255, 255, 255))
        bg.paste(img, mask=img.getchannel('A'))
        return bg
    return img.convert(mode) if img.mode != mode else img


def _clean_exif(exif):
    """EXIF rewritten by Pillow, which leaves out the embedded thumbnail
    (it would still show the old size or crop)."""
    if not exif:
        return None
    try:
        e = Image.Exif()
        e.load(exif)
        return e.tobytes()
    except Exception:
        return exif


def save(pic, path, fmt=None, quality=90, strip=False):
    """Writes pic to path in fmt (a Pillow format name, AVIF or HEIC).

    The file is written to a temporary name next to the destination first
    and then moved into place, so a failure never leaves half a file."""
    fmt = fmt or pic.format
    exif = None if strip else _clean_exif(pic.exif)
    icc = pic.icc  # the colour profile is part of how the image looks: keep it
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix='.spruce-', suffix=os.path.splitext(path)[1], dir=folder)
    os.close(fd)
    try:
        img = pic.image
        frames = pic.frames if pic.frames and fmt in ANIMATED else None
        if fmt in ('AVIF', 'HEIC') and not (fmt == 'AVIF' and features.check('avif')):
            heif.write(img, tmp, fmt, quality, exif, icc)
        else:
            opts = {}
            if exif:
                opts['exif'] = exif
            if icc:
                opts['icc_profile'] = icc
            if fmt == 'JPEG':
                img = _flatten(img)
                opts.update(quality=quality, optimize=True)
                if quality >= 90:
                    opts['subsampling'] = 0
            elif fmt == 'WEBP':
                opts.update(quality=quality, method=4)
            elif fmt == 'AVIF':
                opts.update(quality=quality)
            elif fmt == 'PNG':
                if img.mode not in ('1', 'L', 'LA', 'P', 'RGB', 'RGBA', 'I', 'I;16'):
                    img = img.convert('RGBA')
            elif fmt == 'BMP':
                img = _flatten(img)
                opts.pop('exif', None)
            elif fmt == 'GIF':
                opts.pop('exif', None)
                opts.pop('icc_profile', None)
            if frames:
                first, rest = frames[0], frames[1:]
                if fmt == 'JPEG':
                    first = _flatten(first)
                opts.update(save_all=True, append_images=rest, duration=pic.durations, loop=pic.loop)
                first.save(tmp, fmt, **opts)
            else:
                img.save(tmp, fmt, **opts)
        try:
            os.chmod(tmp, os.stat(pic.path).st_mode & 0o777)
        except OSError:
            os.chmod(tmp, 0o644)
        return tmp
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def free_path(path):
    """path, or "name (2).ext", "name (3).ext" ... whichever does not exist."""
    if not os.path.lexists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.lexists(f'{base} ({n}){ext}'):
        n += 1
    return f'{base} ({n}){ext}'


def commit(tmp, dest, replace=False):
    """Moves a finished temporary file to dest. Without replace, dest must not
    exist; a free name is picked if it appeared meanwhile."""
    if not replace:
        dest = free_path(dest)
        # link() fails instead of overwriting if someone else took the name.
        try:
            os.link(tmp, dest)
            os.unlink(tmp)
            return dest
        except FileExistsError:
            return commit(tmp, dest, False)
        except OSError:
            pass  # no hard links on this drive (FAT, some network shares)
    os.replace(tmp, dest)
    return dest
