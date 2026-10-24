"""The responsive co-op menu. Network and memory operations stay in the controller."""
import html
import ipaddress
import threading

from PyQt6.QtCore import Qt, QRectF, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QAbstractSpinBox, QApplication, QBoxLayout, QButtonGroup, QCheckBox, QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
    QLabel, QLineEdit, QTextEdit, QScrollArea, QGridLayout,
    QSpinBox, QVBoxLayout, QWidget, QComboBox,
)
from src.console import log_tone
from src.overlay.menu_widgets import (
    ALBWAssetStore, GOLD, MENU_STYLE, TUNICS, PartyRoster, SkinButton, TunicSelector,
    WoodPanel, draw_wood_panel,
)


TAB_NAMES = ("Héros", "Salon", "Compagnons", "Journal", "Réglages")
TAB_SESSION, TAB_SETTINGS = 1, 4
# Journal colours: time, ordinary line, something that needs attention, good news.
LOG_COLOURS = {"time": "#A88A5C", "plain": "#F5DDA3", "problem": "#FF9F7E", "good": "#B9DA8C"}


def session_summary(state):
    """Each service's state, appended by « Copier » to diagnose another PC."""
    value = lambda key, empty="?": empty if state.get(key) is None else state.get(key)
    error = lambda key: f" · {state[key]}" if state.get(key) else ""
    return "\n".join((
        "État des services",
        f"Mod {value('version')} · {value('net_mode')} · {value('connection_state')} · "
        f"{value('latency_ms')} ms · en jeu {value('in_game')}",
        f"Rendu : {value('appearance_status')} · compagnon {'visible' if state.get('companion_visible') else 'non affiché'}",
        f"Monde : {value('world_sync_status')} · observés {value('world_events_observed', 0)} · "
        f"appliqués {value('world_events_applied', 0)} · rejoués {value('world_events_seeded', 0)} · "
        f"ignorés {value('world_events_skipped', 0)} · perdus {value('world_events_dropped', 0)} · "
        f"relances {value('world_sync_recoveries', 0)} · attente {value('world_waiting_target', '-')}"
        + error("world_sync_error"),
        f"Ennemis : {value('enemy_sync_status')}" + error("enemy_sync_error"),
        f"Butins : {value('shared_loot_status')}" + error("shared_loot_error"),
        f"Progression : {value('shared_progress_status')}" + error("shared_progress_error"),
    ))


def label(text, role="", parent=None):
    widget = QLabel(text, parent)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    widget.setProperty("role", role)
    if role == "heading":
        shadow = QGraphicsDropShadowEffect(widget)
        shadow.setBlurRadius(2)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor("#290A04"))
        widget.setGraphicsEffect(shadow)
    return widget


def button(text, callback, role=""):
    widget = SkinButton(text)
    widget.setProperty("role", role)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.clicked.connect(callback)
    return widget


