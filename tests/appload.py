"""
Shared helpers for the Idle Shutdown tests.

The app is a single .pyw file, so it is loaded here by path. APPDATA is pointed
at a temporary folder while the module is imported, so the tests never read or
write your real settings or log in %APPDATA%\\IdleShutdown\\.
"""

import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PATH = os.path.join(ROOT, "idle_shutdown_app.pyw")
MODULE_NAME = "idle_shutdown_app"

# Safe defaults for every test: Test mode on, action Lock, 20 second timer.
TEST_SETTINGS = {
    "enabled": False,
    "unit": "seconds",
    "value": 20,
    "action": "lock",
    "warning": 30,
    "test_mode": True,
}

# Mouse messages a WH_MOUSE_LL hook receives
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_MOUSEWHEEL = 0x020A
WM_MOUSEHWHEEL = 0x020E

_module = None


def load_app():
    """Import idle_shutdown_app.pyw once, with APPDATA pointed at a temp folder."""
    global _module
    if _module is not None:
        return _module
    if sys.platform != "win32":
        raise unittest.SkipTest("Idle Shutdown only runs on Windows.")
    old = os.environ.get("APPDATA")
    os.environ["APPDATA"] = tempfile.mkdtemp(prefix="idle_shutdown_tests_")
    try:
        loader = importlib.machinery.SourceFileLoader(MODULE_NAME, APP_PATH)
        spec = importlib.util.spec_from_loader(MODULE_NAME, loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[MODULE_NAME] = module
        loader.exec_module(module)
    finally:
        if old is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = old
    _module = module
    return module


def write_settings(m, **overrides):
    s = dict(TEST_SETTINGS, **overrides)
    with open(m.SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(s, f)
    return s


def write_raw_settings(m, text):
    with open(m.SETTINGS_PATH, "w", encoding="utf-8") as f:
        f.write(text)


def make_app(m, use_tray=False, start_minimized=False, **overrides):
    """
    Create a hidden Tk root and an App with test settings. Returns (root, app).
    No tray icon unless use_tray=True, so most tests leave the tray alone.
    """
    import tkinter as tk
    write_settings(m, **overrides)
    root = tk.Tk()
    root.withdraw()
    app = m.App(root, start_minimized=start_minimized, use_tray=use_tray)
    if not app.s["test_mode"]:
        app.quit()
        raise RuntimeError("Refusing to run a test with Test mode off.")
    return root, app


def close_app(app):
    try:
        app.quit()
    except Exception:
        pass


def pump(root, seconds, until=None):
    """Run the Tk event loop for up to `seconds`, stopping early when until() is true."""
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        if until is not None and until():
            return True
        time.sleep(0.02)
    return until() if until is not None else True


class FakeClock:
    """Stands in for awake_time() so timer tests run instantly."""

    def __init__(self, start=1000.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds
