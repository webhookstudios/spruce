"""AVIF and HEIC through libheif, called directly with ctypes.

Pillow on Ubuntu reads and writes neither format, but libheif is installed
with GNOME (it backs the image viewer). Which formats work depends on its
plugins: AVIF needs libheif-plugin-aomenc / aomdec (installed by default),
HEIC needs libheif-plugin-x265 to write and libheif-plugin-libde265 to read.
"""
import ctypes
import ctypes.util

from PIL import Image

HEVC, AV1 = 1, 4                 # enum heif_compression_format
COLORSPACE_RGB = 1               # enum heif_colorspace
CHROMA_RGB, CHROMA_RGBA = 10, 11  # enum heif_chroma (8 bit interleaved)
CHANNEL_INTERLEAVED = 10         # enum heif_channel


class _Error(ctypes.Structure):
    _fields_ = [('code', ctypes.c_int), ('subcode', ctypes.c_int), ('message', ctypes.c_char_p)]


class HeifError(Exception):
    pass


def _load():
    for name in ('libheif.so.1', ctypes.util.find_library('heif')):
        if not name:
            continue
        try:
            lib = ctypes.CDLL(name)
        except OSError:
            continue
        break
    else:
        return None
    p, i = ctypes.c_void_p, ctypes.c_int
    sig = {
        'heif_init': (p, [p]),
        'heif_context_alloc': (p, []),
        'heif_context_free': (None, [p]),
        'heif_context_read_from_file': (_Error, [p, ctypes.c_char_p, p]),
        'heif_context_get_primary_image_handle': (_Error, [p, ctypes.POINTER(p)]),
        'heif_image_handle_release': (None, [p]),
        'heif_image_handle_has_alpha_channel': (i, [p]),
        'heif_decode_image': (_Error, [p, ctypes.POINTER(p), i, i, p]),
        'heif_image_release': (None, [p]),
        'heif_image_get_width': (i, [p, i]),
        'heif_image_get_height': (i, [p, i]),
        'heif_image_get_plane_readonly': (ctypes.POINTER(ctypes.c_uint8), [p, i, ctypes.POINTER(i)]),
        'heif_image_get_plane': (ctypes.POINTER(ctypes.c_uint8), [p, i, ctypes.POINTER(i)]),
        'heif_image_handle_get_number_of_metadata_blocks': (i, [p, ctypes.c_char_p]),
        'heif_image_handle_get_list_of_metadata_block_IDs': (i, [p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint32), i]),
        'heif_image_handle_get_metadata_size': (ctypes.c_size_t, [p, ctypes.c_uint32]),
        'heif_image_handle_get_metadata': (_Error, [p, ctypes.c_uint32, p]),
        'heif_image_handle_get_raw_color_profile_size': (ctypes.c_size_t, [p]),
        'heif_image_handle_get_raw_color_profile': (_Error, [p, p]),
        'heif_have_encoder_for_format': (i, [i]),
        'heif_have_decoder_for_format': (i, [i]),
        'heif_context_get_encoder_for_format': (_Error, [p, i, ctypes.POINTER(p)]),
        'heif_encoder_release': (None, [p]),
        'heif_encoder_set_lossy_quality': (_Error, [p, i]),
        'heif_image_create': (_Error, [i, i, i, i, ctypes.POINTER(p)]),
        'heif_image_add_plane': (_Error, [p, i, i, i, i]),
        'heif_image_set_raw_color_profile': (_Error, [p, ctypes.c_char_p, p, ctypes.c_size_t]),
        'heif_context_encode_image': (_Error, [p, p, p, p, ctypes.POINTER(p)]),
        'heif_context_add_exif_metadata': (_Error, [p, p, p, i]),
        'heif_context_write_to_file': (_Error, [p, ctypes.c_char_p]),
    }
    try:
        for fn, (res, args) in sig.items():
            f = getattr(lib, fn)
            f.restype, f.argtypes = res, args
    except AttributeError:
        return None
    lib.heif_init(None)
    return lib


_lib = _load()


def _check(err):
    if err.code:
        raise HeifError((err.message or b'libheif error').decode(errors='replace'))


def can_write(fmt):
    """fmt is 'AVIF' or 'HEIC'."""
    return bool(_lib) and bool(_lib.heif_have_encoder_for_format(AV1 if fmt == 'AVIF' else HEVC))


def can_read(fmt):
    return bool(_lib) and bool(_lib.heif_have_decoder_for_format(AV1 if fmt == 'AVIF' else HEVC))


