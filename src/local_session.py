"""Prepare two portable Azahar instances and independent companion profiles.

Original emulator data is only read. Each test save is seeded once, never linked.
ROM headers are interpreted according to Azahar's NCCH/NCSD structures.
"""
import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import time

from PyQt6.QtCore import QLockFile, QRectF, QSettings, Qt, QTimer, QUrl
from PyQt6.QtGui import QColor, QDesktopServices, QPainter
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication, QCheckBox, QFileDialog, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget
from src.overlay.coop_panel import button, label
from src.overlay.albw_ui_components import ALBWAssetStore
from src.overlay.menu_widgets import (GOLD, INK, MENU_STYLE, MUTED, TUNICS, WoodPanel, draw_hearts,
                                     draw_wood_panel, font)
from src.rpc.azahar_bridge import SUPPORTED_TITLE_IDS
from src.rpc.portable_rpc import prepare_port, configured_port

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / ".local" / "coop-lab"


@dataclass(frozen=True)
class GameInfo:
    path: Path
    title_id: int
    process_name: str


def identify_game(path):
    path = Path(path).resolve(strict=True)
    with path.open("rb") as stream:
        header = stream.read(512)
        if len(header) != 512:
            raise ValueError("Le fichier du jeu est incomplet.")
        offset = 0
        if header[0x100:0x104] == b"NCSD":
            offset = struct.unpack_from("<I", header, 0x120)[0] * 512
            stream.seek(offset)
            header = stream.read(512)
        if len(header) != 512 or header[0x100:0x104] != b"NCCH":
            raise ValueError("Choisissez ALBW au format .3ds, .cci, .cxi ou .app déchiffré.")
        title = struct.unpack_from("<Q", header, 0x118)[0]
        if title not in SUPPORTED_TITLE_IDS:
            raise ValueError("Ce fichier n’est pas une version EUR ou USA reconnue d’ALBW.")
        if not header[0x18F] & 4:
            raise ValueError("L’essai local nécessite une copie déchiffrée du jeu.")
        stream.seek(offset + 512)
        name = stream.read(8).split(b"\0", 1)[0]
        if not name or any(c < 32 or c > 126 for c in name):
            raise ValueError("Le nom du programme du jeu est illisible.")
    return GameInfo(path, title, name.decode("ascii"))


def settings_at(path):
    return QSettings(str(path), QSettings.Format.IniFormat)


def source_user_dir(emulator):
    portable = Path(emulator).parent / "user"
    return portable if portable.is_dir() else Path(os.environ["APPDATA"]) / "Azahar"


def discover_paths():
    """Use our saved choices, then Azahar's recent games and nearby installations."""
    try:
        choices = json.loads((LAB / "choices.json").read_text(encoding="utf-8"))
        if isinstance(choices, dict) and all(Path(choices.get(k, "")).is_file() for k in ("emulator", "game")):
            return choices["emulator"], choices["game"]
    except (OSError, ValueError, TypeError):
        pass
    config = Path(os.environ.get("APPDATA", "")) / "Azahar/config/qt-config.ini"
    recent = settings_at(config).value("UI/Paths/recentFiles", [])
    if isinstance(recent, str):
        recent = [recent]
    game = ""
    for candidate in recent or []:
        try:
            game = str(identify_game(candidate).path)
            break
        except (OSError, ValueError):
            continue
    candidates = []
    if game:
        candidates.extend(p / "azahar.exe" for p in Path(game).parents)
    candidates.extend(Path.home().joinpath("Downloads").glob("azahar*/azahar.exe"))
    emulator = next((str(p) for p in candidates if p.is_file()), "")
    return emulator, game


def _set(config, key, value):
    config.setValue(key, value)
    config.setValue(key + "/default", False)


