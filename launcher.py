"""Start VU3INI Climate Studio: local server on a free port, open the browser, tray icon to quit.

    python launcher.py            normal start (console hidden; errors shown in a message box)
    python  launcher.py --debug   console stays open and shows all server output
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEBUG = "--debug" in sys.argv or "--no-tray" in sys.argv
WINDOWS = sys.platform.startswith("win")


def log_dir() -> Path:
    try:
        sys.path.insert(0, str(ROOT))
        from core import paths

        return paths.logs_dir()
    except Exception:
        d = ROOT / "logs"
        d.mkdir(exist_ok=True)
        return d


LOG = log_dir() / "launcher.log"
SERVER_LOG = log_dir() / "server.log"


def note(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    if sys.stdout is not None:
        print(line, flush=True)


def console(show: bool) -> None:
    """Hide or show this launcher's own console window (normal start uses python.exe, minimised)."""
    if not WINDOWS or DEBUG:
        return
    try:
        import ctypes

        h = ctypes.windll.kernel32.GetConsoleWindow()
        if h:
            ctypes.windll.user32.ShowWindow(h, 5 if show else 0)
    except Exception:
        pass


def message_box(title: str, text: str) -> None:
    console(True)
    if DEBUG or not WINDOWS:
        note(f"{title}: {text}")
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)  # MB_ICONERROR
    except Exception:
        pass


def tail(path: Path, n: int = 25) -> str:
    try:
        return "".join(path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)[-n:])
    except Exception:
        return "(no log)"


def free_port(start: int = 8765) -> int:
    for p in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    raise RuntimeError("No free port found between 8765 and 8815.")


def console_python() -> str:
    """python.exe next to pythonw.exe: the server needs real stdout/stderr even when hidden."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
        return str(exe.parent / "python.exe")
    return str(exe)


def wait_until_up(url: str, proc: subprocess.Popen, seconds: int = 240) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never send localhost through a proxy
    end = time.time() + seconds
    while time.time() < end:
        if proc.poll() is not None:
            return False  # server exited
        try:
            opener.open(url, timeout=3)
            return True
        except Exception:
            time.sleep(1)
    return False


_MUTEX = None


def hold_mutex() -> None:
    """Named mutex so the installer/uninstaller can ask the user to close the app first."""
    global _MUTEX
    if WINDOWS:
        try:
            import ctypes

            _MUTEX = ctypes.windll.kernel32.CreateMutexW(None, False, "VU3INI_Climate_Studio_Mutex")
        except Exception:
            pass


def main() -> int:
    console(False)
    hold_mutex()
    note(f"Launcher starting with {sys.executable}")
    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    env = {**os.environ, "PYTHONPATH": str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""),
           "SOLARA_TELEMETRY_MIXPANEL_ENABLE": "False", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    cmd = [console_python(), "-m", "solara", "run", str(ROOT / "app" / "main.py"), "--host", "127.0.0.1",
           "--port", str(port), "--production", "--no-open"]
    note("Server command: " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
    if DEBUG:
        proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env)
    else:
        out = open(SERVER_LOG, "w", encoding="utf-8")
        flags = subprocess.CREATE_NO_WINDOW if WINDOWS else 0
        proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env, stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, creationflags=flags)
    note(f"Waiting for {url} (the first start can take a minute)…")
    if not wait_until_up(url, proc):
        code = proc.poll()
        if code is None:
            proc.terminate()
        message_box("VU3INI Climate Studio could not start",
                    f"The app server {'stopped (code ' + str(code) + ')' if code is not None else 'did not answer in time'}.\n\n"
                    f"Last lines of {SERVER_LOG}:\n\n{tail(SERVER_LOG)}\n\n"
                    "Tip: run 'VU3INI Climate Studio (debug).bat' to see everything, and send the text to the developer.")
        return 1
    note("Server is up; opening the browser")
    webbrowser.open(url)

    if DEBUG:
        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
        return 0
    try:
        import pystray
        from PIL import Image

        icon_img = Image.open(ROOT / "assets" / "vu3ini_logo.png")

        def on_open(_icon, _item):
            webbrowser.open(url)

        def on_quit(icon, _item):
            proc.terminate()
            icon.stop()

        icon = pystray.Icon("VU3INI Climate Studio", icon_img, "VU3INI Climate Studio",
                            menu=pystray.Menu(pystray.MenuItem("Open", on_open, default=True), pystray.MenuItem("Quit", on_quit)))
        icon.run()
    except Exception:
        note("Tray icon unavailable:\n" + traceback.format_exc())
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.terminate()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        err = traceback.format_exc()
        note("Launcher crashed:\n" + err)
        message_box("VU3INI Climate Studio could not start", err[-1500:])
        sys.exit(1)
