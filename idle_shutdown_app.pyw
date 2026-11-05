"""
Idle Shutdown (Windows)

A small desktop app that watches for mouse inactivity and then shuts down,
hibernates or locks the PC.

How detection works:
    A low-level Windows mouse hook (WH_MOUSE_LL) tells the app about every
    mouse event: movement, clicks and wheel scrolls. There is no polling of
    the cursor. The app only wakes up at the earliest moment the timer could
    run out, checks once, and reschedules if there was activity.

Timer rules:
    Seconds: 20 to 59
    Minutes: 5 to 1440
    No hours unit.

Run:
    Double-click this .pyw file, or:  pythonw idle_shutdown_app.pyw
    --minimized   start minimized (used by "Start with Windows")

Settings and log are stored in %APPDATA%\\IdleShutdown\\
Requires Python 3.8+ on Windows. No third-party packages.
"""

import ctypes
import ctypes.wintypes as wt
import json
import logging
import math
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

if sys.platform != "win32":
    sys.exit("This app only runs on Windows.")

import winreg  # noqa: E402  (Windows only)

# ---------------------------------------------------------------------------
# Paths, constants, logging
# ---------------------------------------------------------------------------

APP_NAME = "Idle Shutdown"
APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "IdleShutdown")
os.makedirs(APP_DIR, exist_ok=True)
SETTINGS_PATH = os.path.join(APP_DIR, "settings.json")
LOG_PATH = os.path.join(APP_DIR, "idle_shutdown.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8")],
)

ACTIONS = [("shutdown", "Shut down"), ("hibernate", "Hibernate"), ("lock", "Lock")]
ACTION_LABELS = dict(ACTIONS)
UNIT_LIMITS = {"seconds": (20, 59), "minutes": (5, 1440)}
WARNING_MAX = 60

DEFAULTS = {
    "enabled": False,
    "unit": "minutes",
    "value": 30,
    "action": "shutdown",
    "warning": 30,
    "test_mode": False,
}

# ---------------------------------------------------------------------------
# Win32 bindings
# ---------------------------------------------------------------------------

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_MOUSE_LL = 14
WM_QUIT = 0x0012
ERROR_ALREADY_EXISTS = 183
CREATE_NO_WINDOW = 0x08000000

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
user32.UnhookWindowsHookEx.restype = wt.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.GetMessageW.restype = wt.BOOL
user32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]
user32.PeekMessageW.restype = wt.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostThreadMessageW.restype = wt.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.GetCursorPos.restype = wt.BOOL
user32.LockWorkStation.restype = wt.BOOL

kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HMODULE
kernel32.GetCurrentThreadId.restype = wt.DWORD
kernel32.QueryUnbiasedInterruptTime.argtypes = [ctypes.POINTER(ctypes.c_ulonglong)]
kernel32.QueryUnbiasedInterruptTime.restype = wt.BOOL
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
kernel32.CreateMutexW.restype = wt.HANDLE


def awake_time():
    """Seconds of time the PC has been awake. Does not advance during sleep or hibernate."""
    t = ctypes.c_ulonglong()
    kernel32.QueryUnbiasedInterruptTime(ctypes.byref(t))
    return t.value / 10_000_000


def get_cursor_pos():
    """Cursor (x, y), or None when Windows refuses (for example on the lock screen)."""
    pt = wt.POINT()
    if user32.GetCursorPos(ctypes.byref(pt)):
        return (pt.x, pt.y)
    return None


# ---------------------------------------------------------------------------
# Mouse hook
# ---------------------------------------------------------------------------