class MenuHeader(QWidget):
    def __init__(self, preview=False, parent=None):
        super().__init__(parent)
        self.setFixedHeight(112)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 18, 100, 20)
        # Game status line, readable from every tab.
        self.kicker = label("Aperçu" if preview else "")
        self.kicker.setStyleSheet("color: #DBB773; font-size: 13px;")
        self.title = label("Partie coopérative")
        self.title.setStyleSheet("color: #FFE59A; font-family: 'Trebuchet MS'; font-size: 29px; font-weight: bold;")
        shadow = QGraphicsDropShadowEffect(self.title)
        shadow.setBlurRadius(3)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor("#160900"))
        self.title.setGraphicsEffect(shadow)
        layout.addWidget(self.kicker)
        layout.addWidget(self.title)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        draw_wood_panel(p, QRectF(self.rect()).adjusted(1, 1, -1, -1), "dark")
        # The red title inset and curled ends come from the equipment description panel.
        title_rect = QRectF(self.title.geometry()).adjusted(-12, -4, 0, 4)
        draw_wood_panel(p, title_rect, "red", border=False)
        for right in (False, True):
            p.save()
            p.translate(title_rect.right() - 2 if right else title_rect.left() + 2, title_rect.top())
            if right:
                p.scale(-1, 1)
            h = title_rect.height()
            curve = QPainterPath()
            curve.moveTo(9, 4)
            curve.cubicTo(0, 1, 0, h * .27, 2, h * .5)
            curve.cubicTo(0, h * .73, 0, h - 1, 9, h - 4)
            p.setPen(QPen(QColor(GOLD), 2))
            p.drawPath(curve)
            p.restore()
        small = self.width() < 500
        emblem = ALBWAssetStore.get().pixmap("dual_triforce.png", 30 if small else 42, 54)
        p.drawPixmap(self.width() - (54 if small else 80), (self.height() - emblem.height()) // 2, emblem)


class PageStack(QWidget):
    """Hidden pages must not force the current page to scroll."""
    def __init__(self):
        super().__init__()
        self._pages = []
        self._index = 0
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

    def addWidget(self, page):
        self._layout.addWidget(page)
        page.setVisible(not self._pages)
        self._pages.append(page)

    def setCurrentIndex(self, index):
        self._index = index
        for i, page in enumerate(self._pages):
            page.setVisible(i == index)
        self.updateGeometry()

    def currentIndex(self):
        return self._index

class CoopPanel(QFrame):
    probe_finished=pyqtSignal(object,object)

    def __init__(self, overlay):
        super().__init__(overlay)
        self.overlay = overlay
        self.setObjectName("menu")
        self.setStyleSheet(MENU_STYLE)
        self.setMinimumSize(300, 240)
        self._last_logs = None
        self._logs = []
        self._state = {}
        self._compact = None
        self._probe_ticket=None
        self.probe_finished.connect(self._joined_probe)
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 12)
        root.setSpacing(0)
        self.header = MenuHeader(overlay.preview_only)
        root.addWidget(self.header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.viewport().setObjectName("menuViewport")
        self.body = QWidget()
        self.body.setObjectName("menuBody")
        self.body.setAutoFillBackground(False)
        self.columns = QBoxLayout(QBoxLayout.Direction.LeftToRight, self.body)
        self.columns.setContentsMargins(24, 22, 24, 20)
        self.columns.setSpacing(28)
        self.content = self._build_content()
        self.columns.addWidget(self.content, 1)
        self.scroll.setWidget(self.body)
        root.addWidget(self.scroll, 1)

        self.message = label("", "notice")
        self.message.setContentsMargins(24, 0, 24, 6)
        self.message.hide()
        root.addWidget(self.message)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        footer.setContentsMargins(24, 10, 24, 0)
        footer.addStretch(1)
        self.btn_exit = button("Quitter", overlay.close, "danger")
        self.btn_exit.setToolTip("Fermer le compagnon co-op")
        footer.addWidget(self.btn_exit)
        self.btn_resume = button("Retour au jeu", overlay.toggle_subscreen)
        footer.addWidget(self.btn_resume)
        root.addLayout(footer)

    def _build_hero(self):
        page, outer = self._page()
        outer.addWidget(label("Votre héros", "heading"))
        row = QHBoxLayout()
        row.setSpacing(24)
        outer.addLayout(row)
        frame = WoodPanel("inset")
        frame.setObjectName("profile")
        frame.setFixedSize(150, 150)
        frame_layout = QVBoxLayout(frame)
        self.portrait = QLabel()
        self.portrait.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.portrait.setAccessibleName("Portrait de votre héros")
        frame_layout.addWidget(self.portrait)
        row.addWidget(frame, 0, Qt.AlignmentFlag.AlignTop)
        layout = QVBoxLayout()
        layout.setSpacing(10)
        row.addLayout(layout, 1)
        name_label = label("Pseudo", "muted")
        self.name_edit = QLineEdit(self.overlay.local_name)
        self.name_edit.setMaxLength(24)
        self.name_edit.setPlaceholderText("Votre pseudo")
        self.name_edit.setAccessibleName("Votre pseudo")
        name_label.setBuddy(self.name_edit)
        self.name_edit.textEdited.connect(self.overlay.on_name_edited)
        self.name_edit.editingFinished.connect(self.overlay.save_preferences)
        layout.addWidget(name_label)
        layout.addWidget(self.name_edit)
        layout.addWidget(label("Couleur de tunique", "muted"))
        self.tunic_selector = TunicSelector(self.overlay.local_tunic)
        self.tunic_selector.tunicChanged.connect(self.overlay.on_tunic_selected)
        layout.addWidget(self.tunic_selector)
        self.tunic_note = label("Visible en jeu avec « Couleurs et compagnon 3D » (Réglages).", "muted")
        layout.addWidget(self.tunic_note)
        layout.addStretch(1)
        outer.addStretch(1)
        self.update_portrait()
        return page

    def update_portrait(self):
        self.portrait.setPixmap(ALBWAssetStore.get().pixmap(TUNICS[self.overlay.local_tunic][1], 120, 120))

    def _build_content(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)
        tabs = QGridLayout()
        self.tab_layout = tabs
        tabs.setSpacing(7)
        self.tabs = QButtonGroup(self)
        for index, text in enumerate(TAB_NAMES):
            tab = button(text, lambda checked=False, i=index: self.select_page(i), "tab")
            tab.setCheckable(True)
            self.tabs.addButton(tab, index)
            tabs.addWidget(tab, 0, index)
        layout.addLayout(tabs)
        self.pages = PageStack()
        self.pages.addWidget(self._build_hero())
        self.pages.addWidget(self._build_session())
        self.pages.addWidget(self._build_party())
        self.pages.addWidget(self._build_journal())
        from src.overlay.settings_page import SettingsPage
        self.settings_page = SettingsPage(self.overlay)
        self.pages.addWidget(self.settings_page)
        self.pages.setCurrentIndex(TAB_SESSION)
        self.tabs.button(TAB_SESSION).setChecked(True)
        layout.addWidget(self.pages, 1)
        return content

    def _page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        return page, layout

    def _build_session(self):
        page, layout = self._page()
        layout.addWidget(label("À vous de réunir le groupe", "heading"))
        choices = QHBoxLayout()
        choices.setSpacing(8)
        self.mode_group = QButtonGroup(self)
        self.btn_host = button("Créer un salon", lambda: self.set_join_mode(False), "choice")
        self.btn_join = button("Rejoindre", lambda: self.set_join_mode(True), "choice")
        for i, b in enumerate((self.btn_host, self.btn_join)):
            b.setCheckable(True)
            self.mode_group.addButton(b, i)
            choices.addWidget(b)
        self.btn_host.setChecked(True)
        layout.addLayout(choices)

        self.address_box = QWidget()
        address_layout = QVBoxLayout(self.address_box)
        address_layout.setContentsMargins(0, 0, 0, 0)
        address_layout.setSpacing(6)
        address_label = label("Code de salon ou adresse de l’hôte", "section")
        self.ip_edit = QLineEdit(self.overlay.preferences.get("host", ""))
        self.ip_edit.setPlaceholderText("Collez le code ALBW-… de l’hôte")
        self.ip_edit.setMaxLength(256)
        self.ip_edit.setAccessibleName("Code de salon ou adresse de l’hôte")
        address_label.setBuddy(self.ip_edit)
        self.ip_edit.returnPressed.connect(self.submit_session)
        address_layout.addWidget(address_label)
        address_layout.addWidget(self.ip_edit)
        self.address_box.hide()
        layout.addWidget(self.address_box)
        self.join_progress=QCheckBox('Rejoindre avec la progression de l’hôte')
        self.join_progress.setToolTip('Télécharge la dernière sauvegarde enregistrée. Une copie de secours précède le choix du fichier à remplacer.')
        self.join_progress.hide();layout.addWidget(self.join_progress)

        self.port_box=QWidget();port_row = QHBoxLayout(self.port_box)
        port_row.setContentsMargins(0,0,0,0)
        port_label = label("Port du salon", "section")
        self.port_edit = QSpinBox()
        self.port_edit.setRange(1, 65535)
        self.port_edit.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.port_edit.setValue(int(self.overlay.preferences.get("port", 45990)))
        self.port_edit.setAccessibleName("Port du salon")
        self.port_edit.setFixedWidth(118)
        port_label.setBuddy(self.port_edit)
        port_row.addWidget(port_label, 1)
        port_row.addWidget(self.port_edit)
        layout.addWidget(self.port_box)
        self.btn_connect = button("Créer le salon", self.submit_session, "primary")
        layout.addWidget(self.btn_connect)
        self.session_status = label("Aucun salon ouvert", "section")
        self.session_detail = label("", "muted")
        layout.addSpacing(6)
        layout.addWidget(self.session_status)
        layout.addWidget(self.session_detail)
        self.host_addresses=QComboBox();self.host_addresses.setAccessibleName('Adresse à donner à vos amis')
        self.host_addresses.setMinimumContentsLength(8)
        self.host_addresses.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        layout.addWidget(self.host_addresses)
        self.copy_address=button('Copier l’adresse',self.copy_host_address)
        layout.addWidget(self.copy_address)
        self.network_hint=label('','muted');layout.addWidget(self.network_hint)
        # Host: a friend whose packets this box filters sends back a reply code.
        self.reply_box=QWidget();reply_row=QHBoxLayout(self.reply_box);reply_row.setContentsMargins(0,0,0,0)
        self.reply_edit=QLineEdit();self.reply_edit.setPlaceholderText('Code de réponse d’un ami (ALBW-…)')
        self.reply_edit.setMaxLength(256);self.reply_edit.setAccessibleName('Code de réponse d’un ami')
        self.reply_edit.returnPressed.connect(self.open_for_friend)
        reply_row.addWidget(self.reply_edit,1);reply_row.addWidget(button('Ouvrir le passage',self.open_for_friend))
        self.reply_box.hide();layout.addWidget(self.reply_box)
        self.btn_progress=button('Reprendre la progression de l’hôte…',self.request_progress)
        self.btn_progress.hide();layout.addWidget(self.btn_progress)
        self.progress_status=label('','muted');layout.addWidget(self.progress_status)
        self.host_addresses.hide();self.copy_address.hide()
        self.btn_leave = button("Quitter le salon", self.leave_session, "danger")
        self.btn_leave.hide()
        layout.addWidget(self.btn_leave)
        self.btn_local = button("Essayer à deux sur ce PC…", self.open_local_test)
        self.btn_local.setEnabled(not self.overlay.preview_only)
        layout.addWidget(self.btn_local)
        layout.addStretch(1)
        return page

    def open_local_test(self):
        import os
        from pathlib import Path
        import subprocess
        import sys
        root = Path(__file__).resolve().parents[2]
        python = root / ".venv/Scripts/pythonw.exe"
        try:
            subprocess.Popen([str(python if python.exists() else Path(sys.executable)), "-m", "src.local_session"],
                             cwd=root, env=dict(os.environ, PYTHONUTF8="1"), creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError:
            self.show_message("Le lanceur local n’a pas pu s’ouvrir. Utilisez test_local_coop.bat.", error=True)

    def _build_party(self):
        page, layout = self._page()
        layout.setSpacing(10)
        self.party_heading = label("Vos compagnons", "heading")
        layout.addWidget(self.party_heading)
        # Who waits for a weathervane (life_sync.life_notice); hidden otherwise.
        self.life_note = label("", "notice")
        self.life_note.setWordWrap(True)
        self.life_note.hide()
        layout.addWidget(self.life_note)
        self.roster_widget = PartyRoster()
        layout.addWidget(self.roster_widget)
        self.soul_checkbox = QCheckBox("Cœurs liés (Soul Link)")
        self.soul_checkbox.setToolTip("Partage la vie entre les joueurs. Réglage disponible pour l’hôte.")
        self.soul_checkbox.toggled.connect(self.overlay.on_soullink_changed)
        layout.addWidget(self.soul_checkbox)
        self.btn_tp = button("Rejoindre un allié dans cette salle", self.overlay.on_teleport_clicked)
        layout.addWidget(self.btn_tp)
        layout.addStretch(1)
        return page

    def _build_journal(self):
        page, layout = self._page()
        layout.addWidget(label("Journal de session", "heading"))
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setAccessibleName("Journal de session")
        self.log_view.setMinimumHeight(250)
        self.log_view.setPlaceholderText("Aucun événement.")
        layout.addWidget(self.log_view, 1)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.log_filter = QButtonGroup(self)
        for index, text in enumerate(("Tout", "Problèmes")):
            choice = button(text, self._render_logs, "choice")
            choice.setCheckable(True)
            self.log_filter.addButton(choice, index)
            row.addWidget(choice)
        self.log_filter.button(0).setChecked(True)
        row.addStretch(1)
        self.btn_copy = button("Copier", self.copy_logs)
        row.addWidget(self.btn_copy)
        layout.addLayout(row)
        return page

    def _render_logs(self):
        problems_only = self.log_filter.checkedId() == 1
        rows = []
        for entry in self._logs:
            line, *detail = entry.split("\n")
            stamp, _, message = line.partition("  ")
            if not message:stamp, message = "", line
            tone = log_tone(message)
            if problems_only and tone != "problem":continue
            rows.append(f'<div style="margin:1px 0"><span style="color:{LOG_COLOURS["time"]}">{html.escape(stamp)}</span>'
                        f'&nbsp;&nbsp;<span style="color:{LOG_COLOURS[tone]}">{html.escape(message.strip())}</span></div>')
            # Technical details only where problems are examined.
            if problems_only:
                rows.extend(f'<div style="margin:0 0 1px 74px;color:{LOG_COLOURS["time"]}">{html.escape(text)}</div>'
                            for text in detail)
        bar = self.log_view.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 2
        old_value = bar.value()
        self.log_view.setHtml("".join(rows))
        bar.setValue(bar.maximum() if at_end else old_value)

    def select_page(self, index):
        self.pages.setCurrentIndex(index)
        self.pages.updateGeometry()
        self.tabs.button(index).setChecked(True)
        self.scroll.verticalScrollBar().setValue(0)
        self.message.hide()
        if self._compact:
            # After the stacked layout settles, keep the selected page in view.
            QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(self.content.y()))

    def set_join_mode(self, join):
        self.address_box.setVisible(join)
        self.join_progress.setVisible(join)
        self.port_box.setVisible(not join)
        self.btn_connect.setText("Rejoindre le salon" if join else "Créer le salon")
        self.message.hide()
        if join:
            self.ip_edit.setFocus()

    def submit_session(self):
        if self.overlay.preview_only:
            self.show_message("Aperçu uniquement : aucune connexion réseau n’est ouverte.")
            return
        controller = self.overlay.orchestrator
        if controller is None:
            return
        self.message.hide()
        self.btn_connect.setEnabled(False)
        port = self.port_edit.value()
        if self.mode_group.checkedId() == 1:
            from src.network.probe import probe_any
            from src.network.rendezvous import parse_target
            text=self.ip_edit.text().strip()
            targets=parse_target(text,45990)
            if not targets:
                self.show_message("Collez le code de salon de votre ami (ALBW-…), ou son adresse.", error=True)
                self.btn_connect.setEnabled(True)
                self.ip_edit.setFocus()
                return
            ticket=object();self._probe_ticket=ticket
            self.show_message('Vérification du salon…')
            def check():self.probe_finished.emit((ticket,text),probe_any(targets))
            threading.Thread(target=check,daemon=True).start()
            return
        else:
            ok = controller.start_hosting(port)
            message = f"Salon ouvert sur le port {port}. Vos amis peuvent vous rejoindre."
        self.overlay.save_preferences()
        self.show_message(message if ok else "Impossible d’ouvrir le salon. Consultez le journal pour les détails.", error=not ok)
        self.overlay.refresh_state()

    def _joined_probe(self,request,found):
        ticket,text=request
        if ticket is not self._probe_ticket:return
        self._probe_ticket=None;self.btn_connect.setEnabled(True)
        from src.network.probe import NO_HOST
        from src.network.rendezvous import PREFIX
        code=text.upper().startswith(PREFIX)
        answer=found[0] if found else None
        # A box that filters only answers once punched: a code still connects.
        if answer is None and not code:return self.show_message(NO_HOST,error=True)
        if answer and not answer['compatible']:return self.show_message(answer['detail'],error=True)
        if answer and answer.get('count',0)==answer['max']:return self.show_message('Le salon est complet.',error=True)
        ok=self.overlay.orchestrator.join_target(text)
        if ok and self.join_progress.isChecked():self.overlay.orchestrator.online_progress.auto=True
        self.overlay.save_preferences()
        self.show_message(('Connexion au salon…' if answer else
                           'Connexion en cours. Si rien ne se passe, copiez votre code de réponse ci-dessous pour l’hôte.')
                          if ok else NO_HOST,error=not ok)
        self.overlay.refresh_state()

    def open_for_friend(self):
        if not self.overlay.orchestrator:return
        error=self.overlay.orchestrator.open_for_friend(self.reply_edit.text())
        if error:return self.show_message(error,error=True)
        self.reply_edit.clear();self.show_message('Passage ouvert : votre ami se connecte dans quelques secondes.')

    def copy_host_address(self):
        address=self.host_addresses.currentData()
        if address:
            QApplication.clipboard().setText(address)
            self.show_message('Code copié. Envoyez-le à l’hôte.' if self._state.get('net_mode')=='client' else
                              'Copié. Envoyez-le à votre ami.')

    def request_progress(self):
        if self.overlay.orchestrator and self.overlay.orchestrator.online_progress.request():
            self.show_message('Téléchargement de la progression. Une fenêtre vous permettra de choisir le fichier à remplacer.')

    def leave_session(self):
        self._probe_ticket=None
        if self.overlay.orchestrator:
            self.overlay.orchestrator.leave_session()
        self.overlay.refresh_state()
        self.show_message("Vous avez quitté le salon.")

    def copy_logs(self):
        # One paste is enough to diagnose another PC: the journal, then each service's state.
        QApplication.clipboard().setText("\n".join(self._logs) + "\n\n" + session_summary(self._state))
        self.show_message("Journal et état des services copiés.")

    def show_message(self, text, error=False):
        self.message.setText(text)
        self.message.setProperty("role", "error" if error else "notice")
        self.message.style().unpolish(self.message)
        self.message.style().polish(self.message)
        self.message.show()

    def update_state(self, state, logs):
        self._state = state
        players = state.get("display_players", [])
        self.roster_widget.set_players(players)
        self.party_heading.setText(f"Vos compagnons  ·  {len(players)}/4")
        notice = state.get("life_notice", "")
        self.life_note.setText(notice)
        self.life_note.setVisible(bool(notice))
        mode = state.get("net_mode", "offline")
        rows=state.get('hosting_addresses',[]) if mode=='host' else []
        # The room code first: it carries every path, IPv6 and punching included.
        if mode=='host' and state.get('room_code'):rows=[dict(label='Code de salon',address=state['room_code']),*rows]
        if mode=='client' and state.get('reply_code'):rows=[dict(label='Votre code de réponse pour l’hôte',address=state['reply_code'])]
        if rows!=getattr(self,'_address_rows',None):
            self._address_rows=rows;self.host_addresses.clear()
            for row in rows:
                text=row['address'] if len(row['address'])<=28 else row['address'][:26]+'…'
                self.host_addresses.addItem(f"{row['label']} · {text}",row['address'])
        self.host_addresses.setVisible(bool(rows));self.copy_address.setVisible(bool(rows))
        self.copy_address.setText('Copier le code' if rows and rows[0]['address'].startswith('ALBW-') else 'Copier l’adresse')
        self.reply_box.setVisible(mode=='host' and not self.overlay.preview_only)
        hint=state.get('network_hint','') if mode=='host' else state.get('connection_detail','')
        if mode=='host' and state.get('room_code'):
            hint='Envoyez le code de salon à vos amis. Si l’un d’eux reste bloqué, collez ici son code de réponse.'
        self.network_hint.setText('\n'.join(filter(None,(hint,state.get('nat_note','')))))
        transfer=state.get('progress_transfer',{});receiving=transfer.get('state')=='receiving'
        self.btn_progress.setVisible(mode=='client')
        self.btn_progress.setEnabled(state.get('connection_state')=='connected' and not receiving and not self.overlay.preview_only)
        self.progress_status.setText(f"Progression de l’hôte · {transfer.get('percent',0)} %" if receiving else
            transfer.get('error','') if transfer.get('state')=='error' else
            'Progression reçue · choisissez le fichier dans la fenêtre d’import.' if transfer.get('state')=='complete' else '')
        peers = [p for p in players if not p.get("is_self")]
        self.settings_page.update_state(state)
        # Only what asks for an action: a working tunic or game needs no sentence.
        self.tunic_note.setHidden(bool(state.get('colour_applied')))
        detection = state.get("detection_status")
        if state.get("in_game"):status = "Partie prête"
        elif detection == "albw_detected":status = "ALBW reconnu · ouvrez une sauvegarde"
        elif detection == "other_game":status = "Autre jeu ouvert · lancez ALBW, version EUR ou USA"
        elif detection == "rpc_incompatible":status = "Serveur RPC incompatible · mettez Azahar à jour"
        elif detection == "read_error":status = "Données indisponibles · détails dans le journal"
        elif state.get("rpc_connected"):status = "Azahar connecté · lancez ALBW"
        elif self.overlay.azahar_hwnd:status = "Azahar ouvert · lancez ALBW ou activez son serveur RPC"
        else:status = "En attente du jeu · lancez ALBW dans Azahar"
        self.header.kicker.setText(("Aperçu · " if self.overlay.preview_only else "") + status)
        if mode == "host":
            self.session_status.setText("Salon ouvert")
            self.session_detail.setText(f"Port {state.get('port', self.port_edit.value())} · {len(peers)} connecté{'s' if len(peers)>1 else ''}")
        elif mode == "client":
            connection = state.get("connection_state")
            self.session_status.setText({"connected": "Connecté au salon", "full": "Salon complet",
                "incompatible": "Versions incompatibles", "duplicate": "Profil déjà connecté"}.get(
                    connection, "Échanges réseau actifs" if peers else "En attente de l’hôte…"))
            target = state.get("host_target")
            from src.network.rendezvous import format_endpoint
            self.session_detail.setText(format_endpoint(target) if target else
                                        'Code de salon' if self.ip_edit.text().strip().upper().startswith('ALBW-') else self.ip_edit.text())
        else:
            self.session_status.setText("Aucun salon ouvert")
            self.session_detail.setText("")
        active = mode != "offline"
        self.btn_leave.setVisible(active and not self.overlay.preview_only)
        for widget in (self.btn_connect, self.btn_host, self.btn_join, self.ip_edit, self.port_edit):
            widget.setEnabled(not active)
        self.soul_checkbox.blockSignals(True)
        self.soul_checkbox.setChecked(bool(state.get("soul_link", False)))
        self.soul_checkbox.blockSignals(False)
        self.soul_checkbox.setEnabled(mode == "host" and not self.overlay.preview_only)
        if state.get("connection_state"):
            self.soul_checkbox.setEnabled(mode == "host" and bool(state.get("in_game")) and not self.overlay.preview_only)
        if state.get("latency_ms") is not None:
            self.session_detail.setText(" · ".join(filter(None,(self.session_detail.text(),f"{state['latency_ms']} ms"))))
        if state.get('loss_percent') is not None:
            self.session_detail.setText(self.session_detail.text()+f" · pertes {state['loss_percent']:g} % · {state['quality']}")
        self.session_detail.setHidden(not self.session_detail.text())
        self.btn_tp.setEnabled(bool(state.get("can_teleport")) and not self.overlay.preview_only)
        text = "\n".join(logs)
        if text != self._last_logs:
            self._logs = list(logs)
            self._render_logs()
            self._last_logs = text
        self.btn_copy.setEnabled(bool(text))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        small = self.width() < 500
        if hasattr(self, "tab_layout"):
            for index in range(len(TAB_NAMES)):
                self.tab_layout.addWidget(self.tabs.button(index), index // 3 if small else 0, index % 3 if small else index)
        self.header.title.setStyleSheet("color: #FFE59A; font-family: 'Trebuchet MS'; font-weight: bold; font-size: " + ("22px;" if small else "29px;"))
        self.header.layout().setContentsMargins(20 if small else 30, 14, 64 if small else 100, 14)
        compact = self.width() < 700
        if compact == self._compact:
            return
        self._compact = compact
        self.portrait.parentWidget().setVisible(not compact)
        self.header.setFixedHeight(96 if compact else 112)
        self.columns.setContentsMargins(16 if compact else 24, 18, 16 if compact else 24, 16)
        self.columns.setSpacing(18 if compact else 28)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw_wood_panel(painter, QRectF(self.rect()).adjusted(3, 3, -3, -3))
        if hasattr(self, "btn_exit"):
            top = self.btn_exit.y() - 8
            draw_wood_panel(painter, QRectF(8, top, self.width() - 16, self.height() - top - 8), "dark")
