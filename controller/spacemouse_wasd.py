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
VERSION = '1.1.0'
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

ACTIONS = [
    ('pan_up', 'Pan up'),
    ('pan_down', 'Pan down'),
    ('pan_left', 'Pan left'),
    ('pan_right', 'Pan right'),
    ('zoom_in', 'Zoom in'),
    ('zoom_out', 'Zoom out'),
]


# --------------------------------------------------------------- config ----
def load_config():
    cfg = {'binds': dict(backend.DEFAULT_BINDS), 'fly_button': 2,
           'speed': 1.0}
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
        sp = saved.get('speed')
        if isinstance(sp, (int, float)):
            cfg['speed'] = min(max(float(sp), SPEED_MIN), SPEED_MAX)
        cfg['_other_binds'] = {k: v for k, v in saved.items()
                              if k.startswith('binds_') and k != BINDS_KEY}
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    out = {BINDS_KEY: cfg['binds'], 'fly_button': cfg['fly_button'],
           'speed': cfg['speed']}
    out.update(cfg.get('_other_binds', {}))    # keep the other OS's binds
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

_UIFONT = 'Segoe UI' if sys.platform != 'darwin' else 'Helvetica Neue'
FONT = (_UIFONT, 10)
FONT_SM = (_UIFONT, 9)
FONT_KEY = ('Consolas' if sys.platform != 'darwin' else 'Menlo', 10, 'bold')
FONT_TITLE = (_UIFONT, 17, 'bold')


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
        tk.Label(bcard, text='KEY BINDINGS', font=(_UIFONT, 8, 'bold'),
                 fg=DIM, bg=CARD).grid(row=0, column=0, columnspan=2,
                                       sticky='w', padx=14, pady=(12, 6))
        for i, (action, label) in enumerate(ACTIONS, start=1):
            tk.Label(bcard, text=label, font=FONT, fg=TEXT, bg=CARD,
                     anchor='w').grid(row=i, column=0, sticky='w',
                                      padx=(14, 20), pady=3)
            btn = tk.Button(
                bcard, text=backend.key_name(cfg['binds'][action]),
                font=FONT_KEY, fg=TEXT, bg=FIELD, activebackground=FIELD_HI,
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
        labels = backend.FLY_BUTTON_LABELS
        self.btn_var = tk.StringVar(value=labels[cfg['fly_button']])
        om = tk.OptionMenu(bcard, self.btn_var, *labels.values(),
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
        b = backend.FLY_BUTTON_LABELS[self.engine.fly_button].upper()
        self.hint.config(text='Hold the %s mouse button in Fusion to fly.  '
                              'Scroll = speed, Esc = bail out.' % b)

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
        for num, label in backend.FLY_BUTTON_LABELS.items():
            if label == choice:
                self.cfg['fly_button'] = num
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
        self.cfg['binds'] = dict(backend.DEFAULT_BINDS)
        self.cfg['fly_button'] = 2
        self.cfg['speed'] = 1.0
        self.engine.set_binds(self.cfg['binds'])
        self.engine.fly_button = 2
        self.engine.speed = 1.0
        self.speed_var.set(1.0)
        self.btn_var.set(backend.FLY_BUTTON_LABELS[2])
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
