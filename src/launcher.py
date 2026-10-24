"""Title screen of run_coop.bat: checks, then play (Azahar + ALBW + companion).

Reads Azahar's settings, the game header and the running processes. Writes only
two things, each on the player's request: Azahar's RPC switch (Azahar closed)
and the saved emulator/game choice.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from src.console import BOLD, COLOR, GOLD, GREEN, MUTED, PALE, RED, RESET, SKY, rgb

ROOT = Path(__file__).resolve().parents[1]
PREFERENCES = ROOT / ".local/ui.json"
AZAHAR_DOWNLOAD = "https://github.com/azahar-emu/azahar/releases/latest"
RPC_PORT = 45987          # Azahar's fixed RPC server port
GDB_PORT = 24689          # Azahar's default debugger port
DEFAULT_ROOM_PORT = 45990
GAME_WAIT = 90            # seconds to wait for ALBW after starting Azahar
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

TRIFORCE = ("      ▄      ", "     ███     ", "    █████    ",
            "   ▄     ▄   ", "  ███   ███  ", " █████ █████ ")
TITLE = (" █████╗ ██╗     ██████╗ ██╗    ██╗",
         "██╔══██╗██║     ██╔══██╗██║    ██║",
         "███████║██║     ██████╔╝██║ █╗ ██║",
         "██╔══██║██║     ██╔══██╗██║███╗██║",
         "██║  ██║███████╗██████╔╝╚███╔███╔╝",
         "╚═╝  ╚═╝╚══════╝╚═════╝  ╚══╝╚══╝ ")
SUBTITLE = "O N L I N E    C O - O P"
BOX = 66
ICONS = {"ok": ("✔", GREEN), "warn": ("●", GOLD), "bad": ("✖", RED), "info": ("·", MUTED)}


@dataclass
class Check:
    label: str
    state: str
    detail: str


@dataclass
class Status:
    checks: list
    emulator: str = ""
    game: str = ""
    link_ready: bool = False
    azahar_running: bool = False
    albw_running: bool = False
    version: str = ""
    friend: str = ""


def gradient(row, rows):
    """Gold, lighter at the top, like the game's title plates."""
    t = row / max(1, rows - 1)
    top, bottom = (255, 238, 170), (190, 128, 46)
    return rgb(*(round(a + (b - a) * t) for a, b in zip(top, bottom)))


def version():
    from src.version import VERSION
    return VERSION


def development_version():
    try:
        run = lambda *args: (subprocess.run(["git", *args], cwd=ROOT, capture_output=True, timeout=3,
                                            creationflags=NO_WINDOW).stdout or b"").decode("utf-8", "replace").strip()
        commit, date = run("rev-parse", "--short", "HEAD"), run("log", "-1", "--format=%cd", "--date=short")
        return f"{date} · {commit}" if commit else "version locale"
    except (OSError, subprocess.SubprocessError):
        return "version locale"


def setting(settings, key, default, kind=bool):
    """Azahar ignores a value whose '\\default' twin is true."""
    if settings.value(key + "/default", False, type=bool):
        return default
    return settings.value(key, default, type=kind)


def rpc_enabled(settings):
    return setting(settings, "Debugging/enable_rpc_server", False)


def link_ready(settings):
    """The companion reads the game over RPC and removes its renderer with the debugger.

    Azahar's debugger never pauses the game by itself: it only listens, and
    the companion connects for a fraction of a second when a hook goes away.
    Azahar answers RPC from its emulation loop, so the game must keep running
    while the companion's menu has the focus.
    """
    return (rpc_enabled(settings) and setting(settings, "Debugging/use_gdbstub", False)
            and not setting(settings, "UI/pauseWhenInBackground", False))


def gdb_port(settings):
    port = setting(settings, "Debugging/gdbstub_port", GDB_PORT, int)
    return port if 1024 <= port <= 65535 else GDB_PORT


def azahar_config(emulator):
    from src.local_session import source_user_dir
    if emulator:
        return source_user_dir(emulator) / "config/qt-config.ini"
    return Path(os.environ.get("APPDATA", "")) / "Azahar/config/qt-config.ini"


def azahar_pid():
    """PID of a running Azahar (0 if none)."""
    # tasklist writes in the console's code page, not UTF-8: read bytes.
    try:
        raw = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                             timeout=5, creationflags=NO_WINDOW).stdout or b""
    except (OSError, subprocess.SubprocessError):
        return 0
    for line in raw.decode("ascii", errors="replace").lower().splitlines():
        name, _, rest = line.partition('","')
        if name.strip('"') in ("azahar.exe", "azahar-qt.exe"):
            pid = rest.split('"', 1)[0]
            if pid.isdigit():return int(pid)
    return 0


