# Daily wall-clock schedule, stored separately from WiFi/Telegram secrets.
import json
import os
import time

DEFAULT_TIMES = ['06:00', '13:00']


def parse_times(values):
    if not values or len(values) > 12:
        raise ValueError('Use 1-12 times: /schedule 06:00 13:00')
    result = []
    for value in values:
        if (not isinstance(value, str) or len(value) != 5 or value[2] != ':'
                or not value[:2].isdigit() or not value[3:].isdigit()
                or int(value[:2]) > 23 or int(value[3:]) > 59):
            raise ValueError('Invalid time; use HH:MM (00:00-23:59).')
        if value not in result:
            result.append(value)
    return sorted(result)


class FeedSchedule:
    def __init__(self, path='schedule.json', log=print):
        self.path = path
        self.log = log
        self.times = DEFAULT_TIMES[:]
        self.enabled = True
        self.last = ''
        self.valid = True
        self.synced = False
        self._sync_at = None
        try:
            with open(path) as f:
                data = json.load(f)
            times = parse_times(data['times'])
            if type(data['enabled']) is not bool or not isinstance(data['last'], str):
                raise ValueError('Invalid saved schedule')
            last = data['last']
            if last and (len(last) != 12 or not last.isdigit()):
                raise ValueError('Invalid saved meal marker')
            self.times, self.enabled, self.last = times, data['enabled'], last
        except OSError as e:
            if e.args[0] != 2:  # only a missing file means first boot
                self.valid = False
                self.log('schedule load error:', e)
        except (ValueError, KeyError, TypeError) as e:
            self.valid = False
            self.log('schedule load error:', e)

    def _save(self, times, enabled, last):
        with open(self.path + '.tmp', 'w') as f:
            json.dump({'times': times, 'enabled': enabled, 'last': last}, f)
            f.flush()
        os.rename(self.path + '.tmp', self.path)
        self.times, self.enabled, self.last = times, enabled, last
        self.valid = True

    def configure(self, args):
        if args == ['off']:
            self._save(self.times, False, self.last)
        elif args == ['on']:
            if not self.valid:
                raise ValueError('Saved schedule invalid; set times explicitly first.')
            self._save(self.times, True, self.last)
        else:
            self._save(parse_times(args), True, self.last)

    def sync_clock(self):
        now = time.ticks_ms()
        interval = 21600000 if self.synced else 60000
        if self._sync_at is not None and time.ticks_diff(now, self._sync_at) < interval:
            return
        self._sync_at = now
        try:
            import ntptime
            ntptime.settime()  # UTC RTC; independent of the port's epoch year
            self.synced = time.gmtime()[0] >= 2024
        except Exception as e:
            self.log('clock sync error:', e)

    def claim_due(self):
        if not self.valid or not self.enabled or not self.synced:
            return False
        local = time.gmtime(time.time() - 5 * 3600)  # Ecuador mainland, UTC-5
        hhmm = '%02d:%02d' % (local[3], local[4])
        if hhmm not in self.times:
            return False  # no catch-up outside the scheduled minute
        key = '%04d%02d%02d%02d%02d' % local[:5]
        if key <= self.last:
            return False  # also protects against clock rollback and reboot
        # Claim BEFORE running the motor: never retry an interrupted meal.
        self._save(self.times, self.enabled, key)
        return True

    def describe(self):
        state = 'ON' if self.enabled else 'OFF'
        if not self.valid:
            state = 'ERROR (set times to repair saved schedule)'
        return ('Schedule: %s\nDaily: %s\nTimezone: Ecuador UTC-5\nClock: %s'
                % (state, ', '.join(self.times),
                   'synced' if self.synced else 'waiting for NTP'))
