"""
End-to-end check with the real mouse hook and real time, in Test mode.

This moves, clicks and scrolls your real mouse for about a minute, so it only
runs when IDLE_TEST_MOUSE=1 is set. Do not touch the mouse while it runs.
Clicks and scrolls land on the app's own status text, which is harmless.
"""

import ctypes
import os
import time
import unittest

from appload import close_app, load_app, make_app, pump

MOUSEEVENTF_MOVE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, MOUSEEVENTF_WHEEL = 0x1, 0x2, 0x4, 0x800


@unittest.skipUnless(os.environ.get("IDLE_TEST_MOUSE") == "1",
                     "set IDLE_TEST_MOUSE=1 to run the real mouse test (moves your mouse)")
class RealMouseTests(unittest.TestCase):
    def setUp(self):
        self.m = load_app()
        self.u32 = ctypes.windll.user32
        self.root, self.app = make_app(self.m)
        self.addCleanup(close_app, self.app)
        self.assertTrue(self.app.s["test_mode"])
        self.root.deiconify()
        self.root.geometry("+60+60")
        pump(self.root, 0.5)
        lbl = self.app.event_label
        self.u32.SetCursorPos(lbl.winfo_rootx() + 20, lbl.winfo_rooty() + lbl.winfo_height() // 2)
        pump(self.root, 0.3)
        self.app.toggle()
        self.assertTrue(self.app.hook.ok, "mouse hook was not installed")

    def move(self):
        self.u32.mouse_event(MOUSEEVENTF_MOVE, 1, 0, 0, 0)
        time.sleep(0.05)
        self.u32.mouse_event(MOUSEEVENTF_MOVE, -1, 0, 0, 0)

    def click(self):
        self.u32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.05)
        self.u32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

    def wheel(self):
        self.u32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, 120, 0)

    def wait_for_warning(self):
        self.assertTrue(pump(self.root, 15, lambda: self.app.warning_win is not None),
                        "warning did not appear")
        idle = self.m.awake_time() - self.app.hook.last_activity
        self.assertAlmostEqual(idle, 10, delta=1.0, msg=f"warning at {idle:.2f} s idle")

    def test_move_click_and_wheel_cancel_then_idle_runs_action(self):
        for name, send in [("move", self.move), ("click", self.click), ("wheel", self.wheel)]:
            with self.subTest(event=name):
                self.wait_for_warning()
                pump(self.root, 1.0)
                send()
                self.assertTrue(pump(self.root, 2, lambda: self.app.warning_win is None),
                                f"{name} did not cancel the warning")

        self.wait_for_warning()
        with self.assertLogs(level="INFO") as logs:
            self.assertTrue(pump(self.root, 12, lambda: self.app.paused_after_action))
        self.assertIn("would have run", "\n".join(logs.output))

        pump(self.root, 3)
        self.assertTrue(self.app.paused_after_action)
        self.move()
        self.assertTrue(pump(self.root, 3, lambda: not self.app.paused_after_action),
                        "real mouse activity did not restart the timer")


if __name__ == "__main__":
    unittest.main()
