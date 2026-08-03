"""Windows backend for SpaceMouse WASD.

Low-level keyboard/mouse hooks (pure ctypes, no dependencies), a fullscreen
alpha=1 overlay with an invisible cursor for cursor hiding, and poll-based
mouse capture. Hooks must be installed from the thread that runs the Tk
mainloop - it pumps their messages.
"""

import ctypes
import ctypes.wintypes as w
import threading
import time

from engine_base import BaseEngine, SPEED_MIN, SPEED_MAX

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

WINDOW_MATCH = ('Autodesk Fusion', 'Fusion 360')

DEFAULT_BINDS = {'pan_up': 0x57, 'pan_down': 0x53,        # W / S
                 'pan_left': 0x41, 'pan_right': 0x44,     # A / D
                 'zoom_in': 0x10, 'zoom_out': 0x11}       # Shift / Ctrl
DEFAULT_COMBO = {'code': 0x4C, 'mods': ['ctrl', 'alt']}   # Ctrl+Alt+L
FLY_BUTTON_LABELS = {2: 'Forward side', 1: 'Back side'}
SUPPORTS_AUTOSTART = True

MOD_VKS = {'shift': 0x10, 'ctrl': 0x11, 'alt': 0x12}


def held_mods():
    """Modifier keys physically (or synthetically) held right now."""
    return [m for m, vk in MOD_VKS.items()
            if user32.GetAsyncKeyState(vk) & 0x8000]


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


def tk_event_to_code(event):
    """Translate a Tk <KeyPress> event into a bindable key code.
    Returns (code, is_escape) - code None if the key can't be bound."""
    vk = normalize_vk(event.keycode)
    return vk, vk == VK_ESCAPE


class Engine(BaseEngine):
    def __init__(self, cfg):
        super().__init__(cfg, cfg['binds'])
        self._anchor = w.POINT(0, 0)
        self._hk = self._hm = None
        self._overlay = None
        self._refs = []               # keep ctypes callbacks alive

    # -- lifecycle --
    def _platform_start(self):
        self._create_overlay()
        kb = HOOKPROC(self._kb_hook)
        ms = HOOKPROC(self._ms_hook)
        self._refs += [kb, ms]
        self._hk = user32.SetWindowsHookExW(WH_KEYBOARD_LL, kb, None, 0)
        self._hm = user32.SetWindowsHookExW(WH_MOUSE_LL, ms, None, 0)
        if not self._hk or not self._hm:
            self.error = ('Failed to install input hooks '
                          '(error %d)' % ctypes.get_last_error())

    def _platform_stop(self):
        for h in (self._hk, self._hm):
            if h:
                user32.UnhookWindowsHookEx(h)
        self._hk = self._hm = None

    def fusion_foreground(self):
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        title = buf.value
        return any(m in title for m in WINDOW_MATCH)

    # -- fly transitions --
    def _fly_began(self):
        self._clamp_anchor()
        self._show_overlay()

    def _fly_ended(self):
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

    def _tick_fly(self):
        # FPS-style capture by polling: cursor drift from the anchor since
        # last tick IS the mouse delta; snap it back after. (Reading deltas
        # in the LL mouse hook proved unreliable - see repo history.)
        cur = w.POINT()
        user32.GetCursorPos(ctypes.byref(cur))
        ddx = cur.x - self._anchor.x
        ddy = cur.y - self._anchor.y
        if ddx or ddy:
            with self._lock:
                self._accum[0] += ddx
                self._accum[1] += ddy
            user32.SetCursorPos(self._anchor.x, self._anchor.y)

    # -- hooks --
    def _kb_hook(self, nCode, wParam, lParam):
        try:
            if nCode == 0 and (self.fly or self.trigger_type == 'combo'):
                ks = ctypes.cast(lParam,
                                 ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                vk = normalize_vk(ks.vkCode)

                # Key-combo fly trigger. Injected events are deliberately
                # accepted on this path: mouse drivers (Logitech Options+
                # etc.) remap side buttons to *synthetic* keystrokes, which
                # Windows flags LLKHF_INJECTED - filtering them out locks
                # those users out entirely (GitHub issue #1, thanks TMTYD).
                if self.trigger_type == 'combo' and vk == self.combo_code:
                    if down:
                        if self.fly:
                            return 1              # swallow auto-repeat
                        if (self._mods_held() and self.fusion_foreground()):
                            self._start_fly()
                            return 1
                        # combo not satisfied: fall through so the bare key
                        # still types normally in Fusion
                    elif self.fly:
                        # Stop on trigger-key release *regardless* of
                        # modifier state - requiring the modifiers here
                        # leaves fly mode stuck on if they're released
                        # before the trigger key.
                        self._stop_fly()
                        return 1

                if self.fly and not (ks.flags & LLKHF_INJECTED):
                    if down:
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

    def _mods_held(self):
        return all(user32.GetAsyncKeyState(MOD_VKS[m]) & 0x8000
                   for m in self.combo_mods)

    def _ms_hook(self, nCode, wParam, lParam):
        try:
            # Fast-path: bail before any ctypes work unless this is an event
            # we might act on. Mouse *moves* are by far the most frequent
            # message and are never handled here - keeping them cheap keeps
            # system-wide input latency down.
            if (nCode == 0 and
                    (wParam == WM_XBUTTONDOWN or wParam == WM_XBUTTONUP or
                     (self.fly and wParam == WM_MOUSEWHEEL))):
                ms = ctypes.cast(lParam,
                                 ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if not (ms.flags & LLMHF_INJECTED):
                    if wParam == WM_XBUTTONDOWN:
                        if (self.trigger_type == 'button' and
                                ((ms.mouseData >> 16) & 0xFFFF) ==
                                self.fly_button):
                            if self.fly:
                                return 1
                            if self.fusion_foreground():
                                self._start_fly()
                                return 1
                    elif wParam == WM_XBUTTONUP:
                        if (self.trigger_type == 'button' and
                                ((ms.mouseData >> 16) & 0xFFFF) ==
                                self.fly_button and self.fly):
                            self._stop_fly()
                            return 1
                    elif wParam == WM_MOUSEWHEEL and self.fly:
                        delta = ctypes.c_short(
                            (ms.mouseData >> 16) & 0xFFFF).value
                        self.adjust_speed(1.15 ** (delta / 120.0))
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
