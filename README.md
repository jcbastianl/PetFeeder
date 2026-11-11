# PetFeeder 🐾

A 3D-printed, **ESP32-powered automatic pet feeder** controlled from a **Telegram
bot**. Send `/feed` from your phone and the ESP32 spins an auger to dispense food.

Runs **MicroPython** on the ESP32. The firmware lives in [`micropython/`](micropython/);
this README is the single source of truth for building and running it.

> Fork of [hardrive9000/PetFeeder](https://github.com/hardrive9000/PetFeeder)
> (hardware, 3D parts, schematics). The firmware here is a from-scratch
> MicroPython rewrite with Telegram control, safety guardrails, and a status LED.

## Features

- 📲 **Telegram control** — feed from anywhere: `/feed`, `/stop`, `/status`, …
- 🕒 **Daily meals** — `/schedule 06:00 13:00`, saved on the ESP32 (Ecuador UTC−5)
- 🔒 **Access control** — feeding restricted to an allow-list of chat ids
- 🛡️ **Guardrails** — emergency stop, feed cooldown, busy guard, and anti-flood
  resync (a queue of offline `/feed`s can't dump a pile of food on reconnect)
- 💡 **Status LED** — booting / ready / feeding / error at a glance
- ⚙️ Drives a **continuous-rotation servo** (default) or a **stepper**

## Hardware

- **ESP32 DevKit V1** (WROOM-32) — verified on ESP32-D0WD-V3.
- **Continuous-rotation servo** (MG995 360° / MG996R continuous).
  ⚠️ The auger needs **continuous rotation** — a standard **positional/180° servo
  will not work** (it jerks to fixed angles instead of turning). A stepper is
  also supported (`motor_type: "stepper"`).
- Red **status LED** + resistor (100–330 Ω), a **470 µF** capacitor across the
  servo power, and a **5 V supply capable of ≥1.5–2 A** (the servo alone pulls ~1 A).
- Wiring diagrams in [`Schematic/`](Schematic/) (`pet_feeder_servo_schematic.png`,
  `pet_feeder_stepper_schematic.png`).

### Pin map (`config.json → pins`)

| GPIO | Role |
| ---- | ---- |
| 18 (`D18`) | Servo signal / stepper step (PWM) |
| 5  (`D5`)  | Status LED |
| 21 (`D21`) | Stepper DIR (stepper only) |
| 22 (`D22`) | Stepper EN (stepper only) |
| 19 (`D19`) | Button — defined but **unused** by this firmware |

### 3D-printed parts

In [`3D/`](3D/): `MainBody`, `Hopper`, `LeadScrew_Top` / `LeadScrew_Bottom` (the
auger), `ServoMount`, `ServoShim`, `MotorMount`, `Elbow`.

## Repository layout

```
micropython/  MicroPython firmware (source of truth for the code)
  main.py            entry point (runs at boot): Wi-Fi → motor → LED → bot
  config.py          loads config.json and exposes it as attributes
  config.json.default settings template (copy to config.json)
  wifi.py            Wi-Fi station connect with retry/reconnect
  motor.py           Motor driver: feed / reverse / stop / run_feed_cycle
  schedule.py        persistent daily meals + NTP clock (06:00 / 13:00 defaults)
  bot.py             Telegram bot: long-poll, commands, allow-list, guardrails
  led.py             timer-driven status-LED patterns
  test_motor_feed.py on-device test: motor feeds for 2 s in a row
  tools/espctl.py    host helper: copy files / run scripts / reset the board
3D/           STL files for the printed enclosure and auger
Schematic/    Wiring diagrams (servo and stepper variants)
```

---

# Running it on an ESP32

## 1. Prerequisites (on your computer)

```sh
pip install esptool mpremote            # or: pipx install esptool mpremote
export ESP_PORT=/dev/cu.usbserial-XXXX  # your board's port: ls /dev/cu.usb*
```

## 2. Flash MicroPython (one time — wipes any existing firmware)

```sh
esptool --port "$ESP_PORT" flash-id                 # confirm chip = ESP32
esptool --port "$ESP_PORT" erase-flash
esptool --port "$ESP_PORT" --baud 460800 write-flash -z 0x1000 ESP32_GENERIC-vX.Y.Z.bin
```

Download the matching build from https://micropython.org/download/ESP32_GENERIC/.

## 3. Create a Telegram bot

1. Message [@BotFather](https://t.me/BotFather) → `/newbot`, follow the prompts.
2. Copy the **HTTP API token** it gives you.

## 4. Configure

From the `micropython/` directory:

```sh
cd micropython
cp config.json.default config.json    # then edit config.json
```

Fields to set in `config.json`:

| Field | What to put |
| ----- | ----------- |
| `motor_type` | `"servo"` (default) or `"stepper"` |
| `wifi.ssid` / `wifi.password` | Your 2.4 GHz Wi-Fi network |
| `telegram.token` | The BotFather token |
| `telegram.allowed_chat_ids` | Chat ids allowed to feed (see step 6). Leave `[]` to run **open** — anyone who finds the bot can feed |
| `servo.feed_rate_us` / `feed_stop_us` / `feed_reversal_us` | Continuous-servo pulse widths. If the servo **creeps at rest**, tune `feed_stop_us` (~1500); if it spins the wrong way / too fast, adjust the others |
| `feed.feed_ms` / `reverse_ms` / `cooldown_s` | Cycle timing + cooldown |

`config.json` is **gitignored** (it holds your secrets); `config.json.default`
is the committed template.

## 5. Deploy to the board

`tools/espctl.py` copies files, runs scripts, and resets the board. (It exists
because CH340 boards auto-reset when the serial port opens, which races
`mpremote`'s raw-REPL handshake — `espctl` waits for boot before talking.)

```sh
PY=python3   # any python with pyserial installed

# copy the firmware
for f in config.json config.py wifi.py motor.py schedule.py bot.py led.py main.py; do
  $PY tools/espctl.py put "$f" "$f"
done

# install the requests library the bot needs (Wi-Fi must be set in config.json).
# mpremote's `mip install` can't enter the raw REPL on this CH340 board, so do
# it on-device:
printf 'import wifi, config, mip\nwifi.connect(config)\nmip.install("requests")\nmip.install("ntptime")\n' > /tmp/inst.py
$PY tools/espctl.py run /tmp/inst.py

# reboot so main.py runs the bot
$PY tools/espctl.py reset
```

## 6. Verify

- **LED:** fast blink (connecting) → a brief blip every ~3 s (online & ready).
- In Telegram, message your bot:
  - `/ping` → `pong` (it's alive and online)
  - `/id` → your chat id — add it to `telegram.allowed_chat_ids`, then re-copy
    `config.json` and `reset` to lock feeding to you
  - `/feed` → `Feeding...` → the servo runs the cycle → `Done!`

Once it's running you can power the ESP32 from the 5 V supply and unplug USB —
it reconnects and runs on its own.

## 7. Motor feed test (optional, no Wi-Fi)

A hardware sanity check that the motor feeds for 2 s continuously:

```sh
$PY tools/espctl.py put config.json config.json
$PY tools/espctl.py put config.py   config.py
$PY tools/espctl.py put motor.py    motor.py
$PY tools/espctl.py run test_motor_feed.py
# -> PASS: fed continuously for ~2000 ms ... 0 interruptions
```

---

## Telegram commands

| Command | Action | Access |
| ------- | ------ | ------ |
| `/feed`   | Run one feed cycle (feed 2 s → reverse 0.5 s → feed 2 s → stop) | allow-list |
| `/schedule` | Show daily times and clock sync status (UTC−5) | allow-list |
| `/schedule 06:00 13:00` | Replace and enable daily meal times (1–12, HH:MM) | allow-list |
| `/schedule off` / `/schedule on` | Disable / enable saved daily meals | allow-list |
| `/stop`   | Emergency stop — stops the motor, aborts an in-progress feed | allow-list |
| `/reboot` | Soft-restart the ESP32 | allow-list |
| `/status` | Motor state, uptime, Wi-Fi IP, last feed, cooldown | open |
| `/ping`   | Liveness check → `pong` | open |
| `/id`     | Show your chat id | open |
| `/help`   | Command list | open |

## Automatic meals and updating an existing feeder

The schedule runs **on the ESP32**: no running computer, Telegram user session,
GitHub Actions, paid service, or recurring cost is needed. Updating the firmware
once is required. First-use defaults are **06:00 and 13:00 every day**, Ecuador
mainland time (fixed UTC−5; no daylight saving).

For an already-configured board, connect USB and run from the repository root:

```sh
python3 -m pip install pyserial
python3 micropython/tools/update_schedule.py --port /dev/ttyUSB0
```

Replace the port with the board's actual port (`/dev/ttyACM0` on some Linux
boards, `/dev/cu.usbserial-...` on macOS, or `COM3` on Windows). The uploader
stops the motor, checks/installs `ntptime` over the existing Wi-Fi connection,
then uploads `schedule.py` and `bot.py` and reboots. It **preserves config.json**
(Wi-Fi, token, pins, motor timings) and any existing `schedule.json` settings.
It does not erase flash. Do not interrupt uploading; if it fails, reconnect and
rerun it before using the feeder. Initial installation is still covered above.

After reboot, send `/schedule` to verify `ON`, `06:00, 13:00`, and `Clock: synced`.
To change meals later, send `/schedule 07:30 14:00 20:00`. Times must be 24-hour
`HH:MM`; duplicates are collapsed. `/schedule off` pauses automatic meals and
`/schedule on` resumes saved times. Manual `/feed` remains available.
Settings and the last claimed meal are saved in `schedule.json`; do not delete
this runtime file during routine updates. Rebooting does not reset the schedule.

Clock synchronization uses NTP at boot (retry every minute), then every six
hours. Until the first successful sync after boot, automatic feeding waits.
After syncing, the RTC continues through a network outage while power remains
on. `/schedule` reports whether the clock has ever synced in the current boot.
The existing blocking Telegram poll/network calls can delay checks; a meal only
runs when the loop observes its scheduled minute. There is **no catch-up** for
missed meals after an outage or a long stall. This is not a hard real-time alarm.

Automatic meals use the same feed cycle, cooldown, and `/stop` callback as
manual feeding. The meal is persisted **before** attempting the motor, so a
reset, failed/aborted cycle, or cooldown does not trigger an automatic retry of
that meal. A corrupt schedule or failed state write prevents automatic feeding;
set valid times explicitly with `/schedule HH:MM ...` to repair invalid settings.
`/stop` stops the current cycle; use `/schedule off` to cancel future meals.
Notifications go to the lowest allowed chat ID; in open mode they are logged on
serial. The existing allow-list also controls schedule commands.

Host tests (no hardware and no real Telegram messages):

```sh
python3 -m unittest discover -s tests -v
```

The motor/USB upload still needs verification on the physical ESP32 after this
change; host tests simulate the clock, Telegram transport, and motor.

## Status LED (GPIO5)

| Pattern | State |
| ------- | ----- |
| Fast blink (~5 Hz)       | Booting / connecting to Wi-Fi |
| Blip every ~3 s          | Connected & idle — ready (heartbeat) |
| Solid on                 | Feeding |
| Very fast blink (~10 Hz) | Connection lost / error |

## `espctl` reference

| Command | Does |
| ------- | ---- |
| `espctl.py put <local> <remote>` | Copy a file to the board's filesystem |
| `espctl.py run <script>`         | Run a local script on the board, streaming output |
| `espctl.py reset`                | Reboot the board (so `main.py` runs), no REPL interrupt |

Env: `ESP_PORT` selects the serial port; `ESP_RUN_TIMEOUT` (seconds) extends the
`run` timeout.

## Credits & license

Fork of [hardrive9000/PetFeeder](https://github.com/hardrive9000/PetFeeder).
Licensed under the **Apache License 2.0** — see [`LICENSE`](LICENSE).
