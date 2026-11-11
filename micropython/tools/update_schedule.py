#!/usr/bin/env python3
"""Install scheduling on an already-configured feeder without erasing secrets."""
import argparse
import base64
from pathlib import Path
import sys

import espctl


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default=espctl.PORT)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sources = {name: (root / name).read_bytes() for name in ('schedule.py', 'bot.py')}
    with espctl.serial.Serial(args.port, espctl.BAUD, timeout=0.2) as serial:
        espctl._enter_raw(serial)
        # Stop first and install the clock dependency before changing the bot.
        espctl._exec(serial, '''import config
from motor import Motor
Motor(config).stop()
try:
    import ntptime
except ImportError:
    import wifi, mip
    wifi.connect(config)
    mip.install('ntptime')
    import ntptime
print('Clock dependency ready')
''', stream=True, timeout=120)
        for name, source in sources.items():
            encoded = base64.b64encode(source).decode('ascii')
            code = '''import ubinascii, os
_data = ubinascii.a2b_base64(%r)
compile(_data, %r, 'exec')
with open(%r, 'wb') as _f:
    assert _f.write(_data) == len(_data)
os.rename(%r, %r)
del _data
print('Installed', %r)
''' % (encoded, name, name + '.tmp', name + '.tmp', name, name)
            espctl._exec(serial, code, stream=True)
        # Reset only after both files were successfully copied.
        serial.write(b'\r\x02')
        serial.write(b'import machine; machine.reset()\r\n')
    print('Update uploaded. Check /schedule in Telegram after the board reconnects.')
    print('Defaults on first use: 06:00 and 13:00 daily, Ecuador UTC-5.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Update incomplete:', error, file=sys.stderr)
        print('Reconnect and rerun the uploader before relying on schedules.', file=sys.stderr)
        raise SystemExit(1)
