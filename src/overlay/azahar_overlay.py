"""Responsive native overlay with a standalone waiting/preview screen."""
import json
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QEasingCurve, QRect, QTimer, QVariantAnimation
from PyQt6.QtGui import QColor, QKeySequence, QPainter, QRegion, QShortcut
from PyQt6.QtWidgets import QApplication, QAbstractButton, QGraphicsOpacityEffect, QLabel, QSlider, QWidget

from src.overlay.coop_panel import CoopPanel, TAB_SETTINGS
from src.overlay.menu_widgets import MENU_STYLE, MorphButton
from src.overlay.window_tracker import find_azahar_hwnd_and_pid, get_window_client_logical_rect, user32
from src.overlay.player_hud import LifeBanner, PlayerHUD, normalize_hud, HUD_DEFAULTS
from src.overlay.gamepad import SDLGamepads, ShortcutState, DEFAULT_BINDING, VALID_BUTTONS

PREFERENCES_PATH = Path(__file__).resolve().parents[2] / ".local" / "ui.json"
# The F8 button folds into the Triforce after this idle time (milliseconds).
BUTTON_IDLE_MS, BUTTON_MORPH_MS = 3000, 280
BUTTON_SIZE, EMBLEM_SIZE, BUTTON_MARGIN = (178, 42), 40, 12
# Menu opening fade, and closing fade + drop of a snapshot (ms, px).
MENU_OPEN_MS, MENU_CLOSE_MS, MENU_DROP = 160, 140, 10


