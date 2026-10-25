"""Is this copy of the mod complete? Shown on the title screen and in the journal.

A player's copy holds no development tool: every module the mod imports is
listed here and in requirements.txt (scripts/verify_install keeps both true;
25/10: keystone, a build tool, stopped every frame on a friend's PC). Every
native binary is the one its build produced (binaries.json, written by
scripts/build_remote_model.py); the updater reinstalls a ZIP copy whose files
differ. run_coop.bat runs this module: exit code 1 when a module is missing,
so it reinstalls the requirements.
"""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "src" / "memory"
MANIFEST = NATIVE / "binaries.json"
# Import name -> its package in requirements.txt, for every third-party import in src/.
RUNTIME_MODULES = {"PyQt6": "PyQt6", "pygame": "pygame-ce", "pythoncom": "pywin32", "win32com": "pywin32",
                   "win32con": "pywin32", "win32gui": "pywin32", "win32process": "pywin32"}


def missing_modules():
    """Packages to install, without importing anything."""
    missing = []
    for module, package in RUNTIME_MODULES.items():
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            found = False
        if not found and package not in missing:
            missing.append(package)
    return missing


def digests(folder=NATIVE):
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(folder.glob("*.bin"))}


def write_manifest(folder=NATIVE):
    (folder / MANIFEST.name).write_text(json.dumps(digests(folder), indent=1) + "\n", encoding="utf-8")


def damaged_binaries(folder=NATIVE):
    """Native files missing or different from the build: a partial update or copy.
    A copy older than the manifest has nothing to compare with."""
    try:
        expected = json.loads((folder / MANIFEST.name).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        return [MANIFEST.name]
    if not isinstance(expected, dict):
        return [MANIFEST.name]
    damaged = []
    for name, digest in expected.items():
        try:
            if hashlib.sha256((folder / name).read_bytes()).hexdigest() != digest:
                damaged.append(name)
        except OSError:
            damaged.append(name)
    return sorted(damaged)


def problems():
    """Readable lines for the journal; empty when the copy is complete."""
    lines = []
    missing = missing_modules()
    if missing:
        lines.append("Composants manquants : " + ", ".join(missing) + ". Relancez run_coop.bat.")
    damaged = damaged_binaries()
    if damaged:
        lines.append(f"Fichiers natifs abîmés ou manquants ({len(damaged)}) : " + ", ".join(damaged[:6])
                     + (" …" if len(damaged) > 6 else "") + ". Relancez run_coop.bat : il propose de les réparer.")
    return lines


if __name__ == "__main__":
    sys.exit(1 if missing_modules() else 0)
