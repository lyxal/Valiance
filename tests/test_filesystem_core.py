"""Contract tests for lossless path and sequential file primitives."""
import os
import tempfile
import unittest
from valiance.std._filesystem_core import Encoding, File, Path


class FilesystemCoreTests(unittest.TestCase):
    def test_lines_and_eof(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(os.path.join(root, 'notes.txt'))
            f = File(p, 'wb')
            f.write_line('one')
            f.write_line('')
            f.write_line('two\n')
            f.close()
            r = File(p, 'rb')
            self.assertEqual(r.read_lines(), ['one', '', 'two', ''])
            self.assertIsNone(r.read_line())
            r.close()

    def test_crlf_and_cr(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(os.path.join(root, 'notes.txt'))
            with open(p.native, 'wb') as out:
                out.write(b'a\r\nb\rc\n')
            r = File(p, 'rb')
            self.assertEqual(r.read_lines(), ['a', 'b', 'c'])
            r.close()

    def test_optional_native_path_components_are_lexical(self):
        self.assertIsNone(Path('file').parent())
        self.assertIsNone(Path(os.sep).parent())
        self.assertIsNone(Path(os.sep).name())
        self.assertIsNone(Path('file').extension())
        self.assertEqual(Path('folder/file').parent(), Path('folder'))
        self.assertEqual(Path('folder/file/').name(), Path('file'))
        self.assertEqual(Path('folder/file.txt').extension(), Path('.txt'))

    @unittest.skipIf(os.name == 'nt', 'POSIX byte paths are platform-specific')
    def test_non_unicode_path_components_remain_native(self):
        native = Path(b'directory/bad-\xff.data')
        self.assertEqual(native.parent(), Path(b'directory'))
        self.assertEqual(native.name(), Path(b'bad-\xff.data'))
        self.assertEqual(native.extension(), Path(b'.data'))
        self.assertEqual(native.native, b'directory/bad-\xff.data')

    def test_exact_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(os.path.join(root, 'data.bin'))
            with open(p.native, 'wb') as out:
                out.write(b'abc')
            r = File(p, 'rb')
            self.assertEqual(r.read_bytes(2), b'ab')
            with self.assertRaises(EOFError):
                r.read_bytes(2)
            r.close()

    def test_utf16(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(os.path.join(root, 'utf16.txt'))
            with open(p.native, 'wb') as out:
                out.write(b'\xff\xfe' + 'hi\nthere'.encode('utf-16-le'))
            r = File(p, 'rb', Encoding.UTF16LE)
            self.assertEqual(r.read_line(), 'hi')
            self.assertEqual(r.read_line(), 'there')
            r.close()

    @unittest.skipIf(os.name == 'nt', 'byte paths are POSIX-specific')
    def test_non_utf8_path(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(os.fsencode(root) + b'/bad-\xff')
            f = File(p, 'wb')
            f.write_text('ok')
            f.close()
            r = File(p, 'rb')
            self.assertEqual(r.read_text(), 'ok')
            r.close()
            self.assertIn('\ufffd', p.to_string())


if __name__ == '__main__':
    unittest.main()
