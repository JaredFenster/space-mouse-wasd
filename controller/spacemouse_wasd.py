#!/usr/bin/env python3
"""SpaceMouse WASD - spacemouse-style navigation for Autodesk Fusion.

Desktop controller app. Hold a side mouse button while Fusion is focused to
enter fly mode: the mouse orbits (cursor hidden), bindable keys pan and zoom,
and motion is streamed over localhost UDP to the companion Fusion add-in,
which drives the camera with velocity smoothing.

Pure standard library (ctypes + tkinter). Windows only.
"""

import ctypes
import ctypes.wintypes as w
import json
import os
import socket
import sys
import threading
import time
import tkinter as tk

APP_NAME = 'SpaceMouse WASD'
VERSION = '1.0.0'
UDP_PORT = 42737          # must match the add-in
SEND_HZ = 90              # motion packet rate
SPEED_MIN, SPEED_MAX = 0.15, 5.0
WINDOW_MATCH = ('Autodesk Fusion', 'Fusion 360')
CONFIG_DIR = os.path.join(os.environ.get('APPDATA', '.'), 'SpaceMouseWASD')
CONFIG_PATH = os.path.join(CONFIG_DIR, 'config.json')
ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         '..', 'assets')

ACTIONS = [
    ('pan_up', 'Pan up'),
    ('pan_down', 'Pan down'),
    ('pan_left', 'Pan left'),
    ('pan_right', 'Pan right'),
    ('zoom_in', 'Zoom in'),
    ('zoom_out', 'Zoom out'),
]
DEFAULT_CONFIG = {
    'binds': {'pan_up': 0x57, 'pan_down': 0x53,        # W / S
              'pan_left': 0x41, 'pan_right': 0x44,     # A / D
              'zoom_in': 0x10, 'zoom_out': 0x11},      # Shift / Ctrl
    'fly_button': 2,      # 2 = forward side button, 1 = back side button
    'speed': 1.0,
}

# ---------------------------------------------------------------- win32 ----
user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)

WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
WM_MOUSEWHEEL = 0x020A
WM_XBUTTONDOWN, WM_XBUTTONUP = 0x020B, 0x020C
VK_ESCAPE = 0x1B
LLKHF_INJECTED, LLMHF_INJECTED = 0x10, 0x01

WS_POPUP = 0x80000000
WS_EX_TOPMOST, WS_EX_TOOLWINDOW = 0x0008, 0x0080
WS_EX_NOACTIVATE, WS_EX_LAYERED = 0x08000000, 0x00080000
LWA_ALPHA = 0x2
SW_HIDE = 0
SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x0010, 0x0040
HWND_TOPMOST = -1
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
BLACK_BRUSH = 4

ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [('vkCode', w.DWORD), ('scanCode', w.DWORD),
                ('flags', w.DWORD), ('time', w.DWORD),
                ('dwExtraInfo', ULONG_PTR)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [('pt', w.POINT), ('mouseData', w.DWORD),
                ('flags', w.DWORD), ('time', w.DWORD),
                ('dwExtraInfo', ULONG_PTR)]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, w.WPARAM, w.LPARAM)
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

user32.SetWindowsHookExW.restype = w.HHOOK
user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, w.HINSTANCE,
                                     w.DWORD)
user32.CallNextHookEx.restype = LRESULT
user32.CallNextHookEx.argtypes = (w.HHOOK, ctypes.c_int, w.WPARAM, w.LPARAM)
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = (w.HWND, w.UINT, w.WPARAM, w.LPARAM)
user32.CreateWindowExW.restype = w.HWND
user32.CreateWindowExW.argtypes = (w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE,
                                   w.LPVOID)
user32.CreateCursor.restype = w.HANDLE
user32.CreateCursor.argtypes = (w.HINSTANCE, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int,
                                ctypes.c_void_p, ctypes.c_void_p)
user32.SetWindowPos.argtypes = (w.HWND, w.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, w.UINT)
user32.ShowWindow.argtypes = (w.HWND, ctypes.c_int)
user32.SetLayeredWindowAttributes.argtypes = (w.HWND, w.DWORD,
                                              ctypes.c_ubyte, w.DWORD)
