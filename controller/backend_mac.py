"""macOS backend for SpaceMouse WASD.  (BETA - see README.)

Uses a Quartz CGEventTap for global input capture, which requires pyobjc:

    pip3 install pyobjc-framework-Quartz pyobjc-framework-Cocoa

macOS will also ask you to grant the terminal/Python **Accessibility** and
**Input Monitoring** permission (System Settings > Privacy & Security) the
first time - the tap cannot be created without it.

Cursor capture is native here: CGAssociateMouseAndMouseCursorPosition(False)
freezes the cursor while mouse-moved events keep delivering raw deltas, and
CGDisplayHideCursor hides it. Much cleaner than the Windows overlay trick.
"""

import threading
import time

from engine_base import BaseEngine

try:
    import Quartz
    from AppKit import NSWorkspace
except ImportError:
    Quartz = None
    NSWorkspace = None

APP_MATCH = ('Fusion',)          # frontmost app name must contain one

# macOS ANSI virtual keycodes
KC_ESCAPE = 53
KC_NAMES = {
    0: 'A', 1: 'S', 2: 'D', 3: 'F', 4: 'H', 5: 'G', 6: 'Z', 7: 'X', 8: 'C',
    9: 'V', 11: 'B', 12: 'Q', 13: 'W', 14: 'E', 15: 'R', 16: 'Y', 17: 'T',
    18: '1', 19: '2', 20: '3', 21: '4', 22: '6', 23: '5', 24: '=', 25: '9',
    26: '7', 27: '-', 28: '8', 29: '0', 30: ']', 31: 'O', 32: 'U', 33: '[',
    34: 'I', 35: 'P', 37: 'L', 38: 'J', 39: "'", 40: 'K', 41: ';', 42: '\\',
    43: ',', 44: '/', 45: 'N', 46: 'M', 47: '.', 50: '`',
    36: 'Return', 48: 'Tab', 49: 'Space', 51: 'Delete',
    56: 'Shift', 57: 'CapsLock', 58: 'Option', 59: 'Control',
    123: 'Left', 124: 'Right', 125: 'Down', 126: 'Up',
}
# Tk keysym -> mac keycode, for rebinding in the UI
KEYSYM_TO_KC = {name.lower(): kc for kc, name in KC_NAMES.items()
                if len(name) == 1}
KEYSYM_TO_KC.update({
    'shift_l': 56, 'shift_r': 56, 'control_l': 59, 'control_r': 59,
    'alt_l': 58, 'alt_r': 58, 'option_l': 58, 'option_r': 58,
    'space': 49, 'tab': 48, 'return': 36, 'backspace': 51,
    'left': 123, 'right': 124, 'down': 125, 'up': 126,
    'apostrophe': 39, 'semicolon': 41, 'comma': 43, 'period': 47,
    'slash': 44, 'backslash': 42, 'grave': 50, 'minus': 27, 'equal': 24,
    'bracketleft': 33, 'bracketright': 30,
})

DEFAULT_BINDS = {'pan_up': 13, 'pan_down': 1,          # W / S
                 'pan_left': 0, 'pan_right': 2,        # A / D
                 'zoom_in': 56, 'zoom_out': 59}        # Shift / Control
# CGEvent button numbers: 3 = back side button, 4 = forward side button
FLY_BUTTON_LABELS = {2: 'Forward side', 1: 'Back side'}
_BUTTON_NUM = {2: 4, 1: 3}
SUPPORTS_AUTOSTART = False

# right-hand modifier keycodes fold onto the left-hand ones
_NORMALIZE = {60: 56, 62: 59, 61: 58}
_MOD_FLAG = None
if Quartz:
    _MOD_FLAG = {56: Quartz.kCGEventFlagMaskShift,
                 59: Quartz.kCGEventFlagMaskControl,
                 58: Quartz.kCGEventFlagMaskAlternate,
                 57: Quartz.kCGEventFlagMaskAlphaShift}


def normalize_kc(kc):
    return _NORMALIZE.get(kc, kc)


def key_name(kc):
    return KC_NAMES.get(kc, 'Key %d' % kc)


def tk_event_to_code(event):
    """Translate a Tk <KeyPress> event into a bindable key code.
    Returns (code, is_escape) - code None if the key can't be bound."""
    sym = event.keysym.lower()
    if sym == 'escape':
        return None, True
    return KEYSYM_TO_KC.get(sym), False


