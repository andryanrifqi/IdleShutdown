"""
"Start with Windows" and the single-instance check.

The registry test changes your real HKCU Run key (and restores it after), so it
only runs when IDLE_TEST_REGISTRY=1 is set.
"""

import ctypes
import os
import sys
import unittest
from unittest import mock

from appload import APP_PATH, close_app, load_app, make_app


class StartupCommandTests(unittest.TestCase):
    def setUp(self):
        self.m = load_app()

    def test_script_command_uses_pythonw_and_minimized(self):
        cmd = self.m.startup_command()
        self.assertTrue(cmd.endswith(" --minimized"), cmd)
        self.assertIn(f'"{os.path.abspath(APP_PATH)}"', cmd)
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if os.path.exists(pyw):
            self.assertTrue(cmd.startswith(f'"{pyw}"'), cmd)

    def test_frozen_exe_command_points_to_exe(self):
        exe = r"C:\Apps\Idle Shutdown\IdleShutdown.exe"
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", exe):
            self.assertEqual(self.m.startup_command(), f'"{exe}" --minimized')


@unittest.skipUnless(os.environ.get("IDLE_TEST_REGISTRY") == "1",
                     "set IDLE_TEST_REGISTRY=1 to test the real HKCU Run key")
class RegistryTests(unittest.TestCase):
    def setUp(self):
        import winreg
        self.winreg = winreg
        self.m = load_app()
        self.original = self.read()
        self.addCleanup(self.restore)
        self.root, self.app = make_app(self.m)
        self.addCleanup(close_app, self.app)

    def read(self):
        try:
            with self.winreg.OpenKey(self.winreg.HKEY_CURRENT_USER, self.m.RUN_KEY) as k:
                return self.winreg.QueryValueEx(k, self.m.APP_NAME)[0]
        except OSError:
            return None

    def restore(self):
        with self.winreg.OpenKey(self.winreg.HKEY_CURRENT_USER, self.m.RUN_KEY, 0,
                                 self.winreg.KEY_SET_VALUE) as k:
            if self.original is None:
                try:
                    self.winreg.DeleteValue(k, self.m.APP_NAME)
                except FileNotFoundError:
                    pass
            else:
                self.winreg.SetValueEx(k, self.m.APP_NAME, 0, self.winreg.REG_SZ, self.original)

    def click_checkbox(self, on):
        self.app.startup_var.set(on)
        self.app.on_startup_change()

    def test_checkbox_adds_and_removes_entry(self):
        self.click_checkbox(True)
        self.assertEqual(self.read(), self.m.startup_command())
        self.assertTrue(self.m.is_startup_enabled())
        self.click_checkbox(False)
        self.assertIsNone(self.read())
        self.assertFalse(self.m.is_startup_enabled())

    def test_removing_twice_is_fine(self):
        self.click_checkbox(False)
        self.click_checkbox(False)
        self.assertIsNone(self.read())

    def test_launch_repoints_existing_entry_to_this_copy(self):
        self.m.set_startup(True)
        with self.winreg.OpenKey(self.winreg.HKEY_CURRENT_USER, self.m.RUN_KEY, 0,
                                 self.winreg.KEY_SET_VALUE) as k:
            self.winreg.SetValueEx(k, self.m.APP_NAME, 0, self.winreg.REG_SZ,
                                   r'"C:\old\IdleShutdown.exe" --minimized')
        with mock.patch.object(self.m, "App"), mock.patch.object(self.m.tk, "Tk"), \
                mock.patch.object(self.m, "kernel32") as k32:
            k32.CreateMutexW.return_value = 1
            ctypes.set_last_error(0)
            self.m.main()
        self.assertEqual(self.read(), self.m.startup_command())


class SingleInstanceTests(unittest.TestCase):
    def setUp(self):
        self.m = load_app()

    def test_second_copy_shows_message_and_exits(self):
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        handle = k32.CreateMutexW(None, False, "Local\\IdleShutdownApp")  # pretend a copy is running
        self.addCleanup(k32.CloseHandle, ctypes.c_void_p(handle))
        with mock.patch.object(self.m, "App") as app_cls, \
                mock.patch.object(self.m.tk, "Tk"), \
                mock.patch.object(self.m.messagebox, "showinfo") as showinfo, \
                mock.patch.object(self.m, "is_startup_enabled", return_value=False):
            self.m.main()
        showinfo.assert_called_once()
        self.assertIn("already running", showinfo.call_args[0][1])
        app_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
