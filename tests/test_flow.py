"""
Timer, warning and action flow, using a fake clock.

The real mouse hook is not installed and the real mouse is not touched:
mouse events are fed straight into the hook callback. Real actions are
blocked: perform_action is replaced with one that fails the test.
These tests run in about a second.
"""

import time
import unittest
from unittest import mock

from appload import (FakeClock, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEHWHEEL, WM_MOUSEMOVE,
                     WM_MOUSEWHEEL, WM_RBUTTONDOWN, close_app, load_app, make_app)


def _fake_start(self):
    self.ok = True
    return True


def _fake_stop(self):
    self.ok = False


def _no_real_action(action):
    raise AssertionError(f"A real action ({action}) was attempted during a test.")


class FlowTestBase(unittest.TestCase):
    settings = {}

    def setUp(self):
        self.m = load_app()
        self.clock = FakeClock()
        self.cursor = (100, 100)
        patches = [
            mock.patch.object(self.m, "awake_time", self.clock),
            mock.patch.object(self.m, "get_cursor_pos", lambda: self.cursor),
            mock.patch.object(self.m.MouseHook, "start", _fake_start),
            mock.patch.object(self.m.MouseHook, "stop", _fake_stop),
            mock.patch.object(self.m, "perform_action", _no_real_action),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.root, self.app = make_app(self.m, **self.settings)
        self.addCleanup(close_app, self.app)

    # helpers
    def mouse(self, msg=WM_MOUSEMOVE):
        """Deliver one mouse event through the hook callback, like Windows would."""
        self.app.hook._callback(0, msg, 0)

    def turn_on(self):
        self.app.toggle()
        self.assertTrue(self.app.s["enabled"])

    def wait(self, seconds):
        """Let `seconds` of awake time pass, then let the app check."""
        self.clock.advance(seconds)
        if self.app.warning_win:
            self.app.warning_tick()
        else:
            self.app.check()

    def open_warning(self):
        self.turn_on()
        self.wait(self.app.limit() - self.app.warn_eff())
        self.assertIsNotNone(self.app.warning_win, "warning should be on screen")

    def reach_action(self):
        self.open_warning()
        with self.assertLogs(level="INFO") as logs:
            self.wait(self.app.warn_eff())
        self.assertIn("Test mode: would have run 'Lock' now.", "\n".join(logs.output))
        self.assertTrue(self.app.paused_after_action)

    def simulate_resume_from_sleep(self, slept_seconds=120):
        self.app._last_wall = time.time() - slept_seconds
        self.app._last_awake = self.clock()
        self.app.status_loop()


class WarningTimingTests(FlowTestBase):
    def test_no_warning_before_due(self):
        self.turn_on()
        self.wait(9.5)
        self.assertIsNone(self.app.warning_win)

    def test_warning_appears_at_half_of_20_second_timer(self):
        self.turn_on()
        self.wait(10)
        self.assertIsNotNone(self.app.warning_win)
        self.assertEqual(self.app.warning_msg.cget("text"), "Lock in 10 seconds")
        self.assertEqual(self.app.status_label.cget("text"), "Warning countdown is on screen.")

    def test_countdown_counts_down(self):
        self.open_warning()
        self.wait(3.5)
        self.assertEqual(self.app.warning_msg.cget("text"), "Lock in 7 seconds")

    def test_status_text_while_idle(self):
        self.turn_on()
        self.clock.advance(5)
        self.app.update_status()
        self.assertEqual(self.app.status_label.cget("text"), "Idle 0:05.  Lock in 0:15.")

    def test_activity_before_due_pushes_warning_back(self):
        self.turn_on()
        self.clock.advance(8)
        self.mouse()
        self.wait(2)  # 10 s since start, but only 2 s since activity
        self.assertIsNone(self.app.warning_win)
        self.wait(8)
        self.assertIsNotNone(self.app.warning_win)


class CancelTests(FlowTestBase):
    def test_each_mouse_event_cancels_the_warning(self):
        events = {"move": WM_MOUSEMOVE, "left click": WM_LBUTTONDOWN, "left release": WM_LBUTTONUP,
                  "right click": WM_RBUTTONDOWN, "wheel": WM_MOUSEWHEEL, "horizontal wheel": WM_MOUSEHWHEEL}
        self.turn_on()
        for name, msg in events.items():
            with self.subTest(event=name):
                self.wait(self.app.limit() - self.app.warn_eff())
                self.assertIsNotNone(self.app.warning_win)
                self.clock.advance(2)
                self.mouse(msg)
                with self.assertLogs(level="INFO") as logs:
                    self.app.warning_tick()
                self.assertIsNone(self.app.warning_win)
                self.assertIn("Cancelled by mouse activity.", "\n".join(logs.output))

    def test_timer_restarts_after_cancel(self):
        self.open_warning()
        self.clock.advance(2)
        self.mouse()
        self.app.warning_tick()
        self.wait(9.5)
        self.assertIsNone(self.app.warning_win)
        self.wait(0.5)
        self.assertIsNotNone(self.app.warning_win)

    def test_cancel_button_closes_warning(self):
        self.open_warning()
        self.app.close_warning("Cancelled from the warning window.")
        self.assertIsNone(self.app.warning_win)

    def test_touch_alone_does_not_cancel_warning(self):
        self.open_warning()
        self.clock.advance(1)
        self.app.hook.touch()
        self.app.warning_tick()
        self.assertIsNotNone(self.app.warning_win)


class ActionTests(FlowTestBase):
    def test_idle_logs_would_have_run_and_pauses(self):
        self.reach_action()
        self.assertIsNone(self.app.warning_win)
        self.assertEqual(self.app.status_label.cget("text"),
                         "Waiting for mouse activity after the last action.")

    def test_action_runs_at_full_timer(self):
        self.open_warning()
        self.wait(9.5)
        self.assertFalse(self.app.paused_after_action)
        self.wait(0.5)
        self.assertTrue(self.app.paused_after_action)

    def test_paused_does_not_count_again_without_mouse(self):
        self.reach_action()
        for _ in range(5):
            self.wait(30)
            self.assertTrue(self.app.paused_after_action)
            self.assertIsNone(self.app.warning_win)

    def test_touch_does_not_end_pause(self):
        self.reach_action()
        self.clock.advance(5)
        self.app.hook.touch()
        self.wait(1)
        self.assertTrue(self.app.paused_after_action)

    def test_real_mouse_activity_ends_pause(self):
        self.reach_action()
        self.clock.advance(60)
        self.mouse()
        with self.assertLogs(level="INFO") as logs:
            self.wait(1)
        self.assertFalse(self.app.paused_after_action)
        self.assertIn("Mouse activity detected, timer restarted.", "\n".join(logs.output))
        self.wait(9)  # 10 s after activity: warning again
        self.assertIsNotNone(self.app.warning_win)


class ZeroWarningTests(FlowTestBase):
    settings = {"warning": 0}

    def test_no_warning_window_action_at_timer(self):
        self.turn_on()
        self.wait(19.5)
        self.assertFalse(self.app.paused_after_action)
        with self.assertLogs(level="INFO") as logs:
            self.wait(0.5)
        self.assertIsNone(self.app.warning_win)
        self.assertTrue(self.app.paused_after_action)
        self.assertIn("would have run", "\n".join(logs.output))


class ResumeTests(FlowTestBase):
    def test_resume_restarts_timer(self):
        self.turn_on()
        self.clock.advance(8)
        with self.assertLogs(level="INFO") as logs:
            self.simulate_resume_from_sleep()
        self.assertIn("Resumed from sleep or hibernate, timer restarted.", "\n".join(logs.output))
        self.wait(9.5)
        self.assertIsNone(self.app.warning_win)
        self.wait(0.5)
        self.assertIsNotNone(self.app.warning_win)

    def test_resume_closes_open_warning(self):
        self.open_warning()
        self.simulate_resume_from_sleep()
        self.assertIsNone(self.app.warning_win)
        self.assertFalse(self.app.paused_after_action)

    def test_resume_after_action_keeps_waiting_for_mouse(self):
        self.reach_action()
        with self.assertLogs(level="INFO") as logs:
            self.simulate_resume_from_sleep()
        self.assertIn("still waiting for mouse activity", "\n".join(logs.output))
        self.wait(60)
        self.assertTrue(self.app.paused_after_action)
        self.assertIsNone(self.app.warning_win)

    def test_short_gap_is_not_a_resume(self):
        self.turn_on()
        self.clock.advance(8)
        self.simulate_resume_from_sleep(slept_seconds=5)
        self.wait(2)
        self.assertIsNotNone(self.app.warning_win)


class OnOffTests(FlowTestBase):
    def test_off_by_default_never_warns(self):
        self.wait(1000)
        self.assertIsNone(self.app.warning_win)
        self.assertEqual(self.app.status_label.cget("text"), "Monitoring is off.")

    def test_turning_off_closes_warning(self):
        self.open_warning()
        self.app.toggle()
        self.assertFalse(self.app.s["enabled"])
        self.assertIsNone(self.app.warning_win)
        self.assertFalse(self.app.hook.ok)
        self.wait(1000)
        self.assertIsNone(self.app.warning_win)

    def test_turning_on_clears_pause(self):
        self.reach_action()
        self.app.toggle()
        self.app.toggle()
        self.assertFalse(self.app.paused_after_action)


class SafetyNetTests(FlowTestBase):
    def test_cursor_moved_without_hook_event_reinstalls_hook(self):
        self.turn_on()
        self.cursor = (300, 200)  # cursor moved, but the hook saw nothing
        with mock.patch.object(self.m.MouseHook, "restart", autospec=True) as restart:
            self.wait(10)
        restart.assert_called_once()
        self.assertIsNone(self.app.warning_win)


if __name__ == "__main__":
    unittest.main()