def read(path):
    """Returns (PIL image, exif bytes or None, icc bytes or None)."""
    if not _lib:
        raise HeifError('libheif is not installed')
    ctx = _lib.heif_context_alloc()
    handle, img = ctypes.c_void_p(), ctypes.c_void_p()
    try:
        _check(_lib.heif_context_read_from_file(ctx, path.encode(), None))
        _check(_lib.heif_context_get_primary_image_handle(ctx, ctypes.byref(handle)))
        alpha = bool(_lib.heif_image_handle_has_alpha_channel(handle))
        chroma, mode = (CHROMA_RGBA, 'RGBA') if alpha else (CHROMA_RGB, 'RGB')
        _check(_lib.heif_decode_image(handle, ctypes.byref(img), COLORSPACE_RGB, chroma, None))
        w = _lib.heif_image_get_width(img, CHANNEL_INTERLEAVED)
        h = _lib.heif_image_get_height(img, CHANNEL_INTERLEAVED)
        stride = ctypes.c_int()
        plane = _lib.heif_image_get_plane_readonly(img, CHANNEL_INTERLEAVED, ctypes.byref(stride))
        data = ctypes.string_at(plane, stride.value * h)
        image = Image.frombuffer(mode, (w, h), data, 'raw', mode, stride.value, 1).copy()

        exif = None
        n = _lib.heif_image_handle_get_number_of_metadata_blocks(handle, b'Exif')
        if n > 0:
            ids = (ctypes.c_uint32 * n)()
            _lib.heif_image_handle_get_list_of_metadata_block_IDs(handle, b'Exif', ids, n)
            size = _lib.heif_image_handle_get_metadata_size(handle, ids[0])
            buf = ctypes.create_string_buffer(size)
            _check(_lib.heif_image_handle_get_metadata(handle, ids[0], buf))
            raw = buf.raw
            # The block starts with a 4 byte offset to the TIFF header.
            if len(raw) > 4:
                offset = int.from_bytes(raw[:4], 'big')
                exif = raw[4 + offset:] or None
        icc = None
        size = _lib.heif_image_handle_get_raw_color_profile_size(handle)
        if size:
            buf = ctypes.create_string_buffer(size)
            if not _lib.heif_image_handle_get_raw_color_profile(handle, buf).code:
                icc = buf.raw
        return image, exif, icc
    finally:
        if img:
            _lib.heif_image_release(img)
        if handle:
            _lib.heif_image_handle_release(handle)
        _lib.heif_context_free(ctx)


def write(image, path, fmt='AVIF', quality=80, exif=None, icc=None):
    if not can_write(fmt):
        raise HeifError(f'This system cannot write {fmt} files')
    if image.mode not in ('RGB', 'RGBA'):
        image = image.convert('RGBA' if 'A' in image.getbands() or image.mode == 'P' else 'RGB')
    mode = image.mode
    chroma = CHROMA_RGBA if mode == 'RGBA' else CHROMA_RGB
    w, h = image.size
    ctx = _lib.heif_context_alloc()
    enc, img, handle = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    try:
        _check(_lib.heif_context_get_encoder_for_format(ctx, AV1 if fmt == 'AVIF' else HEVC, ctypes.byref(enc)))
        _check(_lib.heif_encoder_set_lossy_quality(enc, int(quality)))
        _check(_lib.heif_image_create(w, h, COLORSPACE_RGB, chroma, ctypes.byref(img)))
        _check(_lib.heif_image_add_plane(img, CHANNEL_INTERLEAVED, w, h, 8))
        stride = ctypes.c_int()
        plane = _lib.heif_image_get_plane(img, CHANNEL_INTERLEAVED, ctypes.byref(stride))
        row = w * len(mode)
        data = image.tobytes()
        base = ctypes.addressof(plane.contents)
        if stride.value == row:
            ctypes.memmove(base, data, row * h)
        else:
            for y in range(h):
                ctypes.memmove(base + y * stride.value, data[y * row:(y + 1) * row], row)
        if icc:
            _lib.heif_image_set_raw_color_profile(img, b'prof', icc, len(icc))
        _check(_lib.heif_context_encode_image(ctx, img, enc, None, ctypes.byref(handle)))
        if exif:
            if exif.startswith(b'Exif\x00\x00'):
                exif = exif[6:]
            _check(_lib.heif_context_add_exif_metadata(ctx, handle, exif, len(exif)))
        _check(_lib.heif_context_write_to_file(ctx, path.encode()))
    finally:
        if handle:
            _lib.heif_image_handle_release(handle)
        if img:
            _lib.heif_image_release(img)
        if enc:
            _lib.heif_encoder_release(enc)
        _lib.heif_context_free(ctx)