def prepare_instance(emulator, game, index, port, lab=LAB, keyboard=False, rpc_port=None):
    emulator, lab = Path(emulator).resolve(strict=True), Path(lab).resolve()
    if emulator.name.lower() not in ("azahar.exe", "azahar-qt.exe"):
        raise ValueError("Choisissez l’exécutable Azahar.")
    folder = lab / f"player{index}"
    if emulator.is_relative_to(lab):
        raise ValueError("Choisissez votre installation Azahar d’origine.")
    folder.mkdir(parents=True, exist_ok=True)
    user = folder / "user"
    user.mkdir(exist_ok=True)
    source = source_user_dir(emulator)
    original_config = source / "config/qt-config.ini"
    config_path = user / "config/qt-config.ini"
    config_path.parent.mkdir(exist_ok=True)
    if not config_path.exists() and original_config.is_file():
        shutil.copy2(original_config, config_path)
    # DLLs and Qt plugins are loaded from the original installation. Only the EXE
    # is copied, so Azahar selects a private portable user/ directory.
    target_exe = folder / emulator.name
    if not target_exe.exists() or target_exe.stat().st_mtime_ns != emulator.stat().st_mtime_ns:
        shutil.copy2(emulator, target_exe)
    fast_rpc=False
    if rpc_port is not None:
        try:
            prepare_port(target_exe,rpc_port,lab)
            fast_rpc=True
        except ValueError:
            pass  # Other emulator builds keep the existing GDB-only transport.
    (folder / "qt.conf").write_text(f"[Paths]\nPrefix={emulator.parent.as_posix()}\nPlugins=plugins\n", encoding="utf-8")
    source_settings = settings_at(original_config)
    custom = source_settings.value("Data Storage/use_custom_storage", False, type=bool)
    sdmc = Path(source_settings.value("Data Storage/sdmc_directory", str(source / "sdmc"))) if custom else source / "sdmc"
    nand = Path(source_settings.value("Data Storage/nand_directory", str(source / "nand"))) if custom else source / "nand"
    for name, origin in (("sysdata", source / "sysdata"), ("nand", nand)):
        dest = user / name
        if not dest.exists() and origin.is_dir():
            shutil.copytree(origin, dest)
    # Copy only this game's save archive, not the whole SD card or installed titles.
    seed_marker = user / f"save-seeded-{game.title_id:016x}.json"
    copied = []
    if not seed_marker.exists():
        relative = f"Nintendo 3DS/*/*/title/{game.title_id >> 32:08x}/{game.title_id & 0xffffffff:08x}/data"
        for archive in sdmc.glob(relative):
            dest = user / "sdmc" / archive.relative_to(sdmc)
            if not dest.exists():
                shutil.copytree(archive, dest)
                copied.append(str(archive))
        seed_marker.write_text(json.dumps({"copied_from": copied}, indent=2), encoding="utf-8")
    config = settings_at(config_path)
    if keyboard:
        prepare_keyboard_profile(config)
    for key, value in {
        "Debugging/enable_rpc_server": fast_rpc,
        "Debugging/use_gdbstub": True,
        "Debugging/gdbstub_port": port,
        "Data Storage/use_custom_storage": False,
        "Data Storage/nand_directory": str(user / "nand"),
        "Data Storage/sdmc_directory": str(user / "sdmc"),
        "UI/pauseWhenInBackground": False,
        "UI/fullscreen": False,
        "UI/firstStart": False,
        "UI/singleWindowMode": True,
        "UI/Paths/screenshotPath": str(user / "screenshots"),
        "Renderer/resolution_factor": 1,
        "Utility/custom_textures": False,
        "Utility/preload_textures": False,
        "Audio/volume": 1.0 if index == 1 else 0.0,
    }.items():
        _set(config, key, value)
    config.setValue("UI/Paths/recentFiles", [str(game.path)])
    # Start at a manageable window size; never inherit the user's fullscreen state.
    config.remove("UI/UILayout/geometry")
    config.remove("UI/UILayout/state")
    app = QApplication.instance()
    if app and app.primaryScreen():
        from PyQt6.QtWidgets import QMainWindow
        bounds = app.primaryScreen().availableGeometry()
        geometry = QMainWindow()
        width, height = max(480, bounds.width() // 2 - 16), min(720, bounds.height() - 72)
        geometry.setGeometry(bounds.x() + 8 + (index - 1) * (width + 16), bounds.y() + 32, width, height)
        config.setValue("UI/UILayout/geometry", geometry.saveGeometry())
    config.sync()
    if config.status() != QSettings.Status.NoError:
        raise OSError("Impossible d’enregistrer les réglages de l’instance locale.")
    (folder / "prepared.json").write_text(json.dumps({"source": str(emulator), "game": str(game.path),
        "title_id": f"{game.title_id:016X}", "gdb_port": port}, indent=2), encoding="utf-8")
    return target_exe


def prepare_keyboard_profile(config):
    """Give player 2 a dedicated keyboard map; one gamepad must not drive both."""
    index = config.value("ALBWCoop/keyboard_profile", 0, type=int)
    if not index:
        index = config.value("Controls/profiles/size", 0, type=int) + 1
        config.setValue("Controls/profiles/size", index)
        config.setValue("ALBWCoop/keyboard_profile", index)
        prefix = f"Controls/profiles/{index}/"
        _set(config, prefix + "name", "Coop · Clavier")
        _set(config, prefix + "input_maptype", 2)
        keys = dict(a=65, b=66, x=88, y=89, start=16777220, select=16777219,
                    up=16777235, down=16777237, left=16777234, right=16777236,
                    l=69, r=82, zl=49, zr=50, home=72, power=86, debug=79, gpio14=80)
        for name, code in keys.items():
            _set(config, prefix + "button_" + name, f"code:{code},engine:keyboard")
        for control, mapping in (("circle_pad", dict(up=90, down=83, left=81, right=68)),
                                 ("c_stick", dict(up=73, down=75, left=74, right=76))):
            value = "engine:analog_from_button,modifier_scale:0.500000," + ",".join(
                f"{direction}:code$0{code}$1engine$0keyboard" for direction, code in mapping.items())
            _set(config, prefix + control, value)
        _set(config, prefix + "touch_device", "engine:emu_window")
        _set(config, prefix + "motion_device", "engine:motion_emu,sensitivity:0.010000")
        _set(config, prefix + "use_touchpad", False)
        _set(config, prefix + "use_touch_from_button", False)
    _set(config, "Controls/profile", index - 1)


def reserve_ports():
    reservations = []
    try:
        for kind in (socket.SOCK_STREAM, socket.SOCK_STREAM, socket.SOCK_DGRAM,
                     socket.SOCK_DGRAM,socket.SOCK_DGRAM):
            sock = socket.socket(socket.AF_INET, kind)
            reservations.append(sock)
            sock.bind(("127.0.0.1", 0))
        return [s.getsockname()[1] for s in reservations]
    finally:
        for sock in reservations:
            sock.close()


def prepare_profiles():
    profiles = ROOT / ".local/profiles"
    profiles.mkdir(parents=True, exist_ok=True)
    try:
        original = json.loads((ROOT / ".local/ui.json").read_text(encoding="utf-8"))
        inherited = {k: original[k] for k in ("hud", "gamepad_shortcut", "world_rendering") if k in original}
    except (OSError, ValueError, TypeError):
        inherited = {}
    inherited.setdefault("world_rendering", True)
    for index, name, color in ((1, "Link 1", 0), (2, "Link 2", 1)):
        path = profiles / f"local-p{index}.json"
        if not path.exists():
            path.write_text(json.dumps(dict(inherited, name=name, tunic=color, host="127.0.0.1")), encoding="utf-8")


def start_instances(emulator, game, executables, ports, processes=None, indexes=(1, 2)):
    env = dict(os.environ)
    for key in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QT_QPA_PLATFORM"):
        env.pop(key, None)
    env["PATH"] = str(Path(emulator).parent) + os.pathsep + env.get("PATH", "")
    env["PYTHONUTF8"] = "1"
    if processes is None:
        processes = []
    prepare_profiles()
    python = ROOT / ".venv/Scripts/pythonw.exe"
    if not python.exists():
        python = Path(sys.executable)
    for index in indexes:
        executable = executables[index - 1]
        # Keep handles only for processes started here. No unrelated Azahar is stopped.
        log = open(LAB / f"player{index}-emulator.log", "w", encoding="utf-8")
        try:
            process = subprocess.Popen([str(executable), str(game.path)],
                cwd=executable.parent, env=env, stdout=log, stderr=log)
        finally:
            log.close()
        processes.append(process)
        companion_log = open(LAB / f"player{index}-companion.log", "w", encoding="utf-8")
        command = [str(python), "-m", "src.main", "--profile", f"local-p{index}",
            "--emulator-pid", str(process.pid), "--gdb-port", str(ports[index - 1]),
            "--title-id", f"{game.title_id:016X}", "--process-name", game.process_name,
            "--enemy-authority"]
        rpc_port=configured_port(executable)
        if rpc_port:
            command+=['--rpc-port',str(rpc_port)]
        command += ["--host-port", str(ports[2])] if index == 1 else ["--join", f"127.0.0.1:{ports[2]}"]
        try:
            subprocess.Popen(command, cwd=ROOT, env=dict(os.environ, PYTHONUTF8="1"), stdout=companion_log, stderr=companion_log)
        finally:
            companion_log.close()
    (LAB / "session.json").write_text(json.dumps({"pids": [p.pid for p in processes], "ports": ports,
        "rpc_ports": [configured_port(exe) or 0 for exe in executables],
        "title_id": f"{game.title_id:016X}", "process_name": game.process_name}, indent=2), encoding="utf-8")
    return processes


def reopen_launcher(key):
    connection = QLocalSocket()
    connection.connectToServer(key)
    connected = connection.waitForConnected(500)
    connection.disconnectFromServer()
    return connected


def listen_for_reopen(window, key):
    server = QLocalServer(window)
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    def reveal():
        while server.hasPendingConnections():
            connection = server.nextPendingConnection()
            connection.close()
            connection.deleteLater()
        window.showNormal()
        window.raise_()
        window.activateWindow()
    server.newConnection.connect(reveal)
    if not server.listen(key):
        raise OSError(server.errorString())
    return server


SLOT_TONES = {"idle": MUTED, "wait": GOLD, "ok": "#A4D46B", "bad": "#E47A5C"}


def describe(process, state):
    """(text, tone) of one player's card, from its companion's status file."""
    if process is None:
        return "Pas lancé", "idle"
    if process.poll() is not None:
        return "Fermé", "bad"
    if not state or time.time() - state.get("updated_at", 0) > 5:
        return "Connexion…", "wait"
    if not state.get("in_game"):
        return ("Choisissez une partie" if state.get("rpc_connected") else "Connexion…"), "wait"
    if state.get("connected_players", 1) > 1:
        return "En jeu · à deux", "ok"
    return "En jeu · seul", "wait"


class PlayerSlot(QWidget):
    """One player's card: portrait, name, state dot and hearts."""
    def __init__(self, index, parent=None):
        super().__init__(parent)
        self.index = index
        self.player = {}
        self.state, self.tone = "Pas lancé", "idle"
        self.setMinimumWidth(220)
        self.setFixedHeight(78)
        self.setAccessibleName(f"Link {index}")
        self.setAccessibleDescription(self.state)

    def set_state(self, state, tone, player=None):
        player = dict(player or {})
        if (state, tone, player) != (self.state, self.tone, self.player):
            self.state, self.tone, self.player = state, tone, player
            self.setAccessibleDescription(state)
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        draw_wood_panel(p, QRectF(self.rect()).adjusted(1, 1, -1, -1), "inset")
        _, asset, _ = TUNICS[int(self.player.get("tunic_color", self.index - 1)) % 4]
        p.setOpacity(.45 if self.tone == "idle" else 1)
        p.drawPixmap(12, (self.height() - 46) // 2, ALBWAssetStore.get().pixmap(asset, 46, 46))
        p.setOpacity(1)
        x, width = 70, self.width() - 82
        p.setFont(font(15, bold=True))
        p.setPen(QColor(INK))
        name = str(self.player.get("name") or f"Link {self.index}")
        p.drawText(QRectF(x, 9, width, 22), Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, width))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(SLOT_TONES[self.tone]))
        p.drawEllipse(QRectF(x, 38, 8, 8))
        p.setFont(font(12))
        p.setPen(QColor(MUTED))
        p.drawText(QRectF(x + 14, 32, width - 14, 20), Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(self.state, Qt.TextElideMode.ElideRight, width - 14))
        if self.player.get("in_game") and self.player.get("max_hp"):
            draw_hearts(p, QRectF(x, 56, width, 12), int(self.player.get("current_hp", 0)),
                        int(self.player["max_hp"]), largest=12)


