"""Tests for the parts of Spruce that do not need a display.

    python3 -m unittest discover -s tests
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ['XDG_STATE_HOME'] = tempfile.mkdtemp()

from PIL import Image  # noqa: E402

from spruce import images, rename  # noqa: E402
from spruce.rename import Item, Options  # noqa: E402


def touch(folder, *names):
    for n in names:
        open(os.path.join(folder, n), 'w').close()


class RenameTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def items(self, *names, **kw):
        touch(self.dir, *names)
        opts = Options(**kw)
        items = rename.sort_items([Item(os.path.join(self.dir, n)) for n in names], opts.sort)
        rename.compute(items, opts)
        return items, opts

    def names(self, *names, **kw):
        return [i.new_name for i in self.items(*names, **kw)[0]]

    def test_find_replace(self):
        self.assertEqual(self.names('IMG_1.jpg', 'IMG_2.jpg', find='img_', replace='Trip '),
                         ['Trip 1.jpg', 'Trip 2.jpg'])

    def test_match_case_and_first_only(self):
        self.assertEqual(self.names('aAa.txt', find='a', replace='b', match_case=True), ['bAb.txt'])
        self.assertEqual(self.names('xaa.txt', find='a', replace='b', replace_all=False), ['xba.txt'])

    def test_regex_groups(self):
        self.assertEqual(self.names('2024-05-01 notes.md', find=r'(\d+)-(\d+)-(\d+)', replace=r'\3.\2.\1', regex=True),
                         ['01.05.2024 notes.md'])

    def test_bad_regex(self):
        items, _ = self.items('a.txt', find='(', regex=True)
        self.assertEqual(items[0].status, 'error')

    def test_case(self):
        self.assertEqual(self.names("it's MY file.TXT", case='title'), ["It's My File.TXT"])
        self.assertEqual(self.names('HELLO world.txt', case='sentence'), ['Hello world.txt'])

    def test_numbering_natural_order(self):
        got = self.names('b10.png', 'b2.png', 'b1.png', find='b', replace='', number=True,
                         number_position='before', number_start=7, number_padding=3, number_separator='_')
        self.assertEqual(got, ['007_1.png', '008_2.png', '009_10.png'])

    def test_tokens(self):
        got = self.names('x.jpeg', prefix='{parent}-', suffix='-{n}', ext_mode='change', new_ext='jpg')
        self.assertEqual(got, [f'{os.path.basename(self.dir)}-x-01.jpg'])

    def test_extension_modes(self):
        self.assertEqual(self.names('A.JPG', ext_mode='lower'), ['A.jpg'])
        self.assertEqual(self.names('b.tar', ext_mode='remove'), ['b'])

    def test_conflicts(self):
        items, _ = self.items('a1.txt', 'a2.txt', find=r'\d', replace='', regex=True)
        self.assertEqual({i.status for i in items}, {'conflict'})
        touch(self.dir, 'taken.txt')
        items, _ = self.items('c.txt', find='c', replace='taken')
        self.assertEqual(items[0].status, 'exists')
        items, _ = self.items('d.txt', find='d', replace='a/b')
        self.assertEqual(items[0].status, 'invalid')

    def test_swap_and_undo(self):
        touch(self.dir, 'one.txt', 'two.txt')
        with open(os.path.join(self.dir, 'one.txt'), 'w') as f:
            f.write('1')
        opts = Options(find='(one|two)', replace='x', regex=True)
        items = rename.sort_items([Item(os.path.join(self.dir, n)) for n in ('one.txt', 'two.txt')], 'name')
        # swap by hand: one -> two, two -> one
        rename.compute(items, opts)
        items[0].new_name, items[0].status = 'two.txt', 'ok'
        items[1].new_name, items[1].status = 'one.txt', 'ok'
        n, errors = rename.apply(items)
        self.assertEqual((n, errors), (2, []))
        self.assertEqual(open(os.path.join(self.dir, 'two.txt')).read(), '1')
        n, errors = rename.undo()
        self.assertEqual((n, errors), (2, []))
        self.assertEqual(open(os.path.join(self.dir, 'one.txt')).read(), '1')
        self.assertIsNone(rename.load_undo())

    def test_case_only_change_is_not_a_conflict(self):
        items, _ = self.items('Photo.JPG', ext_mode='lower')
        self.assertEqual(items[0].status, 'ok')
        rename.apply(items)
        self.assertEqual(sorted(os.listdir(self.dir)), ['Photo.jpg'])
        rename.undo()
        self.assertEqual(sorted(os.listdir(self.dir)), ['Photo.JPG'])

    def test_exif_tokens(self):
        path = os.path.join(self.dir, 'p.jpg')
        exif = Image.Exif()
        exif[0x0110] = 'Pixel 9'
        exif.get_ifd(0x8769)[0x9003] = '2024:05:06 07:08:09'
        Image.new('RGB', (40, 20)).save(path, exif=exif.tobytes())
        items = [Item(path)]
        rename.compute(items, Options(prefix='{taken} {camera} {width}x{height} '))
        self.assertEqual(items[0].new_name, '2024-05-06 Pixel 9 40x20 p.jpg')


class ImageTest(unittest.TestCase):
    def test_target_size(self):
        self.assertEqual(images.target_size((4000, 3000), (1920, 1080)), ((1440, 1080), None))
        self.assertEqual(images.target_size((4000, 3000), (1920, 1080), 'fill'),
                         ((1920, 1440), (0, 180, 1920, 1260)))
        self.assertEqual(images.target_size((100, 100), (1920, 1080)), ((100, 100), None))
        self.assertEqual(images.target_size((100, 100), (200, 50), keep_aspect=False, shrink_only=False),
                         ((200, 50), None))

    def test_resize_and_convert_keep_original(self):
        d = tempfile.mkdtemp()
        src = os.path.join(d, 'a.png')
        Image.new('RGBA', (800, 400), (255, 0, 0, 100)).save(src)
        before = open(src, 'rb').read()
        for fmt in images.writable_formats():
            pil, ext = images.FORMATS[fmt]
            pic = images.Picture(src)
            images.resize(pic, (200, 200))
            out = images.commit(images.save(pic, os.path.join(d, 'a' + ext), pil, 80), os.path.join(d, 'a' + ext))
            self.assertTrue(os.path.exists(out), fmt)
            self.assertEqual(images.Picture(out).size, (200, 100), fmt)
        self.assertEqual(open(src, 'rb').read(), before)
        self.assertTrue(os.path.exists(os.path.join(d, 'a (2).png')))
        self.assertFalse([f for f in os.listdir(d) if f.startswith('.spruce-')])

    def test_orientation_and_strip(self):
        d = tempfile.mkdtemp()
        src = os.path.join(d, 'o.jpg')
        exif = Image.Exif()
        exif[0x0112] = 6
        exif[0x0110] = 'Cam'
        Image.new('RGB', (60, 30)).save(src, exif=exif.tobytes())
        pic = images.Picture(src)
        self.assertEqual(pic.size, (30, 60))
        out = images.commit(images.save(pic, src, 'JPEG'), src)
        e = Image.open(out).getexif()
        self.assertEqual((e.get(0x0112), e.get(0x0110)), (1, 'Cam'))
        out = images.commit(images.save(images.Picture(src), src, 'JPEG', strip=True), src)
        self.assertFalse(Image.open(out).getexif())


if __name__ == '__main__':
    unittest.main()