class Engine(BaseEngine):
    def __init__(self, cfg):
        super().__init__(cfg, cfg['binds'])
        self._tap = None
        self._tap_loop = None

    # -- lifecycle --
    def _platform_start(self):
        if Quartz is None:
            self.error = ('macOS support needs pyobjc:\n'
                          'pip3 install pyobjc-framework-Quartz '
                          'pyobjc-framework-Cocoa')
            return
        started = threading.Event()
        threading.Thread(target=self._tap_thread, args=(started,),
                         daemon=True).start()
        started.wait(3.0)
        if self._tap is None and not self.error:
            self.error = ('Could not create the event tap. Grant this app '
                          'Accessibility and Input Monitoring permission in '
                          'System Settings > Privacy & Security, then '
                          'relaunch.')

    def _tap_thread(self, started):
        mask = (Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown) |
                Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp) |
                Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged) |
                Quartz.CGEventMaskBit(Quartz.kCGEventOtherMouseDown) |
                Quartz.CGEventMaskBit(Quartz.kCGEventOtherMouseUp) |
                Quartz.CGEventMaskBit(Quartz.kCGEventScrollWheel) |
                Quartz.CGEventMaskBit(Quartz.kCGEventMouseMoved))
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault, mask, self._on_event, None)
        started.set()
        if not self._tap:
            return
        source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        self._tap_loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(self._tap_loop, source,
                                  Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)
        Quartz.CFRunLoopRun()

    def _platform_stop(self):
        if self._tap:
            Quartz.CGEventTapEnable(self._tap, False)
        if self._tap_loop:
            Quartz.CFRunLoopStop(self._tap_loop)

    def fusion_foreground(self):
        try:
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            name = app.localizedName() or ''
            return any(m in name for m in APP_MATCH)
        except Exception:
            return False

    # -- fly transitions --
    def _fly_began(self):
        Quartz.CGAssociateMouseAndMouseCursorPosition(False)
        Quartz.CGDisplayHideCursor(Quartz.CGMainDisplayID())

    def _fly_ended(self):
        Quartz.CGAssociateMouseAndMouseCursorPosition(True)
        Quartz.CGDisplayShowCursor(Quartz.CGMainDisplayID())

    # deltas arrive via the event tap; nothing to poll per tick

    # -- event tap callback --
    def _on_event(self, proxy, etype, event, refcon):
        try:
            if etype in (Quartz.kCGEventTapDisabledByTimeout,
                         Quartz.kCGEventTapDisabledByUserInput):
                Quartz.CGEventTapEnable(self._tap, True)
                return event

            if etype == Quartz.kCGEventOtherMouseDown:
                btn = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGMouseEventButtonNumber)
                if btn == _BUTTON_NUM[self.fly_button]:
                    if self.fly:
                        return None
                    if self.fusion_foreground():
                        self._start_fly()
                        return None
                return event

            if etype == Quartz.kCGEventOtherMouseUp:
                btn = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGMouseEventButtonNumber)
                if btn == _BUTTON_NUM[self.fly_button] and self.fly:
                    self._stop_fly()
                    return None
                return event

            if not self.fly:
                return event

            if etype == Quartz.kCGEventMouseMoved:
                dx = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGMouseEventDeltaX)
                dy = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGMouseEventDeltaY)
                with self._lock:
                    self._accum[0] += dx
                    self._accum[1] += dy
                return None                      # cursor is frozen anyway

            if etype == Quartz.kCGEventKeyDown:
                kc = normalize_kc(Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGKeyboardEventKeycode))
                if kc in self._bound:
                    with self._lock:
                        self._down[kc] = True
                    return None
                if kc == KC_ESCAPE:
                    self._stop_fly()
                    return None
                return event

            if etype == Quartz.kCGEventKeyUp:
                kc = normalize_kc(Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGKeyboardEventKeycode))
                if kc in self._bound:
                    with self._lock:
                        self._down[kc] = False
                # pass key-ups through so nothing sticks (see backend_win)
                return event

            if etype == Quartz.kCGEventFlagsChanged:
                # modifier keys arrive here, not as key down/up
                kc = normalize_kc(Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGKeyboardEventKeycode))
                if kc in self._bound and _MOD_FLAG.get(kc) is not None:
                    flags = Quartz.CGEventGetFlags(event)
                    with self._lock:
                        self._down[kc] = bool(flags & _MOD_FLAG[kc])
                return event                     # never block modifiers

            if etype == Quartz.kCGEventScrollWheel:
                delta = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGScrollWheelEventDeltaAxis1)
                if delta:
                    self.adjust_speed(1.15 ** (1 if delta > 0 else -1))
                return None
        except Exception:
            pass
        return event
