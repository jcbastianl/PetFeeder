import calendar
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'micropython'))
from schedule import FeedSchedule, parse_times


def utc(hour, minute=0, day=11):
    return calendar.timegm((2026, 11, day, hour, minute, 0))


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / 'schedule.json')
        self.schedule = FeedSchedule(self.path, log=lambda *a: None)

    def due(self, stamp):
        with patch('schedule.time.time', return_value=stamp):
            return self.schedule.claim_due()

    def test_defaults_require_sync_and_use_ecuador_time(self):
        self.assertFalse(self.due(utc(11)))
        self.schedule.synced = True
        self.assertFalse(self.due(utc(10, 59)))
        self.assertTrue(self.due(utc(11)))
        self.assertFalse(self.due(utc(11)))
        self.assertTrue(self.due(utc(18)))
        self.assertFalse(self.due(utc(18)))

    def test_restart_and_clock_rollback_do_not_repeat_meals(self):
        self.schedule.synced = True
        self.assertTrue(self.due(utc(18)))
        self.schedule = FeedSchedule(self.path)
        self.schedule.synced = True
        self.assertFalse(self.due(utc(18)))
        self.assertFalse(self.due(utc(11)))
        self.assertTrue(self.due(utc(11, day=12)))

    def test_missed_minute_is_not_replayed(self):
        self.schedule.synced = True
        self.assertFalse(self.due(utc(11, 1)))
        self.assertTrue(self.due(utc(18)))

    def test_settings_survive_reboot_and_off_on_preserves_claim(self):
        self.schedule.synced = True
        self.assertTrue(self.due(utc(11)))
        self.schedule.configure(['off'])
        self.assertFalse(self.due(utc(18)))
        self.schedule.configure(['on'])
        self.assertFalse(self.due(utc(11)))
        self.schedule.configure(['20:00', '00:00', '20:00'])
        loaded = FeedSchedule(self.path)
        self.assertEqual(loaded.times, ['00:00', '20:00'])
        self.assertTrue(loaded.enabled)
        self.schedule = loaded
        self.schedule.synced = True
        self.assertTrue(self.due(utc(1, day=12)))  # Nov 11 20:00 Ecuador

    def test_invalid_input_does_not_change_saved_settings(self):
        self.schedule.configure(['07:15'])
        for values in ([], ['24:00'], ['06:60'], ['6:00'], ['ab:cd'], ['on', 'off'], ['01:00'] * 13):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.schedule.configure(values)
        self.assertEqual(FeedSchedule(self.path).times, ['07:15'])

    def test_corrupt_state_disables_automatic_feeding(self):
        Path(self.path).write_text('{broken')
        self.schedule = FeedSchedule(self.path, log=lambda *a: None)
        self.schedule.synced = True
        self.assertFalse(self.due(utc(11)))
        with self.assertRaises(ValueError):
            self.schedule.configure(['on'])
        self.schedule.configure(['06:00'])
        self.assertTrue(self.due(utc(11)))

    def test_write_failure_prevents_claim(self):
        self.schedule.synced = True
        with patch('schedule.os.rename', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                self.due(utc(11))
        self.assertEqual(self.schedule.last, '')
        self.assertTrue(self.due(utc(11)))

    def test_ntp_retry_and_periodic_refresh(self):
        ntp = types.SimpleNamespace(settime=unittest.mock.Mock(side_effect=OSError('offline')))
        with patch.dict(sys.modules, {'ntptime': ntp}), \
             patch('schedule.time.ticks_ms', create=True, return_value=0) as ticks, \
             patch('schedule.time.ticks_diff', create=True, side_effect=lambda a, b: a-b):
            self.schedule.sync_clock()
            self.assertFalse(self.schedule.synced)
            ticks.return_value = 59000
            self.schedule.sync_clock()
            self.assertEqual(ntp.settime.call_count, 1)
            ticks.return_value = 60000
            ntp.settime.side_effect = None
            self.schedule.sync_clock()
            self.assertTrue(self.schedule.synced)
            ticks.return_value += 21600000
            ntp.settime.side_effect = OSError('offline')
            self.schedule.sync_clock()
            self.assertTrue(self.schedule.synced)  # RTC continues offline
            self.assertEqual(ntp.settime.call_count, 3)


if __name__ == '__main__':
    unittest.main()
