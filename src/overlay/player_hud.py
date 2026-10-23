"""Compact, event-driven ALBW player cards and a click-through desktop HUD."""
from PyQt6.QtCore import Qt, QRect, QRectF, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QWidget
from src.overlay.menu_widgets import ALBWAssetStore, TUNICS, draw_hearts, draw_wood_panel, font, INK, GOLD

HUD_DEFAULTS = {"enabled": True, "scale": 100, "opacity": 88, "x": 100, "y": 6}
CARD_WIDTH, ROW_HEIGHT = 212, 50


def normalize_hud(data):
    data = data if isinstance(data, dict) else {}
    result = dict(HUD_DEFAULTS)
    result["enabled"] = data.get("enabled", True) is not False
    for key, lower, upper in (("scale", 65, 175), ("opacity", 30, 100), ("x", 0, 100), ("y", 0, 100)):
        try:
            result[key] = max(lower, min(upper, int(data.get(key, result[key]))))
        except (ValueError, TypeError, OverflowError):
            pass
    return result


def draw_cards(painter, players, mode, width=CARD_WIDTH):
    for index, player in enumerate(players[:4]):
        y = index * ROW_HEIGHT
        draw_wood_panel(painter, QRectF(2, y + 2, width - 4, ROW_HEIGHT - 4), "dark")
        tunic = TUNICS.get(player.get("tunic_color", 0), TUNICS[0])
        portrait = ALBWAssetStore.get().pixmap(tunic[1], 36, 36)
        painter.drawPixmap(7, y + 7, portrait)
        painter.setFont(font(12, bold=True))
        name = str(player.get("name", "Link"))
        name = painter.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, int(width - 70))
        painter.setPen(QColor("#140A03"))
        painter.drawText(QRectF(49, y + 5, width - 66, 16), Qt.AlignmentFlag.AlignVCenter, name)
        painter.setPen(QColor(INK))
        painter.drawText(QRectF(49, y + 4, width - 66, 16), Qt.AlignmentFlag.AlignVCenter, name)
        connected = mode != "offline" and (not player.get("is_self") or len(players) > 1)
        color = "#9BC982" if connected else ("#D8AE59" if mode != "offline" else "#9E8C74")
        painter.setPen(QPen(QColor(color), 1.3))
        painter.setBrush(QColor(color) if connected else Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(width - 15, y + 10, 5, 5))
        if player.get("in_game") and player.get("life", "alive") != "alive":
            # Fallen: waiting for a weathervane (life_sync), no hearts to show.
            painter.setFont(font(9, bold=True))
            painter.setPen(QColor("#E07A5F"))
            painter.drawText(QRectF(49, y + 22, width - 58, 14), Qt.AlignmentFlag.AlignVCenter,
                             "Tombé · attend une girouette")
        elif player.get("in_game"):
            # Two rows preserve readable hearts at the game's 20-heart maximum.
            hp, maximum = max(0, int(player.get("current_hp", 0))), min(80, int(player.get("max_hp", 12)))
            draw_hearts(painter, QRectF(48, y + 22, width - 58, 11), min(hp, 40), min(maximum, 40), 11)
            if maximum > 40:
                draw_hearts(painter, QRectF(48, y + 34, width - 58, 11), max(0, hp - 40), maximum - 40, 11)
        else:
            painter.setFont(font(10))
            painter.setPen(QColor("#CEB485"))
            painter.drawText(QRectF(49, y + 22, width - 58, 14), Qt.AlignmentFlag.AlignVCenter, "En attente")


class HudPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.players, self.mode = [], "offline"
        self.opacity = .88
        self.setMinimumHeight(ROW_HEIGHT)
        self.setMaximumHeight(ROW_HEIGHT)
        self.setAccessibleName("Aperçu du portrait et des cœurs")

    def set_state(self, state, opacity):
        self.players = state.get("display_players", [])[:1]
        self.mode, self.opacity = state.get("net_mode", "offline"), opacity / 100
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setOpacity(self.opacity)
        draw_cards(p, self.players, self.mode, min(self.width(), CARD_WIDTH))


