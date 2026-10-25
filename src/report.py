"""A diagnostic report for the developer: one zip on the Desktop, sent by the player.

Nothing leaves the PC by itself: the player sends the file. It holds what a
remote diagnosis needed on 25/10 (an older Azahar on a friend's PC, read from
photos of the menu): versions, the Azahar settings that matter, the screens,
the companion journals, Azahar's log and Windows crash reports. The Windows
user folder becomes %USERPROFILE%; Azahar's [WebService] account is never read.
Room codes and addresses stay: they are what a network diagnosis needs.
"""
import ctypes
from ctypes import wintypes
import json
import os
import platform
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
KEEP_JOURNALS = 8
AZAHAR_LINES = 4000
MAX_TEXT = 2 * 2 ** 20
TEXTURE_MISS = "Unable to find replacement for surface"
# Only what changes rendering, the companion's link or the pace of the game.
AZAHAR_SECTIONS = {"Core", "Renderer", "Layout", "Utility", "Debugging", "System", "Audio"}
AZAHAR_UI_KEYS = {"fullscreen", "pauseWhenInBackground", "singleWindowMode", "displayTitleBars", "showStatusBar"}
GRAPHICS_APIS = {"0": "logiciel", "1": "OpenGL", "2": "Vulkan"}


def anonymous(text):
    """The Windows user folder, in every spelling a log or JSON gives it."""
    home = str(Path.home())
    for form in {home, home.replace("\\", "/"), home.replace("\\", "\\\\")}:
        text = re.sub(re.escape(form), lambda _: "%USERPROFILE%", text, flags=re.IGNORECASE)
    return text


def read_text(path, limit=MAX_TEXT):
    """The end of a text file (the latest lines matter), or None."""
    try:
        with open(path, "rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - limit))
            return stream.read().decode("utf-8", "replace")
    except OSError:
        return None


def azahar_settings(text):
    """section/key=value lines of qt-config.ini worth a diagnosis."""
    rows, section = [], ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        key, sep, value = line.partition("=")
        # Per-service LLE switches and portrait layouts: dozens of lines, never the cause so far.
        if not sep or key.endswith("\\default") or key.startswith(("LLE\\", "custom_portrait")):
            continue
        if section in AZAHAR_SECTIONS or section == "UI" and key in AZAHAR_UI_KEYS:
            if section == "Renderer" and key == "graphics_api":
                value = f"{value} ({GRAPHICS_APIS.get(value, '?')})"
            rows.append(f"{section}/{key}={value}")
    return "\n".join(rows)


def azahar_log(text):
    """Azahar's log without the custom-texture misses that bury everything else."""
    lines = text.splitlines()
    kept = [line for line in lines if TEXTURE_MISS not in line]
    note = f"[{len(lines) - len(kept)} lignes « {TEXTURE_MISS} » retirées]\n" if len(kept) != len(lines) else ""
    return note + "\n".join(kept[-AZAHAR_LINES:])


def desktop():
    """The real Desktop (OneDrive may move it), else the mod's own folder."""
    try:
        buffer = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buffer) == 0 and Path(buffer.value).is_dir():
            return Path(buffer.value)
    except (AttributeError, OSError):
        pass
    return LOCAL / "rapports"


MONITOR_ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                                  wintypes.LPARAM)


def screens():
    """Each screen in real pixels with its Windows scale (the overlay follows both)."""
    # The overlay's own declarations: user32 is shared by the whole process.
    from src.overlay.window_tracker import MonitorInfo, user32
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, MONITOR_ENUM, wintypes.LPARAM]
    rows = []

    def visit(monitor, _dc, _rect, _data):
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            r = info.rcMonitor
            dpi_x, dpi_y = wintypes.UINT(), wintypes.UINT()
            try:
                scale = (f"{round(dpi_x.value / 96 * 100)} %" if ctypes.windll.shcore.GetDpiForMonitor(
                    wintypes.HANDLE(monitor), 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) == 0 else "?")
            except (AttributeError, OSError):
                scale = "?"
            rows.append(f"{info.szDevice} {r.right - r.left}x{r.bottom - r.top} à ({r.left},{r.top}) · "
                        f"échelle {scale}{' · principal' if info.dwFlags & 1 else ''}")
        return True

    previous = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    try:
        user32.EnumDisplayMonitors(None, None, MONITOR_ENUM(visit), 0)
    finally:
        if previous:
            user32.SetThreadDpiAwarenessContext(previous)
    return rows


def azahar_windows():
    """(executable, title) of each visible Azahar window: its title names the version."""
    from src.overlay.window_tracker import ENUM_CALLBACK, _process_path, user32
    found = []

    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            path = _process_path(pid.value)
            if path.endswith(("\\azahar.exe", "\\azahar-qt.exe")):
                title = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, title, 256)
                if title.value:
                    found.append((path, title.value))
        return True

    user32.EnumWindows(ENUM_CALLBACK(visit), 0)
    return found


