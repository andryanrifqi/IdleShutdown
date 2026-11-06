"""
Tray icon and hide-to-tray behavior.

These tests add the app's own icon to your notification area for a moment and
remove it again. They never touch the real mouse.
"""

import ctypes
import tkinter as tk
import unittest
from unittest import mock

from appload import close_app, load_app, make_app, pump


def _fake_start(self):
    self.ok = True
    return True


def _fake_stop(self):
    self.ok = False


class IconDrawingTests(unittest.TestCase):
    def setUp(self):
        self.m = load_app()

    def pixel(self, data, size, x, y):
        """(r, g, b, a) at x, y counted from the top-left."""
        i = ((size - 1 - y) * size + x) * 4  # rows are stored bottom-up
        b, g, r, a = data[i:i + 4]
        return r, g, b, a

    def test_pixel_buffer_size(self):
        for size in (16, 20, 24, 32):
            with self.subTest(size=size):
                self.assertEqual(len(self.m.icon_pixels(size, (0, 0, 0))), size * size * 4)

    def test_round_shape_with_white_power_symbol(self):
        size, rgb = 32, (26, 127, 55)
        data = self.m.icon_pixels(size, rgb)
        self.assertEqual(self.pixel(data, size, 0, 0)[3], 0, "corners are transparent")
        self.assertEqual(self.pixel(data, size, 31, 31)[3], 0)
        self.assertEqual(self.pixel(data, size, 16, 17), rgb + (255,), "centre is the state color")
        self.assertEqual(self.pixel(data, size, 16, 7)[:3], (255, 255, 255), "top bar is white")
        self.assertEqual(self.pixel(data, size, 16, 24)[:3], (255, 255, 255), "ring bottom is white")
        self.assertEqual(self.pixel(data, size, 16, 3)[:3], rgb, "gap at the top of the ring")

    def test_png_icon_is_valid_and_loads_in_tk(self):
        import base64
        png = self.m.icon_png(32, (26, 127, 55))
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        root = tk.Tk()
        root.withdraw()
        try:
            img = tk.PhotoImage(master=root, data=base64.b64encode(png))
            self.assertEqual((img.width(), img.height()), (32, 32))
            self.assertEqual(img.get(16, 17), (26, 127, 55))
            self.assertEqual(img.get(16, 7), (255, 255, 255))
            self.assertTrue(img.transparency_get(0, 0))
        finally:
            root.destroy()

    def test_each_state_makes_an_icon(self):
        for state, rgb in self.m.TRAY_COLORS.items():
            with self.subTest(state=state):
                h = self.m.make_icon(24, rgb)
                self.assertTrue(h)
                self.m.user32.DestroyIcon(h)


class TrayIconTests(unittest.TestCase):
    def setUp(self):
        self.m = load_app()
        self.tray = self.m.TrayIcon()
        self.assertTrue(self.tray.start(), "tray window was not created")
        self.addCleanup(self.tray.stop)

    def post(self, l_param):
        self.m.user32.PostMessageW(self.tray.hwnd, self.m.TRAY_CALLBACK, 0, l_param)

    def test_icon_is_added(self):
        self.assertTrue(self.tray.icon_added)
        self.assertEqual(ctypes.sizeof(self.m.NOTIFYICONDATAW), 976 if ctypes.sizeof(ctypes.c_void_p) == 8 else 956)

    def test_left_click_and_double_click_ask_to_show(self):
        for msg in (self.m.WM_LBUTTONUP, self.m.WM_LBUTTONDBLCLK):
            with self.subTest(msg=hex(msg)):
                self.post(msg)
                self.assertEqual(self.tray.events.get(timeout=2), "show")

    def test_set_state_changes_icon_and_tip(self):
        self.tray.set_state("on", "Idle Shutdown: on", True)
        self.assertEqual((self.tray._state, self.tray._tip, self.tray.monitoring),
                         ("on", "Idle Shutdown: on", True))
        self.tray.set_state("warning", "x" * 300, True)  # long tips are cut, not an error
        self.assertEqual(self.tray._state, "warning")

    def test_explorer_restart_adds_icon_again(self):
        msg = self.m.user32.RegisterWindowMessageW("TaskbarCreated")
        self.m.user32.PostMessageW(self.tray.hwnd, msg, 0, 0)
        self.post(self.m.WM_LBUTTONUP)  # processed after the TaskbarCreated message
        self.tray.events.get(timeout=2)
        self.assertTrue(self.tray.icon_added)

    def test_stop_removes_icon_and_window(self):
        hwnd = self.tray.hwnd
        self.tray.stop()
        self.assertFalse(self.tray._thread.is_alive())
        self.assertFalse(self.tray.icon_added)
        self.assertFalse(self.m.user32.IsWindow(hwnd))

    def test_second_copy_can_find_the_tray_window(self):
        self.assertEqual(self.m.user32.FindWindowW(None, self.m.TRAY_WINDOW_TITLE), self.tray.hwnd)


