#!/usr/bin/env python3
"""Minimal, reset-tolerant driver for a CH340 ESP32 running MicroPython.

mpremote's raw-REPL handshake races the CH340 auto-reset on port open and
fails with "could not enter raw repl". This waits for boot, enters raw REPL
deliberately, and can put files or run a script with live-streamed output.

Usage:
    espctl.py put   <local> <remote>
    espctl.py run   <local_script>
"""
import os, sys, time, base64, serial

PORT = os.environ.get("ESP_PORT", "/dev/cu.usbserial-0001")
BAUD = 115200


def _read_until(s, marker, timeout, echo=False):
    buf = bytearray()
    end = time.time() + timeout
    while time.time() < end:
        chunk = s.read(256)
        if chunk:
            buf.extend(chunk)
            if echo:
                sys.stdout.write(chunk.decode("utf-8", "replace"))
                sys.stdout.flush()
            if marker in buf:
                return bytes(buf)
    raise TimeoutError("timed out waiting for %r; got: %r" % (marker, bytes(buf[-120:])))


def _enter_raw(s):
    # Board resets when the port opens; let it finish booting.
    time.sleep(1.5)
    s.reset_input_buffer()
    for _ in range(3):
        s.write(b"\r\x03\x03")   # Ctrl-C twice: interrupt anything running
        time.sleep(0.2)
        s.reset_input_buffer()
        s.write(b"\r\x01")       # Ctrl-A: enter raw REPL
        try:
            _read_until(s, b"raw REPL; CTRL-B to exit\r\n>", 3)
            return
        except TimeoutError:
            time.sleep(0.5)
    raise RuntimeError("could not enter raw REPL")


def _exec(s, code, stream=False, timeout=60):
    # Raw-REPL reply after Ctrl-D: b"OK" <stdout> \x04 <stderr> \x04 ">"
    s.write(code.encode("utf-8") + b"\x04")   # Ctrl-D: run
    buf = bytearray()
    echoed = 0
    started = False
    end = time.time() + timeout
    while time.time() < end:
        chunk = s.read(256)
        if chunk:
            buf.extend(chunk)
            if stream:
                if not started:
                    i = buf.find(b"OK")
                    if i != -1:
                        started, echoed = True, i + 2
                if started:
                    first04 = buf.find(b"\x04", echoed)
                    limit = first04 if first04 != -1 else len(buf)
                    if limit > echoed:
                        sys.stdout.write(buf[echoed:limit].decode("utf-8", "replace"))
                        sys.stdout.flush()
                        echoed = limit
        if buf.count(b"\x04") >= 2:
            break
    if buf.count(b"\x04") < 2:
        raise TimeoutError("board execution did not finish before timeout")
    if b"OK" not in buf:
        raise RuntimeError("no OK from board; got: %r" % bytes(buf[:160]))
    body = buf[buf.find(b"OK") + 2:]
    parts = body.split(b"\x04")
    out = parts[0]
    err_txt = (parts[1] if len(parts) > 1 else b"").decode("utf-8", "replace").strip()
    if err_txt:
        raise RuntimeError("board raised:\n" + err_txt)
    return out


def main():
    cmd = sys.argv[1]
    # "reset" just pulses the port open/closed so the board reboots and runs
    # boot.py/main.py on its own -- no raw REPL, no Ctrl-C to interrupt it.
    if cmd == "reset":
        s = serial.Serial(PORT, BAUD, timeout=0.2)
        time.sleep(0.5)
        s.close()
        print("reset: board rebooted")
        return

    s = serial.Serial(PORT, BAUD, timeout=0.2)
    try:
        _enter_raw(s)
        if cmd == "put":
            local, remote = sys.argv[2], sys.argv[3]
            with open(local, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            code = (
                "import ubinascii\n"
                "with open(%r,'wb') as _f: _f.write(ubinascii.a2b_base64(%r))\n"
                "print('wrote', %r)" % (remote, b64, remote)
            )
            print(_exec(s, code).decode("utf-8", "replace").strip())
        elif cmd == "run":
            with open(sys.argv[2]) as f:
                code = f.read()
            timeout = int(os.environ.get("ESP_RUN_TIMEOUT", "60"))
            _exec(s, code, stream=True, timeout=timeout)
        else:
            raise SystemExit("unknown command: " + cmd)
        s.write(b"\r\x02")  # Ctrl-B: back to friendly REPL
    finally:
        s.close()


if __name__ == "__main__":
    main()