kernel32.GetModuleHandleW.restype = w.HMODULE


class WNDCLASSW(ctypes.Structure):
    _fields_ = [('style', w.UINT), ('lpfnWndProc', WNDPROC),
                ('cbClsExtra', ctypes.c_int), ('cbWndExtra', ctypes.c_int),
                ('hInstance', w.HINSTANCE), ('hIcon', w.HANDLE),
                ('hCursor', w.HANDLE), ('hbrBackground', w.HANDLE),
                ('lpszMenuName', w.LPCWSTR), ('lpszClassName', w.LPCWSTR)]


def fusion_foreground():
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    title = buf.value
    return any(m in title for m in WINDOW_MATCH)


def normalize_vk(vk):
    """Collapse the LL hook's side-specific modifier codes to generic ones."""
    if vk in (0xA0, 0xA1):
        return 0x10   # Shift
    if vk in (0xA2, 0xA3):
        return 0x11   # Ctrl
    if vk in (0xA4, 0xA5):
        return 0x12   # Alt
    return vk


_EXTENDED_VKS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,
                 0x2D, 0x2E, 0x5B, 0x5C}
_VK_NAMES = {0x10: 'Shift', 0x11: 'Ctrl', 0x12: 'Alt', 0x14: 'CapsLock',
             0x20: 'Space', 0x09: 'Tab', 0x0D: 'Enter', 0x08: 'Backspace'}


def key_name(vk):
    if vk in _VK_NAMES:
        return _VK_NAMES[vk]
    sc = user32.MapVirtualKeyW(vk, 0)
    lp = sc << 16
    if vk in _EXTENDED_VKS:
        lp |= 1 << 24
    buf = ctypes.create_unicode_buffer(64)
    if user32.GetKeyNameTextW(lp, buf, 64):
        return buf.value
    return 'VK 0x%02X' % vk


# --------------------------------------------------------------- config ----
def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))   # deep copy
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            saved = json.load(f)
        for k, v in saved.get('binds', {}).items():
            if k in cfg['binds'] and isinstance(v, int):
                cfg['binds'][k] = v
        if saved.get('fly_button') in (1, 2):
            cfg['fly_button'] = saved['fly_button']
        sp = saved.get('speed')
        if isinstance(sp, (int, float)):
            cfg['speed'] = min(max(float(sp), SPEED_MIN), SPEED_MAX)
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass


