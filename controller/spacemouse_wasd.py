#!/usr/bin/env python3
"""SpaceMouse WASD - spacemouse-style navigation for Autodesk Fusion.

Desktop controller app. Hold a side mouse button while Fusion is focused to
enter fly mode: the mouse orbits (cursor hidden), bindable keys pan and zoom,
and motion is streamed over localhost UDP to the companion Fusion add-in,
which drives the camera with velocity smoothing.

Windows: pure standard library.  macOS (beta): needs pyobjc (see README).
"""

import json
import os
import socket
import sys
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if sys.platform == 'darwin':
    import backend_mac as backend
else:
    import backend_win as backend

from engine_base import SPEED_MIN, SPEED_MAX

APP_NAME = 'SpaceMouse WASD'
VERSION = '1.6.0'
LOCK_PORT = 42739         # single-instance guard (bound while app runs)
if sys.platform == 'darwin':
    CONFIG_DIR = os.path.expanduser(
        '~/Library/Application Support/SpaceMouseWASD')
else:
    CONFIG_DIR = os.path.join(os.environ.get('APPDATA', '.'),
                              'SpaceMouseWASD')
CONFIG_PATH = os.path.join(CONFIG_DIR, 'config.json')
ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         '..', 'assets')
BINDS_KEY = 'binds_mac' if sys.platform == 'darwin' else 'binds_win'
COMBO_KEY = 'combo_mac' if sys.platform == 'darwin' else 'combo_win'
COMBO_LABEL = 'Key combo'
SCROLL_LABELS = {'speed': 'Adjust speed', 'zoom': 'Zoom'}
MOD_ORDER = ('ctrl', 'alt', 'shift')
MOD_TITLES = {'ctrl': 'Ctrl', 'alt': 'Alt', 'shift': 'Shift'}


def combo_label(combo):
    parts = [MOD_TITLES[m] for m in MOD_ORDER if m in combo.get('mods', [])]
    parts.append(backend.key_name(combo['code']))
    return '+'.join(parts)

ACTIONS = [
    ('pan_up', 'Pan up'),
    ('pan_down', 'Pan down'),
    ('pan_left', 'Pan left'),
    ('pan_right', 'Pan right'),
    ('zoom_in', 'Zoom in'),
    ('zoom_out', 'Zoom out'),
]

# extra binds that only exist while free orbit is enabled: the mouse covers
# yaw + pitch, these keys supply the remaining rotation (roll)
ROLL_ACTIONS = [
    ('roll_left', 'Roll left'),
    ('roll_right', 'Roll right'),
]


# --------------------------------------------------------------- config ----
def load_config():
    cfg = {'binds': dict(backend.DEFAULT_BINDS), 'fly_button': 2,
           'trigger_type': 'button', 'combo': dict(backend.DEFAULT_COMBO),
           'scroll_mode': 'speed', 'free_orbit': False, 'speed': 1.0}
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            saved = json.load(f)
        # migrate pre-1.1 configs, whose Windows binds lived under 'binds'
        saved_binds = saved.get(BINDS_KEY)
        if saved_binds is None and BINDS_KEY == 'binds_win':
            saved_binds = saved.get('binds')
        for k, v in (saved_binds or {}).items():
            if k in cfg['binds'] and isinstance(v, int):
                cfg['binds'][k] = v
        if saved.get('fly_button') in (1, 2):
            cfg['fly_button'] = saved['fly_button']
        if saved.get('trigger_type') in ('button', 'combo'):
            cfg['trigger_type'] = saved['trigger_type']
        if saved.get('scroll_mode') in ('speed', 'zoom'):
            cfg['scroll_mode'] = saved['scroll_mode']
        if isinstance(saved.get('free_orbit'), bool):
            cfg['free_orbit'] = saved['free_orbit']
        combo = saved.get(COMBO_KEY)
        if (isinstance(combo, dict) and isinstance(combo.get('code'), int)
                and isinstance(combo.get('mods'), list)):
            cfg['combo'] = {'code': combo['code'],
                            'mods': [m for m in combo['mods']
                                     if m in MOD_ORDER]}
        sp = saved.get('speed')
        if isinstance(sp, (int, float)):
            cfg['speed'] = min(max(float(sp), SPEED_MIN), SPEED_MAX)
        cfg['_other_os'] = {k: v for k, v in saved.items()
                            if k.startswith(('binds_', 'combo_')) and
                            k not in (BINDS_KEY, COMBO_KEY)}
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    out = {BINDS_KEY: cfg['binds'], COMBO_KEY: cfg['combo'],
           'trigger_type': cfg['trigger_type'],
           'fly_button': cfg['fly_button'],
           'scroll_mode': cfg['scroll_mode'],
           'free_orbit': cfg['free_orbit'], 'speed': cfg['speed']}
    out.update(cfg.get('_other_os', {}))    # keep the other OS's settings
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(out, f, indent=2)
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