class LifeBanner(QWidget):
    """Click-through banner centred at the top of the game: who waits for a
    weathervane (life_sync.life_notice). Hidden when there is nothing to say."""
    PADDING = 14

    def __init__(self, owner):
        super().__init__(owner, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowTransparentForInput |
                         Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("ALBW — Girouette")
        self.text = ""
        self.bounds = QRect()

    def configure(self, text, bounds):
        if text == self.text and bounds == self.bounds:
            return
        self.text, self.bounds = text, QRect(bounds)
        self.setAccessibleName(text)
        metrics = QFontMetrics(font(12, bold=True))
        width = min(max(120, bounds.width() - 260), metrics.horizontalAdvance(text) + 2 * self.PADDING)
        self.resize(width, 38)
        self.move(bounds.x() + (bounds.width() - width) // 2, bounds.y() + 10)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw_wood_panel(p, QRectF(1, 1, self.width() - 2, self.height() - 2), "dark")
        p.setFont(font(12, bold=True))
        text = p.fontMetrics().elidedText(self.text, Qt.TextElideMode.ElideRight, self.width() - 2 * self.PADDING)
        area = QRectF(self.PADDING, 0, self.width() - 2 * self.PADDING, self.height())
        p.setPen(QColor("#140A03"))
        p.drawText(area.translated(0, 1), Qt.AlignmentFlag.AlignCenter, text)
        p.setPen(QColor("#F2C4A8"))
        p.drawText(area, Qt.AlignmentFlag.AlignCenter, text)


class PlayerHUD(QWidget):
    placement_changed = pyqtSignal(int, int, int)
    edit_finished = pyqtSignal()

    def __init__(self, owner):
        super().__init__(owner, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowTransparentForInput |
                         Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("ALBW — Compagnons")
        self.players, self.mode = [], "offline"
        self.settings = dict(HUD_DEFAULTS)
        self.bounds = QRect()
        self.editing = False
        self._drag = None
        self.setMouseTracking(True)

    def sizeHint(self):
        return QSize(CARD_WIDTH, max(1, len(self.players)) * ROW_HEIGHT)

    def configure(self, state, settings, bounds):
        players = state.get("display_players", [])[:4]
        mode = state.get("net_mode", "offline")
        inner_bounds = bounds.adjusted(12, 12, -12, -12)
        if players == self.players and mode == self.mode and settings == self.settings and inner_bounds == self.bounds:
            return
        self.players, self.mode = players, mode
        self.settings = dict(settings)
        self.bounds = inner_bounds
        base = self.sizeHint()
        scale = min(settings["scale"] / 100, self.bounds.width() / base.width(), self.bounds.height() / base.height())
        self.render_scale = max(.2, scale)
        self.resize(round(base.width() * self.render_scale), round(base.height() * self.render_scale))
        x = self.bounds.x() + round(max(0, self.bounds.width() - self.width()) * settings["x"] / 100)
        y = self.bounds.y() + round(max(0, self.bounds.height() - self.height()) * settings["y"] / 100)
        self.move(x, y)
        self.setWindowOpacity(settings["opacity"] / 100)
        self.setAccessibleName("Compagnons : " + ", ".join(str(p.get("name", "Link")) for p in self.players))
        self.update()

    def set_editing(self, editing):
        if editing == self.editing:
            return
        self.editing = editing
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, not editing)
        self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, not editing)
        self.setCursor(Qt.CursorShape.SizeAllCursor if editing else Qt.CursorShape.ArrowCursor)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.scale(getattr(self, "render_scale", 1), getattr(self, "render_scale", 1))
        draw_cards(p, self.players, self.mode)
        if self.editing:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(GOLD), 1, Qt.PenStyle.DashLine))
            height = self.sizeHint().height()
            p.drawRect(QRectF(1, 1, CARD_WIDTH - 2, height - 2))
            p.setPen(QPen(QColor("#FFE9AF"), 2))
            for offset in (5, 10, 15):
                p.drawLine(CARD_WIDTH - offset - 2, height - 3, CARD_WIDTH - 3, height - offset - 2)

    def _resize_corner(self, point):
        return point.x() > self.width() - 22 and point.y() > self.height() - 22

    def mousePressEvent(self, event):
        if self.editing and event.button() == Qt.MouseButton.LeftButton:
            self._drag = (event.globalPosition().toPoint(), self.pos(), self.width(),
                          self._resize_corner(event.position()))

    def mouseMoveEvent(self, event):
        if not self.editing:
            return
        self.setCursor(Qt.CursorShape.SizeFDiagCursor if self._resize_corner(event.position()) else Qt.CursorShape.SizeAllCursor)
        if self._drag and event.buttons() & Qt.MouseButton.LeftButton:
            start, origin, width, resizing = self._drag
            delta = event.globalPosition().toPoint() - start
            scale = self.settings["scale"]
            if resizing:
                scale = max(65, min(175, round((width + delta.x()) / CARD_WIDTH * 100)))
                target = origin
            else:
                target = origin + delta
            x = round(100 * (target.x() - self.bounds.x()) / max(1, self.bounds.width() - self.width()))
            y = round(100 * (target.y() - self.bounds.y()) / max(1, self.bounds.height() - self.height()))
            self.placement_changed.emit(max(0, min(100, x)), max(0, min(100, y)), scale)

    def mouseReleaseEvent(self, event):
        self._drag = None

    def keyPressEvent(self, event):
        if self.editing and event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Return):
            self.edit_finished.emit()
        else:
            super().keyPressEvent(event)
