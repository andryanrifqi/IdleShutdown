"""Timer and warning limits in the UI: typing values and switching units."""

import json
import unittest

from appload import close_app, load_app, make_app


class LimitsTests(unittest.TestCase):
    def setUp(self):
        self.m = load_app()
        self.root, self.app = make_app(self.m)

    def tearDown(self):
        close_app(self.app)

    # helpers
    def type_value(self, text):
        self.app.value_var.set(text)
        self.app.commit_value()
        return self.app.s["value"]

    def set_unit(self, unit):
        self.app.unit_var.set(unit)
        self.app.on_unit_change()
        return self.app.s["value"]

    def type_warning(self, text):
        self.app.warning_var.set(text)
        self.app.commit_warning()
        return self.app.s["warning"]

    def spin_range(self):
        return float(self.app.value_spin.cget("from")), float(self.app.value_spin.cget("to"))

    # typing values
    def test_typed_seconds_are_clamped(self):
        cases = [("10", 20), ("19", 20), ("20", 20), ("45", 45), ("59", 59), ("60", 59),
                 ("70", 59), ("-5", 20), ("25.9", 25)]
        for text, want in cases:
            with self.subTest(text=text):
                self.assertEqual(self.type_value(text), want)
                self.assertEqual(self.app.value_var.get(), str(want))

    def test_typed_minutes_are_clamped(self):
        self.set_unit("minutes")
        cases = [("2", 5), ("4", 5), ("5", 5), ("90", 90), ("1440", 1440), ("1441", 1440), ("2000", 1440)]
        for text, want in cases:
            with self.subTest(text=text):
                self.assertEqual(self.type_value(text), want)
                self.assertEqual(self.app.value_var.get(), str(want))

    def test_bad_text_keeps_previous_value(self):
        self.type_value("45")
        for text in ["abc", "", "inf", "1e999", "nan"]:
            with self.subTest(text=text):
                self.assertEqual(self.type_value(text), 45)
                self.assertEqual(self.app.value_var.get(), "45")

    # switching units
    def test_switch_seconds_to_minutes_keeps_number_in_range(self):
        self.type_value("45")
        self.assertEqual(self.set_unit("minutes"), 45)
        self.assertEqual(self.spin_range(), (5, 1440))

    def test_switch_minutes_to_seconds_clamps_high(self):
        self.set_unit("minutes")
        self.type_value("1000")
        self.assertEqual(self.set_unit("seconds"), 59)
        self.assertEqual(self.spin_range(), (20, 59))

    def test_switch_minutes_to_seconds_clamps_low(self):
        self.set_unit("minutes")
        self.type_value("5")
        self.assertEqual(self.set_unit("seconds"), 20)

    def test_switch_uses_uncommitted_typed_value(self):
        self.set_unit("minutes")
        self.type_value("30")
        self.app.value_var.set("3000")  # typed but not committed
        self.assertEqual(self.set_unit("seconds"), 59)
        self.app.value_var.set("3")  # typed but not committed
        self.assertEqual(self.set_unit("minutes"), 5)

    def test_switch_to_same_unit_does_nothing(self):
        self.type_value("30")
        self.assertEqual(self.set_unit("seconds"), 30)

    def test_range_hint(self):
        self.assertEqual(self.app.range_hint.cget("text"), "Allowed: 20 to 59 seconds")
        self.set_unit("minutes")
        self.assertEqual(self.app.range_hint.cget("text"), "Allowed: 5 to 1440 minutes")

    # warning
    def test_typed_warning_is_clamped(self):
        for text, want in [("100", 60), ("60", 60), ("-3", 0), ("0", 0), ("30", 30)]:
            with self.subTest(text=text):
                self.assertEqual(self.type_warning(text), want)

    def test_bad_warning_text_keeps_previous(self):
        self.type_warning("15")
        self.assertEqual(self.type_warning("x"), 15)

    def test_warning_capped_at_half_the_timer(self):
        self.type_warning("60")
        cases = [("seconds", "20", 10), ("seconds", "21", 10), ("seconds", "59", 29),
                 ("minutes", "5", 60), ("minutes", "1440", 60)]
        for unit, value, want in cases:
            with self.subTest(unit=unit, value=value):
                self.set_unit(unit)
                self.type_value(value)
                self.assertEqual(self.app.warn_eff(), want)

    def test_warning_hint_texts(self):
        self.type_warning("60")  # 20 s timer: capped to 10
        self.assertIn("Capped at 10 s", self.app.warning_hint.cget("text"))
        self.type_warning("5")
        self.assertIn("Countdown appears 5 s before lock", self.app.warning_hint.cget("text"))
        self.type_warning("0")
        self.assertIn("No warning", self.app.warning_hint.cget("text"))

    # saving
    def test_changes_are_saved(self):
        self.set_unit("minutes")
        self.type_value("90")
        self.type_warning("15")
        with open(self.m.SETTINGS_PATH, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual((saved["unit"], saved["value"], saved["warning"]), ("minutes", 90, 15))


class NoEmDashTests(unittest.TestCase):
    """Project rule: no em dashes in comments, UI text or messages."""

    def test_project_files_have_no_em_or_en_dashes(self):
        import glob
        import os
        from appload import ROOT
        dashes = (chr(0x2014), chr(0x2013))  # em dash, en dash
        files = [os.path.join(ROOT, "idle_shutdown_app.pyw"), os.path.join(ROOT, "README.md"),
                 os.path.join(ROOT, "build_exe.bat")] + glob.glob(os.path.join(ROOT, "tests", "*.py"))
        for path in files:
            with self.subTest(file=os.path.basename(path)):
                with open(path, encoding="utf-8") as f:
                    text = f.read()
                for dash in dashes:
                    self.assertNotIn(dash, text)


if __name__ == "__main__":
    unittest.main()