if sys.platform == 'darwin':
    # Aqua renders a given point size smaller than Win32 does; the bump keeps
    # both platforms optically identical.
    _UIFONT, _MONOFONT, _BUMP = 'Helvetica Neue', 'Menlo', 2
else:
    _UIFONT, _MONOFONT, _BUMP = 'Segoe UI', 'Consolas', 0
FONT = (_UIFONT, 10 + _BUMP)
FONT_SM = (_UIFONT, 9 + _BUMP)
FONT_KEY = (_MONOFONT, 10 + _BUMP, 'bold')
FONT_TITLE = (_UIFONT, 17 + _BUMP, 'bold')
FONT_CAP = (_UIFONT, 8 + _BUMP, 'bold')


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


# Tk's native Button/OptionMenu/Scale are drawn by the platform on macOS and
# silently ignore bg/fg, which leaves light Aqua controls stranded on the dark
# cards. These three are pure Label/Canvas, so both platforms match exactly.
class FlatButton(tk.Label):
    def __init__(self, parent, text, command, width=14, font=FONT_KEY):
        super().__init__(parent, text=text, font=font, fg=TEXT, bg=FIELD,
                         width=width, pady=4, cursor='hand2')
        self._command = command
        self.bind('<Button-1>', lambda _e: self._command())
        self.bind('<Enter>', lambda _e: self.config(bg=FIELD_HI))
        self.bind('<Leave>', lambda _e: self.config(bg=FIELD))


class Dropdown(tk.Frame):
    def __init__(self, parent, options, value, on_change, width=13):
        super().__init__(parent, bg=FIELD, cursor='hand2')
        self.options = list(options)
        self.on_change = on_change
        self.var = tk.StringVar(value=value)
        self._pop = None
        self.lbl = tk.Label(self, textvariable=self.var, font=FONT, fg=TEXT,
                            bg=FIELD, width=width, anchor='w', pady=4,
                            cursor='hand2')
        self.lbl.pack(side='left', padx=(9, 0))
        self.caret = tk.Label(self, text='▾', font=FONT, fg=DIM, bg=FIELD,
                              pady=4, cursor='hand2')
        self.caret.pack(side='right', padx=(0, 9))
        for w in (self, self.lbl, self.caret):
            w.bind('<Button-1>', self._toggle)
            w.bind('<Enter>', self._hover_on)
            w.bind('<Leave>', self._hover_off)

    def get(self):
        return self.var.get()

    def set(self, value):
        self.var.set(value)

    def _hover_on(self, _e=None):
        for w in (self, self.lbl, self.caret):
            w.config(bg=FIELD_HI)

    def _hover_off(self, _e=None):
        for w in (self, self.lbl, self.caret):
            w.config(bg=FIELD)

    def _toggle(self, _e=None):
        if self._pop is not None:
            self._close()
        else:
            self._open()

    def _open(self):
        top = tk.Toplevel(self)
        top.overrideredirect(True)
        top.configure(bg=FIELD_HI)
        try:
            top.attributes('-topmost', True)
        except tk.TclError:
            pass
        for opt in self.options:
            item = tk.Label(top, text=opt, font=FONT, fg=TEXT, bg=FIELD,
                            anchor='w', padx=9, pady=5, cursor='hand2')
            item.pack(fill='x', padx=1, pady=1)
            item.bind('<Enter>', lambda _e, w=item: w.config(bg=FIELD_HI))
            item.bind('<Leave>', lambda _e, w=item: w.config(bg=FIELD))
            item.bind('<Button-1>', lambda _e, o=opt: self._pick(o))
        self.update_idletasks()
        top.update_idletasks()
        top.geometry('%dx%d+%d+%d' % (
            self.winfo_width(), top.winfo_reqheight(),
            self.winfo_rootx(), self.winfo_rooty() + self.winfo_height() + 2))
        # A local grab routes stray clicks here so the popup can dismiss itself.
        top.grab_set()
        top.bind('<Button-1>', lambda _e: self._close())
        top.bind('<Escape>', lambda _e: self._close())
        self._pop = top

    def _pick(self, option):
        self._close()
        if option != self.var.get():
            self.var.set(option)
            self.on_change(option)

    def _close(self):
        if self._pop is not None:
            self._pop.grab_release()
            self._pop.destroy()
            self._pop = None


