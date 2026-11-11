import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

path = Path(__file__).resolve().parents[1] / 'micropython/tools/update_schedule.py'


def load_uploader():
    transport = types.SimpleNamespace(
        PORT='test-port', BAUD=115200, serial=Mock(), _enter_raw=Mock(), _exec=Mock())
    transport.serial.Serial.return_value.__enter__ = Mock(return_value=Mock())
    transport.serial.Serial.return_value.__exit__ = Mock(return_value=False)
    spec = importlib.util.spec_from_file_location('update_test', path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'espctl': transport}):
        spec.loader.exec_module(module)
    return module, transport


class UploaderTests(unittest.TestCase):
    def test_uploads_only_scheduler_and_bot_then_resets(self):
        module, transport = load_uploader()
        with patch.object(sys, 'argv', ['update_schedule.py', '--port', 'test-port']), \
             patch('builtins.print'):
            module.main()
        calls = transport._exec.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertIn('Motor(config).stop()', calls[0].args[1])
        self.assertIn('schedule.py.tmp', calls[1].args[1])
        self.assertIn('bot.py.tmp', calls[2].args[1])
        self.assertNotIn('config.json', calls[1].args[1] + calls[2].args[1])
        serial = transport.serial.Serial.return_value.__enter__.return_value
        self.assertIn(b'import machine; machine.reset()\r\n',
                      [call.args[0] for call in serial.write.call_args_list])

    def test_dependency_failure_prevents_file_upload_and_reset(self):
        module, transport = load_uploader()
        transport._exec.side_effect = RuntimeError('dependency unavailable')
        with patch.object(sys, 'argv', ['update_schedule.py']), self.assertRaises(RuntimeError):
            module.main()
        self.assertEqual(transport._exec.call_count, 1)
        serial = transport.serial.Serial.return_value.__enter__.return_value
        serial.write.assert_not_called()
