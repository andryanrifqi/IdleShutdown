"""Settings parsing, limits and file loading. No window, mouse or registry needed."""

import json
import os
import unittest

from appload import load_app, write_raw_settings, write_settings


class ParseIntTests(unittest.TestCase):
    def setUp(self):
        self.m = load_app()

    def test_valid_numbers(self):
        for text, want in [("25", 25), (" 30 ", 30), ("25.9", 25), ("-5", -5), (42, 42), ("1e3", 1000)]:
            with self.subTest(text=text):
                self.assertEqual(self.m.parse_int(text, 7), want)

    def test_invalid_input_uses_fallback(self):
        for text in ["", "abc", "nan", "inf", "-inf", "1e999", None, "12abc"]:
            with self.subTest(text=text):
                self.assertEqual(self.m.parse_int(text, 7), 7)


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.m = load_app()

    def test_clamp(self):
        self.assertEqual(self.m.clamp(10, 20, 59), 20)
        self.assertEqual(self.m.clamp(70, 20, 59), 59)
        self.assertEqual(self.m.clamp(30, 20, 59), 30)

    def test_fmt(self):
        self.assertEqual(self.m.fmt(0), "0:00")
        self.assertEqual(self.m.fmt(9.2), "0:10")  # rounds up
        self.assertEqual(self.m.fmt(65), "1:05")
        self.assertEqual(self.m.fmt(-3), "0:00")
        self.assertEqual(self.m.fmt(1440 * 60), "1440:00")


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.m = load_app()

    def test_only_seconds_and_minutes(self):
        self.assertEqual(set(self.m.UNIT_LIMITS), {"seconds", "minutes"})

    def test_unit_limits(self):
        self.assertEqual(self.m.UNIT_LIMITS["seconds"], (20, 59))
        self.assertEqual(self.m.UNIT_LIMITS["minutes"], (5, 1440))

    def test_warning_max(self):
        self.assertEqual(self.m.WARNING_MAX, 60)

    def test_actions(self):
        self.assertEqual([k for k, _ in self.m.ACTIONS], ["shutdown", "hibernate", "lock"])

    def test_settings_live_in_appdata_idleshutdown(self):
        self.assertEqual(os.path.basename(self.m.APP_DIR), "IdleShutdown")
        self.assertEqual(os.path.dirname(self.m.SETTINGS_PATH), self.m.APP_DIR)
        self.assertEqual(os.path.dirname(self.m.LOG_PATH), self.m.APP_DIR)


class LoadSettingsTests(unittest.TestCase):
    def setUp(self):
        self.m = load_app()

    def tearDown(self):
        try:
            os.remove(self.m.SETTINGS_PATH)
        except OSError:
            pass

    def test_missing_file_gives_defaults(self):
        self.tearDown()
        self.assertEqual(self.m.load_settings(), self.m.DEFAULTS)

    def test_valid_file_round_trip(self):
        s = write_settings(self.m, unit="minutes", value=90, warning=15, action="hibernate")
        self.assertEqual(self.m.load_settings(), s)

    def test_save_settings_writes_json(self):
        s = dict(self.m.DEFAULTS, value=45)
        self.m.save_settings(s)
        with open(self.m.SETTINGS_PATH, encoding="utf-8") as f:
            self.assertEqual(json.load(f), s)

    def test_hours_unit_rejected(self):
        write_settings(self.m, unit="hours", value=2)
        s = self.m.load_settings()
        self.assertEqual(s["unit"], "minutes")
        self.assertEqual(s["value"], 5)

    def test_values_clamped_per_unit(self):
        cases = [("seconds", 5, 20), ("seconds", 100, 59), ("minutes", 1, 5), ("minutes", 5000, 1440)]
        for unit, value, want in cases:
            with self.subTest(unit=unit, value=value):
                write_settings(self.m, unit=unit, value=value)
                self.assertEqual(self.m.load_settings()["value"], want)

    def test_warning_clamped(self):
        for warning, want in [(-5, 0), (999, 60), ("abc", 30)]:
            with self.subTest(warning=warning):
                write_settings(self.m, warning=warning)
                self.assertEqual(self.m.load_settings()["warning"], want)

    def test_infinity_in_file_does_not_crash(self):
        write_raw_settings(self.m, '{"unit": "minutes", "value": Infinity, "warning": -Infinity}')
        s = self.m.load_settings()
        self.assertEqual(s["value"], 5)
        self.assertEqual(s["warning"], 30)

    def test_unknown_action_falls_back(self):
        write_settings(self.m, action="reboot")
        self.assertEqual(self.m.load_settings()["action"], "shutdown")

    def test_corrupt_or_wrong_type_file(self):
        for text in ["not json", "[1, 2, 3]", "", "null"]:
            with self.subTest(text=text):
                write_raw_settings(self.m, text)
                self.assertEqual(self.m.load_settings(), self.m.DEFAULTS)

    def test_unknown_keys_ignored(self):
        write_raw_settings(self.m, '{"value": 40, "hours": 3, "unit": "seconds"}')
        s = self.m.load_settings()
        self.assertNotIn("hours", s)
        self.assertEqual(s["value"], 40)


if __name__ == "__main__":
    unittest.main()
