"""Accessible controls and visual primitives for the co-op menu."""
from PyQt6.QtCore import Qt, QRectF, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QAbstractButton, QButtonGroup, QHBoxLayout, QPushButton, QSlider, QStyle, QWidget
from src.overlay.albw_ui_components import ALBWAssetStore

INK = "#FFF0BA"
MUTED = "#D5B681"
GOLD = "#E0B65F"
TUNICS = {
    0: ("Verte", "wall_link_green.png", "#A4BF63"),
    1: ("Bleue", "wall_link_blue.png", "#8DBEE1"),
    2: ("Rouge", "wall_link_red.png", "#EF9478"),
    3: ("Violette", "wall_link_purple.png", "#C7A0E0"),
}

MENU_STYLE = """
QWidget { color: #FFF0BA; font-family: 'Trebuchet MS'; font-size: 14px; }
QFrame#menu, QWidget#profile, QWidget#menuBody, QWidget#menuViewport { background: transparent; }
QLabel { background: transparent; }
QLabel[role='heading'] { font-size: 23px; font-weight: bold; color: #FFE5A2; }
QLabel[role='section'] { font-size: 14px; font-weight: bold; color: #FFE9AF; }
QLabel[role='muted'] { color: #D5B681; font-size: 13px; }
QLabel[role='notice'] { color: #FFE7A1; background: #472410; border: 1px solid #A87537; border-radius: 5px; padding: 12px; }
QLabel[role='error'] { color: #FFE2C1; background: #681B14; border: 1px solid #D78252; border-radius: 5px; padding: 10px; }
QLineEdit, QSpinBox { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #DDBE7F,stop:1 #F7DC9A);
    border: 2px solid #8F592B; border-top-color: #4D2B17; border-radius: 5px;
    padding: 7px 9px; color: #392310; min-height: 22px; selection-background-color: #915028;
    selection-color: #FFF0BA; }
QLineEdit:focus, QSpinBox:focus { border: 2px solid #FFE39B; }
QLineEdit:disabled, QSpinBox:disabled { color: #D0AB72; background: #50351C; border-color: #71502A; }
QSpinBox::up-button, QSpinBox::down-button { width: 20px; }
QPushButton { background: transparent; border: none; padding: 10px 14px; font-weight: bold; min-height: 20px; }
QPushButton[role='tab'] { padding: 10px 12px; }
QCheckBox { spacing: 10px; }
QCheckBox::indicator { width: 20px; height: 20px; border: 2px solid #B3894C;
    border-radius: 4px; background: #32180E; }
QCheckBox::indicator:checked { background: #EDD181; border: 4px solid #75461E; }
QCheckBox:focus { color: #FFF9CC; }
QCheckBox:disabled { color: #C09B72; }
QPlainTextEdit, QTextEdit { color: #F5DDA3; background: #28140C; border: 2px solid #A26B35; border-radius: 6px;
    padding: 12px; font-family: 'Cascadia Mono', 'Consolas'; font-size: 13px; }
QScrollArea, QStackedWidget { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical { background: #CA9B51; border-radius: 3px; min-height: 26px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QToolTip { color: #FFF0BA; background: #351B0C; border: 1px solid #E0B65F; padding: 6px; }
QSlider::groove:horizontal { height: 4px; background: #30180C; border: 1px solid #996C35; }
QSlider::sub-page:horizontal { background: #C59B50; }
QSlider::handle:horizontal { background: #F1D18A; border: 1px solid #795122; width: 13px; margin: -6px 0; border-radius: 3px; }
QSlider:focus::handle:horizontal { background: #FFF0BB; border: 2px solid #FFE19A; }
"""


def font(size, family="Trebuchet MS", bold=False):
    result = QFont(family)
    result.setPixelSize(size)
    result.setBold(bold)
    return result


def panel_path(rect, corner=9):
    """Cut corners echo the carved frames on ALBW's equipment screen."""
    x, y, right, bottom = rect.left(), rect.top(), rect.right(), rect.bottom()
    c = min(corner, rect.width() / 4, rect.height() / 4)
    path = QPainterPath()
    path.moveTo(x + c, y)
    for px, py in ((right - c, y), (right, y + c), (right, bottom - c),
                   (right - c, bottom), (x + c, bottom), (x, bottom - c), (x, y + c)):
        path.lineTo(px, py)
    path.closeSubpath()
    return path


def draw_wood_panel(painter, rect, variant="red", border=True):
    painter.save()
    path = panel_path(rect, 14 if variant == "red" else 7)
    painter.setClipPath(path)
    gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    colors = {
        "red": ("#65150D", "#84291B", "#50140C"),
        "dark": ("#3E230E", "#4A2A13", "#261207"),
        "inset": ("#41200E", "#633519", "#361B0C"),
    }[variant]
    for position, color in zip((0, .5, 1), colors):
        gradient.setColorAt(position, QColor(color))
    painter.fillRect(rect, gradient)
    painter.setOpacity(.08 if variant == "red" else .16)
    grain = ALBWAssetStore.get().pixmap("mahogany_grain.png", 480, 480)
    painter.drawTiledPixmap(rect.toRect(), grain)
    painter.setOpacity(1)
    painter.setClipping(False)
    if border:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#180A03"), 2))
        painter.drawPath(path)
        painter.setPen(QPen(QColor("#B77A36"), .8))
        painter.drawPath(panel_path(rect.adjusted(1.2, 1.2, -1.2, -1.2), 13 if variant == "red" else 6))
    painter.restore()