class MouseHook:
    """
    Installs a low-level mouse hook on a background thread. Every mouse event
    (move, click, wheel) updates last_activity. The callback does nothing else,
    so it stays fast enough that Windows never drops the hook.
    """

    def __init__(self):
        self.last_activity = awake_time()
        # Only updated by real mouse events, never by touch(). Used to decide
        # when the user is really back after an action or during a warning.
        self.last_real = 0.0
        self.ok = False
        self._thread = None
        self._thread_id = None
        self._ready = threading.Event()
        self._proc = HOOKPROC(self._callback)  # keep a reference so it is not garbage collected

    def _callback(self, n_code, w_param, l_param):
        if n_code >= 0:
            now = awake_time()
            self.last_activity = now
            self.last_real = now
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _run(self):
        self._thread_id = kernel32.GetCurrentThreadId()
        msg = wt.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # create the message queue
        hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc,
                                        kernel32.GetModuleHandleW(None), 0)
        self.ok = bool(hook)
        self._ready.set()
        if not hook:
            logging.error("SetWindowsHookExW failed, error %s", ctypes.get_last_error())
            return
        logging.info("Mouse hook installed.")
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWindowsHookEx(hook)
        logging.info("Mouse hook removed.")

    def start(self):
        if self._thread and self._thread.is_alive():
            return self.ok
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="mouse-hook", daemon=True)
        self._thread.start()
        self._ready.wait(3)
        return self.ok

    def stop(self):
        if self._thread and self._thread.is_alive() and self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(3)
        self._thread = None
        self._thread_id = None
        self.ok = False

    def restart(self):
        self.stop()
        return self.start()

    def touch(self):
        """Treat now as activity (used after resume, after cancelling, and so on)."""
        self.last_activity = awake_time()


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def perform_action(action):
    """Run the action. Returns (ok, error_text)."""
    if action == "lock":
        return bool(user32.LockWorkStation()), ""
    cmd = ["shutdown", "/s", "/t", "0"] if action == "shutdown" else ["shutdown", "/h"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    except OSError as e:
        return False, str(e)
    return r.returncode == 0, (r.stderr or r.stdout or "").strip()


# ---------------------------------------------------------------------------
# Start with Windows (HKCU Run key, no admin needed)
# ---------------------------------------------------------------------------

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def startup_command():
    if getattr(sys, "frozen", False):  # built with PyInstaller
        return f'"{sys.executable}" --minimized'
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    return f'"{exe}" "{os.path.abspath(__file__)}" --minimized'


def is_startup_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def set_startup(on):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except FileNotFoundError:
                pass


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def parse_int(text, fallback):
    try:
        return int(float(str(text).strip()))
    except (TypeError, ValueError, OverflowError):  # OverflowError: "inf", "1e999"
        return fallback


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            s.update({k: data[k] for k in DEFAULTS if k in data})
    except (OSError, ValueError):
        pass
    if s["unit"] not in UNIT_LIMITS:
        s["unit"] = DEFAULTS["unit"]
    lo, hi = UNIT_LIMITS[s["unit"]]
    s["value"] = clamp(parse_int(s["value"], lo), lo, hi)
    if s["action"] not in ACTION_LABELS:
        s["action"] = DEFAULTS["action"]
    s["warning"] = clamp(parse_int(s["warning"], DEFAULTS["warning"]), 0, WARNING_MAX)
    s["enabled"] = bool(s["enabled"])
    s["test_mode"] = bool(s["test_mode"])
    return s


def save_settings(s):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
    except OSError as e:
        logging.error("Could not save settings: %s", e)


def fmt(seconds):
    seconds = max(0, int(math.ceil(seconds)))
    m, s = divmod(seconds, 60)
    return f"{m}:{s:02d}"


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

class App:
    def __init__(self, root, start_minimized=False):
        self.root = root
        self.s = load_settings()
        self.hook = MouseHook()

        self.check_job = None
        self.warning_win = None
        self.warning_started = 0.0
        self.warning_deadline = 0.0
        self.paused_after_action = False
        self.action_time = 0.0
        self.snapshot = None

        self._last_wall = time.time()
        self._last_awake = awake_time()

        self.build_ui()
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        if self.s["enabled"]:
            self.start_monitoring()
        self.refresh()
        if start_minimized:
            root.iconify()
        self.status_loop()

    # ----- settings helpers -------------------------------------------------

    def limit(self):
        v = self.s["value"]
        return v if self.s["unit"] == "seconds" else v * 60

    def warn_eff(self):
        """Warning length actually used: never more than half the timer."""
        return min(self.s["warning"], self.limit() // 2)

    def action_label(self):
        return ACTION_LABELS[self.s["action"]]

    # ----- UI ---------------------------------------------------------------

    def build_ui(self):
        r = self.root
        r.title(APP_NAME)
        r.resizable(False, False)

        f = ttk.Frame(r, padding=16)
        f.grid(sticky="nsew")
        f.columnconfigure(0, weight=1)

        self.state_label = tk.Label(f, font=("Segoe UI", 11, "bold"))
        self.state_label.grid(row=0, column=0, sticky="w")
        self.toggle_btn = ttk.Button(f, width=18, command=self.toggle)
        self.toggle_btn.grid(row=0, column=1, sticky="e")

        ttk.Separator(f).grid(row=1, column=0, columnspan=2, sticky="ew", pady=12)

        # Timer
        timer = ttk.LabelFrame(f, text="Inactivity timer", padding=10)
        timer.grid(row=2, column=0, columnspan=2, sticky="ew")
        lo, hi = UNIT_LIMITS[self.s["unit"]]
        self.value_var = tk.StringVar(value=str(self.s["value"]))
        self.value_spin = ttk.Spinbox(timer, from_=lo, to=hi, width=6,
                                      textvariable=self.value_var, command=self.commit_value)
        self.value_spin.grid(row=0, column=0, sticky="w")
        self.value_spin.bind("<FocusOut>", self.commit_value)
        self.value_spin.bind("<Return>", self.commit_value)

        self.unit_var = tk.StringVar(value=self.s["unit"])
        ttk.Radiobutton(timer, text="Seconds", value="seconds", variable=self.unit_var,
                        command=self.on_unit_change).grid(row=0, column=1, padx=(12, 6))
        ttk.Radiobutton(timer, text="Minutes", value="minutes", variable=self.unit_var,
                        command=self.on_unit_change).grid(row=0, column=2)
        self.range_hint = ttk.Label(timer, foreground="gray")
        self.range_hint.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))

        # Action
        act = ttk.LabelFrame(f, text="Action when idle", padding=10)
        act.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.action_var = tk.StringVar(value=self.s["action"])
        for i, (key, label) in enumerate(ACTIONS):
            ttk.Radiobutton(act, text=label, value=key, variable=self.action_var,
                            command=self.on_action_change).grid(row=0, column=i, padx=(0, 14))

        # Warning
        warn = ttk.LabelFrame(f, text="Warning before action", padding=10)
        warn.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.warning_var = tk.StringVar(value=str(self.s["warning"]))
        self.warning_spin = ttk.Spinbox(warn, from_=0, to=WARNING_MAX, width=6,
                                        textvariable=self.warning_var, command=self.commit_warning)
        self.warning_spin.grid(row=0, column=0, sticky="w")
        self.warning_spin.bind("<FocusOut>", self.commit_warning)
        self.warning_spin.bind("<Return>", self.commit_warning)
        ttk.Label(warn, text="seconds (0 = no warning)").grid(row=0, column=1, padx=8)
        self.warning_hint = ttk.Label(warn, foreground="gray")
        self.warning_hint.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        # Options
        opts = ttk.Frame(f)
        opts.grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.test_var = tk.BooleanVar(value=self.s["test_mode"])
        ttk.Checkbutton(opts, text="Test mode (only log the action, don't do it)",
                        variable=self.test_var, command=self.on_test_change).grid(row=0, column=0, sticky="w")
        self.startup_var = tk.BooleanVar(value=is_startup_enabled())
        ttk.Checkbutton(opts, text="Start with Windows",
                        variable=self.startup_var, command=self.on_startup_change).grid(row=1, column=0, sticky="w")

        ttk.Separator(f).grid(row=6, column=0, columnspan=2, sticky="ew", pady=12)

        # Status
        self.progress = ttk.Progressbar(f, length=360, maximum=100)
        self.progress.grid(row=7, column=0, columnspan=2, sticky="ew")
        self.status_label = ttk.Label(f)
        self.status_label.grid(row=8, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.event_label = ttk.Label(f, foreground="gray")
        self.event_label.grid(row=9, column=0, columnspan=2, sticky="w")

        self.update_hints()

    def update_hints(self):
        lo, hi = UNIT_LIMITS[self.s["unit"]]
        self.range_hint.config(text=f"Allowed: {lo} to {hi} {self.s['unit']}")
        eff = self.warn_eff()
        if eff == 0:
            text = f"No warning. {self.action_label()} runs as soon as the timer ends."
        elif eff < self.s["warning"]:
            text = f"Capped at {eff} s for this timer (max is half the timer)."
        else:
            text = f"Countdown appears {eff} s before {self.action_label().lower()}."
        self.warning_hint.config(text=text)

    def refresh(self):
        if self.s["enabled"]:
            self.state_label.config(text="Monitoring: ON", fg="#1a7f37")
            self.toggle_btn.config(text="Turn off")
        else:
            self.state_label.config(text="Monitoring: OFF", fg="#888888")
            self.toggle_btn.config(text="Turn on")
        self.update_status()

    def update_status(self):
        if not self.s["enabled"]:
            self.progress["value"] = 0
            self.status_label.config(text="Monitoring is off.")
            return
        if self.paused_after_action:
            self.progress["value"] = 0
            self.status_label.config(text="Waiting for mouse activity after the last action.")
            return
        if self.warning_win:
            self.progress["value"] = 100
            self.status_label.config(text="Warning countdown is on screen.")
            return
        limit = self.limit()
        idle = max(0.0, awake_time() - self.hook.last_activity)
        self.progress["value"] = min(100, idle / limit * 100)
        self.status_label.config(
            text=f"Idle {fmt(idle)}.  {self.action_label()} in {fmt(limit - idle)}.")

    def set_event(self, text):
        self.event_label.config(text=f"{time.strftime('%H:%M:%S')}  {text}")
        logging.info(text)

    # ----- setting changes --------------------------------------------------

    def settings_changed(self):
        save_settings(self.s)
        self.update_hints()
        if self.s["enabled"] and not self.warning_win and not self.paused_after_action:
            self.schedule_check()
        self.update_status()

    def commit_value(self, *_):
        lo, hi = UNIT_LIMITS[self.s["unit"]]
        v = clamp(parse_int(self.value_var.get(), self.s["value"]), lo, hi)
        self.value_var.set(str(v))
        if v != self.s["value"]:
            self.s["value"] = v
            self.settings_changed()

    def on_unit_change(self):
        unit = self.unit_var.get()
        if unit == self.s["unit"]:
            return
        self.s["unit"] = unit
        lo, hi = UNIT_LIMITS[unit]
        self.value_spin.config(from_=lo, to=hi)
        # Use what is typed in the box, even if it was never committed
        typed = parse_int(self.value_var.get(), self.s["value"])
        self.s["value"] = clamp(typed, lo, hi)
        self.value_var.set(str(self.s["value"]))
        self.settings_changed()

    def on_action_change(self):
        self.s["action"] = self.action_var.get()
        self.settings_changed()

    def commit_warning(self, *_):
        v = clamp(parse_int(self.warning_var.get(), self.s["warning"]), 0, WARNING_MAX)
        self.warning_var.set(str(v))
        if v != self.s["warning"]:
            self.s["warning"] = v
            self.settings_changed()

    def on_test_change(self):
        self.s["test_mode"] = bool(self.test_var.get())
        self.settings_changed()

    def on_startup_change(self):
        want = bool(self.startup_var.get())
        try:
            set_startup(want)
            logging.info("Start with Windows: %s", want)
        except OSError as e:
            self.startup_var.set(not want)
            messagebox.showerror(APP_NAME, f"Could not change the startup setting:\n{e}")

    # ----- monitoring -------------------------------------------------------

    def toggle(self):
        # Make sure typed-but-not-committed values are applied first
        self.commit_value()
        self.commit_warning()
        if self.s["enabled"]:
            self.stop_monitoring()
        else:
            self.start_monitoring()
        self.refresh()

    def start_monitoring(self):
        if not self.hook.start():
            self.s["enabled"] = False
            save_settings(self.s)
            messagebox.showerror(APP_NAME, "Could not install the mouse hook. "
                                           f"See the log:\n{LOG_PATH}")
            return
        self.hook.touch()
        self.paused_after_action = False
        self.s["enabled"] = True
        save_settings(self.s)
        self.set_event(f"Monitoring on: {self.action_label().lower()} after "
                       f"{self.s['value']} {self.s['unit']} of no mouse activity.")
        self.schedule_check()

    def stop_monitoring(self):
        if self.check_job:
            self.root.after_cancel(self.check_job)
            self.check_job = None
        if self.warning_win:
            self.close_warning(None)
        self.hook.stop()
        self.paused_after_action = False
        self.s["enabled"] = False
        save_settings(self.s)
        self.set_event("Monitoring off.")

    def schedule_check(self, delay=None):
        """
        Sleep until the earliest moment the warning (or action) could be due.
        A cursor snapshot is taken now; it is compared at the deadline as a
        safety net in case Windows ever drops the hook silently.
        """
        if self.check_job:
            self.root.after_cancel(self.check_job)
            self.check_job = None
        if not self.s["enabled"]:
            return
        self.snapshot = get_cursor_pos()
        if delay is None:
            due = self.hook.last_activity + self.limit() - self.warn_eff()
            delay = max(0.05, due - awake_time())
        self.check_job = self.root.after(int(delay * 1000) + 20, self.check)

    def check(self):
        self.check_job = None
        if not self.s["enabled"] or self.warning_win:
            return

        if self.paused_after_action:
            if self.hook.last_real > self.action_time:
                self.paused_after_action = False
                self.set_event("Mouse activity detected, timer restarted.")
            else:
                self.schedule_check(1.0)
                return

        due = self.hook.last_activity + self.limit() - self.warn_eff()
        if awake_time() < due:
            self.schedule_check()
            return

        # Safety net: did the cursor move without the hook seeing it?
        pos = get_cursor_pos()
        if pos is not None and self.snapshot is not None and pos != self.snapshot:
            logging.warning("Cursor moved but the hook missed it. Reinstalling the hook.")
            self.hook.restart()
            self.hook.touch()
            self.schedule_check()
            return

        if self.warn_eff() > 0:
            self.open_warning()
        else:
            self.run_action()

    # ----- warning window ---------------------------------------------------

    def open_warning(self):
        self.warning_started = awake_time()
        self.warning_deadline = self.hook.last_activity + self.limit()

        w = tk.Toplevel(self.root)
        w.title(APP_NAME)
        w.attributes("-topmost", True)
        w.resizable(False, False)
        w.protocol("WM_DELETE_WINDOW", lambda: self.close_warning("Cancelled from the warning window."))

        ttk.Label(w, text="No mouse activity detected.",
                  font=("Segoe UI", 12, "bold")).pack(padx=28, pady=(20, 6))
        self.warning_msg = ttk.Label(w, font=("Segoe UI", 11))
        self.warning_msg.pack(padx=28)
        ttk.Label(w, text="Move the mouse or press Cancel to stop it.").pack(padx=28, pady=(6, 10))
        ttk.Button(w, text="Cancel", width=14,
                   command=lambda: self.close_warning("Cancelled from the warning window.")).pack(pady=(0, 20))

        w.update_idletasks()
        x = (w.winfo_screenwidth() - w.winfo_width()) // 2
        y = (w.winfo_screenheight() - w.winfo_height()) // 2
        w.geometry(f"+{x}+{y}")

        self.warning_win = w
        logging.info("Warning shown.")
        self.update_status()
        self.warning_tick()

    def warning_tick(self):
        if not self.warning_win:
            return
        if self.hook.last_real > self.warning_started:
            self.close_warning("Cancelled by mouse activity.")
            return
        left = self.warning_deadline - awake_time()
        if left <= 0:
            self.close_warning(None)
            self.run_action()
            return
        self.warning_msg.config(text=f"{self.action_label()} in {int(math.ceil(left))} seconds")
        self.root.after(200, self.warning_tick)

    def close_warning(self, reason):
        if self.warning_win:
            try:
                self.warning_win.destroy()
            except tk.TclError:
                pass
        self.warning_win = None
        if reason:
            self.hook.touch()
            self.set_event(reason)
            self.schedule_check()
        self.update_status()

    # ----- action -----------------------------------------------------------

    def run_action(self):
        action = self.s["action"]
        label = self.action_label()
        self.action_time = awake_time()

        if self.s["test_mode"]:
            # Behave like a real action: wait for real mouse activity before
            # counting again, so test mode shows the same flow.
            self.set_event(f"Test mode: would have run '{label}' now.")
            self.paused_after_action = True
            self.schedule_check(1.0)
            self.update_status()
            return

        logging.info("Running action: %s", action)
        ok, err = perform_action(action)

        # Pause until real mouse activity, so a failed or blocked shutdown,
        # a lock, or a resume from hibernate never triggers it again right away.
        self.paused_after_action = True
        self.schedule_check(1.0)

        if ok:
            self.set_event(f"{label} started.")
        else:
            msg = f"{label} failed. {err}".strip()
            if action == "hibernate":
                msg += ("\n\nHibernate may be turned off on this PC. To enable it, run "
                        "'powercfg /hibernate on' in an administrator Command Prompt.")
            self.set_event(f"{label} failed.")
            logging.error(msg)
            messagebox.showerror(APP_NAME, msg)
        self.update_status()

    # ----- background loop: status display and resume detection ------------

    def status_loop(self):
        wall, awake = time.time(), awake_time()
        slept = (wall - self._last_wall) - (awake - self._last_awake)
        self._last_wall, self._last_awake = wall, awake

        if slept > 10 and self.s["enabled"]:
            # The PC was asleep or hibernating. Start the timer fresh.
            # If we were waiting for mouse activity after an action, keep
            # waiting: a resume (for example from a wake timer) is not the
            # user coming back.
            self.hook.touch()
            if self.warning_win:
                self.close_warning(None)
            if self.paused_after_action:
                self.set_event("Resumed from sleep or hibernate, still waiting for mouse activity.")
            else:
                self.set_event("Resumed from sleep or hibernate, timer restarted.")
            self.schedule_check()

        if self.root.state() != "iconic":
            self.update_status()
        self.root.after(1000, self.status_loop)

    # ----- closing ----------------------------------------------------------

    def on_close(self):
        if self.s["enabled"]:
            ans = messagebox.askyesnocancel(
                APP_NAME,
                "Monitoring is on.\n\n"
                "Yes: minimize and keep monitoring\n"
                "No: close the app and stop monitoring")
            if ans is None:
                return
            if ans:
                self.root.iconify()
                return
        self.quit()

    def quit(self):
        self.hook.stop()
        logging.info("App closed.")
        self.root.destroy()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass

    root = tk.Tk()

    # Only one copy at a time, otherwise two apps could both trigger the action
    ctypes.set_last_error(0)  # CreateMutexW does not always clear it on success
    mutex = kernel32.CreateMutexW(None, False, "Local\\IdleShutdownApp")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        root.withdraw()
        messagebox.showinfo(APP_NAME, "Idle Shutdown is already running.")
        return
    root._single_instance_mutex = mutex  # keep the handle alive

    logging.info("App started.")
    # If "Start with Windows" is on, point the entry at this copy (for example
    # after switching from the .pyw to the built .exe).
    try:
        if is_startup_enabled():
            set_startup(True)
    except OSError as e:
        logging.warning("Could not refresh the startup entry: %s", e)
    App(root, start_minimized="--minimized" in sys.argv)
    root.mainloop()


if __name__ == "__main__":
    main()
