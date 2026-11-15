"""Genera assets/icon.png (1024 px) a partir del icono que dibuja la propia app."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication  # noqa: E402

import backup_studio  # noqa: E402

app = QApplication(sys.argv)
out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "icon.png")
backup_studio.app_icon_pixmap(1024).save(out)
print(out)
