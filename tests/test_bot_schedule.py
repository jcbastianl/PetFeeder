import importlib
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'micropython'))
with patch.dict(sys.modules, {
    'machine': types.SimpleNamespace(),
    'network': types.SimpleNamespace(),
    'requests': types.SimpleNamespace(),
    'led': types.SimpleNamespace(READY=1, FEEDING=2, ERROR=3),
}):
    bot_module = importlib.import_module('bot')
    TelegramBot = bot_module.TelegramBot


class BotScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(os.chdir, self.old_cwd)
        self.ticks = patch.object(bot_module.time, 'ticks_ms', create=True, return_value=100000)
        self.ticks.start()
        self.addCleanup(self.ticks.stop)
        diff = patch.object(bot_module.time, 'ticks_diff', create=True, side_effect=lambda a, b: a-b)
        diff.start()
        self.addCleanup(diff.stop)
        self.motor = Mock(state='stop')
        self.motor.run_feed_cycle.return_value = True
        cfg = types.SimpleNamespace(TELEGRAM_TOKEN='test', TELEGRAM_ALLOWED_CHATIDS=[123])
        self.bot = TelegramBot(cfg, self.motor, log=lambda *a: None)
        self.bot.send = Mock()

    def command(self, text, chat=123):
        self.bot.handle({'update_id': 1, 'message': {'chat': {'id': chat}, 'text': text}})

    def test_authorized_change_and_read_survive_reload(self):
        self.command('/schedule@FeederBot 08:00 17:30')
        self.command('/schedule')
        self.assertIn('08:00, 17:30', self.bot.send.call_args.args[1])
        from schedule import FeedSchedule
        self.assertEqual(FeedSchedule().times, ['08:00', '17:30'])
        self.motor.run_feed_cycle.assert_not_called()

    def test_unauthorized_change_is_rejected(self):
        self.command('/schedule off', chat=999)
        self.assertTrue(self.bot.schedule.enabled)
        self.assertFalse(Path('schedule.json').exists())
        self.assertIn('Not authorized', self.bot.send.call_args.args[1])

    def test_invalid_command_preserves_settings(self):
        self.command('/schedule 24:00')
        self.assertEqual(self.bot.schedule.times, ['06:00', '13:00'])
        self.assertIn('not changed', self.bot.send.call_args.args[1])

    def test_automatic_cycle_uses_existing_motor_and_stop_callback(self):
        self.bot.schedule.sync_clock = Mock()
        self.bot.schedule.synced = True
        with patch('schedule.time.time', return_value=1794394800):  # 2026-11-11 11:00 UTC
            self.bot._check_schedule()
            self.bot._check_schedule()
        self.motor.run_feed_cycle.assert_called_once_with(ok=self.bot._feed_ok)
        self.assertFalse(self.bot._feeding)

    def test_storage_error_never_runs_motor(self):
        self.bot.schedule.sync_clock = Mock()
        self.bot.schedule.claim_due = Mock(side_effect=OSError('flash failure'))
        self.bot._check_schedule()
        self.motor.run_feed_cycle.assert_not_called()

    def test_cooldown_still_applies_to_automatic_meals(self):
        self.bot._last_feed = 99999
        self.bot._do_feed(123, automatic=True)
        self.motor.run_feed_cycle.assert_not_called()
        self.assertIn('Cooldown', self.bot.send.call_args.args[1])

    def test_stop_callback_aborts_automatic_feed(self):
        self.bot.get_updates = Mock(return_value=[{
            'update_id': 2, 'message': {'chat': {'id': 123}, 'text': '/stop'}
        }])
        self.motor.run_feed_cycle.side_effect = lambda ok: ok()
        self.bot._do_feed(123, automatic=True)
        self.assertTrue(self.bot._abort)
        self.assertEqual(self.bot.send.call_args.args[1], 'Feed stopped.')


if __name__ == '__main__':
    unittest.main()