# --------------------------------------------------------------- engine ----
class Engine:
    """Input capture + motion streaming. Hooks must be installed from the
    thread that runs the Tk mainloop (it pumps their messages)."""

    def __init__(self, cfg):
        self.binds = dict(cfg['binds'])
        self.fly_button = cfg['fly_button']
        self.speed = cfg['speed']
        self.speed_dirty = False      # set when wheel changes speed mid-fly
        self.fly = False
        self.last_ack = 0.0
        self.error = None
        self._bound = set(self.binds.values())
        self._down = {}
        self._accum = [0.0, 0.0]
        self._anchor = w.POINT(0, 0)
        self._last_fly_end = 0.0
        self._lock = threading.Lock()
        self._running = False
        self._hk = self._hm = None
        self._overlay = None
        self._refs = []               # keep ctypes callbacks alive

    # -- lifecycle --
    def start(self):
        self._create_overlay()
        kb = HOOKPROC(self._kb_hook)
        ms = HOOKPROC(self._ms_hook)
        self._refs += [kb, ms]
        self._hk = user32.SetWindowsHookExW(WH_KEYBOARD_LL, kb, None, 0)
        self._hm = user32.SetWindowsHookExW(WH_MOUSE_LL, ms, None, 0)
        if not self._hk or not self._hm:
            self.error = ('Failed to install input hooks '
                          '(error %d)' % ctypes.get_last_error())
            return
        self._running = True
        threading.Thread(target=self._sender, daemon=True).start()

    def stop(self):
        self._running = False
        if self.fly:
            self._stop_fly()
        for h in (self._hk, self._hm):
            if h:
                user32.UnhookWindowsHookEx(h)
        self._hk = self._hm = None

    def set_binds(self, binds):
        with self._lock:
            self.binds = dict(binds)
            self._bound = set(binds.values())
            self._down.clear()

    # -- fly mode --
    def _start_fly(self):
        with self._lock:
            self.fly = True
            self._down.clear()
            self._accum[0] = self._accum[1] = 0.0
        self._clamp_anchor()
        self._show_overlay()

    def _stop_fly(self):
        with self._lock:
            self.fly = False
            self._down.clear()
            self._accum[0] = self._accum[1] = 0.0
            self._last_fly_end = time.monotonic()
        self._hide_overlay()

    def _clamp_anchor(self):
        """Keep the anchor away from screen edges so raw deltas don't clip."""
        pt = w.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        sw = user32.GetSystemMetrics(0)
        sh = user32.GetSystemMetrics(1)
        nx = min(max(pt.x, 60), sw - 60)
        ny = min(max(pt.y, 60), sh - 60)
        if (nx, ny) != (pt.x, pt.y):
            user32.SetCursorPos(nx, ny)
            user32.GetCursorPos(ctypes.byref(pt))
        self._anchor = pt

    # -- hooks --
    def _kb_hook(self, nCode, wParam, lParam):
        try:
            if nCode == 0 and self.fly:
                ks = ctypes.cast(lParam,
                                 ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if not (ks.flags & LLKHF_INJECTED):
                    vk = normalize_vk(ks.vkCode)
                    if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                        if vk in self._bound:
                            with self._lock:
                                self._down[vk] = True
                            return 1
                        if vk == VK_ESCAPE:
                            self._stop_fly()
                            return 1
                    elif vk in self._bound:
                        with self._lock:
                            self._down[vk] = False
                        # Deliberately NOT blocked: a key-up can't trigger
                        # anything in Fusion, and swallowing it leaves keys
                        # (especially Shift/Ctrl) stuck held system-wide if
                        # they were pressed before fly mode began.
        except Exception:
            pass
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _ms_hook(self, nCode, wParam, lParam):
        try:
            # Fast-path: bail before any ctypes work unless this is an event
            # we might act on. Mouse *moves* are by far the most frequent
            # message and are never handled here (the sender loop polls
            # cursor drift instead) - keeping them cheap keeps system-wide
            # input latency down.
            if (nCode == 0 and
                    (wParam == WM_XBUTTONDOWN or wParam == WM_XBUTTONUP or
                     (self.fly and wParam == WM_MOUSEWHEEL))):
                ms = ctypes.cast(lParam,
                                 ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if not (ms.flags & LLMHF_INJECTED):
                    if wParam == WM_XBUTTONDOWN:
                        if ((ms.mouseData >> 16) & 0xFFFF) == self.fly_button:
                            if self.fly:
                                return 1
                            if fusion_foreground():
                                self._start_fly()
                                return 1
                    elif wParam == WM_XBUTTONUP:
                        if (((ms.mouseData >> 16) & 0xFFFF) == self.fly_button
                                and self.fly):
                            self._stop_fly()
                            return 1
                    elif wParam == WM_MOUSEWHEEL and self.fly:
                        delta = ctypes.c_short(
                            (ms.mouseData >> 16) & 0xFFFF).value
                        with self._lock:
                            self.speed = min(
                                max(self.speed * (1.15 ** (delta / 120.0)),
                                    SPEED_MIN), SPEED_MAX)
                            self.speed_dirty = True
                        return 1
        except Exception:
            pass
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    # -- cursor-hiding overlay --
    def _create_overlay(self):
        """Fullscreen, imperceptible (alpha=1), topmost, non-activating window
        with an invisible cursor. Shown only during fly mode: the pointer sits
        on it, so the cursor disappears without touching system cursors."""
        and_mask = (ctypes.c_ubyte * 128)(*([0xFF] * 128))
        xor_mask = (ctypes.c_ubyte * 128)(*([0x00] * 128))
        hinst = kernel32.GetModuleHandleW(None)
        invis = user32.CreateCursor(hinst, 0, 0, 32, 32, and_mask, xor_mask)

        proc = WNDPROC(lambda h, m, wp, lp: user32.DefWindowProcW(h, m, wp, lp))
        self._refs.append(proc)

        wc = WNDCLASSW()
        wc.lpfnWndProc = proc
        wc.hInstance = hinst
        wc.hCursor = invis
        wc.hbrBackground = gdi32.GetStockObject(BLACK_BRUSH)
        wc.lpszClassName = 'SpaceMouseWASDOverlay'
        if not user32.RegisterClassW(ctypes.byref(wc)):
            return
        self._overlay = user32.CreateWindowExW(
            WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED,
            'SpaceMouseWASDOverlay', '', WS_POPUP, 0, 0, 10, 10,
            None, None, hinst, None)
        if self._overlay:
            user32.SetLayeredWindowAttributes(self._overlay, 0, 1, LWA_ALPHA)

    def _show_overlay(self):
        if self._overlay:
            user32.SetWindowPos(
                self._overlay, HWND_TOPMOST,
                user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
                user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
                user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
                user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
                SWP_NOACTIVATE | SWP_SHOWWINDOW)

    def _hide_overlay(self):
        if self._overlay:
            user32.ShowWindow(self._overlay, SW_HIDE)

    # -- motion streaming --
    def _sender(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # stop Windows raising ConnectionReset on ICMP port-unreachable
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

            if self.fly and not fusion_foreground():
                self._stop_fly()

            if self.fly:
                # FPS-style capture by polling: cursor drift from the anchor
                # since last tick IS the mouse delta; snap it back after.
                cur = w.POINT()
                user32.GetCursorPos(ctypes.byref(cur))
                ddx = cur.x - self._anchor.x
                ddy = cur.y - self._anchor.y
                if ddx or ddy:
                    with self._lock:
                        self._accum[0] += ddx
                        self._accum[1] += ddy
                    user32.SetCursorPos(self._anchor.x, self._anchor.y)

            with self._lock:
                active = (self.fly or
                          now - self._last_fly_end < 1.5)
                if not active:
                    continue
                dx, dy = self._accum
                self._accum[0] = self._accum[1] = 0.0
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
                   'sp': sp, 'boost': False}
            try:
                sock.sendto(json.dumps(pkt).encode('utf-8'), addr)
            except OSError:
                pass


# ------------------------------------------------------------------- ui ----
BG = '#10131a'
CARD = '#181d26'
FIELD = '#222938'
FIELD_HI = '#2b3344'
ACCENT = '#38e1c8'
TEXT = '#e8ecf1'
DIM = '#8a93a3'
OK_GREEN = '#48d17c'
WAIT_AMBER = '#f0b45a'

FONT = ('Segoe UI', 10)
FONT_SM = ('Segoe UI', 9)
FONT_KEY = ('Consolas', 10, 'bold')
FONT_TITLE = ('Segoe UI Semibold', 17)


def draw_logo(cv, size):
    """Draw the orbit-ring logo on a square canvas."""
    s = size / 256.0
    def sc(*vals):
        return [v * s for v in vals]
    cv.create_oval(*sc(44, 44, 212, 212), outline='#2ec9b4',
                   width=max(10 * s, 2))
    cv.create_oval(*sc(98, 98, 158, 158), fill=ACCENT, outline='')
    chev = max(10 * s, 2)
    for pts in ((114, 72, 128, 54, 142, 72), (114, 184, 128, 202, 142, 184),
                (72, 114, 54, 128, 72, 142), (184, 114, 202, 128, 184, 142)):
        cv.create_line(*sc(*pts), fill=TEXT, width=chev,
                       capstyle='round', joinstyle='round')


class App:
    def __init__(self, root, engine, cfg):
        self.root = root
        self.engine = engine
        self.cfg = cfg
        self.capturing = None
        self.bind_buttons = {}
        self._save_job = None

        root.title(APP_NAME)
        root.configure(bg=BG)
        root.resizable(False, False)
        try:
            icon = tk.PhotoImage(file=os.path.join(ASSET_DIR, 'icon.png'))
            root.iconphoto(True, icon)
            self._icon = icon
        except tk.TclError:
            pass

        pad = 18

        # header
        head = tk.Frame(root, bg=BG)
        head.pack(fill='x', padx=pad, pady=(pad, 10))
        logo = tk.Canvas(head, width=56, height=56, bg=BG,
                         highlightthickness=0)
        logo.pack(side='left')
        draw_logo(logo, 56)
        tbox = tk.Frame(head, bg=BG)
        tbox.pack(side='left', padx=(14, 0))
        trow = tk.Frame(tbox, bg=BG)
        trow.pack(anchor='w')
        tk.Label(trow, text='SpaceMouse ', font=FONT_TITLE, fg=TEXT,
                 bg=BG).pack(side='left')
        tk.Label(trow, text='WASD', font=FONT_TITLE, fg=ACCENT,
                 bg=BG).pack(side='left')
        tk.Label(tbox, text='Fly-through navigation for Fusion',
                 font=FONT_SM, fg=DIM, bg=BG).pack(anchor='w')

        # status card
        card = self._card(pad)
        self.fly_dot, self.fly_val = self._status_row(card, 'Fly mode')
        self.link_dot, self.link_val = self._status_row(card, 'Fusion add-in')

        # bindings card
        bcard = self._card(pad)
        tk.Label(bcard, text='KEY BINDINGS', font=('Segoe UI', 8, 'bold'),
                 fg=DIM, bg=CARD).grid(row=0, column=0, columnspan=2,
                                       sticky='w', padx=14, pady=(12, 6))
        for i, (action, label) in enumerate(ACTIONS, start=1):
            tk.Label(bcard, text=label, font=FONT, fg=TEXT, bg=CARD,
                     anchor='w').grid(row=i, column=0, sticky='w',
                                      padx=(14, 20), pady=3)
            btn = tk.Button(
                bcard, text=key_name(cfg['binds'][action]), font=FONT_KEY,
                fg=TEXT, bg=FIELD, activebackground=FIELD_HI,
                activeforeground=TEXT, relief='flat', bd=0, width=14,
                cursor='hand2', pady=3,
                command=lambda a=action: self.begin_capture(a))
            btn.grid(row=i, column=1, sticky='e', padx=(0, 14), pady=3)
            self.bind_buttons[action] = btn
        bcard.grid_columnconfigure(0, weight=1)

        r = len(ACTIONS) + 1
        tk.Label(bcard, text='Fly button', font=FONT, fg=TEXT, bg=CARD,
                 anchor='w').grid(row=r, column=0, sticky='w',
                                  padx=(14, 20), pady=(10, 3))
        self.btn_var = tk.StringVar(
            value='Forward side' if cfg['fly_button'] == 2 else 'Back side')
        om = tk.OptionMenu(bcard, self.btn_var, 'Forward side', 'Back side',
                           command=self.on_fly_button)
        om.configure(font=FONT, fg=TEXT, bg=FIELD, activebackground=FIELD_HI,
                     activeforeground=TEXT, relief='flat', bd=0, width=12,
                     highlightthickness=0, cursor='hand2')
        om['menu'].configure(font=FONT, fg=TEXT, bg=FIELD,
                             activebackground=FIELD_HI,
                             activeforeground=TEXT, bd=0)
        om.grid(row=r, column=1, sticky='e', padx=(0, 14), pady=(10, 3))

        r += 1
        srow = tk.Frame(bcard, bg=CARD)
        srow.grid(row=r, column=0, columnspan=2, sticky='ew',
                  padx=14, pady=(6, 12))
        tk.Label(srow, text='Speed', font=FONT, fg=TEXT,
                 bg=CARD).pack(side='left')
        self.speed_lbl = tk.Label(srow, text='x%.2f' % cfg['speed'],
                                  font=FONT_KEY, fg=ACCENT, bg=CARD, width=6)
        self.speed_lbl.pack(side='right')
        self.speed_var = tk.DoubleVar(value=cfg['speed'])
        sc = tk.Scale(srow, variable=self.speed_var, from_=SPEED_MIN,
                      to=SPEED_MAX, resolution=0.05, orient='horizontal',
                      showvalue=0, bg=CARD, fg=TEXT, troughcolor=FIELD,
                      highlightthickness=0, bd=0, sliderrelief='flat',
                      activebackground=ACCENT, command=self.on_speed)
        sc.pack(side='left', fill='x', expand=True, padx=12)

        # footer
        foot = tk.Frame(root, bg=BG)
        foot.pack(fill='x', padx=pad, pady=(4, pad - 4))
        self.hint = tk.Label(foot, font=FONT_SM, fg=DIM, bg=BG,
                             justify='left')
        self.hint.pack(anchor='w')
        self._update_hint()
        brow = tk.Frame(foot, bg=BG)
        brow.pack(fill='x', pady=(8, 0))
        self.auto_var = tk.BooleanVar(value=os.path.exists(startup_path()))
        tk.Checkbutton(brow, text='Launch at Windows startup',
                       variable=self.auto_var, command=self.on_autostart,
                       font=FONT_SM, fg=DIM, bg=BG, activebackground=BG,
                       activeforeground=TEXT, selectcolor=FIELD,
                       highlightthickness=0, cursor='hand2').pack(side='left')
        reset = tk.Label(brow, text='Reset defaults', font=FONT_SM,
                         fg=DIM, bg=BG, cursor='hand2')
        reset.pack(side='right')
        reset.bind('<Button-1>', self.on_reset)
        tk.Label(brow, text='v' + VERSION + '  ', font=FONT_SM, fg='#3d4452',
                 bg=BG).pack(side='right')

        if engine.error:
            tk.Label(root, text=engine.error, font=FONT_SM, fg='#f56565',
                     bg=BG, wraplength=360).pack(padx=pad, pady=(0, pad))

        root.protocol('WM_DELETE_WINDOW', self.close)
        self.tick()

    # -- ui helpers --
    def _card(self, pad):
        c = tk.Frame(self.root, bg=CARD)
        c.pack(fill='x', padx=pad, pady=7)
        return c

    def _status_row(self, card, label):
        row = tk.Frame(card, bg=CARD)
        row.pack(fill='x', padx=14, pady=6)
        dot = tk.Canvas(row, width=10, height=10, bg=CARD,
                        highlightthickness=0)
        dot.pack(side='left', pady=2)
        dot_id = dot.create_oval(1, 1, 9, 9, fill=DIM, outline='')
        tk.Label(row, text=label, font=FONT, fg=TEXT,
                 bg=CARD).pack(side='left', padx=(8, 0))
        val = tk.Label(row, text='...', font=FONT, fg=DIM, bg=CARD)
        val.pack(side='right')
        return (dot, dot_id), val

    def _update_hint(self):
        b = 'FORWARD' if self.engine.fly_button == 2 else 'BACK'
        self.hint.config(text='Hold the %s side mouse button in Fusion to '
                              'fly.  Scroll = speed, Esc = bail out.' % b)

    # -- callbacks --
    def begin_capture(self, action):
        if self.capturing:
            self.end_capture()
        self.capturing = action
        self.bind_buttons[action].config(text='press a key...', fg=ACCENT)
        self.root.bind('<KeyPress>', self.on_capture_key)

    def on_capture_key(self, event):
        action = self.capturing
        if action is None:
            return 'break'
        vk = normalize_vk(event.keycode)
        if vk != VK_ESCAPE:               # Esc cancels (it's the bail key)
            binds = self.cfg['binds']
            for other, other_vk in binds.items():
                if other != action and other_vk == vk:
                    binds[other] = binds[action]   # swap to avoid duplicates
            binds[action] = vk
            self.engine.set_binds(binds)
            save_config(self.cfg)
        self.end_capture()
        return 'break'

    def end_capture(self):
        self.root.unbind('<KeyPress>')
        self.capturing = None
        for action, btn in self.bind_buttons.items():
            btn.config(text=key_name(self.cfg['binds'][action]), fg=TEXT)

    def on_fly_button(self, choice):
        self.cfg['fly_button'] = 2 if choice == 'Forward side' else 1
        self.engine.fly_button = self.cfg['fly_button']
        save_config(self.cfg)
        self._update_hint()

    def on_speed(self, val):
        v = float(val)
        self.engine.speed = v
        self.cfg['speed'] = v
        self.speed_lbl.config(text='x%.2f' % v)
        if self._save_job:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(
            800, lambda: save_config(self.cfg))

    def on_reset(self, _event=None):
        self.cfg['binds'] = dict(DEFAULT_CONFIG['binds'])
        self.cfg['fly_button'] = DEFAULT_CONFIG['fly_button']
        self.cfg['speed'] = DEFAULT_CONFIG['speed']
        self.engine.set_binds(self.cfg['binds'])
        self.engine.fly_button = self.cfg['fly_button']
        self.engine.speed = self.cfg['speed']
        self.speed_var.set(self.cfg['speed'])
        self.btn_var.set('Forward side')
        save_config(self.cfg)
        self.end_capture()
        self._update_hint()

    def on_autostart(self):
        path = startup_path()
        try:
            if self.auto_var.get():
                script = os.path.abspath(__file__)
                with open(path, 'w', encoding='ascii') as f:
                    f.write('@echo off\nstart "" pythonw "%s"\n' % script)
            elif os.path.exists(path):
                os.remove(path)
        except OSError:
            self.auto_var.set(os.path.exists(path))

    def tick(self):
        e = self.engine
        (dot, dot_id), val = self.fly_dot, self.fly_val
        if e.fly:
            dot.itemconfig(dot_id, fill=ACCENT)
            val.config(text='Flying', fg=ACCENT)
        else:
            dot.itemconfig(dot_id, fill='#3d4452')
            val.config(text='Idle', fg=DIM)
        (dot, dot_id), val = self.link_dot, self.link_val
        if time.monotonic() - e.last_ack < 3.0:
            dot.itemconfig(dot_id, fill=OK_GREEN)
            val.config(text='Connected', fg=OK_GREEN)
        else:
            dot.itemconfig(dot_id, fill=WAIT_AMBER)
            val.config(text='Waiting...', fg=WAIT_AMBER)
        if e.speed_dirty:                 # wheel changed speed mid-fly
            e.speed_dirty = False
            self.speed_var.set(e.speed)
            self.cfg['speed'] = e.speed
            self.speed_lbl.config(text='x%.2f' % e.speed)
            save_config(self.cfg)
        self.root.after(200, self.tick)

    def close(self):
        save_config(self.cfg)
        self.engine.stop()
        self.root.destroy()


def startup_path():
    return os.path.join(os.environ.get('APPDATA', '.'),
                        r'Microsoft\Windows\Start Menu\Programs\Startup',
                        'SpaceMouseWASD.bat')


def main():
    selftest = '--selftest' in sys.argv

    # Single instance: two copies mean two sets of hooks fighting over the
    # cursor and double-blocking keys - a reliable source of input glitches.
    kernel32.CreateMutexW.restype = w.HANDLE
    kernel32.CreateMutexW.argtypes = (w.LPVOID, w.BOOL, w.LPCWSTR)
    kernel32.CreateMutexW(None, False, 'SpaceMouseWASD_SingleInstance')
    if ctypes.get_last_error() == 183:      # ERROR_ALREADY_EXISTS
        root = tk.Tk()
        root.withdraw()
        from tkinter import messagebox
        messagebox.showinfo(APP_NAME, APP_NAME + ' is already running.\n'
                            'Check the taskbar or system tray.')
        return

    cfg = load_config()
    root = tk.Tk()
    engine = Engine(cfg)
    engine.start()
    app = App(root, engine, cfg)
    if selftest:
        def finish():
            print('SELFTEST OK')
            app.close()
        root.after(1500, finish)
    root.mainloop()


if __name__ == '__main__':
    main()