def load_preferences():
    try:
        data = json.loads(PREFERENCES_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        binding = data.get("gamepad_shortcut", DEFAULT_BINDING)
        if not isinstance(binding, list) or not all(isinstance(k, str) and k in VALID_BUTTONS for k in binding):
            binding = DEFAULT_BINDING
        return {
            "name": str(data.get("name", "Link"))[:24],
            "tunic": int(data.get("tunic", 0)) % 4,
            "host": str(data.get("host", ""))[:256],  # a room code or an address
            "port": max(1, min(65535, int(data.get("port", 45990)))),
            "hud": normalize_hud(data.get("hud")),
            "world_rendering": data.get("world_rendering", True) is True,
            "gamepad_shortcut": list(dict.fromkeys(binding)),
        }
    except (OSError, ValueError, TypeError):
        return {}


class AzaharNativeOverlay(QWidget):
    def __init__(self, orchestrator=None, preview_only=False, target_emulator_pid=0):
        super().__init__()
        self.orchestrator = orchestrator
        self.preview_only = preview_only
        self.target_emulator_pid = target_emulator_pid
        self.preferences = {} if preview_only else load_preferences()
        self.local_name = self.preferences.get("name", "Link")
        self.local_tunic = self.preferences.get("tunic", 0)
        self.world_rendering=self.preferences.get('world_rendering',True)
        self.hud_settings = normalize_hud(self.preferences.get("hud"))
        self.pad_shortcut = ShortcutState(self.preferences.get("gamepad_shortcut", DEFAULT_BINDING))
        self.gamepad_names = []
        self._pad_previous = {}
        self.hud_editing = False
        self.azahar_hwnd = None
        self.azahar_pid = 0
        self.subscreen_open = not bool(target_emulator_pid)
        self._standalone = True
        self._next_scan = 0
        self._hotkey_was_down = False
        self._drag_offset = None
        self.state_cache = {"rpc_connected": False, "in_game": False, "net_mode": "offline",
                            "soul_link": False, "rupees": None, "players": []}
        self.preview_players = False
        self._logs = []

        self.setWindowTitle("ALBW Online Co-op" + (" — Aperçu" if preview_only else ""))
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(MENU_STYLE)
        self.sanctuary = CoopPanel(self)
        self.player_hud = PlayerHUD(self)
        self.player_hud.placement_changed.connect(self.on_hud_placement)
        self.player_hud.edit_finished.connect(self.finish_hud_edit)
        self.life_banner = LifeBanner(self)
        if preview_only:
            self.sanctuary.btn_resume.setText("Replier le menu")
        self.hud_button = MorphButton("Co-op   ·   F8", self)
        self.hud_button.setProperty("role", "primary")
        self.hud_button.setAccessibleName("Ouvrir le menu coopératif, F8")
        self.hud_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hud_button.clicked.connect(self.toggle_subscreen)
        self.hud_button.hovered.connect(self._button_hovered)
        self.button_morph = QVariantAnimation(self)
        self.button_morph.setDuration(BUTTON_MORPH_MS)
        self.button_morph.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.button_morph.valueChanged.connect(self._set_button_morph)
        self.button_idle = QTimer(self)
        self.button_idle.setSingleShot(True)
        self.button_idle.setInterval(BUTTON_IDLE_MS)
        self.button_idle.timeout.connect(lambda: self._fold_button(True))
        self.button_idle.start()
        self.menu_fade = QVariantAnimation(self)
        self.menu_fade.setDuration(MENU_OPEN_MS)
        self.menu_fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        # Window opacity would make Windows drop keyboard focus: fade the panel only,
        # and remove the effect once opaque (its labels keep their own shadows).
        self.menu_fade.valueChanged.connect(self._menu_frame)
        self.menu_fade.finished.connect(lambda: self.sanctuary.setGraphicsEffect(None))
        # The closed menu leaves as a picture: the real panel is already gone,
        # so input, focus and the game are released at once.
        self.ghost = QLabel(self)
        self.ghost.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.ghost.hide()
        self.ghost_effect = QGraphicsOpacityEffect(self.ghost)
        self.ghost.setGraphicsEffect(self.ghost_effect)
        self.ghost_anim = QVariantAnimation(self)
        self.ghost_anim.setDuration(MENU_CLOSE_MS)
        self.ghost_anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self.ghost_anim.setStartValue(1.0)
        self.ghost_anim.setEndValue(0.0)
        self.ghost_anim.valueChanged.connect(self._ghost_frame)
        self.ghost_anim.finished.connect(self._ghost_done)
        self.shortcut_escape = QShortcut(QKeySequence("Escape"), self)
        self.shortcut_escape.activated.connect(self.close_menu)
        if preview_only:
            self.shortcut_f8 = QShortcut(QKeySequence("F8"), self)
            self.shortcut_f8.activated.connect(self.toggle_subscreen)
        self.resize(940, 760)
        self._center_standalone()
        self._layout_subscreen()
        if orchestrator:
            orchestrator.set_identity(self.local_name, self.local_tunic)
            orchestrator.set_appearance_enabled(self.world_rendering)
            orchestrator.snapshot_changed.connect(self.receive_snapshot, Qt.ConnectionType.QueuedConnection)
            orchestrator.logs_changed.connect(self.receive_logs, Qt.ConnectionType.QueuedConnection)
        self.refresh_state()
        self.gamepads = SDLGamepads()
        self.pad_timer = QTimer(self)
        self.pad_timer.timeout.connect(self._poll_gamepads)
        self.pad_timer.start(33)
        if not preview_only:
            self.track_timer = QTimer(self)
            self.track_timer.timeout.connect(self._tick_overlay)
            self.track_timer.start(50)

    def _center_standalone(self):
        screen = self.screen()
        if screen:
            available = screen.availableGeometry()
            self.resize(min(940, available.width() - 32), min(760, available.height() - 32))
            self.move(available.center() - self.rect().center())

    def demo_players(self):
        return [dict(player_id=f"preview_{i}", name=name, tunic_color=i, is_self=i == 0,
                     in_game=True, current_hp=hp, max_hp=20, world=world)
                for i, (name, hp, world) in enumerate([
                    (self.local_name, 20, "Hyrule"), ("Marine", 15, "Hyrule"),
                    ("Ravio", 18, "Lorule"), ("Hilda", 12, "Palais de l’Est")])]

    def receive_snapshot(self, snapshot):
        self.state_cache.update(snapshot)
        self.refresh_state(pull=False)

    def receive_logs(self, logs):
        self._logs = logs
        self.sanctuary.update_state(self.state_cache, self._logs)

    def refresh_state(self, pull=True):
        if pull and self.orchestrator:
            self.state_cache.update(self.orchestrator.get_ui_snapshot())
            self._logs = self.orchestrator.get_ui_logs()
        players = [dict(p) for p in self.state_cache.get("players", [])]
        if self.preview_only and self.preview_players:
            players = self.demo_players()
        if not players:
            players = [{"name": self.local_name, "tunic_color": self.local_tunic,
                        "is_self": True, "in_game": False}]
        for player in players:
            if player.get("is_self"):
                player.update(name=self.local_name, tunic_color=self.local_tunic)
        self.state_cache["display_players"] = players
        self.sanctuary.update_state(self.state_cache, self._logs)
        caption = f"{self.local_name[:12]}  ·  F8" if self.target_emulator_pid else f"Co-op  {len(players)}/4   ·   F8"
        self.hud_button.setText("Terminer le placement  ·  Échap" if self.hud_editing else caption)
        self._place_hud()

    def save_preferences(self):
        if self.preview_only:
            return
        data = {"name": self.local_name, "tunic": self.local_tunic,
                "host": self.sanctuary.ip_edit.text().strip(), "port": self.sanctuary.port_edit.value(),
                "hud": self.hud_settings, "gamepad_shortcut": self.pad_shortcut.binding,
                "world_rendering": self.world_rendering}
        try:
            PREFERENCES_PATH.parent.mkdir(parents=True, exist_ok=True)
            temporary = PREFERENCES_PATH.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(PREFERENCES_PATH)
        except OSError:
            self.sanctuary.show_message("Les préférences n’ont pas pu être enregistrées.", error=True)

    def _layout_subscreen(self):
        if not hasattr(self, "sanctuary"):
            return
        margin = 12 if self.width() < 720 else 28
        width = min(880, max(300, self.width() - margin * 2))
        height = min(704, max(240, self.height() - margin * 2))
        self.sanctuary.setGeometry((self.width() - width) // 2, (self.height() - height) // 2, width, height)
        self.sanctuary.setVisible(self.subscreen_open)
        if self.hud_editing:
            self.button_idle.stop();self.button_morph.stop();self.hud_button.set_morph(0)
        elif self.hud_button.isHidden() and not self.subscreen_open:
            # Back from the menu: show the full button, fold it again later.
            self.button_morph.stop();self.hud_button.set_morph(0);self.button_idle.start()
        self._place_button()
        self.hud_button.setVisible(not self.subscreen_open)
        self._update_input_region()
        self._place_hud()

    def _place_button(self):
        if self.hud_editing:
            self.hud_button.setGeometry(max(8, (self.width() - 278) // 2), self.height() - 62, 278, 42)
            return
        m = self.hud_button.morph
        width = round(BUTTON_SIZE[0] + (EMBLEM_SIZE - BUTTON_SIZE[0]) * m)
        height = round(BUTTON_SIZE[1] + (EMBLEM_SIZE - BUTTON_SIZE[1]) * m)
        # Anchored by its top-right corner: it folds toward the window corner.
        self.hud_button.setGeometry(QRect(max(8, self.width() - BUTTON_MARGIN - width), BUTTON_MARGIN, width, height))

    def _set_button_morph(self, value):
        self.hud_button.set_morph(float(value))
        self._place_button()
        self._update_input_region()

    def _fold_button(self, fold):
        target = 1.0 if fold else 0.0
        if self.hud_editing or abs(self.hud_button.morph - target) < 1e-3:return
        self.button_morph.stop()
        self.button_morph.setStartValue(self.hud_button.morph)
        self.button_morph.setEndValue(target)
        self.button_morph.start()

    def _button_hovered(self, inside):
        if inside:
            self.button_idle.stop()
            self._fold_button(False)
        else:
            self.button_idle.start()

    def _place_hud(self):
        if not hasattr(self, "player_hud"):
            return
        self.player_hud.configure(self.state_cache, self.hud_settings, self.geometry())
        visible = self.isVisible() and self.hud_settings["enabled"] and (
            self.hud_editing or (not self.subscreen_open and self.state_cache.get("in_game", False)))
        self.player_hud.setVisible(visible)
        # Who waits for a weathervane, centred at the top of the game.
        notice = self.state_cache.get("life_notice", "")
        self.life_banner.configure(notice, self.geometry())
        self.life_banner.setVisible(bool(notice) and self.isVisible() and not self.subscreen_open
                                    and self.state_cache.get("in_game", False))

    def set_hud_option(self, key, value):
        self.hud_settings = normalize_hud(dict(self.hud_settings, **{key: value}))
        self.sanctuary.settings_page.sync_controls()
        self.sanctuary.settings_page.update_state(self.state_cache)
        self._place_hud()
        self.save_preferences()

    def reset_hud(self):
        self.hud_settings = dict(HUD_DEFAULTS)
        self.sanctuary.settings_page.sync_controls()
        self.sanctuary.settings_page.update_state(self.state_cache)
        self._place_hud()
        self.save_preferences()

    def begin_hud_edit(self):
        self.pad_shortcut.cancel_capture()
        self.hud_editing = True
        self.player_hud.set_editing(True)
        self.subscreen_open = False
        self.refresh_state(pull=False)
        self._layout_subscreen()

    def on_hud_placement(self, x, y, scale):
        self.hud_settings.update(x=x, y=y, scale=scale)
        self.sanctuary.settings_page.sync_controls()
        self._place_hud()

    def finish_hud_edit(self):
        if not self.hud_editing:
            return
        self.hud_editing = False
        self.player_hud.set_editing(False)
        self.subscreen_open = True
        self.sanctuary.select_page(TAB_SETTINGS)
        self.refresh_state(pull=False)
        self._layout_subscreen()
        self.save_preferences()
        self.activateWindow()
        self.sanctuary.settings_page.move_button.setFocus()

    def record_gamepad_shortcut(self):
        if self.pad_shortcut.recording:
            self.pad_shortcut.cancel_capture()
        else:
            self.pad_shortcut.begin_capture(time.monotonic())
        self.sanctuary.settings_page.update_pad_status()

    def disable_gamepad_shortcut(self):
        self.pad_shortcut.binding = []
        self.pad_shortcut.cancel_capture()
        self.sanctuary.settings_page.update_pad_status()
        self.save_preferences()

    def _poll_gamepads(self):
        devices = self.gamepads.read()
        names = [name for name, _ in devices.values()]
        if names != self.gamepad_names:
            self.gamepad_names = names
            self.sanctuary.settings_page.update_pad_status()
        foreground = user32.GetForegroundWindow()
        active = self.isVisible() and foreground in (int(self.winId()), self.azahar_hwnd, int(self.player_hud.winId()))
        self._handle_gamepad_frame({ident: buttons for ident, (_, buttons) in devices.items()}, active)

    def _handle_gamepad_frame(self, devices, active):
        was_recording = self.pad_shortcut.recording
        pressed, captured, timeout = self.pad_shortcut.update(devices, active, time.monotonic())
        previous, self._pad_previous = self._pad_previous, devices
        if captured is not None:
            self.save_preferences()
        if captured is not None or timeout:
            self.sanctuary.settings_page.update_pad_status()
        if timeout and active:
            hint = self.sanctuary.settings_page.pad_hint
            hint.setText("Aucune combinaison enregistrée. Réessayez.")
            hint.show()
        if was_recording:
            return
        if pressed:
            self.toggle_subscreen()
            return
        if not active or not self.subscreen_open:
            return
        # Never combine inputs across controllers. Navigation is edge-triggered.
        for ident, keys in devices.items():
            new = set(keys) - set(previous.get(ident, keys))
            if set(self.pad_shortcut.binding) & set(keys):
                continue
            focus = QApplication.focusWidget()
            if "east" in new:
                self.close_menu()
            elif "down" in new or "up" in new:
                self.focusNextPrevChild("down" in new)
            elif "south" in new and isinstance(focus, QAbstractButton):
                focus.click()
            elif ("left" in new or "right" in new) and isinstance(focus, QSlider):
                focus.setValue(focus.value() + (5 if "right" in new else -5))
            elif "left" in new or "right" in new:
                self.focusNextPrevChild("right" in new)

    def _update_input_region(self):
        if self._standalone and self.subscreen_open:
            self.clearMask()
        else:
            # A window region, not just transparent paint, leaves the game clickable.
            rect = self.sanctuary.geometry() if self.subscreen_open else self.hud_button.geometry()
            region = QRegion(rect)
            if not self.ghost.isHidden():
                region = region.united(QRegion(self.ghost.geometry().adjusted(0, 0, 0, MENU_DROP)))
            self.setMask(region)

    def _menu_frame(self, value):
        effect = self.sanctuary.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(self.sanctuary)
            self.sanctuary.setGraphicsEffect(effect)
        effect.setOpacity(float(value))

    def _start_ghost(self):
        if self.sanctuary.isHidden():return
        self.ghost.setPixmap(self.sanctuary.grab())
        self.ghost.setGeometry(self.sanctuary.geometry())
        self.ghost.setProperty("base_y", self.sanctuary.y())
        self.ghost_effect.setOpacity(1)
        self.ghost.show()
        self.ghost.raise_()
        self.ghost_anim.stop()
        self.ghost_anim.start()

    def _ghost_frame(self, value):
        value = float(value)
        self.ghost_effect.setOpacity(value)
        base = self.ghost.property("base_y")
        if base is not None:self.ghost.move(self.ghost.x(), base + round((1 - value) * MENU_DROP))

    def _ghost_done(self):
        self.ghost.hide()
        self.ghost.clear()
        self._update_input_region()

    def _tick_overlay(self):
        now = time.monotonic()
        if now >= self._next_scan:
            previous_hwnd = self.azahar_hwnd
            self.azahar_hwnd, self.azahar_pid = find_azahar_hwnd_and_pid(int(self.winId()), self.target_emulator_pid)
            self._next_scan = now + .8
            if previous_hwnd != self.azahar_hwnd:
                self.sanctuary.update_state(self.state_cache, self._logs)
        if not self.azahar_hwnd:
            if self.target_emulator_pid:
                # A managed companion belongs to this one emulator. Its window
                # disappearing must not open the large standalone waiting menu
                # while the process-lifetime watcher finishes shutting down.
                self.hide()
                self.player_hud.hide()
                self.life_banner.hide()
                self._hotkey_was_down = False
                return
            if not self._standalone:
                self._standalone = True
                self.subscreen_open = True
                self._center_standalone()
                self._layout_subscreen()
            if not self.isVisible():
                self.show()
            self._poll_hotkey(user32.GetForegroundWindow() == int(self.winId()))
            self._place_hud()
            return
        foreground = user32.GetForegroundWindow()
        mine = int(self.winId())
        visible = foreground in (self.azahar_hwnd, mine, int(self.player_hud.winId()))
        screen = self.screen()
        geometry = get_window_client_logical_rect(self.azahar_hwnd, screen.devicePixelRatio() if screen else 1)
        if not visible or geometry is None:
            self.hide()
            self.player_hud.hide()
            self.life_banner.hide()
            self._hotkey_was_down = False
            return
        if self._standalone:
            self._standalone = False
            self._update_input_region()
        if self.geometry() != geometry:
            self.setGeometry(geometry)
            self._layout_subscreen()
        if not self.isVisible():
            self.show()
        self._poll_hotkey(True)
        self._place_hud()

    def _poll_hotkey(self, active):
        down = bool(user32.GetAsyncKeyState(0x77) & 0x8000)
        if active and down and not self._hotkey_was_down:
            self.toggle_subscreen()
        self._hotkey_was_down = down

    def toggle_subscreen(self):
        if self.hud_editing:
            self.finish_hud_edit()
            return
        self.pad_shortcut.cancel_capture()
        self.sanctuary.settings_page.update_pad_status()
        closing = self.subscreen_open
        if closing:
            self.menu_fade.stop();self.sanctuary.setGraphicsEffect(None)
            self._start_ghost()
        else:
            self.ghost_anim.stop();self._ghost_done()
        self.subscreen_open = not self.subscreen_open
        self._layout_subscreen()
        self.update()
        if not closing:
            self.menu_fade.stop()
            self.menu_fade.setStartValue(0.0)
            self.menu_fade.setEndValue(1.0)
            self.menu_fade.start()
        if self.subscreen_open:
            self.activateWindow()
            self.sanctuary.tabs.button(self.sanctuary.pages.currentIndex()).setFocus()
        elif self.azahar_hwnd:
            user32.SetForegroundWindow(self.azahar_hwnd)

    def close_menu(self):
        if self.hud_editing:
            self.finish_hud_edit()
            return
        if self.pad_shortcut.recording:
            self.pad_shortcut.cancel_capture()
            self.sanctuary.settings_page.update_pad_status()
            return
        if self.subscreen_open:
            self.toggle_subscreen()

    def on_name_edited(self, text):
        self.local_name = text.strip() or "Link"
        if self.orchestrator:
            self.orchestrator.set_identity(self.local_name, self.local_tunic)
        self.refresh_state()

    def on_tunic_selected(self, tunic):
        self.local_tunic = tunic % 4
        if self.orchestrator:
            self.orchestrator.set_identity(self.local_name, self.local_tunic)
        self.sanctuary.update_portrait()
        self.save_preferences()
        self.refresh_state()

    def on_soullink_changed(self, checked):
        if self.orchestrator:
            self.orchestrator.set_soul_link(checked)
        self.refresh_state()

    def on_world_rendering_changed(self,checked):
        self.world_rendering=bool(checked)
        if self.orchestrator:
            self.orchestrator.set_appearance_enabled(self.world_rendering)
        self.save_preferences()
        self.refresh_state()

    def on_teleport_clicked(self):
        ok = self.orchestrator and self.orchestrator.teleport_to_friend()
        self.sanctuary.show_message("Vous avez rejoint votre allié." if ok else
                                   "Aucun allié disponible dans votre salle.", error=not ok)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_subscreen()

    def moveEvent(self, event):
        super().moveEvent(event)
        self._place_hud()

    def mousePressEvent(self, event):
        if self._standalone and event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        if self._standalone and self.subscreen_open:
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor("#1D1109"))

    def closeEvent(self, event):
        self.save_preferences()
        if hasattr(self, "track_timer"):
            self.track_timer.stop()
        self.pad_timer.stop()
        self.gamepads.close()
        self.player_hud.hide()
        if self.orchestrator:
            self.orchestrator.shutdown()
        event.accept()
        QApplication.instance().quit()