def azahar_running():
    return azahar_pid() != 0


def albw_attached():
    from src.rpc.azahar_bridge import AzaharBridge
    bridge = AzaharBridge(port=RPC_PORT, timeout=.25, retries=1)
    try:
        return bridge.auto_attach_albw(force=True)
    finally:
        bridge.close()


def lan_address():
    """The address friends type to join; a UDP connect sends no packet."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            return probe.getsockname()[0]
    except OSError:
        return ""


def preferences():
    """The in-game menu's own preferences (.local/ui.json)."""
    try:
        data = json.loads(PREFERENCES.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def room_port():
    try:
        port = int(preferences().get("port", DEFAULT_ROOM_PORT))
        return port if 1 <= port <= 65535 else DEFAULT_ROOM_PORT
    except (ValueError, TypeError):
        return DEFAULT_ROOM_PORT


def saved_friend():
    host = str(preferences().get("host", ""))[:15]
    return f"{host}:{room_port()}" if host else ""


def save_friend(host, port):
    """Same keys as the in-game menu, so both remember the last friend."""
    data = dict(preferences(), host=host, port=port)
    PREFERENCES.parent.mkdir(parents=True, exist_ok=True)
    temporary = PREFERENCES.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(PREFERENCES)


def port_free(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as test:
            test.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False


def controllers():
    try:
        from src.overlay.gamepad import SDLGamepads
        pads = SDLGamepads()
        try:
            return [name for name, _ in pads.read().values()]
        finally:
            pads.close()
    except Exception:
        return None


def safely(check, fallback):
    """An optional check that fails must not keep the title screen from opening."""
    try:
        return check()
    except Exception:
        return fallback


def gather():
    from src.local_session import discover_paths, identify_game, settings_at
    from src.rpc.azahar_bridge import SUPPORTED_TITLE_IDS
    checks = []
    try:
        import PyQt6.QtCore as qt
        checks.append(Check("Composants", "ok", f"Python {sys.version.split()[0]} · PyQt {qt.PYQT_VERSION_STR}"))
    except ImportError:
        checks.append(Check("Composants", "bad", "PyQt6 manquant : relancez run_coop.bat"))
    emulator, game = discover_paths()
    status = Status(checks, emulator=emulator, game=game, version=version())
    if emulator and Path(emulator).is_file():
        checks.append(Check("Azahar", "ok", emulator))
    else:
        checks.append(Check("Azahar", "bad", "introuvable"))
        status.emulator = ""
    try:
        info = identify_game(game) if game else None
    except (OSError, ValueError) as exc:
        info = None
        checks.append(Check("Jeu", "bad", str(exc)))
    if info:
        region = SUPPORTED_TITLE_IDS[info.title_id].split()[0]
        checks.append(Check("Jeu", "ok", clip(f"ALBW {region} · {info.path.stem}", BOX - 19, tail=False)))
    elif not game:
        checks.append(Check("Jeu", "bad", "introuvable"))
    if not info:status.game = ""
    status.link_ready = link_ready(settings_at(azahar_config(status.emulator)))
    checks.append(Check("Liaison", "ok" if status.link_ready else "bad",
                        "serveur RPC et débogueur" if status.link_ready else "à activer (Azahar fermé)"))
    status.azahar_running = safely(azahar_running, False)
    status.albw_running = status.azahar_running and status.link_ready and safely(albw_attached, False)
    checks.append(Check("Partie", "ok" if status.albw_running else "info",
                        "ALBW ouvert" if status.albw_running else
                        "Azahar ouvert" if status.azahar_running else "Azahar fermé"))
    pads = controllers()
    checks.append(Check("Manette", "ok" if pads else "info",
                        " · ".join(pads) if pads else "aucune" if pads is not None else "illisible"))
    address, port = lan_address(), room_port()
    free = port_free(port)
    checks.append(Check("Réseau", "ok" if address and free else "warn",
                        (f"{address}:{port}" if address else "hors ligne") + ("" if free else " · port occupé")))
    status.friend = saved_friend()
    return status


def clip(text, width, tail=True):
    """Shorten to width; paths keep their end (the file), names their start."""
    if len(text) <= width:return text
    return "…" + text[-(width - 1):] if tail else text[:width - 1] + "…"


class Screen:
    def __init__(self):
        self.selected = 0
        self.message = ("", "info")
        self.status = None

    def items(self):
        s = self.status
        items = [("play", "Jouer", "Azahar, ALBW puis le compagnon"),
                 ("host", "Héberger un salon", f"Vos amis rejoignent {self.lan}" if self.lan else "Ouvre un salon"),
                 ("join", "Rejoindre un ami", f"Dernier : {s.friend}" if s and s.friend else "Teste puis rejoint son salon")]
        if s and not s.emulator:
            items.append(("download", "Télécharger Azahar", "Ouvre la page GitHub d’Azahar"))
        if s and not s.link_ready:
            items.append(("rpc", "Activer la liaison du compagnon", "Serveur RPC et débogueur d’Azahar"))
        items += [("local", "Essayer à deux sur ce PC", "Deux Azahar côte à côte"),
                  ("paths", "Choisir Azahar et le jeu", "Glisser-déposer les fichiers"),
                  ("refresh", "Vérifier à nouveau", ""),
                  ("folder", "Ouvrir le dossier du mod", ""),
                  ("quit", "Quitter", "")]
        return items

    @property
    def lan(self):
        check = next((c for c in self.status.checks if c.label == "Réseau"), None) if self.status else None
        return check.detail.split(" ")[0] if check and check.state == "ok" else ""

    def render(self):
        width = shutil.get_terminal_size((100, 40)).columns
        out = ["\033[2J\033[H" if COLOR else "\n" * 3, ""]
        art = width >= 60
        for row in range(len(TITLE)):
            left = (gradient(row, 6) + TRIFORCE[row] + RESET + "  ") if art else ""
            out.append("  " + left + gradient(row, 6) + BOLD + TITLE[row] + RESET)
        pad = " " * (len(TRIFORCE[0]) + 2 if art else 0)
        out.append(f"  {pad}{PALE}{SUBTITLE}{RESET}   {MUTED}{self.status.version}{RESET}")
        out.append("")
        title = " Prêt à jouer ? "
        out.append(f"  {GOLD}╭─{RESET}{PALE}{BOLD}{title}{RESET}{GOLD}{'─' * (BOX - 3 - len(title))}╮{RESET}")
        for check in self.status.checks:
            icon, colour = ICONS[check.state]
            detail = clip(check.detail, BOX - 19)
            out.append(f"  {GOLD}│{RESET} {colour}{icon}{RESET} {PALE}{check.label:<12}{RESET} "
                       f"{MUTED if check.state == 'info' else ''}{detail:<{BOX - 19}}{RESET} {GOLD}│{RESET}")
        out.append(f"  {GOLD}╰{'─' * (BOX - 2)}╯{RESET}")
        out.append("")
        for index, (_, name, hint) in enumerate(self.items()):
            if index == self.selected:
                out.append(f"  {GOLD}▶ {BOLD}{PALE}{name:<34}{RESET}{MUTED}{clip(hint, max(10, width - 42))}{RESET}")
            else:
                out.append(f"    {MUTED}{name}{RESET}")
        out.append("")
        out.append(f"  {MUTED}↑↓ choisir   Entrée valider   Échap quitter{RESET}")
        text, tone = self.message
        if text:
            colour = {"ok": GREEN, "bad": RED, "info": SKY}[tone]
            out.append(f"\n  {colour}{text}{RESET}")
        sys.stdout.write("\n".join(out) + "\n")
        sys.stdout.flush()

    def say(self, text, tone="info"):
        self.message = (text, tone)


def read_key():
    import msvcrt
    key = msvcrt.getwch()
    if key in ("\x00", "\xe0"):
        return {"H": "up", "P": "down"}.get(msvcrt.getwch(), "")
    return {"\r": "enter", "\x1b": "escape", "\x03": "escape"}.get(key, key)


def cursor(visible):
    if COLOR:
        sys.stdout.write("\033[?25h" if visible else "\033[?25l")
        sys.stdout.flush()


def ask(prompt):
    cursor(True)
    try:
        return input(f"  {PALE}{prompt}{RESET}").strip().strip('"').strip()
    except EOFError:
        return ""
    finally:
        cursor(False)


def background(*args):
    python = ROOT / ".venv/Scripts/pythonw.exe"
    subprocess.Popen([str(python if python.exists() else sys.executable), *args], cwd=ROOT,
                     env=dict(os.environ, PYTHONUTF8="1"), creationflags=NO_WINDOW)


def choose_paths(screen):
    from src.local_session import LAB, identify_game
    status = screen.status
    print(f"\n  {MUTED}Glissez un fichier dans cette fenêtre, ou laissez vide pour garder le choix actuel.{RESET}")
    emulator = ask("Azahar (azahar.exe) : ") or status.emulator
    if not emulator or not Path(emulator).is_file() or not Path(emulator).name.lower().startswith("azahar"):
        return screen.say("Ce fichier n’est pas azahar.exe.", "bad")
    game = ask("Jeu ALBW (.3ds, .cci, .cxi) : ") or status.game
    try:
        info = identify_game(game)
    except (OSError, ValueError) as exc:
        return screen.say(str(exc) or "Jeu illisible.", "bad")
    LAB.mkdir(parents=True, exist_ok=True)
    (LAB / "choices.json").write_text(json.dumps({"emulator": str(Path(emulator).resolve()),
                                                  "game": str(info.path)}), encoding="utf-8")
    screen.say("Azahar et le jeu sont enregistrés.", "ok")


def enable_link(screen):
    from src.local_session import _set, settings_at
    if azahar_running():
        return screen.say("Fermez Azahar d’abord : il réécrit ses réglages en quittant.", "bad")
    settings = settings_at(azahar_config(screen.status.emulator))
    port = gdb_port(settings)
    _set(settings, "Debugging/enable_rpc_server", True)
    _set(settings, "Debugging/use_gdbstub", True)
    _set(settings, "Debugging/gdbstub_port", port)
    _set(settings, "UI/pauseWhenInBackground", False)
    settings.sync()
    ok = link_ready(settings_at(azahar_config(screen.status.emulator)))
    screen.say("Liaison du compagnon activée." if ok else "Le réglage n’a pas pu être écrit.", "ok" if ok else "bad")


def wait_for_game():
    """Spinner until ALBW answers over RPC; Échap stops waiting."""
    import msvcrt
    frames = "◐◓◑◒"
    deadline = time.monotonic() + GAME_WAIT
    step = 0
    while time.monotonic() < deadline:
        if albw_attached():
            sys.stdout.write(f"\r  {GREEN}✔ ALBW est prêt.{' ' * 40}{RESET}\n")
            return True
        sys.stdout.write(f"\r  {GOLD}{frames[step % 4]}{RESET} {PALE}Ouverture d’ALBW dans Azahar…{RESET}"
                         f"  {MUTED}Échap pour ne plus attendre{RESET}")
        sys.stdout.flush()
        step += 1
        for _ in range(5):
            if msvcrt.kbhit() and msvcrt.getwch() == "\x1b":
                sys.stdout.write("\n")
                return False
            time.sleep(.1)
    sys.stdout.write(f"\n  {GOLD}ALBW ne répond pas encore : le compagnon l’attendra.{RESET}\n")
    return False


def join_friend(screen):
    """Real reachability test (PROBE), then play straight into the friend's room."""
    from src.network.probe import parse_address, probe
    print(f"\n  {MUTED}Adresse de votre ami, par exemple 192.168.1.24 ou 192.168.1.24:45990.{RESET}")
    text = ask(f"Ami{f' [{screen.status.friend}]' if screen.status.friend else ''} : ") or screen.status.friend
    target = parse_address(text, room_port())
    if target is None:
        screen.say("Adresse invalide.", "bad")
        return False
    sys.stdout.write(f"  {GOLD}◐{RESET} {PALE}Test de connexion vers {target[0]}:{target[1]}…{RESET}\n")
    sys.stdout.flush()
    answer = probe(*target)
    save_friend(*target)
    screen.status.friend = f"{target[0]}:{target[1]}"
    if answer is None:
        from src.network.probe import NO_HOST
        screen.say(NO_HOST, "bad")
        return False
    if not answer["compatible"]:
        screen.say(answer.get('detail',"Salon trouvé, mais vos versions du mod diffèrent. Mettez le mod à jour."), "bad")
        return False
    if answer["count"] is not None and answer["count"] >= answer["max"]:
        screen.say(f"Salon complet ({answer['count']}/{answer['max']}).", "bad")
        return False
    players = f" · {answer['count']}/{answer['max']} joueurs" if answer["count"] else ""
    print(f"  {GREEN}✔ Salon joignable · {answer['ms']} ms{players}{RESET}")
    shared=ask('Rejoindre avec la progression enregistrée de l’hôte ? Une copie de secours sera créée. [o/N] ')
    arguments=["--join", f"{target[0]}:{target[1]}"]
    if shared.lower() in ('o','oui'):arguments.append('--host-progress')
    return play(screen, arguments)


def companion_link(pid, status):
    """The local trial's full link for this Azahar: RPC memory, debugger, game identity.

    Without it the companion only exchanges hearts: no colour, no friends drawn,
    no shared world (its renderer needs the debugger to remove its hooks).
    """
    from src.local_session import identify_game, settings_at
    from src.overlay.window_tracker import _process_path
    emulator = _process_path(pid) or status.emulator
    try:
        from src.online_progress import running_game
        game = running_game(pid) or status.game
    except Exception:
        game = status.game
    if not game:
        raise ValueError("Jeu ALBW introuvable.")
    info = identify_game(game)
    return ["--rpc-port", str(RPC_PORT), "--gdb-port", str(gdb_port(settings_at(azahar_config(emulator)))),
            "--title-id", f"{info.title_id:016X}", "--process-name", info.process_name]


def play(screen, arguments=()):
    """Run a session tied to one Azahar, then come back to the title screen.

    The companion is a child process in this console, bound to Azahar's PID:
    closing Azahar ends it (no lone waiting window), and a fresh process
    gives every session its own Qt application. Always returns False.
    """
    status = screen.status
    pid = safely(azahar_pid, 0)
    if not pid and (not status.emulator or not status.game):
        screen.say("Choisissez d’abord Azahar et le jeu.", "bad")
        return False
    if not status.link_ready:
        # Azahar reads its settings when it starts and rewrites them when it quits.
        if pid:
            screen.say("Fermez Azahar : le mod doit activer sa liaison (serveur RPC et débogueur).", "bad")
            return False
        answer = ask("Le compagnon a besoin du serveur RPC et du débogueur d’Azahar, "
                     "et d’un jeu qui continue en arrière-plan. Les activer ? [O/n] ")
        if answer.lower() in ("n", "non"):
            screen.say("Sans cette liaison, le compagnon ne peut ni lire la partie ni afficher vos amis.", "bad")
            return False
        enable_link(screen)
        from src.local_session import settings_at
        if not link_ready(settings_at(azahar_config(status.emulator))):
            return False
        status.link_ready = True
    if not pid:
        pid = subprocess.Popen([status.emulator, status.game], cwd=str(Path(status.emulator).parent),
                               close_fds=True).pid
        print(f"\n  {GOLD}▶{RESET} {PALE}Azahar démarre avec ALBW.{RESET}")
        wait_for_game()
    try:
        link = companion_link(pid, status)
    except (OSError, ValueError) as exc:
        screen.say(f"{exc} Choisissez le jeu dans « Choisir Azahar et le jeu ».", "bad")
        return False
    cursor(True)
    print(f"\n  {MUTED}Le compagnon s’ouvre. Fermer Azahar termine la session.{RESET}")
    started = time.monotonic()
    try:
        subprocess.run([sys.executable, "-m", "src.main", *arguments, *link, "--emulator-pid", str(pid)],
                       cwd=ROOT, env=dict(os.environ, PYTHONUTF8="1"))
    except KeyboardInterrupt:
        pass
    finally:
        cursor(False)
    # The companion leaves at once when this profile is already open.
    if time.monotonic() - started < 3:
        screen.say("Un compagnon est déjà ouvert : utilisez F8 dans Azahar.", "info")
    else:
        screen.say("Session terminée.", "ok")
    return False


def run():
    if not sys.stdin or not sys.stdin.isatty():
        from src.main import main
        return main()
    os.system("title ALBW Online Co-op")
    screen = Screen()
    cursor(False)
    try:
        screen.status = gather()
        while True:
            screen.render()
            items = screen.items()
            key = read_key()
            if key == "up":screen.selected = (screen.selected - 1) % len(items)
            elif key == "down":screen.selected = (screen.selected + 1) % len(items)
            elif key.isdigit() and 1 <= int(key) <= len(items):screen.selected = int(key) - 1
            elif key == "escape":return
            elif key == "enter":
                action = items[screen.selected][0]
                screen.say("")
                if action == "quit":return
                if action == "play":
                    if play(screen):return
                elif action == "host":
                    if play(screen, ["--host-port", str(room_port())]):return
                elif action == "join":
                    if join_friend(screen):return
                elif action == "download":
                    os.startfile(AZAHAR_DOWNLOAD)
                    screen.say("Installez Azahar, puis « Choisir Azahar et le jeu ».", "info")
                elif action == "rpc":enable_link(screen)
                elif action == "local":
                    background("-m", "src.local_session")
                    screen.say("Fenêtre d’essai à deux ouverte.", "ok")
                elif action == "paths":choose_paths(screen)
                elif action == "folder":os.startfile(ROOT)
                elif action == "refresh":screen.say("Vérifications à jour.", "ok")
                if action in ("rpc", "paths", "refresh", "play", "host", "join"):
                    message = screen.message
                    screen.status = gather()
                    screen.message = message
                screen.selected = min(screen.selected, len(screen.items()) - 1)
    except KeyboardInterrupt:
        pass
    finally:
        cursor(True)
        if COLOR:sys.stdout.write(RESET)


if __name__ == "__main__":
    run()
