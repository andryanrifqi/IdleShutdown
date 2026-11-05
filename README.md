# Idle Shutdown

A small Windows desktop app that watches for mouse inactivity and then shuts down, hibernates or locks the PC.

## How it works

- A low-level Windows mouse hook (`WH_MOUSE_LL`) reports every mouse event: movement, clicks and wheel scrolls. The cursor is not polled.
- When the mouse has been idle for the set time, a countdown warning appears. Any mouse activity or the Cancel button stops it.
- After an action runs, the app waits for real mouse activity before it starts counting again.
- The timer restarts after the PC wakes from sleep or hibernate.
- Only one copy can run at a time.

## Settings

| Setting | Range |
| --- | --- |
| Timer (Seconds) | 20 to 59 |
| Timer (Minutes) | 5 to 1440 |
| Warning countdown | 0 to 60 seconds, capped at half the timer |
| Action | Shut down, Hibernate, Lock |

Test mode only writes to the log ("Test mode: would have run ...") and does not run the action.

Settings and the log are stored in `%APPDATA%\IdleShutdown\`.

## Run from source

Requires Python 3.8 or newer on Windows. Standard library only.

```
pythonw idle_shutdown_app.pyw
pythonw idle_shutdown_app.pyw --minimized
```

## Tests

The tests use only Python's built-in `unittest`. They load the app with a temporary settings folder, so your real settings and log are never touched, and real actions are blocked.

Run the default suite from the project folder (about 5 seconds):

```
py -m unittest discover -s tests -v
```

| File | What it covers |
| --- | --- |
| `test_settings.py` | Number parsing, limits, loading bad or missing settings files |
| `test_limits.py` | Typing values and switching Seconds/Minutes in the UI, warning cap, no em dashes in the source |
| `test_flow.py` | Warning timing, cancel by move/click/wheel, Test mode action, waiting for mouse after an action, resume from sleep (uses a fake clock) |
| `test_startup.py` | Start with Windows command for the .pyw and the .exe, single-instance check |
| `test_mouse_e2e.py` | Real mouse hook end to end (opt-in) |

Two groups are skipped unless you turn them on, because they touch the real system:

```
set IDLE_TEST_REGISTRY=1
py -m unittest discover -s tests -p test_startup.py -v
```

Writes to your real `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` key and restores it afterwards.

```
set IDLE_TEST_MOUSE=1
py -m unittest discover -s tests -p test_mouse_e2e.py -v
```

Moves, clicks and scrolls your real mouse for about a minute in Test mode. Do not touch the mouse while it runs.

In PowerShell, use `$env:IDLE_TEST_MOUSE = "1"` instead of `set`. To run a single file, use `discover -p <file>` as above, since running a test file directly by path will not find the shared `appload.py` helper.

## Build the .exe

```
py -m pip install pyinstaller
build_exe.bat
```

The result is `dist\IdleShutdown.exe`. When "Start with Windows" is on, the startup entry points at whichever copy was started last, so start the .exe once after building.

## Hibernate

If Hibernate fails, it may be turned off on the PC. Check with `powercfg /a`. To turn it on, run `powercfg /hibernate on` in an administrator Command Prompt.
