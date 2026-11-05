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

## Build the .exe

```
py -m pip install pyinstaller
build_exe.bat
```

The result is `dist\IdleShutdown.exe`. When "Start with Windows" is on, the startup entry points at whichever copy was started last, so start the .exe once after building.

## Hibernate

If Hibernate fails, it may be turned off on the PC. Check with `powercfg /a`. To turn it on, run `powercfg /hibernate on` in an administrator Command Prompt.