class AppTrayTests(unittest.TestCase):
    def setUp(self):
        self.m = load_app()
        for p in (mock.patch.object(self.m.MouseHook, "start", _fake_start),
                  mock.patch.object(self.m.MouseHook, "stop", _fake_stop)):
            p.start()
            self.addCleanup(p.stop)

    def make(self, **kw):
        root, app = make_app(self.m, use_tray=True, **kw)
        self.addCleanup(close_app, app)
        self.assertIsNotNone(app.tray, "tray icon was not created")
        return root, app

    def show(self, root, app):
        app.show_window()
        pump(root, 0.3)
        self.assertEqual(root.state(), "normal")

    def test_window_uses_power_icon(self):
        root, app = self.make()
        self.assertEqual([(i.width(), i.height()) for i in app._icon_images], [(48, 48), (32, 32), (16, 16)])

    def test_start_minimized_goes_to_tray(self):
        root, app = self.make(start_minimized=True)
        pump(root, 0.3)
        self.assertEqual(root.state(), "withdrawn")

    def test_minimize_button_hides_to_tray(self):
        root, app = self.make()
        self.show(root, app)
        root.iconify()
        self.assertTrue(pump(root, 2, lambda: root.state() == "withdrawn"))

    def test_hint_notification_shown_only_once(self):
        root, app = self.make()
        with mock.patch.object(app.tray, "notify") as notify:
            app.hide_to_tray()
            self.show(root, app)
            app.hide_to_tray()
        notify.assert_called_once()
        self.assertNotIn(chr(0x2014), notify.call_args[0][1])  # no em dash

    def test_tray_click_opens_window(self):
        root, app = self.make(start_minimized=True)
        self.m.user32.PostMessageW(app.tray.hwnd, self.m.TRAY_CALLBACK, 0, self.m.WM_LBUTTONUP)
        self.assertTrue(pump(root, 2, lambda: root.state() == "normal"))

    def test_menu_toggle_turns_monitoring_on_and_off(self):
        root, app = self.make()
        app.tray.events.put("toggle")
        self.assertTrue(pump(root, 2, lambda: app.s["enabled"]))
        self.assertTrue(app.tray.monitoring)
        app.tray.events.put("toggle")
        self.assertTrue(pump(root, 2, lambda: not app.s["enabled"]))
        self.assertFalse(app.tray.monitoring)

    def test_menu_exit_closes_app(self):
        root, app = self.make()
        tray = app.tray
        tray.events.put("exit")
        self.assertTrue(pump_safely(root, 2, lambda: app.tray is None), "app did not exit")
        self.assertFalse(tray._thread.is_alive())
        self.assertFalse(tray.icon_added)
        with self.assertRaises(tk.TclError):
            root.winfo_exists()

    def test_close_with_monitoring_on_can_hide_to_tray(self):
        root, app = self.make(enabled=True)
        self.show(root, app)
        with mock.patch.object(self.m.messagebox, "askyesnocancel", return_value=True) as ask:
            app.on_close()
        self.assertIn("hide to the tray", ask.call_args[0][1])
        self.assertEqual(root.state(), "withdrawn")
        self.assertTrue(app.s["enabled"])

    def test_tray_state_follows_app_state(self):
        root, app = self.make()
        self.assertEqual(app.tray._state, "off")
        self.assertEqual(app.tray._tip, "Idle Shutdown: off")
        app.toggle()
        self.assertEqual(app.tray._state, "on")
        self.assertEqual(app.tray._tip, "Idle Shutdown: on, lock after 20 seconds idle")
        app.open_warning()
        self.assertEqual(app.tray._state, "warning")
        self.assertEqual(app.tray._tip, "Idle Shutdown: lock soon")
        app.close_warning("test")
        self.assertEqual(app.tray._state, "on")


def pump_safely(root, seconds, until):
    """Like pump(), but keeps going if the window gets destroyed on the way."""
    import time
    end = time.time() + seconds
    while time.time() < end:
        if until():
            return True
        try:
            root.update()
        except tk.TclError:
            pass
        time.sleep(0.02)
    return until()


if __name__ == "__main__":
    unittest.main()