class Slider(tk.Canvas):
    def __init__(self, parent, from_, to, value, on_change,
                 resolution=0.05, width=150, height=22):
        super().__init__(parent, width=width, height=height, bg=CARD,
                         highlightthickness=0, bd=0, cursor='hand2')
        self.from_, self.to = from_, to
        self.resolution = resolution
        self.on_change = on_change
        self.value = value
        self.bind('<Configure>', lambda _e: self._redraw())
        self.bind('<Button-1>', self._drag)
        self.bind('<B1-Motion>', self._drag)

    def set(self, value):
        self.value = min(max(value, self.from_), self.to)
        self._redraw()

    def _track(self):
        return 10, max(self.winfo_width(), 20) - 10

    def _redraw(self):
        self.delete('all')
        x0, x1 = self._track()
        cy = int(self['height']) // 2
        frac = (self.value - self.from_) / float(self.to - self.from_)
        kx = x0 + frac * (x1 - x0)
        self.create_line(x0, cy, x1, cy, fill=FIELD, width=6, capstyle='round')
        if kx > x0 + 0.5:
            self.create_line(x0, cy, kx, cy, fill=ACCENT, width=6,
                             capstyle='round')
        self.create_oval(kx - 7, cy - 7, kx + 7, cy + 7, fill=TEXT,
                         outline='')

    def _drag(self, event):
        x0, x1 = self._track()
        frac = min(max((event.x - x0) / float(max(x1 - x0, 1)), 0.0), 1.0)
        raw = self.from_ + frac * (self.to - self.from_)
        stepped = round(raw / self.resolution) * self.resolution
        stepped = min(max(stepped, self.from_), self.to)
        if stepped != self.value:
            self.value = stepped
            self._redraw()
            self.on_change(stepped)


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
        tk.Label(bcard, text='KEY BINDINGS', font=FONT_CAP,
                 fg=DIM, bg=CARD).grid(row=0, column=0, columnspan=2,
                                       sticky='w', padx=14, pady=(12, 6))
        for i, (action, label) in enumerate(ACTIONS, start=1):
            tk.Label(bcard, text=label, font=FONT, fg=TEXT, bg=CARD,
                     anchor='w').grid(row=i, column=0, sticky='w',
                                      padx=(14, 20), pady=3)
            btn = FlatButton(bcard,
                             text=backend.key_name(cfg['binds'][action]),
                             command=lambda a=action: self.begin_capture(a))
            btn.grid(row=i, column=1, sticky='e', padx=(0, 14), pady=3)
            self.bind_buttons[action] = btn
        bcard.grid_columnconfigure(0, weight=1)

        r = len(ACTIONS) + 1
        tk.Label(bcard, text='Free orbit', font=FONT, fg=TEXT, bg=CARD,
                 anchor='w').grid(row=r, column=0, sticky='w',
                                  padx=(14, 20), pady=(10, 3))
        self.free_btn = FlatButton(
            bcard, text='On' if cfg['free_orbit'] else 'Off',
            command=self.on_free_orbit)
        if cfg['free_orbit']:
            self.free_btn.config(fg=ACCENT)
        self.free_btn.grid(row=r, column=1, sticky='e', padx=(0, 14),
                           pady=(10, 3))

        self.roll_widgets = []
        for action, label in ROLL_ACTIONS:
            r += 1
            lbl = tk.Label(bcard, text=label, font=FONT, fg=TEXT, bg=CARD,
                           anchor='w')
            lbl.grid(row=r, column=0, sticky='w', padx=(14, 20), pady=3)
            btn = FlatButton(bcard,
                             text=backend.key_name(cfg['binds'][action]),
                             command=lambda a=action: self.begin_capture(a))
            btn.grid(row=r, column=1, sticky='e', padx=(0, 14), pady=3)
            self.bind_buttons[action] = btn
            self.roll_widgets += [lbl, btn]
        if not cfg['free_orbit']:
            for wdg in self.roll_widgets:
                wdg.grid_remove()

        r += 1
        tk.Label(bcard, text='Fly trigger', font=FONT, fg=TEXT, bg=CARD,
                 anchor='w').grid(row=r, column=0, sticky='w',
                                  padx=(14, 20), pady=(10, 3))
        labels = backend.FLY_BUTTON_LABELS
        current = (COMBO_LABEL if cfg['trigger_type'] == 'combo'
                   else labels[cfg['fly_button']])
        self.fly_dd = Dropdown(bcard, list(labels.values()) + [COMBO_LABEL],
                               current, self.on_fly_button)
        self.fly_dd.grid(row=r, column=1, sticky='e', padx=(0, 14),
                         pady=(10, 3))

        r += 1
        self.combo_row_lbl = tk.Label(bcard, text='Trigger keys', font=FONT,
                                      fg=TEXT, bg=CARD, anchor='w')
        self.combo_row_lbl.grid(row=r, column=0, sticky='w',
                                padx=(14, 20), pady=3)
        self.combo_btn = FlatButton(bcard, text=combo_label(cfg['combo']),
                                    command=self.begin_combo_capture)
        self.combo_btn.grid(row=r, column=1, sticky='e', padx=(0, 14), pady=3)
        if cfg['trigger_type'] != 'combo':
            self.combo_row_lbl.grid_remove()
            self.combo_btn.grid_remove()

        r += 1
        tk.Label(bcard, text='Scroll wheel', font=FONT, fg=TEXT, bg=CARD,
                 anchor='w').grid(row=r, column=0, sticky='w',
                                  padx=(14, 20), pady=3)
        self.scroll_dd = Dropdown(
            bcard, list(SCROLL_LABELS.values()),
            SCROLL_LABELS[cfg['scroll_mode']], self.on_scroll_mode)
        self.scroll_dd.grid(row=r, column=1, sticky='e', padx=(0, 14), pady=3)

        r += 1
        srow = tk.Frame(bcard, bg=CARD)
        srow.grid(row=r, column=0, columnspan=2, sticky='ew',
                  padx=14, pady=(6, 12))
        tk.Label(srow, text='Speed', font=FONT, fg=TEXT,
                 bg=CARD).pack(side='left')
        self.speed_lbl = tk.Label(srow, text='x%.2f' % cfg['speed'],
                                  font=FONT_KEY, fg=ACCENT, bg=CARD, width=6)
        self.speed_lbl.pack(side='right')
        self.speed_slider = Slider(srow, SPEED_MIN, SPEED_MAX, cfg['speed'],
                                   self.on_speed)
        self.speed_slider.pack(side='left', fill='x', expand=True, padx=12)

        # footer
        foot = tk.Frame(root, bg=BG)
        foot.pack(fill='x', padx=pad, pady=(4, pad - 4))
        self.hint = tk.Label(foot, font=FONT_SM, fg=DIM, bg=BG,
                             justify='left')
        self.hint.pack(anchor='w')
        self._update_hint()
        brow = tk.Frame(foot, bg=BG)
        brow.pack(fill='x', pady=(8, 0))
        if backend.SUPPORTS_AUTOSTART:
            self.auto_var = tk.BooleanVar(
                value=os.path.exists(startup_path()))
            tk.Checkbutton(brow, text='Launch at Windows startup',
                           variable=self.auto_var, command=self.on_autostart,
                           font=FONT_SM, fg=DIM, bg=BG, activebackground=BG,
                           activeforeground=TEXT, selectcolor=FIELD,
                           highlightthickness=0,
                           cursor='hand2').pack(side='left')
        reset = tk.Label(brow, text='Reset defaults', font=FONT_SM,
                         fg=DIM, bg=BG, cursor='hand2')
        reset.pack(side='right')
        reset.bind('<Button-1>', self.on_reset)
        tk.Label(brow, text='v' + VERSION + '  ', font=FONT_SM, fg='#3d4452',
                 bg=BG).pack(side='right')

        if engine.error:
            tk.Label(root, text=engine.error, font=FONT_SM, fg='#f56565',
                     bg=BG, wraplength=360, justify='left').pack(
                         padx=pad, pady=(0, pad))

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
        if self.engine.trigger_type == 'combo':
            what = combo_label(self.cfg['combo'])
        else:
            what = ('the %s mouse button' %
                    backend.FLY_BUTTON_LABELS[self.engine.fly_button].upper())
        scroll = ('zoom' if self.engine.scroll_mode == 'zoom' else 'speed')
        self.hint.config(text='Hold %s in Fusion to fly.  '
                              'Scroll = %s, Esc = bail out.' % (what, scroll))

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
        code, is_escape = backend.tk_event_to_code(event)
        if not is_escape and code is not None:   # Esc cancels (the bail key)
            binds = self.cfg['binds']
            for other, other_code in binds.items():
                if other != action and other_code == code:
                    binds[other] = binds[action]  # swap to avoid duplicates
            binds[action] = code
            self.engine.set_binds(binds)
            save_config(self.cfg)
        self.end_capture()
        return 'break'

    def end_capture(self):
        self.root.unbind('<KeyPress>')
        self.capturing = None
        for action, btn in self.bind_buttons.items():
            btn.config(text=backend.key_name(self.cfg['binds'][action]),
                       fg=TEXT)

    def on_fly_button(self, choice):
        if choice == COMBO_LABEL:
            self.cfg['trigger_type'] = 'combo'
            self.combo_row_lbl.grid()
            self.combo_btn.grid()
        else:
            self.cfg['trigger_type'] = 'button'
            self.combo_row_lbl.grid_remove()
            self.combo_btn.grid_remove()
            for num, label in backend.FLY_BUTTON_LABELS.items():
                if label == choice:
                    self.cfg['fly_button'] = num
            self.engine.fly_button = self.cfg['fly_button']
        self.engine.trigger_type = self.cfg['trigger_type']
        save_config(self.cfg)
        self._update_hint()

    def on_scroll_mode(self, choice):
        for mode, label in SCROLL_LABELS.items():
            if label == choice:
                self.cfg['scroll_mode'] = mode
        self.engine.scroll_mode = self.cfg['scroll_mode']
        save_config(self.cfg)
        self._update_hint()

    def on_free_orbit(self):
        on = not self.cfg['free_orbit']
        self.cfg['free_orbit'] = on
        self.engine.set_free_orbit(on)
        self.free_btn.config(text='On' if on else 'Off',
                             fg=ACCENT if on else TEXT)
        for wdg in self.roll_widgets:
            if on:
                wdg.grid()
            else:
                wdg.grid_remove()
        save_config(self.cfg)

    def begin_combo_capture(self):
        if self.capturing:
            self.end_capture()
        self.combo_btn.config(text='press combo...', fg=ACCENT)
        self.root.bind('<KeyPress>', self.on_capture_combo)

    def on_capture_combo(self, event):
        sym = event.keysym.lower()
        if sym.startswith(('shift', 'control', 'alt', 'option', 'meta',
                           'caps', 'super', 'win')):
            return 'break'          # a bare modifier: wait for the real key
        code, is_escape = backend.tk_event_to_code(event)
        if not is_escape and code is not None:   # Esc cancels
            self.cfg['combo'] = {'code': code, 'mods': backend.held_mods()}
            self.engine.combo_code = code
            self.engine.combo_mods = list(self.cfg['combo']['mods'])
            save_config(self.cfg)
        self.root.unbind('<KeyPress>')
        self.combo_btn.config(text=combo_label(self.cfg['combo']), fg=TEXT)
        self._update_hint()
        return 'break'

    def on_speed(self, value):
        v = float(value)
        self.engine.speed = v
        self.cfg['speed'] = v
        self.speed_lbl.config(text='x%.2f' % v)
        if self._save_job:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(
            800, lambda: save_config(self.cfg))

    def on_reset(self, _event=None):
        self.cfg['binds'] = dict(backend.DEFAULT_BINDS)
        self.cfg['fly_button'] = 2
        self.cfg['trigger_type'] = 'button'
        self.cfg['combo'] = dict(backend.DEFAULT_COMBO)
        self.cfg['scroll_mode'] = 'speed'
        self.cfg['free_orbit'] = False
        self.cfg['speed'] = 1.0
        self.engine.set_free_orbit(False)
        self.free_btn.config(text='Off', fg=TEXT)
        for wdg in self.roll_widgets:
            wdg.grid_remove()
        self.engine.set_binds(self.cfg['binds'])
        self.engine.fly_button = 2
        self.engine.trigger_type = 'button'
        self.engine.combo_code = self.cfg['combo']['code']
        self.engine.combo_mods = list(self.cfg['combo']['mods'])
        self.engine.scroll_mode = 'speed'
        self.scroll_dd.set(SCROLL_LABELS['speed'])
        self.engine.speed = 1.0
        self.speed_slider.set(1.0)
        self.speed_lbl.config(text='x%.2f' % 1.0)
        self.fly_dd.set(backend.FLY_BUTTON_LABELS[2])
        self.combo_btn.config(text=combo_label(self.cfg['combo']), fg=TEXT)
        self.combo_row_lbl.grid_remove()
        self.combo_btn.grid_remove()
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
            self.speed_slider.set(e.speed)
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