def crashes(days=7):
    """Windows Error Reporting entries for Azahar or Python, newest first."""
    command = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
               f"Get-WinEvent -FilterHashtable @{{LogName='Application';Id=1000,1001;StartTime=(Get-Date).AddDays(-{days})}}"
               " -ErrorAction SilentlyContinue | Where-Object { $_.Message -match 'azahar|python' } |"
               " Select-Object -First 30 | ForEach-Object { '{0:yyyy-MM-dd HH:mm:ss} [{1}]' -f $_.TimeCreated,$_.Id;"
               " $_.Message; '' }")
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                                capture_output=True, timeout=25, creationflags=NO_WINDOW)
        return result.stdout.decode("utf-8", "replace").strip() or "Aucun plantage d’Azahar ou de Python sur 7 jours."
    except (OSError, subprocess.SubprocessError) as exc:
        return f"Lecture impossible : {exc}"


def summary(state, checks, emulator, game, windows):
    from src.version import VERSION
    from src.install_check import problems
    try:
        from src.launcher import development_version
        build = development_version()
    except Exception:
        build = "?"
    lines = [f"Rapport ALBW Online Co-op · {time.strftime('%d/%m/%Y %H:%M:%S')}",
             f"Mod {VERSION} · {build}",
             f"Windows {platform.version()} · Python {sys.version.split()[0]}"]
    try:
        from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
        lines[-1] += f" · PyQt {PYQT_VERSION_STR} (Qt {QT_VERSION_STR})"
    except ImportError:
        pass
    lines.append("Installation : " + ("; ".join(problems()) or "complète"))
    lines += ["", "Écrans"] + [f"  {row}" for row in safely(screens, ["illisibles"])]
    lines += ["", f"Azahar : {emulator or 'introuvable'}"]
    lines += [f"  fenêtre ouverte : {title}" for _, title in windows] or ["  aucune fenêtre ouverte"]
    if game:
        try:
            from src.local_session import identify_game
            from src.rpc.azahar_bridge import SUPPORTED_TITLE_IDS
            info = identify_game(game)
            lines.append(f"Jeu : {SUPPORTED_TITLE_IDS[info.title_id]} · {Path(game).name}")
        except (OSError, ValueError) as exc:
            lines.append(f"Jeu : {Path(game).name} · {exc}")
    if checks:
        lines += ["", "Écran d’accueil"] + [f"  {c.label} : {c.detail} [{c.state}]" for c in checks]
    if state:
        from src.overlay.coop_panel import session_summary
        lines += ["", f"Jeu lu : {state.get('detection_status')} · région {state.get('region')} · "
                      f"connecté au jeu {state.get('rpc_connected')}",
                  f"Salon : {state.get('net_mode')} · {state.get('connection_state')} · "
                  f"{len(state.get('players') or [])} joueur(s) · {state.get('connection_detail') or ''}",
                  session_summary(state)]
    return "\n".join(lines)


def safely(call, fallback):
    try:
        return call()
    except Exception:
        return fallback


def create(state=None, journal=None, checks=None, folder=None, emulator=None):
    """Write the report and return its path. state/journal: the open menu's, if any."""
    from src.local_session import discover_paths, source_user_dir
    windows = safely(azahar_windows, [])
    found_emulator, game = safely(discover_paths, ("", ""))
    emulator = emulator or (windows[0][0] if windows else found_emulator)
    user = source_user_dir(emulator) if emulator else Path(os.environ.get("APPDATA", "")) / "Azahar"
    folder = Path(folder) if folder else desktop()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"rapport-albw-coop-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    files = {"resume.txt": summary(state, checks, emulator, game, windows)}
    if journal:
        files["journal-du-menu.txt"] = "\n".join(journal)
    if state:
        files["etat-des-services.json"] = json.dumps(state, ensure_ascii=False, indent=1, default=str)
    journals = sorted((LOCAL / "logs").glob("companion-*.log"), key=lambda p: p.stat().st_mtime)[-KEEP_JOURNALS:]
    for log in journals:
        files[f"compagnon/{log.name}"] = read_text(log)
    for name in ("azahar_log.txt", "azahar_log.old.txt"):
        text = read_text(user / "log" / name)
        if text is not None:
            files[f"azahar/{name}"] = azahar_log(text)
    config = read_text(user / "config" / "qt-config.ini")
    files["azahar/reglages.txt"] = azahar_settings(config) if config is not None else "qt-config.ini introuvable"
    files["windows/plantages.txt"] = crashes()
    lives = sorted((LOCAL / "coop-lab").glob("life-*.json"), key=lambda p: p.stat().st_mtime)[-3:]
    for extra in [LOCAL / "ui.json", LOCAL / "updater.json", LOCAL / "local-launcher-error.log", *lives]:
        text = read_text(extra)
        if text:
            files[f"mod/{extra.name}"] = text
    temporary = path.with_suffix(".tmp")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in files.items():
            if text is not None:
                archive.writestr(name, anonymous(text))
    temporary.replace(path)
    return path


def reveal(path):
    """Explorer opens on the file, selected, ready to be dragged into a message."""
    try:
        subprocess.Popen(f'explorer /select,"{path}"', creationflags=NO_WINDOW)
    except OSError:
        pass


if __name__ == "__main__":
    created = create()
    print(created)
    reveal(created)
