"""Terminal output: Windows ANSI colours, the session header and live events."""
import ctypes
import sys


def _supports_color():
    if not sys.stdout or not sys.stdout.isatty():
        return False
    try:
        from ctypes import wintypes
        api = ctypes.windll.kernel32
        api.GetStdHandle.argtypes = [wintypes.DWORD]
        api.GetStdHandle.restype = wintypes.HANDLE
        api.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        api.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        handle = api.GetStdHandle(-11 & 0xFFFFFFFF)
        mode = wintypes.DWORD()
        return bool(api.GetConsoleMode(handle, ctypes.byref(mode)) and api.SetConsoleMode(handle, mode.value | 4))
    except (AttributeError, OSError):
        return False


COLOR = _supports_color()

# Message tones, shared with the in-game journal.
PROBLEM_WORDS = ("erreur", "impossible", "interromp", "échec", "refus", "incompatible", "non confirm",
                 "indisponible", "ne répond", "sautée", "non affich", "inattendu", "invalide", "perdu")
GOOD_WORDS = ("a rejoint", "connecté", "prête", "prêt ", "activé", "actif", "ouvert", "reconnecté")


def log_tone(message):
    text = message.lower()
    if any(word in text for word in PROBLEM_WORDS):return "problem"
    if any(word in text for word in GOOD_WORDS):return "good"
    return "plain"


def rgb(r, g, b):
    return f"\033[38;2;{r};{g};{b}m" if COLOR else ""


RESET = "\033[0m" if COLOR else ""
DIM = "\033[2m" if COLOR else ""
BOLD = "\033[1m" if COLOR else ""
GOLD, PALE, MUTED = rgb(232, 196, 110), rgb(255, 236, 178), rgb(168, 138, 92)
GREEN, RED, SKY = rgb(160, 205, 130), rgb(255, 150, 120), rgb(140, 190, 225)
TONES = {"plain": ("·", PALE), "good": ("●", GREEN), "problem": ("▲", RED)}


def _write(text):
    if sys.stdout:
        try:
            print(text, flush=True)
        except (OSError, UnicodeEncodeError):
            pass


def print_banner(preview=False):
    rule = "─" * 14
    _write(f"\n  {GOLD}{rule}{RESET}  {PALE}{BOLD}Session{RESET}  {GOLD}{rule}{RESET}")
    _write(f"  {MUTED}{'Aperçu visuel, aucune connexion.' if preview else 'Menu dans Azahar :'}"
           f"{'' if preview else '  F8  ·  Échap pour revenir au jeu'}{RESET}\n")


def where(exc, frames=3):
    """Where an exception came from in the mod's own code: file:line function → …"""
    import traceback
    from pathlib import Path
    source = Path(__file__).resolve().parent
    stack = traceback.extract_tb(exc.__traceback__)
    own = [f for f in stack if Path(f.filename).resolve().is_relative_to(source)] or stack
    return f"{type(exc).__name__} · " + " → ".join(f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in own[-frames:])


def print_event(stamp, message, detail=()):
    mark, colour = TONES[log_tone(message)]
    _write(f"  {MUTED}{stamp}{RESET}  {colour}{mark} {message}{RESET}")
    for line in detail:
        _write(f"  {' ' * len(stamp)}    {MUTED}{line}{RESET}")