TK_TOO_OLD = """\
This Python is linked against Tk {ver}, which cannot draw windows correctly
on modern macOS - the app would open completely blank.

Apple's /usr/bin/python3 always ships this old Tk. Install a Python with
Tk 8.6+ and launch with that instead:

    brew install python@3.13 python-tk@3.13
    /opt/homebrew/bin/python3.13 controller/spacemouse_wasd.py

SpaceMouseWASD.command picks a suitable interpreter automatically."""


def tk_is_usable(root):
    """Guard against the blank-window failure mode rather than shipping a
    UI the user cannot see."""
    if sys.platform != 'darwin' or tk.TkVersion >= 8.6:
        return True
    msg = TK_TOO_OLD.format(ver=root.tk.call('info', 'patchlevel'))
    print(msg, file=sys.stderr)
    try:
        from tkinter import messagebox
        messagebox.showerror(APP_NAME, msg)
    except Exception:
        pass
    return False


def acquire_single_instance():
    """Bind a localhost port as a cross-platform single-instance lock.
    Two copies mean two sets of input hooks fighting - a reliable source
    of glitches."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(('127.0.0.1', LOCK_PORT))
        s.listen(1)
        return s
    except OSError:
        return None


def main():
    selftest = '--selftest' in sys.argv

    lock = acquire_single_instance()
    if lock is None:
        root = tk.Tk()
        root.withdraw()
        from tkinter import messagebox
        messagebox.showinfo(APP_NAME, APP_NAME + ' is already running.')
        return

    cfg = load_config()
    root = tk.Tk()
    if not tk_is_usable(root):
        root.destroy()
        lock.close()
        return
    engine = backend.Engine(cfg)
    engine.start()
    app = App(root, engine, cfg)
    if selftest:
        def finish():
            print('SELFTEST OK')
            app.close()
        root.after(1500, finish)
    root.mainloop()
    lock.close()


if __name__ == '__main__':
    main()
