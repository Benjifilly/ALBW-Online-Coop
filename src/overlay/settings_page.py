"""HUD placement and controller preferences, applied immediately."""
from PyQt6.QtWidgets import QCheckBox, QHBoxLayout, QVBoxLayout, QWidget
from src.overlay.player_hud import HudPreview
from src.overlay.gamepad import binding_label
from src.overlay.menu_widgets import SkinSlider


class SettingsPage(QWidget):
    def __init__(self, overlay):
        super().__init__()
        from src.overlay.coop_panel import label, button
        self.overlay = overlay
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(label("À votre façon", "heading"))
        layout.addWidget(label('Dans le monde','section'))
        self.world_rendering=QCheckBox('Couleurs et compagnon 3D')
        self.world_rendering.setChecked(overlay.world_rendering)
        self.world_rendering.setEnabled(not overlay.preview_only)
        self.world_rendering.toggled.connect(overlay.on_world_rendering_changed)
        layout.addWidget(self.world_rendering)
        self.render_status=label('','muted')
        layout.addWidget(self.render_status)
        layout.addSpacing(4)
        layout.addWidget(label('Affichage des joueurs','section'))
        self.enabled = QCheckBox("Afficher les portraits et les cœurs")
        self.enabled.setChecked(overlay.hud_settings["enabled"])
        self.enabled.toggled.connect(lambda checked: overlay.set_hud_option("enabled", checked))
        layout.addWidget(self.enabled)
        self.preview = HudPreview()
        layout.addWidget(self.preview)
        self.sliders, self.values = {}, {}
        for key, title, low, high in (("scale", "Taille", 65, 175), ("opacity", "Opacité", 30, 100),
                                       ("x", "Horizontal", 0, 100), ("y", "Vertical", 0, 100)):
            row = QHBoxLayout()
            caption = label(title, "muted")
            caption.setMinimumWidth(70)
            slider = SkinSlider()
            slider.setRange(low, high)
            slider.setValue(overlay.hud_settings[key])
            slider.setAccessibleName(title + " de l’affichage en jeu")
            slider.setMinimumWidth(50)
            caption.setBuddy(slider)
            value = label(f"{slider.value()} %", "muted")
            value.setFixedWidth(44)
            slider.valueChanged.connect(lambda v, k=key: overlay.set_hud_option(k, v))
            self.sliders[key], self.values[key] = slider, value
            row.addWidget(caption)
            row.addWidget(slider, 1)
            row.addWidget(value)
            layout.addLayout(row)
        row = QHBoxLayout()
        self.move_button = button("Placer à la souris", overlay.begin_hud_edit)
        self.reset_button = button("Réinitialiser", overlay.reset_hud)
        row.addWidget(self.move_button, 1)
        row.addWidget(self.reset_button)
        layout.addLayout(row)
        layout.addSpacing(4)
        layout.addWidget(label("Raccourci manette", "section"))
        self.pad_status = label("Recherche de manette…", "muted")
        layout.addWidget(self.pad_status)
        row = QHBoxLayout()
        self.record_button = button("", overlay.record_gamepad_shortcut)
        self.disable_button = button("Désactiver", overlay.disable_gamepad_shortcut)
        row.addWidget(self.record_button, 1)
        row.addWidget(self.disable_button)
        layout.addLayout(row)
        self.pad_hint = label("", "muted")
        self.pad_hint.hide()
        layout.addWidget(self.pad_hint)
        layout.addSpacing(4)
        self.detection = label("", "muted")
        layout.addWidget(self.detection)
        layout.addStretch(1)
        self.sync_controls()

    def sync_controls(self):
        for key, slider in self.sliders.items():
            slider.blockSignals(True)
            slider.setValue(self.overlay.hud_settings[key])
            slider.blockSignals(False)
            self.values[key].setText(f"{slider.value()} %")
        self.enabled.blockSignals(True)
        self.enabled.setChecked(self.overlay.hud_settings["enabled"])
        self.enabled.blockSignals(False)
        enabled = self.overlay.hud_settings["enabled"]
        for slider in self.sliders.values():
            slider.setEnabled(enabled)
        self.move_button.setEnabled(enabled)
        self.update_pad_status()

    def update_pad_status(self):
        names = self.overlay.gamepad_names
        self.pad_status.setText(" · ".join(names) if names else "Branchez une manette PlayStation, Switch ou Xbox.")
        reader = getattr(self.overlay, "gamepads", None)
        if reader and reader.error:
            self.pad_status.setText("Manette indisponible. Relancez run_coop.bat pour vérifier les composants.")
        recording = self.overlay.pad_shortcut.recording
        self.record_button.setText("Annuler l’enregistrement" if recording else
                                   binding_label(self.overlay.pad_shortcut.binding, names[0] if names else "") + "  ·  Modifier")
        self.disable_button.setEnabled(bool(self.overlay.pad_shortcut.binding) and not recording)
        # Only the recording needs instructions; the timeout message is set by the overlay.
        if recording:self.pad_hint.setText("Pressez votre combinaison, puis relâchez-la.")
        self.pad_hint.setVisible(recording)

    def update_state(self, state):
        self.preview.set_state(state, self.overlay.hud_settings["opacity"])
        # Silent when it works; a short action when it does not.
        self.render_status.setText({
            'needs_launcher':'Relancez le jeu depuis run_coop.bat pour activer la liaison.',
            'unsupported':'Disponible pour la version EUR.',
            'waiting_game':'Ouvrez une sauvegarde.',
            'preparing':'Préparation du compagnon…',
            'other_room':'Votre compagnon est dans une autre zone.',
            'changing_room':'Changement de zone…',
            'model_unavailable':'Relancez cette copie Azahar pour recréer le compagnon.',
            'restore_pending':'Reprenez le jeu pour terminer la restauration.',
            'error':'Rendu interrompu : voir le journal.',
        }.get(state.get('appearance_status','off'),''))
        self.render_status.setHidden(not self.render_status.text())
        if self.overlay.preview_only:
            self.detection.setText("Aperçu : réglages non enregistrés.")
        elif state.get("in_game"):
            self.detection.setText("")
        elif state.get("detection_status") == "albw_detected":
            self.detection.setText("ALBW reconnu : ouvrez une sauvegarde.")
        else:
            self.detection.setText("Lancez le jeu depuis run_coop.bat : il active la liaison d’Azahar.")
        self.detection.setHidden(not self.detection.text())
