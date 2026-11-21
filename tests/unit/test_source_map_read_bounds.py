"""SARIF source-map reads must enforce the byte limit on the actual stream."""

import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from searcharis.site_source_map import _MAX_BYTES, read_source_map


class SourceMapReadBoundsTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / "source-map.json"
        self.report = {"pages": [], "coverage": {"unlisted": []}}

    def read_map(self):
        return read_source_map(self.path, self.report, source_root=self.root)

    def test_stale_small_stat_cannot_accept_oversized_map(self):
        prefix = b'{"version":1,"sources":{}}'
        self.path.write_bytes(prefix + b" " * (_MAX_BYTES + 1 - len(prefix)))
        with patch.object(Path, "stat", return_value=SimpleNamespace(st_size=1)):
            with self.assertRaisesRegex(ValueError, "Source map exceeds 1000000 bytes"):
                self.read_map()

    def test_exact_byte_limit_and_utf8_crlf_are_supported(self):
        prefix = '{\r\n"version": 1, "sources": {}\r\n}'.encode("utf-8")
        self.path.write_bytes(prefix + b" " * (_MAX_BYTES - len(prefix)))
        self.assertEqual(self.read_map(), {})

    def test_actual_oversized_file_is_rejected(self):
        self.path.write_bytes(b" " * (_MAX_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "Source map exceeds 1000000 bytes"):
            self.read_map()

    def test_invalid_utf8_still_raises(self):
        self.path.write_bytes(b'{"version":1,"sources":{}}\xff')
        with self.assertRaises(UnicodeDecodeError):
            self.read_map()

    def test_stream_read_is_bounded_and_closed_on_error(self):
        class RecordedStream(io.BytesIO):
            def __init__(self, data):
                super().__init__(data)
                self.sizes = []

            def read(self, size=-1):
                self.sizes.append(size)
                return super().read(size)

        stream = RecordedStream(b" " * (_MAX_BYTES + 20))
        with patch.object(Path, "open", return_value=stream) as opened:
            with self.assertRaisesRegex(ValueError, "Source map exceeds 1000000 bytes"):
                self.read_map()
        opened.assert_called_once_with("rb")
        self.assertEqual(stream.sizes, [_MAX_BYTES + 1])
        self.assertTrue(stream.closed)


if __name__ == "__main__":
    unittest.main()
