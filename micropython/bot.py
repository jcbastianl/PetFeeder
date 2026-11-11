# bot.py -- Telegram bot that long-polls getUpdates and drives the feeder.
#
# Commands:
#   /start, /help  greeting + command list + your chat id
#   /feed          run one feed cycle (guarded by a cooldown + busy check)
#   /schedule      show/set daily HH:MM times, or on/off (Ecuador UTC-5)
#   /stop          emergency stop: stop the motor / abort an in-progress feed
#   /status        motor state, uptime, WiFi, last feed, cooldown
#   /ping          quick liveness check -> "pong"
#   /id            reply with your chat id (use it to fill allowed_chat_ids)
#
# Access: if config allowed_chat_ids is non-empty, only those ids may /feed or
# /stop or change schedules. If it is empty the bot runs OPEN (anyone who finds it can feed) + warns.
#
# Anti-flood: on boot AND after any network outage the bot RESYNCS -- it discards
# whatever piled up on Telegram's side instead of feeding once per queued /feed.

import time
import gc
import machine
import network
import requests

from led import READY, FEEDING, ERROR
from schedule import FeedSchedule

_API = "https://api.telegram.org/bot%s/%s"
_LONG_POLL = 20        # seconds Telegram holds a getUpdates open
_STOP_CHECK_MS = 700   # min gap between network polls while feeding


