import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

path = Path(__file__).resolve().parents[1] / 'micropython/tools/espctl.py'
spec = importlib.util.spec_from_file_location('espctl_test', path)
espctl = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'serial': types.SimpleNamespace()}):
    spec.loader.exec_module(espctl)


class TransportTests(unittest.TestCase):
    def test_complete_reply(self):
        serial = Mock()
        serial.read.return_value = b'OKdone\r\n\x04\x04>'
        self.assertEqual(espctl._exec(serial, 'pass'), b'done\r\n')

    def test_partial_reply_is_not_upload_success(self):
        serial = Mock()
        serial.read.return_value = b'OKpartial'
        with patch.object(espctl.time, 'time', side_effect=[0, 0, 61]):
            with self.assertRaises(TimeoutError):
                espctl._exec(serial, 'pass')

    def test_board_exception_is_reported(self):
        serial = Mock()
        serial.read.return_value = b'OK\x04OSError: disk full\x04>'
        with self.assertRaisesRegex(RuntimeError, 'disk full'):
            espctl._exec(serial, 'pass')