class LocalSessionWindow(QWidget):
    def __init__(self, emulator="", game=""):
        super().__init__()
        self.setWindowTitle("ALBW · Essai à deux")
        self.setObjectName("menu")
        self.setStyleSheet(MENU_STYLE + "LocalSessionWindow { background: #28140C; }")
        self.resize(560, 420)
        self.processes = []
        self.launch_context = None
        self.browse_buttons = []
        root = QVBoxLayout(self)
        panel = WoodPanel("red")
        root.addWidget(panel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(22, 20, 22, 22)
        layout.setSpacing(12)
        layout.addWidget(label("Essai à deux", "heading"))
        cards = QHBoxLayout()
        cards.setSpacing(10)
        self.slots = [PlayerSlot(i) for i in (1, 2)]
        for slot in self.slots:
            cards.addWidget(slot)
        layout.addLayout(cards)
        # Choices only matter before launching; they hide while the games run.
        self.setup = QWidget()
        setup = QVBoxLayout(self.setup)
        setup.setContentsMargins(0, 4, 0, 0)
        setup.setSpacing(8)
        self.emulator_edit = self.path_row(setup, "Azahar", emulator, "Azahar (*.exe)")
        self.game_edit = self.path_row(setup, "Jeu ALBW", game, "Jeu 3DS (*.3ds *.cci *.cxi *.app)")
        self.keyboard = QCheckBox("Link 2 au clavier")
        self.keyboard.setChecked(True)
        self.keyboard.setToolTip("ZQSD bouger · A B X Y · Entrée Start\nLa manette reste à Link 1.")
        setup.addWidget(self.keyboard)
        layout.addWidget(self.setup)
        self.launch = button("Lancer Link 1", self.start, "primary")
        layout.addWidget(self.launch)
        self.status = label("", "muted")
        self.status.hide()
        layout.addWidget(self.status)
        layout.addStretch(1)
        footer = QHBoxLayout()
        self.repair_saves = button("Sauvegarde 1 → 2", self.repair_player_two)
        self.repair_saves.setToolTip("Remplace la sauvegarde de Link 2 par celle de Link 1.\n"
                                     "Une copie de secours est gardée ; le jeu de Link 2 doit être fermé.")
        footer.addWidget(self.repair_saves)
        footer.addStretch(1)
        footer.addWidget(button("Dossier", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(LAB)))))
        layout.addLayout(footer)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(1000)

    def say(self, text):
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def repair_player_two(self):
        try:
            from src.local_saves import repair_player_two
            game=identify_game(self.game_edit.text())
            repair_player_two(LAB,game.title_id)
            self.say("Sauvegarde de Link 2 remplacée · copie de secours gardée.")
        except (OSError,RuntimeError,ValueError) as exc:
            self.say(str(exc))

    def path_row(self, layout, title, value, file_filter):
        row = QHBoxLayout()
        row.setSpacing(6)
        edit = QLineEdit(value)
        edit.setPlaceholderText(title)
        edit.setAccessibleName(title)
        edit.setToolTip(title)
        row.addWidget(edit, 1)
        def browse():
            selected, _ = QFileDialog.getOpenFileName(self, title, edit.text(), file_filter)
            if selected:
                edit.setText(selected)
        picker = button("…", browse)
        picker.setAccessibleName(f"Choisir {title}")
        picker.setToolTip(f"Choisir {title}")
        picker.setFixedWidth(46)
        self.browse_buttons.append(picker)
        row.addWidget(picker)
        layout.addLayout(row)
        return edit

    def set_choosing(self, choosing):
        for widget in (self.emulator_edit, self.game_edit, self.keyboard, self.repair_saves, *self.browse_buttons):
            widget.setEnabled(choosing)
        self.setup.setVisible(choosing)
        self.layout().activate()
        self.resize(self.width(), self.sizeHint().height())

    def start(self):
        self.launch.setEnabled(False)
        self.say("Préparation…")
        QApplication.processEvents()
        try:
            if not self.processes:
                emulator = Path(self.emulator_edit.text()).resolve(strict=True)
                game = identify_game(self.game_edit.text())
                ports = reserve_ports()
                executables = [prepare_instance(emulator, game, i, ports[i - 1],
                    keyboard=i == 2 and self.keyboard.isChecked(),
                    rpc_port=ports[i+2] if len(ports)>i+2 else None) for i in (1, 2)]
                (LAB / "choices.json").write_text(json.dumps({"emulator": str(emulator), "game": str(game.path)}), encoding="utf-8")
                self.launch_context = emulator, game, executables, ports
            index = len(self.processes) + 1
            if index > 2:
                return
            start_instances(*self.launch_context, self.processes, indexes=(index,))
            if index == 1:
                self.say("Chargez une partie, puis lancez Link 2.")
                self.launch.setText("Lancer Link 2")
                self.launch.setEnabled(True)
            else:
                self.say("Même case de sauvegarde chez Link 2 · F8 ouvre le menu.")
                self.launch.setText("Essai en cours")
            self.set_choosing(False)
            self.poll()
        except (OSError, ValueError) as exc:
            self.say(str(exc) + (" Fermez d’abord les jeux de cet essai." if self.processes else ""))
            self.launch.setEnabled(not self.processes)

    def poll(self):
        for index, slot in enumerate(self.slots, 1):
            process = self.processes[index - 1] if index <= len(self.processes) else None
            try:
                state = json.loads((ROOT / f".local/profiles/local-p{index}.status.json").read_text(encoding="utf-8"))
                state = state if isinstance(state, dict) else {}
            except (OSError, ValueError):
                state = {}
            me = next((p for p in state.get("players", []) if isinstance(p, dict) and p.get("is_self")), {})
            slot.set_state(*describe(process, state), me if process else {})
        if self.processes and all(p.poll() is not None for p in self.processes):
            self.processes = []
            self.say("Jeux fermés · sauvegardes conservées.")
            self.launch_context = None
            self.launch.setText("Lancer Link 1")
            self.launch.setEnabled(True)
            self.set_choosing(True)
            for slot in self.slots:
                slot.set_state(*describe(None, {}))
            if not self.isVisible():
                QApplication.quit()

    def closeEvent(self, event):
        # Keep the session lock until both children exit, even with this panel closed.
        if not self.processes:
            QApplication.quit()
        event.accept()


