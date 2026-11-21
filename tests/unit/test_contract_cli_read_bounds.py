"""The offline contract reader must bound actual bytes before decoding."""

import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from searcharis import contract_cli


class ContractCLIReadBoundsTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "input.html"

    def test_small_utf8_and_empty_inputs(self):
        for text in ("", "hello", "Živjo 🌍"):
            with self.subTest(text=text):
                self.path.write_bytes(text.encode("utf-8"))
                self.assertEqual(contract_cli._read(self.path), text)

    def test_universal_newlines_are_preserved(self):
        self.path.write_bytes(b"first\r\nsecond\rthird\n")
        self.assertEqual(contract_cli._read(self.path), "first\nsecond\nthird\n")

    def test_exact_byte_limit_is_accepted(self):
        for character in ("a", "é", "🌍"):
            with self.subTest(character=character):
                text = character * (4_000_000 // len(character.encode("utf-8")))
                self.path.write_bytes(text.encode("utf-8"))
                self.assertEqual(contract_cli._read(self.path), text)

    def test_oversized_ascii_and_multibyte_inputs_are_rejected(self):
        for data in (b"a" * 4_000_001, "é".encode() * 2_000_001):
            with self.subTest(size=len(data)):
                self.path.write_bytes(data)
                with self.assertRaisesRegex(ValueError, "File exceeds 4000000 bytes"):
                    contract_cli._read(self.path)

    def test_invalid_utf8_below_limit_still_raises_decode_error(self):
        self.path.write_bytes(b"hello\xff")
        with self.assertRaises(UnicodeDecodeError):
            contract_cli._read(self.path)

    def test_stale_small_stat_cannot_allow_oversized_input(self):
        # Invalid UTF-8 at the boundary also proves size is checked before decoding.
        for suffix in (b"a", b"\xff", "é".encode()):
            with self.subTest(suffix=suffix):
                self.path.write_bytes(b"a" * 4_000_000 + suffix)
                with (
                    patch.object(Path, "stat", return_value=SimpleNamespace(st_size=1)),
                    self.assertRaisesRegex(ValueError, "File exceeds 4000000 bytes"),
                ):
                    contract_cli._read(self.path)

    def test_stale_stat_read_is_bounded_and_stream_is_closed(self):
        stream = io.BytesIO(b"a" * 4_000_100)
        with (
            patch.object(Path, "stat", return_value=SimpleNamespace(st_size=1)),
            patch.object(Path, "open", return_value=stream) as open_file,
            patch.object(stream, "read", wraps=stream.read) as read,
            self.assertRaisesRegex(ValueError, "File exceeds 4000000 bytes"),
        ):
            contract_cli._read(self.path)
        open_file.assert_called_once_with("rb")
        read.assert_called_once_with(4_000_001)
        self.assertTrue(stream.closed)


if __name__ == "__main__":
    unittest.main()