def draw_selection(painter, rect, color=GOLD):
    """Four corner brackets, like the equipment selection cursor."""
    painter.save()
    pen = QPen(QColor(color), 3)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    for x, y, dx, dy in ((rect.left(), rect.top(), 1, 1), (rect.right(), rect.top(), -1, 1),
                         (rect.left(), rect.bottom(), 1, -1), (rect.right(), rect.bottom(), -1, -1)):
        path = QPainterPath()
        path.moveTo(x, y + dy * 6)
        path.lineTo(x, y)
        path.lineTo(x + dx * 6, y)
        painter.drawPath(path)
    painter.restore()


class WoodPanel(QWidget):
    def __init__(self, variant="inset", parent=None):
        super().__init__(parent)
        self.variant = variant

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw_wood_panel(painter, QRectF(self.rect()).adjusted(2, 2, -2, -2), self.variant)


class SkinButton(QPushButton):
    """Native button behavior with ALBW's warm, bevelled wooden surfaces."""
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.paint_skin(p)

    def paint_skin(self, p):
        role = self.property("role")
        bright = role == "primary" or self.isChecked()
        enabled = self.isEnabled()
        r = QRectF(self.rect()).adjusted(2, 2, -2, -3)
        if self.isDown():
            r.translate(0, 1)
        fill = QLinearGradient(r.topLeft(), r.bottomLeft())
        if not enabled:
            palette = ("#795630", "#654325", "#4E301B")
        elif bright:
            palette = ("#F1CD72", "#C79236", "#AC7125")
        elif self.underMouse():
            palette = ("#BD8B41", "#8E5C27", "#5D3517")
        else:
            palette = ("#92632E", "#70461F", "#4C2A13")
        for pos, color in zip((0, .48, 1), palette):
            fill.setColorAt(pos, QColor(color))
        path = panel_path(r, 6)
        p.fillPath(path, fill)
        p.setPen(QPen(QColor("#271106"), 2))
        p.drawPath(path)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor("#F3CF80" if bright and enabled else "#B68A4B"), 1))
        p.drawPath(panel_path(r.adjusted(2, 2, -2, -2), 4))
        p.setPen(QPen(QColor(255, 230, 166, 32), 1))
        for offset in (8, 12):
            p.drawLine(int(r.left() + 8), int(r.top() + offset), int(r.right() - 8), int(r.top() + offset))
        if enabled and (self.hasFocus() or (self.isChecked() and role == "tab")):
            draw_selection(p, r.adjusted(1, 1, -1, -1), "#FFE4A2")
        p.setFont(self.font())
        text_rect = r.adjusted(8, 0, -8, 0)
        text = p.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, int(text_rect.width()))
        p.setPen(QColor("#FCE7AC" if bright and enabled else "#271005"))
        p.drawText(text_rect.translated(0, 1), Qt.AlignmentFlag.AlignCenter, text)
        p.setPen(QColor("#3A230D" if bright and enabled else ("#FFF0BF" if enabled else "#C5AA82")))
        p.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, text)