def main():
    parser = argparse.ArgumentParser(description="Essai coop ALBW à deux sur ce PC")
    parser.add_argument("--emulator")
    parser.add_argument("--game")
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--start-both", action="store_true", help="Ouvrir les deux copies, avec un délai entre leurs démarrages.")
    parser.add_argument("--player2-keyboard", action=argparse.BooleanOptionalAction, default=None)
    args = parser.parse_args()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    LAB.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(LAB / "session.lock"))
    lock.setStaleLockTime(0)
    activation_key = "albw-coop-lab-" + hashlib.sha256(str(ROOT).lower().encode()).hexdigest()[:16]
    if not lock.tryLock(0):
        if reopen_launcher(activation_key):
            return
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.information(None, "ALBW · Essai à deux", "Un essai à deux est déjà en cours : fermez ses deux jeux d’abord.")
        return
    detected_emulator, detected_game = discover_paths()
    window = LocalSessionWindow(args.emulator or detected_emulator, args.game or detected_game)
    if args.player2_keyboard is not None:
        window.keyboard.setChecked(args.player2_keyboard)
    try:
        activation_server = listen_for_reopen(window, activation_key)
    except OSError:
        activation_server = None
    window.show()
    if args.start or args.start_both:
        QTimer.singleShot(100, window.start)
    if args.start_both:
        # The ordinary start method retains its save/port preparation and
        # one-player-at-a-time checks. Never retry a failed first launch here.
        def start_second():
            if len(window.processes)==1 and window.launch.isEnabled():
                window.start()
        QTimer.singleShot(4000, start_second)
    app.exec()
    lock.unlock()


if __name__ == "__main__":
    main()
