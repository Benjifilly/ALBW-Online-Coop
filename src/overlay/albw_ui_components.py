# -*- coding: utf-8 -*-
import os
from typing import Dict, Optional
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap


class ALBWAssetStore:
    _instance: Optional["ALBWAssetStore"] = None

    def __init__(self) -> None:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.assets_dir = os.path.join(base_dir, "assets")
        self._cache: Dict[str, QPixmap] = {}

    @classmethod
    def get(cls) -> "ALBWAssetStore":
        if cls._instance is None:
            cls._instance = ALBWAssetStore()
        return cls._instance

    def pixmap(self, filename: str, width: int = 0, height: int = 0) -> QPixmap:
        key = f"{filename}:{width}x{height}"
        if key in self._cache:
            return self._cache[key]
        raw_key = f"{filename}:0x0"
        if raw_key not in self._cache:
            path = os.path.join(self.assets_dir, filename)
            pm = QPixmap(path) if os.path.exists(path) else QPixmap()
            self._cache[raw_key] = pm
        base_pm = self._cache[raw_key]
        if base_pm.isNull() or (width <= 0 and height <= 0):
            return base_pm
        scaled = base_pm.scaled(
            width if width > 0 else base_pm.width(),
            height if height > 0 else base_pm.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._cache[key] = scaled
        return scaled