class TelegramBot:
    def __init__(self, cfg, motor, led=None, log=print):
        self.cfg = cfg
        self.motor = motor
        self.led = led
        self.token = cfg.TELEGRAM_TOKEN
        self.allowed = set(cfg.TELEGRAM_ALLOWED_CHATIDS or [])
        self.log = log
        self.offset = 0
        self.schedule = FeedSchedule(log=log)

        self.cooldown_ms = int(getattr(cfg, "FEED_COOLDOWN_S", 30)) * 1000
        self._boot = time.ticks_ms()
        self._last_feed = None
        self._feeding = False
        self._abort = False
        self._abort_chat = None
        self._last_stopcheck = 0

        if not self.allowed:
            self.log("WARNING: allowed_chat_ids empty -> bot is OPEN to anyone")

    # -- Telegram transport ---------------------------------------------------
    def _url(self, method):
        return _API % (self.token, method)

    def send(self, chat_id, text):
        if chat_id is None:
            self.log(text)
            return
        gc.collect()
        try:
            r = requests.post(self._url("sendMessage"),
                              json={"chat_id": chat_id, "text": text})
            r.close()
        except Exception as e:  # never let a send failure kill the poll loop
            self.log("send error:", e)

    def get_updates(self, timeout):
        gc.collect()
        url = self._url("getUpdates") + "?timeout=%d&offset=%d" % (timeout, self.offset)
        r = requests.get(url)
        try:
            result = r.json().get("result", [])
        finally:
            r.close()
        return result

    # -- helpers --------------------------------------------------------------
    def _set_led(self, mode):
        if self.led is not None:
            self.led.set(mode)

    def _authorized(self, chat_id):
        return not self.allowed or chat_id in self.allowed

    @staticmethod
    def _cmd_of(msg):
        text = (msg.get("text") or "").strip()
        if not text:
            return ""
        cmd = text.split()[0].lower()
        return cmd.split("@", 1)[0]        # strip /feed@BotName in groups

    def _cooldown_left(self):
        if self._last_feed is None:
            return 0
        left = self.cooldown_ms - time.ticks_diff(time.ticks_ms(), self._last_feed)
        return (left // 1000 + 1) if left > 0 else 0

    def _wifi_ip(self):
        try:
            return network.WLAN(network.STA_IF).ifconfig()[0]
        except Exception:
            return "?"

    # -- anti-flood: drop whatever queued up while we were away ---------------
    def resync(self):
        """Advance past all pending updates WITHOUT handling them, so commands
        that piled up while offline don't all fire at once."""
        total = 0
        while True:
            updates = self.get_updates(0)
            if not updates:
                break
            self.offset = updates[-1]["update_id"] + 1
            total += len(updates)
        if total:
            self.log("resync: dropped", total, "backlogged update(s)")

    # -- mid-feed stop polling ------------------------------------------------
    def _feed_ok(self):
        """Passed to run_feed_cycle; checked often but only hits the network
        every ~_STOP_CHECK_MS. Returns False once an authorized /stop arrives."""
        now = time.ticks_ms()
        if time.ticks_diff(now, self._last_stopcheck) >= _STOP_CHECK_MS:
            self._last_stopcheck = now
            try:
                for u in self.get_updates(0):
                    self.offset = u["update_id"] + 1
                    msg = u.get("message") or u.get("edited_message")
                    if not msg:
                        continue
                    if self._cmd_of(msg) == "/stop" and self._authorized(msg["chat"]["id"]):
                        self._abort = True
                        self._abort_chat = msg["chat"]["id"]
            except Exception as e:
                self.log("stop-check error:", e)
        return not self._abort

    # -- command handling -----------------------------------------------------
    def handle(self, update):
        self.offset = update["update_id"] + 1
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return
        chat_id = msg["chat"]["id"]
        cmd = self._cmd_of(msg)
        self.log("msg from", chat_id, ":", cmd or "(non-text)")

        if cmd in ("/start", "/help"):
            self.send(chat_id,
                      "Pet Feeder\n"
                      "/feed - dispense food\n"
                      "/schedule [HH:MM ... | on | off] - daily meals (UTC-5)\n"
                      "/stop - emergency stop\n"
                      "/status - show status\n"
                      "/ping - liveness check\n"
                      "/reboot - restart the feeder\n"
                      "/id - show your chat id\n\n"
                      "Your chat id: %d" % chat_id)
        elif cmd == "/id":
            self.send(chat_id, "Your chat id: %d" % chat_id)
        elif cmd == "/ping":
            self.send(chat_id, "pong")
        elif cmd == "/status":
            up = time.ticks_diff(time.ticks_ms(), self._boot) // 1000
            last = ("%ds ago" % (time.ticks_diff(time.ticks_ms(), self._last_feed) // 1000)
                    if self._last_feed is not None else "never")
            self.send(chat_id,
                      "Status\n"
                      "motor: %s\n"
                      "feeding: %s\n"
                      "uptime: %ds\n"
                      "wifi: %s\n"
                      "last feed: %s\n"
                      "cooldown left: %ds"
                      % (self.motor.state, self._feeding, up,
                         self._wifi_ip(), last, self._cooldown_left()))
        elif cmd == "/stop":
            if not self._authorized(chat_id):
                self.send(chat_id, "Not authorized (chat id %d)." % chat_id)
                return
            self._abort = True            # aborts a feed if one is mid-cycle
            self.motor.stop()
            self._set_led(READY)
            self.send(chat_id, "Stopped. Motor is off.")
        elif cmd == "/reboot":
            if not self._authorized(chat_id):
                self.send(chat_id, "Not authorized (chat id %d)." % chat_id)
                return
            self.motor.stop()
            self.send(chat_id, "Rebooting...")
            time.sleep(1)
            machine.reset()
        elif cmd == "/schedule":
            if not self._authorized(chat_id):
                self.send(chat_id, "Not authorized (chat id %d)." % chat_id)
                return
            args = (msg.get("text") or "").split()[1:]
            try:
                if args:
                    self.schedule.configure(args)
                self.send(chat_id, self.schedule.describe())
            except (ValueError, OSError) as e:
                self.send(chat_id, "Schedule not changed: %s" % e)
        elif cmd == "/feed":
            self._do_feed(chat_id)
        elif cmd:
            self.send(chat_id, "Unknown command. Try /help.")

    def _do_feed(self, chat_id, automatic=False):
        if not automatic and not self._authorized(chat_id):
            self.send(chat_id, "Not authorized (chat id %d)." % chat_id)
            return
        if self._feeding:
            self.send(chat_id, "Already feeding -- ignoring.")
            return
        left = self._cooldown_left()
        if left > 0:
            self.send(chat_id, "Cooldown: wait %ds before feeding again." % left)
            return

        self._feeding = True
        self._abort = False
        self._abort_chat = None
        self._set_led(FEEDING)
        self.send(chat_id, "Feeding...")
        try:
            completed = self.motor.run_feed_cycle(ok=self._feed_ok)
        except Exception as e:
            self.motor.stop()
            self.send(chat_id, "Feed failed: %s" % e)
            self.log("feed error:", e)
            return
        finally:
            self._feeding = False
            self._last_feed = time.ticks_ms()
            self._set_led(READY)

        if completed:
            self.send(chat_id, "Done!")
        else:
            self.send(self._abort_chat or chat_id, "Feed stopped.")

    def _check_schedule(self):
        try:
            self.schedule.sync_clock()
            if self.schedule.claim_due():
                chat_id = min(self.allowed) if self.allowed else None
                self._do_feed(chat_id, automatic=True)
        except Exception as e:
            self.log("schedule error:", e)

    # -- main loop ------------------------------------------------------------
    def poll_forever(self):
        self.log("bot polling as", self.token.split(":")[0])
        resync_needed = True           # treat startup like a reconnect
        while True:
            self._check_schedule()
            try:
                if resync_needed:
                    self.resync()      # drop backlog from boot / the outage
                    resync_needed = False
                    self._set_led(READY)
                    self.log("synced; older messages ignored")
                for update in self.get_updates(_LONG_POLL):
                    self.handle(update)
            except Exception as e:
                self.log("poll error:", e)
                self._set_led(ERROR)
                resync_needed = True   # on recovery, drop whatever queued up
                time.sleep(3)