class MorphButton(SkinButton):
    """The in-game F8 button: wooden button, folded into a faint Triforce when idle.

    morph runs from 0 (button) to 1 (Triforce); the owner animates it and the
    geometry, this widget only draws the cross-fade.
    """
    hovered = pyqtSignal(bool)
    IDLE_OPACITY = .55

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.morph = 0.0

    def set_morph(self, value):
        self.morph = max(0.0, min(1.0, value))
        self.update()

    def enterEvent(self, event):
        self.hovered.emit(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hovered.emit(False)
        super().leaveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self.morph < 1:
            p.setOpacity(1 - self.morph)
            self.paint_skin(p)
        if self.morph > 0:
            p.setOpacity(self.morph * self.IDLE_OPACITY)
            size = min(self.width(), self.height()) - 4
            emblem = ALBWAssetStore.get().pixmap("dual_triforce.png", size, size)
            p.drawPixmap((self.width() - emblem.width()) // 2, (self.height() - emblem.height()) // 2, emblem)


class SkinSlider(QSlider):
    """A fine brass rail; keep Qt's keyboard, mouse and accessible value behavior."""
    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setFixedHeight(24)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        x = 7 + QStyle.sliderPositionFromValue(self.minimum(), self.maximum(), self.value(), max(1, self.width() - 14))
        y = self.height() / 2
        p.setPen(QPen(QColor("#A4783C"), .8))
        p.setBrush(QColor("#2F180D"))
        p.drawRoundedRect(QRectF(7, y - 2, self.width() - 14, 4), 2, 2)
        p.setPen(QPen(QColor("#D1A656" if self.isEnabled() else "#86603A"), 2))
        p.drawLine(7, round(y), x, round(y))
        p.setPen(QPen(QColor("#FFE9AF" if self.hasFocus() else "#5F3919"), 1))
        p.setBrush(QColor("#EAC779" if self.isEnabled() else "#886442"))
        p.drawPath(panel_path(QRectF(x - 6, y - 8, 12, 16), 3))


def draw_hearts(painter, rect, current, maximum, largest=15):
    """Use the existing ALBW HUD textures while preserving quarter hearts."""
    count = max(1, min(20, (maximum + 3) // 4))
    size = int(min(largest, (rect.width() - 2 * (count - 1)) / count))
    store = ALBWAssetStore.get()
    full = store.pixmap("heart_full.png", size, size)
    empty = store.pixmap("heart_empty.png", size, size)
    for i in range(count):
        x, y = rect.x() + i * (size + 2), rect.y()
        painter.drawPixmap(int(x), int(y), empty)
        amount = max(0, min(4, current - i * 4)) / 4
        painter.save()
        painter.setClipRect(QRectF(x, y, size * amount, size))
        painter.drawPixmap(int(x), int(y), full)
        painter.restore()


class TunicButton(QAbstractButton):
    def __init__(self, index, parent=None):
        super().__init__(parent)
        self.index = index
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setText(TUNICS[index][0])
        self.setAccessibleName("Tunique " + self.text().lower())
        self.setToolTip(self.accessibleName())
        self.setMinimumSize(42, 64)

    def sizeHint(self):
        return QSize(48, 64)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        name, asset, color = TUNICS[self.index]
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        draw_wood_panel(p, r, "inset")
        if self.isChecked():
            draw_selection(p, r.adjusted(2, 2, -2, -2))
        pm = ALBWAssetStore.get().pixmap(asset, 34, 34)
        p.drawPixmap((self.width() - pm.width()) // 2, 5, pm)
        p.setFont(font(11, bold=self.isChecked()))
        p.setPen(QColor(INK if self.isChecked() else MUTED))
        p.drawText(QRectF(1, 40, self.width() - 2, 18), Qt.AlignmentFlag.AlignCenter, name)
        if self.hasFocus():
            p.setPen(QPen(QColor("#FFF1BC"), 1, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(4, 4, -4, -4), 3, 3)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
        else:
            super().keyPressEvent(event)


class TunicSelector(QWidget):
    tunicChanged = pyqtSignal(int)

    def __init__(self, initial_tunic=0, parent=None):
        super().__init__(parent)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        for index in TUNICS:
            button = TunicButton(index)
            self.group.addButton(button, index)
            layout.addWidget(button)
        self.set_tunic(initial_tunic)
        self.group.idClicked.connect(self.tunicChanged)

    def set_tunic(self, index):
        self.group.button(index % 4).setChecked(True)


class PartyRoster(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.players = []
        self.setMinimumHeight(256)
        self.setAccessibleName("Compagnons du salon")

    def set_players(self, players):
        if self.players != players[:4]:
            self.players = [dict(p) for p in players[:4]]
            self.setAccessibleDescription(", ".join(str(p.get("name", "Link")) for p in self.players))
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        for i in range(4):
            y = i * 64
            p.setPen(QPen(QColor("#AC6538"), 1))
            if i:
                p.drawLine(0, y, self.width(), y)
            if i >= len(self.players):
                p.setPen(QPen(QColor("#C99B5C"), 1, Qt.PenStyle.DashLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(panel_path(QRectF(4, y + 10, 44, 44), 5))
                p.setFont(font(22))
                p.drawText(QRectF(4, y + 10, 42, 42), Qt.AlignmentFlag.AlignCenter, "+")
                p.setFont(font(14))
                p.setPen(QColor(MUTED))
                p.drawText(QRectF(60, y + 12, self.width() - 62, 42), Qt.AlignmentFlag.AlignVCenter, "Place libre")
                continue
            player = self.players[i]
            _, asset, _ = TUNICS[int(player.get("tunic_color", 0)) % 4]
            draw_wood_panel(p, QRectF(1, y + 6, 50, 50), "inset")
            pm = ALBWAssetStore.get().pixmap(asset, 42, 42)
            p.drawPixmap(5, y + 10, pm)
            p.setPen(QColor(INK))
            p.setFont(font(15, bold=True))
            name = str(player.get("name", "Link")) + (" · vous" if player.get("is_self") else "")
            name = p.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, max(30, self.width() - 65))
            p.drawText(QRectF(60, y + 5, self.width() - 62, 25), Qt.AlignmentFlag.AlignVCenter, name)
            p.setFont(font(12))
            p.setPen(QColor(MUTED))
            world = str(player.get("world", "En attente du jeu")) if player.get("in_game") else "En attente du jeu"
            world = p.fontMetrics().elidedText(world, Qt.TextElideMode.ElideRight, max(30, self.width() - 65))
            p.drawText(QRectF(60, y + 29, self.width() - 62, 18), Qt.AlignmentFlag.AlignVCenter, world)
            if player.get("in_game"):
                draw_hearts(p, QRectF(60, y + 47, self.width() - 68, 15),
                            int(player.get("current_hp", 0)), int(player.get("max_hp", 12)))
