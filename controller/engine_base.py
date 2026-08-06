"""Platform-neutral engine core for SpaceMouse WASD.

Owns fly-mode state, bound-key state, and the UDP motion-streaming loop.
Platform backends (backend_win / backend_mac) subclass BaseEngine and
implement input capture, cursor hiding, and foreground-app detection.
"""

import json
import socket
import threading
import time

UDP_PORT = 42737          # must match the add-in
SEND_HZ = 90              # motion packet rate
SPEED_MIN, SPEED_MAX = 0.15, 5.0


class BaseEngine:
    def __init__(self, cfg, binds):
        self.binds = dict(binds)
        self.fly_button = cfg['fly_button']   # 1 = back side, 2 = forward side
        self.trigger_type = cfg.get('trigger_type', 'button')  # or 'combo'
        combo = cfg.get('combo') or {}
        self.combo_code = combo.get('code')
        self.combo_mods = list(combo.get('mods', []))
        self.speed = cfg['speed']
        self.speed_dirty = False              # set when wheel changes speed
        self.scroll_mode = cfg.get('scroll_mode', 'speed')   # or 'zoom'
        self._wheel = 0.0                     # zoom notches since last packet
        self.fly = False
        self.last_ack = 0.0
        self.error = None
        self._bound = set(self.binds.values())
        self._down = {}
        self._accum = [0.0, 0.0]
        self._lock = threading.Lock()
        self._running = False
        self._last_fly_end = 0.0

    # -- overridden by platform backends --
    def _platform_start(self):
        raise NotImplementedError

    def _platform_stop(self):
        pass

    def _fly_began(self):
        """Hide/capture the cursor."""

    def _fly_ended(self):
        """Restore the cursor."""

    def _tick_fly(self):
        """Called each sender tick while flying; collect mouse deltas into
        self._accum if the platform polls rather than hooks them."""

    def fusion_foreground(self):
        raise NotImplementedError

    # -- lifecycle --
    def start(self):
        self._platform_start()
        if self.error:
            return
        self._running = True
        threading.Thread(target=self._sender, daemon=True).start()

    def stop(self):
        self._running = False
        if self.fly:
            self._stop_fly()
        self._platform_stop()

    def set_binds(self, binds):
        with self._lock:
            self.binds = dict(binds)
            self._bound = set(binds.values())
            self._down.clear()

    def adjust_speed(self, factor):
        with self._lock:
            self.speed = min(max(self.speed * factor, SPEED_MIN), SPEED_MAX)
            self.speed_dirty = True

    def wheel_input(self, notches):
        """Scroll wheel during fly: speed multiplier or zoom impulse,
        depending on the configured scroll mode."""
        if self.scroll_mode == 'zoom':
            with self._lock:
                self._wheel += notches
        else:
            self.adjust_speed(1.15 ** notches)

    # -- fly mode --
    def _start_fly(self):
        with self._lock:
            self.fly = True
            self._down.clear()
            self._accum[0] = self._accum[1] = 0.0
        self._fly_began()

    def _stop_fly(self):
        with self._lock:
            self.fly = False
            self._down.clear()
            self._accum[0] = self._accum[1] = 0.0
            self._last_fly_end = time.monotonic()
        self._fly_ended()

    # -- motion streaming --
    def _sender(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Windows: stop ConnectionReset on ICMP port-unreachable
            sock.ioctl(socket.SIO_UDP_CONNRESET, False)
        except (AttributeError, OSError):
            pass
        sock.setblocking(False)
        addr = ('127.0.0.1', UDP_PORT)
        interval = 1.0 / SEND_HZ
        last_ping = 0.0
        while self._running:
            time.sleep(interval)
            now = time.monotonic()

            if now - last_ping > 1.0:
                last_ping = now
                try:
                    sock.sendto(b'{"ping":1}', addr)
                except OSError:
                    pass
            try:
                while True:
                    sock.recvfrom(64)
                    self.last_ack = time.monotonic()
            except OSError:
                pass

            if self.fly and not self.fusion_foreground():
                self._stop_fly()

            if self.fly:
                self._tick_fly()

            with self._lock:
                active = self.fly or now - self._last_fly_end < 1.5
                if not active:
                    continue
                dx, dy = self._accum
                self._accum[0] = self._accum[1] = 0.0
                wz = self._wheel
                self._wheel = 0.0
                b, d = self.binds, self._down
                tx = ((1.0 if d.get(b['pan_right']) else 0.0) -
                      (1.0 if d.get(b['pan_left']) else 0.0))
                ty = ((1.0 if d.get(b['pan_up']) else 0.0) -
                      (1.0 if d.get(b['pan_down']) else 0.0))
                tz = ((1.0 if d.get(b['zoom_in']) else 0.0) -
                      (1.0 if d.get(b['zoom_out']) else 0.0))
                sp = self.speed

            pkt = {'tx': tx, 'ty': ty, 'tz': tz,
                   'rx': dx * SEND_HZ, 'ry': dy * SEND_HZ,   # px/sec rates
                   'wz': wz,                                 # wheel zoom notches
                   'sp': sp, 'boost': False}
            try:
                sock.sendto(json.dumps(pkt).encode('utf-8'), addr)
            except OSError:
                pass
