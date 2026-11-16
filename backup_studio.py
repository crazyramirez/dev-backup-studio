"""
DEV Backup Studio — copia de seguridad incremental con progreso en vivo.

    pythonw backup_studio.py
"""

from __future__ import annotations

import ctypes
import faulthandler
import getpass
import html
import json
import math
import os
import plistlib
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import (QAbstractNativeEventFilter, QAbstractTableModel, QByteArray, QEasingCurve, QEvent,
                          QModelIndex, QObject, QPoint, QPointF, QRect, QRectF, QSettings, QSize, Qt, QTimer,
                          QUrl, QVariantAnimation, pyqtSignal)
from PyQt6.QtGui import (QBrush, QColor, QConicalGradient, QDesktopServices, QFont,
                         QFontDatabase, QFontMetricsF, QIcon, QImage, QKeySequence, QLinearGradient,
                         QPainter, QPainterPath, QPalette, QPen, QPixmap, QRadialGradient, QShortcut)
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (QAbstractButton, QAbstractItemView, QApplication, QButtonGroup, QComboBox,
                             QDialog, QFileDialog, QFrame, QGraphicsBlurEffect, QGraphicsOpacityEffect,
                             QGraphicsPixmapItem, QGraphicsScene, QGridLayout, QHBoxLayout, QHeaderView,
                             QLabel, QLayout, QLineEdit, QMainWindow, QMenu, QMessageBox,
                             QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
                             QSpinBox, QStackedWidget, QSystemTrayIcon, QTableView, QTableWidget,
                             QTableWidgetItem, QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                             QWidget)

import engine
import keepawake
import reminders
import updater

APP_NAME = "DEV Backup Studio"
APP_VERSION = "1.7.0"
FROZEN = getattr(sys, "frozen", False)
APP_DIR = os.path.dirname(os.path.abspath(__file__))
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"


def _data_dir() -> str:
    """Ajustes, índices y registros: %APPDATA% en Windows, Application Support en macOS."""
    if IS_WIN:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif IS_MAC:
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, APP_NAME)


DATA_DIR = _data_dir()
LOG_DIR = os.path.join(DATA_DIR, "logs")

DEFAULT_SRC = r"D:\DEV" if IS_WIN else os.path.expanduser("~/DEV")
DEFAULT_DST = r"E:\DEV" if IS_WIN else ""
PRESET_RULES = ["node_modules", "__pycache__", ".venv", "venv", ".next", ".nuxt", ".turbo",
                ".parcel-cache", ".cache", ".pio", "dist", "build", "target", ".gradle"]
DEFAULT_RULES = {"node_modules"}

# Coste fijo estimado por archivo (en bytes equivalentes) para que el tiempo
# restante tenga en cuenta que mil archivos pequeños tardan más que uno grande.
# En un disco USB mecánico cada archivo cuesta lo que ~1 MB de datos.
FILE_COST = 1024 * 1024
SIDEBAR_W = 428
TILE_MIN = 146  # ancho mínimo de una casilla de datos (Progreso, resumen)
DEFAULT_FOLDED = ["modo", "integridad", "energia", "actualizaciones"]  # tarjetas plegadas al empezar
DEFAULT_THREADS = 16

C = {
    "bg": "#0A0C11", "panel": "#0E1117", "card": "#13171F", "card2": "#1A1F2A",
    "line": "#212735", "line2": "#2B3243",
    "text": "#E9EBF1", "muted": "#8D94A7", "faint": "#5D6478",
    "accent": "#7C6CFF", "accent2": "#2DD4EE", "folder": "#F2C14E",
    "ok": "#34D399", "warn": "#FBBF24", "err": "#F87171",
}
FONT = {"ui": "Segoe UI", "display": "Segoe UI", "mono": "Consolas"}


def _p(d: str) -> str:
    return f'<path d="{d}"/>'


# Iconos propios en SVG (24×24, trazo redondeado): iguales en Windows y macOS.
ICONS = {
    "add": _p("M12 5v14M5 12h14"),
    "close": _p("M6 6l12 12M18 6L6 18"),
    "gear": _p("M4 7h9M17 7h3M4 17h3M11 17h9") + '<circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/>' + _p("M16 16l4.5 4.5"),
    "refresh": _p("M20 12a8 8 0 1 1-2.4-5.7") + _p("M20 4.5v5h-5"),
    "check": _p("M5 12.5l4.5 4.5L19 7.5"),
    "play": _p("M8 5.8v12.4a.8.8 0 0 0 1.2.7l9.8-6.2a.8.8 0 0 0 0-1.4L9.2 5.1A.8.8 0 0 0 8 5.8z"),
    "pause": _p("M9 5.5v13M15 5.5v13"),
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2.5"/>',
    "warn": _p("M10.3 4.2L2.6 17.6A2 2 0 0 0 4.3 20.6h15.4a2 2 0 0 0 1.7-3L13.7 4.2a2 2 0 0 0-3.4 0z")
            + _p("M12 9.5v4.5M12 17.2v.1"),
    "error": '<circle cx="12" cy="12" r="9"/>' + _p("M9.2 9.2l5.6 5.6M14.8 9.2l-5.6 5.6"),
    "flag": _p("M5.5 21V4.5") + _p("M5.5 4.5h11.5l-2.5 4.5 2.5 4.5H5.5"),
    "open": _p("M4 18.5V6.5A2 2 0 0 1 6 4.5h3.6l2 2.5H18a2 2 0 0 1 2 2v1.5")
            + _p("M4 18.5l2.4-6.2A2 2 0 0 1 8.3 11H21l-2.7 7.1a2 2 0 0 1-1.9 1.4H5a1 1 0 0 1-1-1z"),
    "folder": _p("M3.5 7A2.5 2.5 0 0 1 6 4.5h3.6l2.2 2.6H18A2.5 2.5 0 0 1 20.5 9.6v7.9A2.5 2.5 0 0 1 18 20H6"
                 "a2.5 2.5 0 0 1-2.5-2.5z"),
    "next": _p("M6 5.8v12.4l9-6.2z") + _p("M18.5 5.5v13"),
    "doc": _p("M14 3.5H7.5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V8z") + _p("M14 3.5V8h4.5"),
    "copy": '<rect x="8.5" y="8.5" width="11.5" height="11.5" rx="2"/>'
            + _p("M15.5 8.5V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7.5a2 2 0 0 0 2 2h2.5"),
    "tree": _p("M9.5 6.5H20M9.5 12H20M9.5 17.5H20M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01"),
    "sync": _p("M19.5 9.5A8 8 0 0 0 5.6 7.2L4 8.8") + _p("M4 4.5v4.3h4.3")
            + _p("M4.5 14.5a8 8 0 0 0 13.9 2.3l1.6-1.6") + _p("M20 19.5v-4.3h-4.3"),
    "timer": '<circle cx="12" cy="13.5" r="7.5"/>' + _p("M12 10v3.5l2.2 1.6M9.8 3h4.4M18.2 6.6l1.4-1.4"),
    "done": '<circle cx="12" cy="12" r="9"/>' + _p("M8.2 12.4l2.6 2.6 5-5.4"),
    "info": '<circle cx="12" cy="12" r="9"/>' + _p("M12 11v5.5M12 7.7v.1"),
    "clock": '<circle cx="12" cy="12" r="9"/>' + _p("M12 7v5l3.2 2"),
    "speed": _p("M4.3 17.5a8.6 8.6 0 1 1 15.4 0") + _p("M12 13.5l4.2-4.2") + '<circle cx="12" cy="13.5" r="1"/>',
    "drive": '<rect x="3" y="13" width="18" height="7" rx="2"/>' + _p("M5 13l2.3-6.6A2 2 0 0 1 9.2 5h5.6a2 2 0 0 1 "
                                                                       "1.9 1.4L19 13") + _p("M16.5 16.5h.01"),
    "filter": _p("M4 5h16l-6.2 7.6v5.6l-3.6 1.8v-7.4z"),
    "shield": _p("M12 3l7.5 3v5.6c0 4.5-3.1 8.2-7.5 9.4-4.4-1.2-7.5-4.9-7.5-9.4V6z") + _p("M8.8 12.2l2.2 2.2 4.3-4.4"),
    "download": _p("M12 4v11M7.5 10.5L12 15l4.5-4.5") + _p("M5 19.5h14"),
    "power": _p("M12 3.5v8") + _p("M7.2 6.4a7.5 7.5 0 1 0 9.6 0"),
    "arrow": _p("M5 12h14M13 6l6 6-6 6"),
    "back": _p("M19 12H5M11 6l-6 6 6 6"),
    "help": '<circle cx="12" cy="12" r="9"/>' + _p("M9.5 9.4a2.6 2.6 0 0 1 5 .9c0 1.8-2.5 2.2-2.5 3.9")
            + _p("M12 17.1v.1"),
    "spark": _p("M11 3.5l1.8 4.9 4.9 1.8-4.9 1.8L11 16.9l-1.8-4.9-4.9-1.8 4.9-1.8z")
             + _p("M18 14.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z"),
    "bell": _p("M6.2 10a5.8 5.8 0 1 1 11.6 0c0 4.6 1.9 6.3 1.9 6.3H4.3S6.2 14.6 6.2 10z")
            + _p("M10.1 19.6a2.1 2.1 0 0 0 3.8 0"),
    "more": '<circle cx="5.5" cy="12" r=".6"/><circle cx="12" cy="12" r=".6"/><circle cx="18.5" cy="12" r=".6"/>',
    "chevdown": _p("M7 10l5 5 5-5"),
    "chevright": _p("M10 7l5 5-5 5"),
    "arrowdown": _p("M12 5v14M6.5 13.5L12 19l5.5-5.5"),
}


class G:
    """Nombres de los iconos de ICONS."""
    ADD, CLOSE, GEAR, SEARCH, REFRESH = "add", "close", "gear", "search", "refresh"
    CHECK, PLAY, PAUSE, STOP, WARN = "check", "play", "pause", "stop", "warn"
    ERROR, FLAG, OPEN, FOLDER, NEXT = "error", "flag", "open", "folder", "next"
    DOC, COPY, TREE, SYNC, TIMER = "doc", "copy", "tree", "sync", "timer"
    DONE, INFO, CLOCK, SPEED, DRIVE, FILTER = "done", "info", "clock", "speed", "drive", "filter"
    SHIELD, DOWNLOAD, POWER = "shield", "download", "power"
    ARROW, BACK, HELP, SPARK, BELL, MORE = "arrow", "back", "help", "spark", "bell", "more"


# ── formato (es-ES) ──────────────────────────────────────────────────────────

def fmt_int(n) -> str:
    return f"{int(n):,}".replace(",", ".")


def fmt_dec(x: float, d: int = 1) -> str:
    return f"{x:,.{d}f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def fmt_bytes(n) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(n)} B"
            return f"{fmt_dec(n, 2 if abs(n) < 10 else 1)} {unit}"
        n /= 1024
    return ""


def fmt_speed(bps: float) -> str:
    return fmt_bytes(bps) + "/s"


def fmt_clock(sec: float, ms: bool = False) -> str:
    sec = max(0.0, sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if ms:
        whole = int(s)
        millis = min(999, int(round((s - whole) * 1000)))
        return f"{int(h):02d}:{int(m):02d}:{whole:02d},{millis:03d}"
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d}"


def fmt_human(sec: float) -> str:
    sec = int(max(0, sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} h {m:02d} min"
    if m:
        return f"{m} min {s:02d} s"
    return f"{s} s"


def pct(x: float) -> str:
    return f"{fmt_dec(x * 100, 1)} %"


def rgba(color: str, alpha: int) -> str:
    c = QColor(color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


# ── recursos gráficos ────────────────────────────────────────────────────────

def pick_fonts() -> None:
    families = set(QFontDatabase.families())
    system = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
    fixed = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()

    def pick(*names):
        return next((n for n in names if n in families), names[-1])

    FONT.update(
        ui=pick("Segoe UI Variable Text", "Segoe UI", "SF Pro Text", "Inter", system),
        display=pick("Segoe UI Variable Display", "Segoe UI", "SF Pro Display", "Inter", system),
        mono=pick("Cascadia Mono", "Cascadia Code", "SF Mono", "Menlo", "Consolas", fixed),
    )


def _render_svg(svg: str, painter: QPainter, rect: QRectF) -> None:
    QSvgRenderer(QByteArray(svg.encode())).render(painter, rect)


def icon_svg(name: str, color: str, stroke: float = 1.9) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</svg>')


def glyph_pixmap(name: str, color: str, px: int, dpr: float = 2.0) -> QPixmap:
    pm = QPixmap(int(px * dpr), int(px * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    _render_svg(icon_svg(name, color), p, QRectF(0, 0, px, px))
    p.end()
    return pm


def glyph_icon(name: str, color: str, px: int = 16, on_color: str | None = None) -> QIcon:
    icon = QIcon()
    icon.addPixmap(glyph_pixmap(name, color, px), QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(glyph_pixmap(name, on_color or color, px), QIcon.Mode.Normal, QIcon.State.On)
    icon.addPixmap(glyph_pixmap(name, C["faint"], px), QIcon.Mode.Disabled, QIcon.State.Off)
    return icon


def set_glyph(label: QLabel, name: str, color: str, px: int) -> None:
    label.setPixmap(glyph_pixmap(name, color, px))
    label.setFixedSize(px, px)


def glyph_label(name: str, color: str, px: int = 14) -> QLabel:
    lbl = QLabel()
    set_glyph(lbl, name, color, px)
    return lbl


def app_icon_pixmap(px: int) -> QPixmap:
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QLinearGradient(0, 0, px, px)
    g.setColorAt(0, QColor(C["accent"]))
    g.setColorAt(1, QColor(C["accent2"]))
    p.setBrush(g)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(QRectF(0, 0, px, px), px * 0.24, px * 0.24)
    m = px * 0.2
    _render_svg(icon_svg("sync", "#FFFFFF", 2.2), p, QRectF(m, m, px - 2 * m, px - 2 * m))
    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for px in (16, 20, 24, 32, 40, 48, 64, 128, 256, 512):
        icon.addPixmap(app_icon_pixmap(px))
    return icon


def write_assets() -> str:
    folder = os.path.join(tempfile.gettempdir(), "dev_backup_studio_assets")
    os.makedirs(folder, exist_ok=True)

    def chevron(d, color):
        return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
                f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.7" '
                'stroke-linecap="round" stroke-linejoin="round"/></svg>')

    files = {
        "check.svg": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
                      '<path d="M3.6 8.4l2.9 2.9 5.9-6.6" fill="none" stroke="#ffffff" '
                      'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>'),
        "down.svg": chevron("M4 6l4 4 4-4", C["muted"]),
        "up.svg": chevron("M4 10l4-4 4 4", C["muted"]),
        "right.svg": chevron("M6 4l4 4-4 4", C["muted"]),
        "open.svg": chevron("M4 6l4 4 4-4", C["text"]),
    }
    for name, svg in files.items():
        with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
            fh.write(svg)
    return folder.replace("\\", "/")


def build_qss(assets: str) -> str:
    v = dict(C, a=assets, ui=FONT["ui"], display=FONT["display"], mono=FONT["mono"],
             accent_soft=rgba(C["accent"], 42), accent_mid=rgba(C["accent"], 70),
             hover=rgba("#FFFFFF", 9))
    return """
* { outline: none; }
QWidget { color: %(text)s; font-family: "%(ui)s"; font-size: 13px; }
QMainWindow, QWidget#root { background: %(bg)s; }
QLabel { background: transparent; }
QLabel#h1 { font-family: "%(display)s"; font-size: 19px; font-weight: 700; }
QLabel#muted { color: %(muted)s; }
QLabel#faint { color: %(faint)s; font-size: 12px; }
QLabel#mono { font-family: "%(mono)s"; font-size: 12px; color: %(muted)s; }
QLabel#cardTitle { color: %(muted)s; font-size: 11px; font-weight: 700; }
QLabel#fieldLabel { color: %(muted)s; font-size: 12px; font-weight: 600; }
QLabel#barLabel { color: %(muted)s; font-size: 12px; font-weight: 600; }
QLabel#barValue { font-family: "%(display)s"; font-size: 13px; font-weight: 600; }
QLabel#pill { border-radius: 12px; padding: 4px 12px; font-size: 12px; font-weight: 700; }
QFrame#awake { border-radius: 12px; }
QFrame#awake QLabel { background: transparent; font-size: 12px; font-weight: 700; }

QFrame#card { background: %(card)s; border: 1px solid %(line)s; border-radius: 14px; }
QFrame#tile { background: %(card)s; border: 1px solid %(line)s; border-radius: 12px; }
QLabel#tileTitle { color: %(muted)s; font-size: 11px; font-weight: 700; }
QLabel#tileValue { font-family: "%(display)s"; font-size: 23px; font-weight: 600; }
QLabel#tileSub { color: %(faint)s; font-size: 11px; }
QFrame#footer { background: %(panel)s; border-top: 1px solid %(line)s; }
QFrame#segbar { background: %(panel)s; border: 1px solid %(line)s; border-radius: 11px; }
QFrame#hint { background: %(panel)s; border: 1px solid %(line)s; border-radius: 12px; }

QFrame#banner { border-radius: 14px; border: 1px solid %(line2)s; background: %(card)s; }
QFrame#banner[kind="info"] { border-color: rgba(45,212,238,70); background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 rgba(45,212,238,22), stop:1 rgba(124,108,255,10)); }
QFrame#banner[kind="run"] { border-color: rgba(124,108,255,90); background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 rgba(124,108,255,34), stop:1 rgba(45,212,238,10)); }
QFrame#banner[kind="ok"] { border-color: rgba(52,211,153,90); background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 rgba(52,211,153,30), stop:1 rgba(52,211,153,6)); }
QFrame#banner[kind="warn"] { border-color: rgba(251,191,36,90); background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 rgba(251,191,36,28), stop:1 rgba(251,191,36,6)); }
QFrame#banner[kind="err"] { border-color: rgba(248,113,113,100); background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 rgba(248,113,113,32), stop:1 rgba(248,113,113,6)); }
QLabel#bannerTitle { font-family: "%(display)s"; font-size: 15px; font-weight: 700; }
QLabel#bannerText { color: %(muted)s; font-size: 12px; }

QLineEdit, QSpinBox, QComboBox {
    background: %(panel)s; border: 1px solid %(line2)s; border-radius: 9px;
    padding: 7px 10px; selection-background-color: %(accent)s; min-height: 20px;
}
QLineEdit#path { font-family: "%(mono)s"; font-size: 12px; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border: 1px solid %(accent)s; }
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled { color: %(faint)s; border-color: %(line)s; }
QComboBox::drop-down { border: none; width: 28px; }
QComboBox::down-arrow { image: url(%(a)s/down.svg); width: 12px; height: 12px; }
QComboBox QAbstractItemView {
    background: %(card2)s; border: 1px solid %(line2)s; border-radius: 8px; padding: 4px;
    selection-background-color: %(accent_mid)s; selection-color: %(text)s; outline: none;
}
QSpinBox::up-button, QSpinBox::down-button { border: none; width: 20px; background: transparent; }
QSpinBox::up-arrow { image: url(%(a)s/up.svg); width: 10px; height: 10px; }
QSpinBox::down-arrow { image: url(%(a)s/down.svg); width: 10px; height: 10px; }

QPushButton {
    background: %(card2)s; border: 1px solid %(line2)s; border-radius: 10px;
    padding: 8px 16px; font-weight: 600;
}
QPushButton:hover { background: #222838; border-color: #3A4258; }
QPushButton:pressed { background: #181C26; }
QPushButton:disabled { color: %(faint)s; background: %(panel)s; border-color: %(line)s; }
QPushButton#icon { padding: 7px; }
QPushButton#primary {
    border: none; color: #ffffff; font-size: 14px; padding: 11px 26px;
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #7C6CFF, stop:1 #4F8BFF);
}
QPushButton#primary:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #8E80FF, stop:1 #64A0FF); }
QPushButton#primary:pressed { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #6A5AF0, stop:1 #4079F0); }
QPushButton#primary:disabled { background: #232838; color: %(faint)s; }
QPushButton#danger:hover { border-color: %(err)s; color: %(err)s; }
QPushButton#chip {
    background: transparent; border: 1px solid %(line2)s; border-radius: 13px;
    padding: 4px 12px; font-size: 12px; font-weight: 600; color: %(muted)s;
}
QPushButton#chip:hover { border-color: #3C455C; color: %(text)s; }
QPushButton#chip:checked { background: %(accent_soft)s; border-color: %(accent)s; color: %(text)s; }
QPushButton#chip:disabled { color: %(faint)s; border-color: %(line)s; }
QPushButton#seg { background: transparent; border: none; border-radius: 8px; padding: 7px 16px; color: %(muted)s; }
QPushButton#seg:hover { color: %(text)s; }
QPushButton#seg:checked { background: %(card2)s; color: %(text)s; }
QPushButton#link { background: transparent; border: none; color: %(accent2)s; padding: 4px 6px; }
QPushButton#link:hover { color: #7DE6F5; }
QPushButton#link:disabled { color: %(faint)s; }
QPushButton#help {
    background: transparent; border: 1px solid %(line2)s; border-radius: 13px;
    padding: 5px 13px 5px 10px; font-size: 12px; font-weight: 700; color: %(muted)s;
}
QPushButton#help:hover { color: %(text)s; border-color: %(accent)s; background: %(accent_soft)s; }
QPushButton#tourClose {
    background: rgba(8,10,15,165); border: 1px solid rgba(255,255,255,32); border-radius: 17px; padding: 0;
}
QPushButton#tourClose:hover { background: rgba(26,30,42,235); border-color: rgba(255,255,255,80); }
QPushButton#tourSkip { background: transparent; border: none; color: %(muted)s; padding: 8px; font-weight: 600; }
QPushButton#tourSkip:hover { color: %(text)s; }
QPushButton#tourBack { padding: 0 18px; border-radius: 12px; }
QPushButton#tourBack:focus { border-color: %(accent)s; }
QFrame#remind {
    border-radius: 14px; border: 1px solid rgba(251,191,36,85);
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 rgba(251,191,36,26), stop:1 rgba(124,108,255,12));
}
QLabel#remBadge { background: rgba(251,191,36,30); border: 1px solid rgba(251,191,36,60); border-radius: 12px; }
QPushButton#remGo {
    border: none; color: #ffffff; padding: 8px 16px; border-radius: 10px;
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #7C6CFF, stop:1 #4F8BFF);
}
QPushButton#remGo:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #8E80FF, stop:1 #64A0FF); }
QPushButton#remLater { padding: 8px 14px; border-radius: 10px; background: rgba(255,255,255,8); }
QPushButton#remClose, QPushButton#remMore { background: transparent; border: none; border-radius: 8px; padding: 0; }
QPushButton#remClose:hover, QPushButton#remMore:hover { background: %(hover)s; }
QLabel#group { color: %(faint)s; font-size: 10px; font-weight: 800; padding-left: 6px; }
QLabel#sectionSummary { color: %(muted)s; font-size: 12px; padding-top: 7px; }
QLabel#rowTitle { font-size: 13px; font-weight: 600; }
QLabel#rowSub { color: %(faint)s; font-size: 12px; }
QFrame#pathBlock { background: transparent; border: none; }
QFrame#remRow { background: %(panel)s; border: 1px solid %(line)s; border-radius: 11px; }
QFrame#remRow:hover { border-color: %(line2)s; background: #12161E; }
QLabel#remTitle { font-weight: 700; font-size: 13px; }
QLabel#remSub { font-size: 11px; }
QLabel#remTag {
    color: %(accent2)s; font-size: 9px; font-weight: 800; padding: 2px 6px;
    border: 1px solid rgba(45,212,238,70); border-radius: 6px;
}

QProgressBar { background: %(panel)s; border: none; border-radius: 4px; }
QProgressBar::chunk { border-radius: 4px; background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 %(accent)s, stop:1 %(accent2)s); }
QProgressBar#disk::chunk { background: %(faint)s; }

QTreeWidget, QTableWidget, QTableView {
    background: %(panel)s; border: 1px solid %(line)s; border-radius: 12px;
    padding: 4px; selection-background-color: transparent;
}
QCheckBox { spacing: 10px; background: transparent; }
QCheckBox:disabled { color: %(faint)s; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 5px; border: 1px solid #3A4258; background: %(bg)s; }
QCheckBox::indicator:hover { border-color: %(accent)s; }
QCheckBox::indicator:checked { background: %(accent)s; border-color: %(accent)s; image: url(%(a)s/check.svg); }
QDialog { background: %(card)s; }
QTextBrowser {
    background: %(panel)s; border: 1px solid %(line)s; border-radius: 12px; padding: 10px 14px;
    color: %(text)s; font-size: 13px;
}
QLabel#count { color: %(faint)s; font-size: 12px; }
QTreeView::item, QTableView::item { padding: 5px 2px; border: none; }
QTreeView::item:hover { background: %(hover)s; border-radius: 6px; }
QTreeView::item:selected, QTableView::item:selected { background: %(accent_soft)s; color: %(text)s; border-radius: 6px; }
QTreeView::branch { background: transparent; }
QTreeView::branch:has-children:!has-siblings:closed, QTreeView::branch:closed:has-children:has-siblings { image: url(%(a)s/right.svg); }
QTreeView::branch:open:has-children:!has-siblings, QTreeView::branch:open:has-children:has-siblings { image: url(%(a)s/open.svg); }
QTreeView::indicator { width: 15px; height: 15px; border-radius: 5px; border: 1px solid #3A4258; background: %(bg)s; }
QTreeView::indicator:hover { border-color: %(accent)s; }
QTreeView::indicator:checked { background: %(accent)s; border-color: %(accent)s; image: url(%(a)s/check.svg); }
QTreeView::indicator:unchecked:!enabled { background: %(panel)s; border-color: %(line)s; }
QHeaderView { background: transparent; }
QHeaderView::section {
    background: %(panel)s; color: %(muted)s; border: none; border-bottom: 1px solid %(line)s;
    padding: 8px 6px; font-size: 11px; font-weight: 700;
}
QTableCornerButton::section { background: %(panel)s; border: none; }

QPlainTextEdit {
    background: %(panel)s; border: 1px solid %(line)s; border-radius: 12px; padding: 8px 10px;
    font-family: "%(mono)s"; font-size: 12px; color: %(muted)s;
}
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 3px 2px; }
QScrollBar::handle:vertical { background: #2A3142; border-radius: 3px; min-height: 36px; }
QScrollBar::handle:vertical:hover { background: #39425A; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px 3px; }
QScrollBar::handle:horizontal { background: #2A3142; border-radius: 3px; min-width: 36px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QToolTip { background: %(card2)s; color: %(text)s; border: 1px solid %(line2)s; padding: 6px 8px; border-radius: 6px; }
QMenu { background: %(card2)s; border: 1px solid %(line2)s; padding: 6px; border-radius: 10px; }
QMenu::item { padding: 6px 18px; border-radius: 6px; }
QMenu::item:selected { background: %(accent_mid)s; }
QMessageBox { background: %(card)s; }
""" % v


def dark_palette() -> QPalette:
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: C["card"], QPalette.ColorRole.WindowText: C["text"],
        QPalette.ColorRole.Base: C["panel"], QPalette.ColorRole.AlternateBase: C["card"],
        QPalette.ColorRole.Text: C["text"], QPalette.ColorRole.Button: C["card2"],
        QPalette.ColorRole.ButtonText: C["text"], QPalette.ColorRole.Highlight: C["accent"],
        QPalette.ColorRole.HighlightedText: "#ffffff", QPalette.ColorRole.ToolTipBase: C["card2"],
        QPalette.ColorRole.ToolTipText: C["text"], QPalette.ColorRole.PlaceholderText: C["faint"],
        QPalette.ColorRole.Link: C["accent2"],
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(C["faint"]))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(C["faint"]))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(C["faint"]))
    return pal


# ── integración con el sistema ───────────────────────────────────────────────

class App(QApplication):
    """Deja que la ventana decida si se puede salir cuando lo pide el sistema."""
    quit_guard = None  # función que devuelve True si se puede salir

    def event(self, e):
        if e.type() == QEvent.Type.Quit and self.quit_guard is not None and not self.quit_guard():
            e.ignore()  # en macOS, cancela también el cierre de sesión o el apagado
            return True
        return super().event(e)


# Pausa larga: pasado este tiempo en pausa el equipo vuelve a poder suspenderse.
PAUSE_GRACE = 20 * 60

if IS_WIN:
    from ctypes import wintypes

    class PowerEvents(QAbstractNativeEventFilter):
        """Avisos de suspensión y reanudación de Windows (WM_POWERBROADCAST).

        Llegan una vez por cada ventana de nivel superior de la app: los manejadores
        tienen que poder llamarse varias veces seguidas.
        """
        WM_POWERBROADCAST, PBT_APMSUSPEND = 0x0218, 0x0004
        PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC = 0x0007, 0x0012

        def __init__(self, on_suspend, on_resume):
            super().__init__()
            self.on_suspend, self.on_resume = on_suspend, on_resume

        def nativeEventFilter(self, event_type, message):  # noqa: N802
            if bytes(event_type) == b"windows_generic_MSG" and message:
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == self.WM_POWERBROADCAST:
                    if msg.wParam == self.PBT_APMSUSPEND:
                        self.on_suspend()
                    elif msg.wParam in (self.PBT_APMRESUMESUSPEND, self.PBT_APMRESUMEAUTOMATIC):
                        self.on_resume()
            return False, 0


class Taskbar:
    """Barra de progreso en el icono de la barra de tareas (ITaskbarList3)."""
    OFF, BUSY, NORMAL, ERROR, PAUSED = 0, 1, 2, 4, 8

    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        self.ptr = None
        self._state = None
        if os.name != "nt":
            return
        try:
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort),
                            ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]

            ole32 = ctypes.windll.ole32
            clsid, iid = GUID(), GUID()
            ole32.CLSIDFromString("{56FDF344-FD6D-11d0-958A-006097C9A090}", ctypes.byref(clsid))
            ole32.CLSIDFromString("{ea1afb91-9e28-4b86-90e9-9e9f8a5eefaf}", ctypes.byref(iid))
            ptr = ctypes.c_void_p()
            if ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(ptr)) or not ptr:
                return
            vt = ctypes.cast(ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
            hr_init = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vt[3])
            self._value = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.HWND,
                                             ctypes.c_ulonglong, ctypes.c_ulonglong)(vt[9])
            self._set = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.HWND, ctypes.c_int)(vt[10])
            hr_init(ptr)
            self.ptr = ptr
        except Exception:  # noqa: BLE001 - sin barra de tareas no pasa nada
            self.ptr = None

    def state(self, flag: int) -> None:
        if self.ptr and flag != self._state:
            self._state = flag
            self._set(self.ptr, self.hwnd, flag)

    def value(self, done: float, total: float = 1.0) -> None:
        if self.ptr:
            self._value(self.ptr, self.hwnd, int(done * 10000), int(total * 10000))


def set_dark_titlebar(widget: QWidget) -> None:
    if os.name != "nt":
        return
    try:
        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        on = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), 4)
        bg = QColor(C["bg"])
        ref = ctypes.c_int(bg.red() | bg.green() << 8 | bg.blue() << 16)
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(ref), 4)
    except Exception:  # noqa: BLE001
        pass


# ── widgets ──────────────────────────────────────────────────────────────────

class FlowLayout(QLayout):
    def __init__(self, parent=None, spacing: int = 8):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._layout(QRect(0, 0, width, 0), dry=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._layout(rect, dry=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _layout(self, rect, dry):
        x, y, line_h = rect.x(), rect.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > rect.right() + 1 and line_h > 0:
                x, y, line_h = rect.x(), y + line_h + self._spacing, 0
            if not dry:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


class ElideLabel(QLabel):
    def __init__(self, text: str = "", mode=Qt.TextElideMode.ElideMiddle):
        super().__init__()
        self._full = ""
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(20)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - API de Qt
        if text == self._full and self.text():
            return
        self._full = text
        self.setToolTip(text if len(text) > 40 else "")
        self._refresh()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self):
        super().setText(self.fontMetrics().elidedText(self._full, self._mode, max(20, self.width())))


class Card(QFrame):
    def __init__(self, title: str | None = None, glyph: int | None = None, right: QWidget | None = None,
                 margins=(18, 16, 18, 18)):
        super().__init__()
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(*margins)
        self.body.setSpacing(12)
        if title:
            head = QHBoxLayout()
            head.setSpacing(8)
            if glyph:
                head.addWidget(glyph_label(glyph, C["accent2"], 14))
            lbl = QLabel(title.upper())
            lbl.setObjectName("cardTitle")
            head.addWidget(lbl)
            head.addStretch(1)
            if right:
                head.addWidget(right)
            self.body.addLayout(head)


def thin_bar(height: int = 6, name: str | None = None) -> QProgressBar:
    bar = QProgressBar()
    bar.setRange(0, 10000)
    bar.setTextVisible(False)
    bar.setFixedHeight(height)
    if name:
        bar.setObjectName(name)
    return bar


class Tile(QFrame):
    def __init__(self, title: str, glyph: int, color: str):
        super().__init__()
        self.setObjectName("tile")
        self.setMinimumWidth(TILE_MIN)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(15, 13, 15, 13)
        lay.setSpacing(3)
        head = QHBoxLayout()
        head.setSpacing(7)
        head.addWidget(glyph_label(glyph, color, 13))
        t = QLabel(title.upper())
        t.setObjectName("tileTitle")
        head.addWidget(t)
        head.addStretch(1)
        lay.addLayout(head)
        self.value = QLabel("—")
        self.value.setObjectName("tileValue")
        self.sub = ElideLabel("", Qt.TextElideMode.ElideRight)
        self.sub.setObjectName("tileSub")
        lay.addWidget(self.value)
        lay.addWidget(self.sub)
        self._color = None

    def set(self, value: str, sub: str = "", color: str | None = None) -> None:
        self.value.setText(value)
        self.sub.setText(sub)
        if color != self._color:
            self._color = color
            self.value.setStyleSheet(f"color:{color};" if color else "")


class Banner(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("banner")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(14)
        self.icon = glyph_label(G.INFO, C["accent2"], 22)
        lay.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(3)
        self.title = ElideLabel("")
        self.title.setObjectName("bannerTitle")
        self.text = QLabel()
        self.text.setObjectName("bannerText")
        self.text.setWordWrap(True)
        col.addWidget(self.title)
        col.addWidget(self.text)
        lay.addLayout(col, 1)
        self.action = QPushButton()
        self.action.setObjectName("link")
        self.action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action.hide()
        lay.addWidget(self.action, 0, Qt.AlignmentFlag.AlignVCenter)
        self._kind = None
        self._icon_key = None
        self._action_cb = None
        self.action.clicked.connect(lambda: self._action_cb and self._action_cb())

    COLORS = {"info": C["accent2"], "run": C["accent"], "ok": C["ok"], "warn": C["warn"], "err": C["err"]}

    def set(self, kind: str, title: str, text: str, glyph: int, action: tuple[str, callable] | None = None):
        if kind != self._kind:
            self._kind = kind
            self.setProperty("kind", kind)
            self.style().unpolish(self)
            self.style().polish(self)
        if (kind, glyph) != self._icon_key:
            self._icon_key = (kind, glyph)
            set_glyph(self.icon, glyph, self.COLORS[kind], 22)
        self.title.setText(title)
        self.text.setText(text)
        if action:
            self.action.setText(action[0])
            self._action_cb = action[1]
            self.action.show()
        else:
            self.action.hide()


class Ring(QWidget):
    """Anillo de progreso con degradado, brillo y modo ocupado animado."""

    def __init__(self):
        super().__init__()
        self.setMinimumSize(230, 230)
        self._shown = 0.0
        self._target = 0.0
        self._spin = 0.0
        self._busy = False
        self.big, self.small, self.caption = "0 %", "", "PREPARADO"
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(500)
        self._anim.setEasingCurve(QEasingCurve(QEasingCurve.Type.OutCubic))
        self._anim.valueChanged.connect(self._on_anim)
        self._spinner = QTimer(self)
        self._spinner.setInterval(16)
        self._spinner.timeout.connect(self._on_spin)

    def _on_anim(self, v):
        self._shown = float(v)
        self.update()

    def _on_spin(self):
        self._spin = (self._spin + 4.5) % 360
        self.update()

    def set_value(self, v: float, animate: bool = True) -> None:
        v = max(0.0, min(1.0, v))
        if abs(v - self._target) < 1e-5:
            return
        self._target = v
        self._anim.stop()
        if animate:
            self._anim.setStartValue(self._shown)
            self._anim.setEndValue(v)
            self._anim.start()
        else:
            self._shown = v
            self.update()

    def set_busy(self, on: bool) -> None:
        if on == self._busy:
            return
        self._busy = on
        if on:
            self._spinner.start()
        else:
            self._spinner.stop()
        self.update()

    def set_text(self, big: str, small: str, caption: str) -> None:
        if (big, small, caption) != (self.big, self.small, self.caption):
            self.big, self.small, self.caption = big, small, caption
            self.update()

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height()) - 30
        rect = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        thick = max(10.0, side * 0.07)
        center = rect.center()
        cap = Qt.PenCapStyle.RoundCap

        p.setPen(QPen(QColor(C["line"]), thick, Qt.PenStyle.SolidLine, cap))
        p.drawArc(rect, 0, 360 * 16)

        if self._busy:
            start = 90 - self._spin
            grad = QConicalGradient(center, start)
            grad.setColorAt(0.0, QColor(C["accent2"]))
            grad.setColorAt(0.68, QColor(C["accent2"]))
            tail = QColor(C["accent"])
            tail.setAlpha(0)
            grad.setColorAt(1.0, tail)
            p.setPen(QPen(QBrush(grad), thick, Qt.PenStyle.SolidLine, cap))
            p.drawArc(rect, int(start * 16), int(-115 * 16))
        elif self._shown > 0.0005:
            span = -int(self._shown * 360 * 16)
            glow = QColor(C["accent"])
            glow.setAlpha(38)
            p.setPen(QPen(glow, thick * 2.1, Qt.PenStyle.SolidLine, cap))
            p.drawArc(rect, 90 * 16, span)
            grad = QConicalGradient(center, 90)
            grad.setColorAt(0.0, QColor(C["accent2"]))
            grad.setColorAt(1.0, QColor(C["accent"]))
            p.setPen(QPen(QBrush(grad), thick, Qt.PenStyle.SolidLine, cap))
            p.drawArc(rect, 90 * 16, span)
            ang = math.radians(90 - self._shown * 360)
            rad = side / 2
            head = QPointF(center.x() + rad * math.cos(ang), center.y() - rad * math.sin(ang))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#FFFFFF"))
            p.drawEllipse(head, thick * 0.22, thick * 0.22)

        f = QFont(FONT["ui"])
        f.setPixelSize(11)
        f.setWeight(QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.4)
        p.setFont(f)
        p.setPen(QColor(C["muted"]))
        p.drawText(QRectF(rect.left(), center.y() - side * 0.25, side, 18), Qt.AlignmentFlag.AlignCenter, self.caption)

        f = QFont(FONT["display"])
        f.setPixelSize(int(side * 0.17))
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor(C["text"]))
        p.drawText(QRectF(rect.left(), center.y() - side * 0.14, side, side * 0.26), Qt.AlignmentFlag.AlignCenter, self.big)

        f = QFont(FONT["ui"])
        f.setPixelSize(12)
        p.setFont(f)
        p.setPen(QColor(C["muted"]))
        p.drawText(QRectF(rect.left() + side * 0.12, center.y() + side * 0.13, side * 0.76, 20),
                   Qt.AlignmentFlag.AlignCenter, self.small)


class SpeedChart(QWidget):
    """Gráfica de velocidad de los últimos dos minutos."""

    def __init__(self, points: int = 240):
        super().__init__()
        self.data: deque[float] = deque(maxlen=points)
        self.setMinimumHeight(150)

    def push(self, bps: float) -> None:
        self.data.append(max(0.0, bps))
        self.update()

    def reset(self) -> None:
        self.data.clear()
        self.update()

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        area = QRectF(2, 34, w - 4, h - 40)
        data = list(self.data)
        peak = max(data) if data else 0.0
        top = max(peak * 1.18, 1.0)

        f = QFont(FONT["ui"])
        f.setPixelSize(11)
        f.setWeight(QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
        p.setFont(f)
        p.setPen(QColor(C["muted"]))
        p.drawText(QRectF(2, 0, w / 2, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "VELOCIDAD")
        f = QFont(FONT["display"])
        f.setPixelSize(15)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor(C["text"]))
        cur = fmt_speed(data[-1]) if data else "—"
        p.drawText(QRectF(w / 2, 0, w / 2 - 2, 20), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, cur)

        p.setPen(QPen(QColor(C["line"]), 1, Qt.PenStyle.DashLine))
        for i in range(1, 4):
            y = area.top() + area.height() * i / 4
            p.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))

        if len(data) < 2:
            f = QFont(FONT["ui"])
            f.setPixelSize(12)
            p.setFont(f)
            p.setPen(QColor(C["faint"]))
            p.drawText(area, Qt.AlignmentFlag.AlignCenter, "La gráfica aparece al empezar la copia")
            return

        # al principio la gráfica ocupa todo el ancho; a partir de 30 s se desplaza
        step = area.width() / (min(self.data.maxlen, max(len(data), 60)) - 1)
        x0 = area.right() - (len(data) - 1) * step
        pts = [QPointF(x0 + i * step, area.bottom() - v / top * area.height()) for i, v in enumerate(data)]
        line = QPainterPath(pts[0])
        for a, b in zip(pts, pts[1:]):
            mx = (a.x() + b.x()) / 2
            line.cubicTo(QPointF(mx, a.y()), QPointF(mx, b.y()), b)
        fill = QPainterPath(line)
        fill.lineTo(pts[-1].x(), area.bottom())
        fill.lineTo(pts[0].x(), area.bottom())
        fill.closeSubpath()
        grad = QLinearGradient(0, area.top(), 0, area.bottom())
        c1 = QColor(C["accent"])
        c1.setAlpha(120)
        c2 = QColor(C["accent"])
        c2.setAlpha(0)
        grad.setColorAt(0, c1)
        grad.setColorAt(1, c2)
        p.fillPath(fill, grad)
        p.setPen(QPen(QColor(C["accent2"]), 2))
        p.drawPath(line)

        py = area.bottom() - peak / top * area.height()
        pk = QColor(C["accent2"])
        pk.setAlpha(90)
        p.setPen(QPen(pk, 1, Qt.PenStyle.DotLine))
        p.drawLine(QPointF(area.left(), py), QPointF(area.right(), py))
        f = QFont(FONT["ui"])
        f.setPixelSize(11)
        p.setFont(f)
        p.setPen(QColor(C["muted"]))
        p.drawText(QRectF(area.left() + 4, py - 17, 200, 15), Qt.AlignmentFlag.AlignLeft, f"pico {fmt_speed(peak)}")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C["accent2"]))
        p.drawEllipse(pts[-1], 3.5, 3.5)


class ActiveRow(QWidget):
    def __init__(self, index: int):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)
        top = QHBoxLayout()
        top.setSpacing(8)
        tag = QLabel(f"{index}")
        tag.setObjectName("faint")
        tag.setFixedWidth(14)
        self.name = ElideLabel("en espera…")
        self.name.setObjectName("mono")
        self.size = QLabel("")
        self.size.setObjectName("faint")
        top.addWidget(tag)
        top.addWidget(self.name, 1)
        top.addWidget(self.size)
        lay.addLayout(top)
        self.bar = thin_bar(4)
        lay.addWidget(self.bar)

    def show_slot(self, slot, root: str) -> None:
        if not slot or not slot[0]:
            self.name.setText("en espera…")
            self.size.setText("")
            self.bar.setValue(0)
            return
        path, size, done = slot[0], slot[1], slot[2]
        rel = path[len(root):].lstrip("\\/") if path.startswith(root) else path
        self.name.setText(rel)
        self.size.setText(f"{fmt_bytes(done)} / {fmt_bytes(size)}" if size >= 8 << 20 else fmt_bytes(size))
        self.bar.setValue(int(min(1.0, done / size) * 10000) if size else 10000)


class FolderTree(QTreeWidget):
    """Árbol de carpetas del origen con casillas; carga perezosa al expandir."""
    changed = pyqtSignal()
    REL = Qt.ItemDataRole.UserRole
    LOADED = Qt.ItemDataRole.UserRole + 1

    def __init__(self):
        super().__init__()
        self.setHeaderLabels(["CARPETA", "ESTADO"])
        self.setUniformRowHeights(True)
        self.setAnimated(True)
        self.setIndentation(20)
        self.setIconSize(QSize(16, 16))
        hdr = self.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(1, 210)
        self.root = ""
        self.excluded: set[str] = set()
        self.patterns: list[str] = []
        self._busy = False
        self._icon_on = glyph_icon(G.FOLDER, C["folder"], 16)
        self._icon_off = glyph_icon(G.FOLDER, C["faint"], 16)
        self.itemExpanded.connect(self._on_expand)
        self.itemChanged.connect(self._on_changed)

    def load(self, root: str, excluded: set[str], patterns: list[str]) -> int:
        self.root, self.excluded, self.patterns = root, excluded, patterns
        self.clear()
        count = self._populate(self.invisibleRootItem(), "")
        self.refresh()
        return count

    def set_patterns(self, patterns: list[str]) -> None:
        self.patterns = patterns
        self.refresh()

    def refresh(self) -> None:
        root = self.invisibleRootItem()
        for i in range(root.childCount()):
            self._apply(root.child(i))

    def reset(self) -> None:
        self.excluded.clear()
        self.refresh()
        self.changed.emit()

    def _subdirs(self, rel: str) -> list[str]:
        path = os.path.join(self.root, rel) if rel else self.root
        try:
            with os.scandir(engine.lp(path)) as it:
                names = [e.name for e in it if not engine.is_link(e) and e.is_dir(follow_symlinks=False)]
        except OSError:
            return []
        return sorted(names, key=str.lower)

    def _populate(self, parent: QTreeWidgetItem, rel: str) -> int:
        names = self._subdirs(rel)
        self._busy = True
        try:
            for name in names:
                item = QTreeWidgetItem(parent, [name, ""])
                item.setData(0, self.REL, os.path.join(rel, name) if rel else name)
                item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
            parent.setData(0, self.LOADED, True)
        finally:
            self._busy = False
        return len(names)

    def _on_expand(self, item: QTreeWidgetItem) -> None:
        if item.data(0, self.LOADED):
            return
        if self._populate(item, item.data(0, self.REL)) == 0:
            item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicatorWhenChildless)
        for i in range(item.childCount()):
            self._apply(item.child(i))

    def _apply(self, item: QTreeWidgetItem) -> None:
        rel = item.data(0, self.REL)
        rule = engine.match_rule(item.text(0), self.patterns)
        manual = engine.norm_rel(rel) in self.excluded
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        self._busy = True
        try:
            if rule:
                item.setFlags(base)
                item.setCheckState(0, Qt.CheckState.Unchecked)
                status, color = f"Regla «{rule}»", C["warn"]
            else:
                item.setFlags(base | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Unchecked if manual else Qt.CheckState.Checked)
                status, color = ("Excluida", C["err"]) if manual else ("", C["text"])
            off = bool(rule or manual)
            item.setIcon(0, self._icon_off if off else self._icon_on)
            item.setForeground(0, QColor(C["faint"] if off else C["text"]))
            item.setText(1, status)
            item.setForeground(1, QColor(color))
            if off:
                item.setExpanded(False)
                item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicator)
            elif not item.data(0, self.LOADED) or item.childCount():
                item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        finally:
            self._busy = False
        if not (rule or manual):
            for i in range(item.childCount()):
                self._apply(item.child(i))

    def _on_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._busy or column != 0 or not (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            return
        key = engine.norm_rel(item.data(0, self.REL))
        if item.checkState(0) == Qt.CheckState.Checked:
            self.excluded.discard(key)
        else:
            self.excluded.add(key)
        self._apply(item)
        self.changed.emit()


# ── componentes de ajustes ───────────────────────────────────────────────────

class Toggle(QAbstractButton):
    """Interruptor con el pomo animado. Misma API que una casilla (isChecked, setChecked, toggled)."""
    W, H = 38, 22

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedSize(self.W, self.H)
        self._pos = 0.0
        self._hover = False
        self._kbd = False
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(170)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self.toggled.connect(self._animate)

    def sizeHint(self):  # noqa: N802
        return QSize(self.W, self.H)

    def _animate(self, on: bool):
        if not ui_animations():
            self.update()
            return
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _on_anim(self, v):
        self._pos = float(v)
        self.update()

    def enterEvent(self, _):  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, _):  # noqa: N802
        self._hover = False
        self.update()

    def focusInEvent(self, e):  # noqa: N802
        self._kbd = e.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
        super().focusInEvent(e)

    def focusOutEvent(self, e):  # noqa: N802
        self._kbd = False
        super().focusOutEvent(e)

    def paintEvent(self, _):  # noqa: N802
        if self._anim.state() != QVariantAnimation.State.Running:
            self._pos = 1.0 if self.isChecked() else 0.0  # también tras setChecked con las señales bloqueadas
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.42)
        track = QRectF(1, 1, self.W - 2, self.H - 2)
        r = track.height() / 2
        off = QColor("#2E3546" if self._hover else "#262C3A")
        p.setPen(QPen(QColor("#3A4258"), 1))
        p.setBrush(off)
        p.drawRoundedRect(track, r, r)
        if self._pos > 0:
            g = QLinearGradient(track.topLeft(), track.topRight())
            g.setColorAt(0, QColor("#8E80FF" if self._hover else C["accent"]))
            g.setColorAt(1, QColor("#64A0FF" if self._hover else "#4F8BFF"))
            p.save()
            p.setOpacity(p.opacity() * self._pos)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawRoundedRect(track, r, r)
            p.restore()
        d = track.height() - 6
        x = track.left() + 3 + (track.width() - d - 6) * self._pos
        knob = QRectF(x, track.top() + 3, d, d)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 70))
        p.drawEllipse(knob.translated(0, 1))
        p.setBrush(QColor("#FFFFFF") if self._pos > 0.5 else QColor("#C9CEDB"))
        p.drawEllipse(knob)
        if self._kbd and self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(45, 212, 238, 200), 1.6))
            p.drawRoundedRect(QRectF(0.8, 0.8, self.W - 1.6, self.H - 1.6), r + 1, r + 1)


class SettingRow(QWidget):
    """Ajuste: título, explicación de una línea y el control a la derecha. Clic en el texto = interruptor."""

    def __init__(self, title: str, subtitle: str, control: QWidget, tip: str = ""):
        super().__init__()
        self.control = control
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.title = QLabel(title)
        self.title.setObjectName("rowTitle")
        col.addWidget(self.title)
        self.sub = QLabel(subtitle)
        self.sub.setObjectName("rowSub")
        self.sub.setWordWrap(True)
        self.sub.setVisible(bool(subtitle))
        col.addWidget(self.sub)
        lay.addLayout(col, 1)
        lay.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        if tip:
            self.setToolTip(tip)
        if isinstance(control, Toggle):
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, e):  # noqa: N802
        inside = self.rect().contains(e.position().toPoint())
        if isinstance(self.control, Toggle) and self.control.isEnabled() and inside:
            self.control.toggle()
        super().mouseReleaseEvent(e)


class Section(QFrame):
    """Tarjeta de ajustes plegable: icono, título y, plegada, un resumen de una línea de lo elegido."""
    expanded_changed = pyqtSignal(str, bool)

    def __init__(self, key: str, title: str, glyph: str, collapsible: bool = True):
        super().__init__()
        self.key = key
        self.collapsible = collapsible
        self.expanded = True
        self.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 14, 16)
        outer.setSpacing(0)
        self.head = QWidget()
        self.head.setObjectName("sectionHead")
        hl = QHBoxLayout(self.head)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)
        hl.addWidget(glyph_label(glyph, C["accent2"], 14))
        lbl = QLabel(title.upper())
        lbl.setObjectName("cardTitle")
        hl.addWidget(lbl)
        hl.addStretch(1)
        self.chevron = QLabel()
        self.chevron.setFixedSize(24, 22)
        self.chevron.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hl.addWidget(self.chevron)
        self.chevron.setVisible(collapsible)
        outer.addWidget(self.head)
        self.summary = ElideLabel("", Qt.TextElideMode.ElideRight)
        self.summary.setObjectName("sectionSummary")
        self.summary.hide()
        outer.addWidget(self.summary)
        self.content = QWidget()
        self.body = QVBoxLayout(self.content)
        self.body.setContentsMargins(0, 14, 4, 0)
        self.body.setSpacing(14)
        outer.addWidget(self.content)
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(lambda v: self.content.setMaximumHeight(int(v)))
        self._anim.finished.connect(self._anim_done)
        if collapsible:
            self.head.setCursor(Qt.CursorShape.PointingHandCursor)
            self.head.setToolTip("Plegar o desplegar")
        self._chevron()

    def _chevron(self):
        self.chevron.setPixmap(glyph_pixmap("chevdown" if self.expanded else "chevright", C["muted"], 16))

    def set_summary(self, text: str) -> None:
        self.summary.setText(text)
        self.summary.setToolTip(text)

    def set_expanded(self, on: bool, animate: bool = True) -> None:
        if not self.collapsible or on == self.expanded:
            return
        self.expanded = on
        self._chevron()
        self.summary.setVisible(not on)
        self._anim.stop()
        if not (animate and ui_animations() and self.isVisible()):
            self.content.setMaximumHeight(16777215)
            self.content.setVisible(on)
        else:
            full = self.content.sizeHint().height()
            self.content.setVisible(True)
            self._anim.setStartValue(0 if on else self.content.height())
            self._anim.setEndValue(full if on else 0)
            self._anim.setDuration(220 if on else 180)
            self._anim.start()
        self.expanded_changed.emit(self.key, on)

    def _anim_done(self):
        self.content.setMaximumHeight(16777215)
        self.content.setVisible(self.expanded)

    def mouseReleaseEvent(self, e):  # noqa: N802
        if self.collapsible and self.head.geometry().contains(e.position().toPoint()):
            self.set_expanded(not self.expanded)
        super().mouseReleaseEvent(e)


class FlowConnector(QWidget):
    """Separador entre origen y destino con una flecha hacia abajo: de aquí → a allí."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(30)

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cy = self.height() / 2
        p.setPen(QPen(QColor(C["line"]), 1))
        p.drawLine(QPointF(0, cy), QPointF(self.width(), cy))
        c = QPointF(self.width() / 2, cy)
        p.setPen(QPen(QColor(124, 108, 255, 110), 1))
        g = QLinearGradient(c.x() - 13, c.y() - 13, c.x() + 13, c.y() + 13)
        g.setColorAt(0, QColor(124, 108, 255, 70))
        g.setColorAt(1, QColor(45, 212, 238, 50))
        p.setBrush(QColor(C["card"]))
        p.drawEllipse(c, 13, 13)
        p.setBrush(g)
        p.drawEllipse(c, 13, 13)
        icon = cached_glyph("arrowdown", C["text"], 14)
        p.drawPixmap(QRectF(c.x() - 7, c.y() - 7, 14, 14), icon, QRectF(icon.rect()))


class TileGrid(QWidget):
    """Rejilla de casillas que pasa de 4 a 2 columnas cuando no caben (ventana estrecha)."""

    def __init__(self, tiles: list, spacing: int = 12):
        super().__init__()
        self.tiles = tiles
        self.spacing = spacing
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(spacing)
        self.cols = 0
        self._place(4)

    def _need(self, cols: int) -> int:
        return cols * TILE_MIN + (cols - 1) * self.spacing

    def _place(self, cols: int):
        if cols == self.cols:
            return
        self.cols = cols
        for t in self.tiles:
            self.grid.removeWidget(t)
        for i, t in enumerate(self.tiles):
            self.grid.addWidget(t, i // cols, i % cols)
        for c in range(4):
            self.grid.setColumnStretch(c, 1 if c < cols else 0)

    def minimumSizeHint(self):  # noqa: N802
        return QSize(self._need(2), super().minimumSizeHint().height())

    def resizeEvent(self, e):  # noqa: N802
        self._place(4 if e.size().width() >= self._need(4) else 2)
        super().resizeEvent(e)


class PathCard(Card):
    changed = pyqtSignal()

    def __init__(self, title: str, glyph: int, dialog_title: str, frameless: bool = False):
        super().__init__(title, glyph)
        if frameless:  # va dentro de otra tarjeta (origen → destino)
            self.setObjectName("pathBlock")
            self.body.setContentsMargins(0, 0, 0, 0)
            self.body.setSpacing(10)
        self.dialog_title = dialog_title
        row = QHBoxLayout()
        row.setSpacing(8)
        self.edit = QLineEdit()
        self.edit.setObjectName("path")
        self.btn = QPushButton()
        self.btn.setObjectName("icon")
        self.btn.setIcon(glyph_icon(G.OPEN, C["text"], 16))
        self.btn.setIconSize(QSize(16, 16))
        self.btn.setFixedSize(38, 36)
        self.btn.setToolTip("Elegir carpeta…")
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        row.addWidget(self.edit, 1)
        row.addWidget(self.btn)
        self.body.addLayout(row)
        self.disk = thin_bar(4, "disk")
        self.info = QLabel("")
        self.info.setObjectName("faint")
        self.body.addWidget(self.disk)
        self.body.addWidget(self.info)
        self._last = ""
        self.btn.clicked.connect(self._browse)
        self.edit.editingFinished.connect(self._edited)

    def path(self) -> str:
        return os.path.normpath(self.edit.text().strip().strip('"')) if self.edit.text().strip() else ""

    def set_path(self, path: str) -> None:
        self.edit.setText(path)
        self._last = self.path()
        self.refresh()

    def _browse(self):
        start = self.path() if os.path.isdir(self.path()) else ""
        chosen = QFileDialog.getExistingDirectory(self, self.dialog_title, start)
        if chosen:
            self.set_path(os.path.normpath(chosen))
            self.changed.emit()

    def _edited(self):
        if self.path() != self._last:
            self._last = self.path()
            self.refresh()
            self.changed.emit()

    def refresh(self) -> None:
        path = self.path()
        if not path:
            self.info.setText("Sin carpeta")
            self.disk.setValue(0)
            return
        root = engine.volume_root(path)
        try:
            if not engine.volume_present(path):
                raise OSError
            du = shutil.disk_usage(root)
        except OSError:
            self.info.setText("Unidad no conectada")
            self.disk.setValue(0)
            return
        self.disk.setValue(int(du.used / du.total * 10000) if du.total else 0)
        note = "" if os.path.isdir(path) else "  ·  se creará"
        self.info.setText(f"{engine.vol_name(root)}  ·  {fmt_bytes(du.free)} libres de {fmt_bytes(du.total)}{note}")


# ── ventana principal ────────────────────────────────────────────────────────

PILL = {
    "idle": ("Listo", C["muted"]), "ready": ("Análisis listo", C["accent2"]),
    "scanning": ("Analizando", C["accent2"]), "copying": ("Sincronizando", C["accent"]),
    "paused": ("En pausa", C["warn"]), "done": ("Al día", C["ok"]),
    "cancelled": ("Cancelado", C["warn"]), "failed": ("Error", C["err"]),
}
RUNNING = ("scanning", "copying", "paused")
PAGE_TREE, PAGE_CHANGES, PAGE_PROGRESS, PAGE_ISSUES = range(4)

KEEP_OPTIONS = [(30, "30 días"), (90, "90 días"), (365, "1 año"), (0, "Siempre")]
CONNECT_ACTIONS = {"ask": "Preguntar si sincronizar", "auto": "Sincronizar sin preguntar", "none": "Solo avisar"}
KIND_INFO = {"n": ("Nuevo", C["ok"]), "c": ("Modificado", C["accent2"]),
             "m": ("Movido", C["accent"]), "d": ("A versiones", C["warn"])}
MODE_HINTS = {
    engine.MODE_MIRROR: "El destino queda idéntico al origen. Lo modificado o borrado no se pierde: la versión "
                        "anterior se guarda en .devbackup/versiones del disco de copia.",
    engine.MODE_UPDATE: "Copia lo nuevo y lo modificado pero nunca quita nada del destino, aunque lo borres en "
                        "el origen.",
}

SCRUB_OPTIONS = [(0, "Desactivada"), (120, "2 min"), (300, "5 min"), (900, "15 min"), (3600, "1 hora"),
                 (-1, "Hasta revisar todo")]

INSTANCE = "DevBackupStudio-" + getpass.getuser()
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "DEV Backup Studio"
LAUNCH_AGENT = os.path.expanduser("~/Library/LaunchAgents/com.crazyramirez.devbackupstudio.plist")


def fmt_ago(iso: str | None) -> str:
    if not iso:
        return "nunca"
    try:
        sec = (datetime.now() - datetime.fromisoformat(iso)).total_seconds()
    except ValueError:
        return "nunca"
    if sec < 90:
        return "hace un momento"
    if sec < 3600:
        return f"hace {int(sec // 60)} min"
    if sec < 86400:
        return f"hace {int(sec // 3600)} h"
    days = int(sec // 86400)
    return "ayer" if days == 1 else f"hace {days} días"


def autostart_command() -> list[str]:
    if FROZEN:
        return [sys.executable, "--tray"]
    exe = sys.executable
    if IS_WIN:
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        exe = pyw if os.path.exists(pyw) else exe
    return [exe, os.path.join(APP_DIR, "backup_studio.py"), "--tray"]


def set_autostart(on: bool) -> None:
    """Arranque al iniciar sesión: clave Run en Windows, LaunchAgent en macOS (sin administrador)."""
    if IS_WIN:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if on:
                winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(autostart_command()))
            else:
                try:
                    winreg.DeleteValue(key, RUN_NAME)
                except FileNotFoundError:
                    pass
    elif IS_MAC:
        if on:
            os.makedirs(os.path.dirname(LAUNCH_AGENT), exist_ok=True)
            with open(LAUNCH_AGENT, "wb") as fh:
                plistlib.dump({"Label": "com.crazyramirez.devbackupstudio", "ProgramArguments": autostart_command(),
                               "RunAtLoad": True, "ProcessType": "Interactive"}, fh)
        else:
            try:
                os.remove(LAUNCH_AGENT)
            except FileNotFoundError:
                pass


def migrate_data() -> None:
    """La versión que se ejecutaba desde el código guardaba todo junto al script; lo pasa a DATA_DIR."""
    old = APP_DIR
    if FROZEN or os.path.exists(os.path.join(DATA_DIR, "settings.ini")):
        return
    if not os.path.exists(os.path.join(old, "settings.ini")):
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    shutil.copy2(os.path.join(old, "settings.ini"), os.path.join(DATA_DIR, "settings.ini"))
    for name in ("estado", "logs"):
        if os.path.isdir(os.path.join(old, name)):
            shutil.copytree(os.path.join(old, name), os.path.join(DATA_DIR, name), dirs_exist_ok=True)


def send_to_running(message: str) -> bool:
    """Pasa el mensaje a la instancia que ya está abierta, si la hay."""
    sock = QLocalSocket()
    sock.connectToServer(INSTANCE)
    if not sock.waitForConnected(400):
        return False
    sock.write(message.encode())
    sock.flush()
    sock.waitForBytesWritten(400)
    sock.disconnectFromServer()
    return True


class DiskWatcher(QObject):
    """Avisa cuando se conecta una unidad (consultarlas cada 2 s no cuesta nada)."""
    arrived = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._known = engine.mounted_volumes()
        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    def _poll(self):
        now = engine.mounted_volumes()
        for root in sorted(now - self._known):
            self.arrived.emit(root)
        self._known = now


class ChangesModel(QAbstractTableModel):
    HEAD = ["TIPO", "RUTA", "TAMAÑO"]

    def __init__(self):
        super().__init__()
        self.all: list[tuple] = []
        self.rows: list[tuple] = []

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else 3

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEAD[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        kind, path, size, extra = self.rows[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return KIND_INFO[kind][0]
            if col == 1:
                return f"{path}      ← {extra}" if extra else path
            return fmt_bytes(size)
        if role == Qt.ItemDataRole.ForegroundRole:
            return QColor(KIND_INFO[kind][1] if col == 0 else C["muted"] if col == 2 else C["text"])
        if role == Qt.ItemDataRole.TextAlignmentRole and col == 2:
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        if role == Qt.ItemDataRole.ToolTipRole and col == 1:
            return f"{path}\nantes en: {extra}" if extra else path
        return None

    def load(self, rows: list[tuple]) -> None:
        self.beginResetModel()
        self.all = rows
        self.rows = rows
        self.endResetModel()

    def apply_filter(self, kind: str | None, text: str) -> None:
        text = text.lower().strip()
        self.beginResetModel()
        self.rows = [r for r in self.all
                     if (kind is None or r[0] == kind) and (not text or text in r[1].lower() or text in r[3].lower())]
        self.endResetModel()


def change_rows(job: engine.BackupJob) -> list[tuple]:
    cut, dcut = len(job.src), len(job.dst)
    rows = [(c[3], c[0][cut:].lstrip("\\/"), c[1], "") for c in job.copies]
    rows += [("m", item[0][cut:].lstrip("\\/"), item[1], old[dcut:].lstrip("\\/")) for old, item in job.moves]
    rows += [("d", p[dcut:].lstrip("\\/"), size, "") for p, size, _ in job.deletes]
    return rows


def estimate_seconds(root: str, s: engine.Stats) -> float | None:
    """Tiempo previsto según el ritmo de la última copia en este disco."""
    last = engine.last_history(root)
    if not last or float(last.get("copia_s", 0)) < 5:
        return None
    work_then = last.get("bytes", 0) + (last.get("copiados", 0) + last.get("movidos", 0)
                                        + last.get("eliminados", 0)) * FILE_COST
    if work_then <= 0:
        return None
    rate = work_then / float(last["copia_s"])
    return (s.plan_bytes + s.ops_total * FILE_COST) / rate


class ConnectDialog(QDialog):
    """Lo que aparece al conectar el disco: analiza y pregunta si sincronizar."""

    def __init__(self, win: "MainWindow", root: str):
        super().__init__(win)
        self.win = win
        self.root = root
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.setMinimumWidth(700)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 24, 26, 22)
        lay.setSpacing(16)

        head = QHBoxLayout()
        head.setSpacing(14)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(46, 46))
        head.addWidget(logo, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(3)
        self.title = QLabel("Tu disco de copia está conectado")
        self.title.setObjectName("h1")
        label = engine.volume_info(self.root)[0] or "Disco de copia"
        last = engine.last_history(self.root)
        where = f" ({engine.vol_name(root)})" if IS_WIN else ""
        sub = QLabel(f"{label}{where}  ·  última copia {fmt_ago(last and last.get('fin'))}  ·  "
                     f"{win.integrity_text()}")
        sub.setObjectName("muted")
        col.addWidget(self.title)
        col.addWidget(sub)
        head.addLayout(col, 1)
        lay.addLayout(head)

        self.status = QLabel(f"Buscando cambios en {win.src_card.path()}…")
        self.status.setObjectName("muted")
        lay.addWidget(self.status)
        self.busy = thin_bar(4)
        self.busy.setRange(0, 0)
        lay.addWidget(self.busy)

        grid = QGridLayout()
        grid.setSpacing(10)
        self.t_new = Tile("Nuevos", G.ADD, C["ok"])
        self.t_chg = Tile("Modificados", G.SYNC, C["accent2"])
        self.t_mov = Tile("Movidos", G.NEXT, C["accent"])
        self.t_del = Tile("A versiones", G.FLAG, C["warn"])
        for i, t in enumerate((self.t_new, self.t_chg, self.t_mov, self.t_del)):
            t.setMinimumWidth(130)
            grid.addWidget(t, 0, i)
            grid.setColumnStretch(i, 1)
        lay.addLayout(grid)
        self.note = QLabel("")
        self.note.setObjectName("faint")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch(1)
        self.b_later = QPushButton("Ahora no")
        self.b_view = QPushButton("  Ver cambios")
        self.b_view.setIcon(glyph_icon(G.TREE, C["text"], 14))
        self.b_sync = QPushButton("  Sincronizar ahora")
        self.b_sync.setObjectName("primary")
        self.b_sync.setIcon(glyph_icon(G.PLAY, "#FFFFFF", 14))
        self.b_sync.setEnabled(False)
        self.b_view.setEnabled(False)
        for b in (self.b_later, self.b_view, self.b_sync):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            buttons.addWidget(b)
        lay.addLayout(buttons)
        self.b_later.clicked.connect(self.reject)
        self.b_view.clicked.connect(self._view)
        self.b_sync.clicked.connect(self._sync)

        self._timer = QTimer(self)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        set_dark_titlebar(self)

    def _fill(self, s: engine.Stats):
        self.t_new.set(fmt_int(s.new_files), fmt_bytes(s.new_bytes))
        self.t_chg.set(fmt_int(s.changed_files), fmt_bytes(s.changed_bytes))
        self.t_mov.set(fmt_int(s.moved_files), fmt_bytes(s.moved_bytes) + " sin recopiar")
        self.t_del.set(fmt_int(s.del_files), "borrados del origen")

    def _poll(self):
        job = self.win.job
        if job is None:
            return
        s = job.stats
        if job.phase == "scanning":
            self._fill(s)
            self.status.setText(f"Buscando cambios…  {fmt_int(s.files_found)} archivos revisados")
        elif job.phase == "ready":
            self._timer.stop()
            self.busy.hide()
            self._fill(s)
            note = job.index_note
            if not s.has_changes:
                self.title.setText("Todo está al día")
                self.status.setText(f"No hay nada que copiar desde la última vez  ·  "
                                    f"{fmt_int(s.files_found)} archivos revisados en "
                                    f"{fmt_clock(s.scan_end - s.scan_start, ms=True)}")
                self.b_view.hide()
                if self.win.scrub_seconds():
                    # sin cambios, el botón principal sirve para avanzar la revisión de integridad
                    self.b_sync.setText("  Comprobar integridad")
                    self.b_sync.setIcon(glyph_icon(G.SHIELD, "#FFFFFF", 14))
                    self.b_sync.setEnabled(True)
                    self.b_sync.setDefault(True)
                    self.b_later.setText("Cerrar")
                else:
                    self.b_sync.hide()
                    self.b_later.setText("Cerrar")
            else:
                self.status.setText(f"{fmt_int(s.ops_total)} cambios listos  ·  {fmt_bytes(s.plan_bytes)} por "
                                    f"copiar  ·  {fmt_int(s.skip_files)} archivos sin cambios")
                eta = estimate_seconds(self.root, s)
                if eta is not None:
                    note += f"  ·  tiempo previsto ≈ {fmt_human(max(1, eta))}"
                self.b_sync.setEnabled(True)
                self.b_view.setEnabled(True)
                self.b_sync.setDefault(True)
                self.b_sync.setFocus()
            self.note.setText(note)
        elif job.phase in ("cancelled", "failed"):
            self._timer.stop()
            self.busy.hide()
            self.status.setText("No se pudo completar el análisis. " + (job.fatal or ""))
            self.b_later.setText("Cerrar")

    def _view(self):
        self.accept()
        self.win.seg_buttons[PAGE_CHANGES].click()

    def _sync(self):
        self.accept()
        self.win._start(copy=True)

    def reject(self):
        job = self.win.job
        if job and job.phase == "scanning":
            job.cancel()
        super().reject()


CURRENT_VERSION = os.environ.get("DEVBACKUP_PRETEND_VERSION") or APP_VERSION  # para probar el actualizador


class UpdateChecker(QObject):
    """Consulta GitHub en un hilo; el resultado vuelve por señales al hilo de la UI."""
    finished = pyqtSignal(object, str, bool)  # Release o None, error, manual

    def check(self, manual: bool) -> None:
        def work():
            try:
                rel = updater.fetch_latest(CURRENT_VERSION)
                self.finished.emit(rel, "", manual)
            except Exception as exc:  # noqa: BLE001 - sin red, límite de la API…
                self.finished.emit(None, str(exc), manual)
        threading.Thread(target=work, name="actualizaciones", daemon=True).start()


class UpdateDialog(QDialog):
    """Novedades de la versión nueva y descarga con progreso."""
    progressed = pyqtSignal(int, int)
    downloaded = pyqtSignal(str, str)  # ruta, error

    def __init__(self, win: "MainWindow", rel: updater.Release):
        super().__init__(win)
        self.win = win
        self.rel = rel
        self._stop = threading.Event()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.setMinimumSize(640, 520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 24, 26, 22)
        lay.setSpacing(14)
        head = QHBoxLayout()
        head.setSpacing(14)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(46, 46))
        head.addWidget(logo, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(3)
        title = QLabel(f"Hay una versión nueva: {rel.version}")
        title.setObjectName("h1")
        when = ""
        if rel.published:
            try:
                # GitHub da la hora en UTC; fmt_ago compara con la hora local
                local = datetime.fromisoformat(rel.published.replace("Z", "+00:00")).astimezone()
                when = f"  ·  publicada {fmt_ago(local.replace(tzinfo=None).isoformat())}"
            except ValueError:
                pass
        size = f"  ·  {fmt_bytes(rel.asset_size)}" if rel.asset_size else ""
        sub = QLabel(f"Tienes la {CURRENT_VERSION}{when}{size}")
        sub.setObjectName("muted")
        col.addWidget(title)
        col.addWidget(sub)
        head.addLayout(col, 1)
        lay.addLayout(head)

        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(rel.notes.strip() or "Sin notas de versión.")
        lay.addWidget(notes, 1)

        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.bar = thin_bar(6)
        self.bar.hide()
        lay.addWidget(self.bar)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.b_skip = QPushButton("Omitir esta versión")
        self.b_skip.setObjectName("link")
        buttons.addWidget(self.b_skip)
        buttons.addStretch(1)
        self.b_later = QPushButton("Más tarde")
        self.b_go = QPushButton()
        self.b_go.setObjectName("primary")
        for b in (self.b_skip, self.b_later, self.b_go):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        buttons.addWidget(self.b_later)
        buttons.addWidget(self.b_go)
        lay.addLayout(buttons)

        if updater.can_self_update() and rel.asset_url:
            self.b_go.setText("  Actualizar ahora")
            self.b_go.setIcon(glyph_icon(G.DOWNLOAD, "#FFFFFF", 14))
            self.b_go.clicked.connect(self._update)
            how = ("se descarga el instalador, se comprueba su SHA-256 y se instala; la app se cierra y vuelve a "
                   "abrirse sola.")
            self.status.setText(f"Al actualizar {how} Tus ajustes y copias no se tocan.")
        else:
            self.b_go.setText("  Ver en GitHub")
            self.b_go.setIcon(glyph_icon(G.OPEN, "#FFFFFF", 14))
            self.b_go.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(rel.page)))
            self.status.setText("Esta copia se ejecuta desde el código: actualízala con «git pull»."
                                if not FROZEN else "No hay instalador para este sistema en esta versión.")
        self.b_later.clicked.connect(self.reject)
        self.b_skip.clicked.connect(self._skip)
        self.progressed.connect(self._on_progress)
        self.downloaded.connect(self._on_downloaded)
        self._sync_busy()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        set_dark_titlebar(self)

    def _sync_busy(self):
        if self.win._state in RUNNING and updater.can_self_update():
            self.b_go.setEnabled(False)
            self.status.setText("Hay una sincronización en marcha: podrás actualizar en cuanto termine.")

    def _skip(self):
        self.win.update_skip = self.rel.version
        self.win._save_settings()
        self.win._refresh_update_status()
        self.reject()

    def _update(self):
        if self.win._state in RUNNING:
            self._sync_busy()
            return
        self.b_go.setEnabled(False)
        self.b_skip.setEnabled(False)
        self.b_later.setText("Cancelar")
        self.bar.show()
        self.bar.setRange(0, 0)
        self.status.setText(f"Descargando {self.rel.asset_name}…")

        def work():
            try:
                path = updater.download(self.rel, CURRENT_VERSION, lambda d, t: self.progressed.emit(d, t), self._stop)
                self.downloaded.emit(path, "")
            except Exception as exc:  # noqa: BLE001
                self.downloaded.emit("", str(exc))
        threading.Thread(target=work, name="descarga", daemon=True).start()

    def _on_progress(self, done: int, total: int):
        if total:
            self.bar.setRange(0, 10000)
            self.bar.setValue(int(done / total * 10000))
            self.status.setText(f"Descargando {self.rel.asset_name}…  {fmt_bytes(done)} de {fmt_bytes(total)}")

    def _on_downloaded(self, path: str, error: str):
        if error:
            if self._stop.is_set():
                return
            self.bar.hide()
            self.status.setText(f"No se pudo descargar la actualización: {error}")
            self.b_go.setEnabled(True)
            self.b_skip.setEnabled(True)
            self.b_later.setText("Más tarde")
            return
        self.bar.setRange(0, 10000)
        self.bar.setValue(10000)
        self.status.setText("Descarga comprobada con SHA-256. Instalando: la app se cerrará y volverá a abrirse…")
        try:
            updater.launch_install(path)
        except OSError as exc:
            self.status.setText(f"No se pudo lanzar el instalador: {exc}")
            return
        QTimer.singleShot(600, self.win.quit_for_update)

    def reject(self):
        self._stop.set()
        super().reject()


# ── guía rápida ──────────────────────────────────────────────────────────────

TOUR_VERSION = 1
TOUR_DIR = os.path.join(getattr(sys, "_MEIPASS", APP_DIR), "assets", "tour")


def tour_steps() -> list[dict]:
    """Pasos de la guía. Las ilustraciones salen de tools/make_tour.py (capturas reales de la app)."""
    tray = "la barra de menús" if IS_MAC else "la bandeja"
    return [
        {"image": "1-bienvenida", "glyph": G.SPARK, "eyebrow": "Guía rápida",
         "title": "Tus proyectos, siempre a salvo",
         "text": "Copia tu carpeta de proyectos a un disco externo en cuatro pasos: rápido, comprobado y con "
                 "historial de versiones.",
         "chips": [(G.SHIELD, "Verificado con SHA-256"), (G.CLOCK, "Versiones de lo que cambias"),
                   (G.NEXT, "Detecta movidos")]},
        {"image": "2-origen-destino", "glyph": G.FOLDER, "title": "Elige origen y destino",
         "text": "El origen es tu carpeta de proyectos; el destino, una carpeta del disco externo.",
         "chips": [(G.FOLDER, "Una carpeta o un disco entero"), (G.DRIVE, "Lo reconoce aunque cambie de letra")]},
        {"image": "3-que-copiar", "glyph": G.FILTER, "title": "Decide qué se copia",
         "text": "Desmarca carpetas en el árbol o exclúyelas por nombre, en cualquier nivel de cada proyecto.",
         "chips": [(G.FILTER, "node_modules, .venv, dist…"), (G.TREE, "Se recuerda para la próxima")]},
        {"image": "4-sincroniza", "glyph": G.PLAY, "title": "Analiza y sincroniza",
         "text": "Analizar enseña lo nuevo, modificado y movido sin tocar nada. Sincronizar lo copia y lo "
                 "comprueba.",
         "chips": [(G.SEARCH, "Revisa antes de copiar"), (G.SHIELD, "Cada archivo verificado"),
                   (G.CLOCK, "Lo borrado va a versiones")]},
        {"image": "5-conecta", "glyph": G.BELL, "title": "Conecta el disco y listo",
         "text": f"Desde {tray} vigila el disco y te recuerda copiar si llevas días con cambios sin guardar. "
                 "Al enchufarlo, sincronizas con un clic.",
         "chips": [(G.SYNC, "Vigila en segundo plano"), (G.BELL, "Te recuerda copiar"),
                   (G.POWER, "Sin suspensión al copiar")]},
    ]


def tour_assets() -> tuple[dict[str, QPixmap], dict[str, list], tuple[float, float]]:
    """Ilustraciones de assets/tour y zonas que se destacan en cada una (tour.json)."""
    spots: dict[str, list] = {}
    size = (920.0, 400.0)
    try:
        with open(os.path.join(TOUR_DIR, "tour.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        spots = data.get("spots", {})
        size = (float(data["size"][0]), float(data["size"][1]))
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        pass
    images = {s["image"]: QPixmap(os.path.join(TOUR_DIR, s["image"] + ".jpg")) for s in tour_steps()}
    return images, spots, size


def ui_animations() -> bool:
    """False si el sistema pide menos animaciones (Windows: «Efectos de animación» desactivados)."""
    if IS_WIN:
        try:
            on = ctypes.c_int(1)
            spi_getclientareaanimation = 0x1042
            if ctypes.windll.user32.SystemParametersInfoW(spi_getclientareaanimation, 0, ctypes.byref(on), 0):
                return bool(on.value)
        except Exception:  # noqa: BLE001
            pass
    return True


def help_key() -> str:
    return QKeySequence(QKeySequence.StandardKey.HelpContents).toString(
        QKeySequence.SequenceFormat.NativeText) or "F1"


def _ease(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 1 - (1 - t) ** 3


def _font(px: int, weight=QFont.Weight.Normal, family: str | None = None, spacing: float = 0) -> QFont:
    f = QFont(family or FONT["ui"])
    f.setPixelSize(px)
    f.setWeight(weight)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return f


_GLYPHS: dict[tuple, QPixmap] = {}


def cached_glyph(name: str, color: str, px: int) -> QPixmap:
    key = (name, color, px)
    if key not in _GLYPHS:
        _GLYPHS[key] = glyph_pixmap(name, color, px)
    return _GLYPHS[key]


def blur_image(img: QImage, radius: float, fill: QColor | None = None) -> QImage:
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(QPixmap.fromImage(img))
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(radius)
    effect.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
    item.setGraphicsEffect(effect)
    scene.addItem(item)
    out = QImage(img.size(), QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(fill if fill is not None else QColor(0, 0, 0, 0))
    p = QPainter(out)
    scene.render(p, QRectF(out.rect()), QRectF(img.rect()))
    p.end()
    return out


def frosted(pm: QPixmap) -> QPixmap:
    """Versión desenfocada de una captura (cristal esmerilado detrás de la guía)."""
    img = pm.toImage()
    small = img.scaled(max(1, img.width() // 5), max(1, img.height() // 5),
                       Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return QPixmap.fromImage(blur_image(small, 9, QColor(C["bg"])))


def soft_shadow(w: float, h: float, radius: float, blur: float, alpha: int) -> QPixmap:
    k = 0.5  # se difumina a media resolución: igual de suave y cuatro veces más rápido
    pad = blur * 2
    img = QImage(int((w + 2 * pad) * k), int((h + 2 * pad) * k), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, alpha))
    p.drawRoundedRect(QRectF(pad * k, pad * k, w * k, h * k), radius * k, radius * k)
    p.end()
    return QPixmap.fromImage(blur_image(img, blur * k))


class TourDots(QWidget):
    """Puntos de progreso: el activo se estira en una píldora con degradado. Clic para saltar a un paso."""
    picked = pyqtSignal(int)
    DOT, ACTIVE, GAP = 8.0, 28.0, 8.0

    def __init__(self, count: int, parent: QWidget):
        super().__init__(parent)
        self.count, self.cur, self.prev, self.p = count, 0, -1, 1.0
        self.hover = -1
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(int((count - 1) * (self.DOT + self.GAP) + self.ACTIVE) + 2, 24)

    def set_step(self, cur: int, prev: int) -> None:
        self.cur, self.prev = cur, prev
        self.update()

    def set_progress(self, p: float) -> None:
        self.p = p
        self.update()

    def _rects(self) -> list[QRectF]:
        e = _ease(self.p / 0.7) if 0 <= self.prev != self.cur else 1.0
        out, x = [], 1.0
        for i in range(self.count):
            w = self.DOT
            if i == self.cur:
                w += (self.ACTIVE - self.DOT) * e
            elif i == self.prev:
                w += (self.ACTIVE - self.DOT) * (1 - e)
            out.append(QRectF(x, (self.height() - self.DOT) / 2, w, self.DOT))
            x += w + self.GAP
        return out

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        for i, r in enumerate(self._rects()):
            base = "#5B6580" if i == self.hover else ("#475066" if i < self.cur else "#2C3343")
            p.setBrush(QColor(base))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
            on = (r.width() - self.DOT) / (self.ACTIVE - self.DOT)
            if on > 0.01:
                g = QLinearGradient(r.topLeft(), r.topRight())
                g.setColorAt(0, QColor(C["accent"]))
                g.setColorAt(1, QColor(C["accent2"]))
                p.setOpacity(min(1.0, on))
                p.setBrush(g)
                p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
                p.setOpacity(1.0)

    def _at(self, pos: QPointF) -> int:
        for i, r in enumerate(self._rects()):
            if r.adjusted(-self.GAP / 2, -8, self.GAP / 2, 8).contains(pos):
                return i
        return -1

    def mouseMoveEvent(self, e):  # noqa: N802
        i = self._at(e.position())
        if i != self.hover:
            self.hover = i
            self.update()

    def leaveEvent(self, _):  # noqa: N802
        self.hover = -1
        self.update()

    def mousePressEvent(self, e):  # noqa: N802
        i = self._at(e.position())
        if i >= 0:
            self.picked.emit(i)


class TourCard(QWidget):
    """La tarjeta de la guía: ilustración, textos y pie con navegación. Todo se pinta para animarlo fino."""
    PAD, FOOT_H, RADIUS, MAX_W = 10, 74, 22, 940

    def __init__(self, overlay: "TourOverlay"):
        super().__init__(overlay)
        self.overlay = overlay
        self.steps = tour_steps()
        self.images, self.spots, self.canvas = tour_assets()
        self.idx, self.prev, self.dir, self.p = 0, -1, 1, 1.0
        self.pulse = 0.0
        self.ill_h = 380.0
        self.text_h = 140.0
        self.setAccessibleName("Guía rápida")

        self.close_btn = QPushButton(self)
        self.close_btn.setObjectName("tourClose")
        self.close_btn.setIcon(glyph_icon(G.CLOSE, C["text"], 14))
        self.close_btn.setFixedSize(34, 34)
        self.close_btn.setToolTip("Cerrar la guía  (Esc)")
        self.close_btn.setAccessibleName("Cerrar la guía")
        self.skip_btn = QPushButton("Saltar la guía", self)
        self.skip_btn.setObjectName("tourSkip")
        self.dots = TourDots(len(self.steps), self)
        self.back_btn = QPushButton("  Atrás", self)
        self.back_btn.setObjectName("tourBack")
        self.back_btn.setIcon(glyph_icon(G.BACK, C["text"], 14))
        self.next_btn = QPushButton(self)
        self.next_btn.setObjectName("primary")
        self.next_btn.setLayoutDirection(Qt.LayoutDirection.RightToLeft)  # flecha a la derecha del texto
        self.next_btn.setMinimumWidth(156)
        for b in (self.close_btn, self.skip_btn, self.back_btn, self.next_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        for b in (self.back_btn, self.next_btn):
            b.setFixedHeight(42)
        self.close_btn.clicked.connect(lambda: overlay.finish(False))
        self.skip_btn.clicked.connect(lambda: overlay.finish(False))
        self.back_btn.clicked.connect(self.back)
        self.next_btn.clicked.connect(self.next)
        self.dots.picked.connect(self.go)

        self.anim = QVariantAnimation(self)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setDuration(600)
        self.anim.valueChanged.connect(self._on_anim)
        self.pulse_anim = QVariantAnimation(self)
        self.pulse_anim.setStartValue(0.0)
        self.pulse_anim.setEndValue(1.0)
        self.pulse_anim.setDuration(1900)
        self.pulse_anim.setLoopCount(-1)
        self.pulse_anim.valueChanged.connect(self._on_pulse)

    # ── navegación ──
    @property
    def last(self) -> int:
        return len(self.steps) - 1

    def start(self) -> None:
        self.idx, self.prev, self.dir = 0, -1, 1
        self._sync()
        self._play()

    def go(self, i: int) -> None:
        i = max(0, min(self.last, i))
        if i == self.idx:
            return
        self.prev, self.idx = self.idx, i
        self.dir = 1 if i > self.prev else -1
        self._sync()
        self._play()

    def next(self) -> None:
        if self.idx >= self.last:
            self.overlay.finish(True)
        else:
            self.go(self.idx + 1)

    def forward(self) -> None:
        if self.idx < self.last:
            self.go(self.idx + 1)

    def back(self) -> None:
        self.go(self.idx - 1)

    def _play(self) -> None:
        self.anim.stop()
        if self.overlay.animate:
            self.anim.start()
        else:
            self._on_anim(1.0)

    def _on_anim(self, v) -> None:
        self.p = float(v)
        self.dots.set_progress(self.p)
        self.update()

    def _on_pulse(self, v) -> None:
        self.pulse = float(v)
        self.update(self._ill_rect().toAlignedRect())

    def _sync(self) -> None:
        last = self.idx == self.last
        self.back_btn.setVisible(self.idx > 0)
        self.skip_btn.setVisible(not last)
        self.next_btn.setText("Empezar  " if last else "Siguiente  ")
        self.next_btn.setIcon(glyph_icon(G.CHECK if last else G.ARROW, "#FFFFFF", 15))
        self.dots.set_step(self.idx, self.prev)
        st = self.steps[self.idx]
        self.setAccessibleDescription(f"{self._eyebrow(self.idx)}. {st['title']}. {st['text']}")
        self._place()
        if not self.next_btn.hasFocus() and not self.back_btn.hasFocus():
            self.next_btn.setFocus()

    def _eyebrow(self, i: int) -> str:
        return self.steps[i].get("eyebrow") or f"Paso {i} de {self.last}"

    # ── geometría ──
    def fit(self, avail_w: int, avail_h: int) -> None:
        w = max(640, min(self.MAX_W, avail_w - 56))
        natural = (w - 2 * self.PAD) * self.canvas[1] / self.canvas[0]
        # alto del bloque de texto: el del paso más largo, para que la tarjeta no cambie de tamaño al avanzar
        tw = w - (34 + 48 + 18) - 34
        self.text_h = max(58 + self._desc_height(i, tw) + 14 + 30 for i in range(len(self.steps))) + 8
        rest = self.PAD + 26 + self.text_h + self.FOOT_H
        self.ill_h = max(190.0, min(natural, avail_h - 48 - rest - self.PAD))
        self.setFixedSize(int(w), int(self.PAD + self.ill_h + rest))
        self._place()

    def _place(self) -> None:
        w, h = self.width(), self.height()
        self.close_btn.move(w - self.PAD - 14 - 34, self.PAD + 14)
        cy = h - self.FOOT_H / 2
        self.skip_btn.adjustSize()
        self.skip_btn.move(24, int(cy - self.skip_btn.height() / 2))
        self.dots.move(int((w - self.dots.width()) / 2), int(cy - self.dots.height() / 2))
        self.next_btn.adjustSize()
        x = w - 30 - self.next_btn.width()
        self.next_btn.move(x, int(cy - 21))
        self.back_btn.adjustSize()
        self.back_btn.move(x - 10 - self.back_btn.width(), int(cy - 21))

    def _ill_rect(self) -> QRectF:
        return QRectF(self.PAD, self.PAD, self.width() - 2 * self.PAD, self.ill_h)

    def _map(self, r: QRectF, zoom: float = 1.0, dx: float = 0.0) -> tuple[float, float, float]:
        cw, ch = self.canvas
        s = max(r.width() / cw, r.height() / ch) * zoom
        return s, r.x() + (r.width() - cw * s) / 2 + dx, r.y() + (r.height() - ch * s) / 2

    def _text_geom(self) -> tuple[float, float, float, float]:
        top = self.PAD + self.ill_h + 26
        left = 34.0
        tx = left + 48 + 18
        return top, left, tx, self.width() - tx - 34

    # ── pintura ──
    def _phases(self, k: int) -> tuple[float, float, float, float]:
        """Opacidad y desplazamiento del elemento k al salir (paso anterior) y al entrar (paso nuevo)."""
        out = _ease(self.p / 0.24)
        new = _ease((self.p - (0.1 + k * 0.05)) / 0.5)
        return 1 - out, -self.dir * 16 * out, new, self.dir * 28 * (1 - new)

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
                         | QPainter.RenderHint.TextAntialiasing)
        w, h, rad = self.width(), self.height(), self.RADIUS
        body = QPainterPath()
        body.addRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), rad, rad)
        g = QLinearGradient(0, 0, 0, h)
        g.setColorAt(0, QColor("#181D29"))
        g.setColorAt(1, QColor("#0F121A"))
        p.fillPath(body, g)
        p.setPen(QPen(QColor(255, 255, 255, 24), 1))
        p.drawPath(body)
        shine = QLinearGradient(rad, 0, w - rad, 0)
        shine.setColorAt(0, QColor(255, 255, 255, 0))
        shine.setColorAt(0.5, QColor(255, 255, 255, 52))
        shine.setColorAt(1, QColor(255, 255, 255, 0))
        p.setPen(QPen(QBrush(shine), 1))
        p.drawLine(QPointF(rad, 0.5), QPointF(w - rad, 0.5))

        self._paint_illustration(p)
        for k in range(5):
            old_a, old_dx, new_a, new_dx = self._phases(k)
            if self.prev >= 0 and old_a > 0:
                self._element(p, k, self.prev, old_a, old_dx)
            if new_a > 0:
                self._element(p, k, self.idx, new_a, new_dx)
        fy = h - self.FOOT_H
        p.setPen(QPen(QColor(255, 255, 255, 15), 1))
        p.drawLine(QPointF(1, fy), QPointF(w - 1, fy))
        if self.idx == self.last:
            self._paint_hint(p, self._phases(4)[2])

    def _paint_illustration(self, p: QPainter) -> None:
        r = self._ill_rect()
        clip = QPainterPath()
        clip.addRoundedRect(r, 16, 16)
        p.save()
        p.setClipPath(clip)
        p.fillRect(r, QColor(C["panel"]))
        if self.prev >= 0:
            new = _ease((self.p - 0.04) / 0.6)
            if new < 1:  # la anterior sigue opaca debajo: fundido sin bajón de brillo
                self._image(p, self.prev, r, 1.0, 1.0, 0)
            self._image(p, self.idx, r, new, 1.045 - 0.045 * new, self.dir * 22 * (1 - new))
        else:
            new = _ease(self.p / 0.75)
            self._image(p, self.idx, r, new, 1.06 - 0.06 * new, 0)
        spot = _ease((self.p - 0.62) / 0.38)
        if spot > 0:
            self._paint_spots(p, r, spot)
        p.restore()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 20), 1))
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 16, 16)

    def _image(self, p: QPainter, i: int, r: QRectF, alpha: float, zoom: float, dx: float) -> None:
        if alpha <= 0:
            return
        p.setOpacity(alpha)
        pm = self.images.get(self.steps[i]["image"])
        if pm is not None and not pm.isNull():
            s, ox, oy = self._map(r, zoom, dx)
            p.drawPixmap(QRectF(ox, oy, self.canvas[0] * s, self.canvas[1] * s), pm, QRectF(pm.rect()))
        else:
            # sin ilustración (no debería pasar): degradado con el icono del paso
            g = QLinearGradient(r.topLeft(), r.bottomRight())
            g.setColorAt(0, QColor("#1B1F3A"))
            g.setColorAt(1, QColor("#0F2A33"))
            p.fillRect(r, g)
            c = r.center() + QPointF(dx, 0)
            rg = QRadialGradient(c, r.height() * 0.6)
            rg.setColorAt(0, QColor(124, 108, 255, 90))
            rg.setColorAt(1, QColor(124, 108, 255, 0))
            p.fillRect(r, rg)
            size = r.height() * 0.34 * zoom
            icon = cached_glyph(self.steps[i]["glyph"], "#FFFFFF", 96)
            p.drawPixmap(QRectF(c.x() - size / 2, c.y() - size / 2, size, size), icon, QRectF(icon.rect()))
        p.setOpacity(1.0)

    def _paint_spots(self, p: QPainter, r: QRectF, a: float) -> None:
        rects = self.spots.get(self.steps[self.idx]["image"]) or []
        if not rects:
            return
        s, ox, oy = self._map(r)
        mapped = [QRectF(ox + x * s, oy + y * s, w * s, h * s) for x, y, w, h in rects]
        shade = QPainterPath()
        shade.addRect(r)
        for m in mapped:
            hole = QPainterPath()
            hole.addRoundedRect(m, 10, 10)
            shade = shade.subtracted(hole)
        p.fillPath(shade, QColor(4, 6, 10, int(105 * a)))  # el resto se apaga: la vista va a lo importante
        p.setBrush(Qt.BrushStyle.NoBrush)
        for n, m in enumerate(mapped):
            ph = (self.pulse + n * 0.5) % 1.0
            grow = 2 + 10 * _ease(ph)
            p.setPen(QPen(QColor(45, 212, 238, int(120 * a * (1 - ph))), 2))
            p.drawRoundedRect(m.adjusted(-grow, -grow, grow, grow), 10 + grow, 10 + grow)
            p.setPen(QPen(QColor(45, 212, 238, int(240 * a)), 2))
            p.drawRoundedRect(m, 10, 10)

    def _desc_height(self, i: int, width: float) -> float:
        fm = QFontMetricsF(_font(14))
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap)
        return min(44.0, fm.boundingRect(QRectF(0, 0, width, 200), flags, self.steps[i]["text"]).height())

    def _element(self, p: QPainter, k: int, i: int, alpha: float, dx: float) -> None:
        st = self.steps[i]
        top, left, tx, tw = self._text_geom()
        p.save()
        p.setOpacity(alpha)
        p.translate(dx, 0)
        if k == 0:  # icono del paso
            box = QRectF(left, top + 2, 48, 48)
            halo = QRadialGradient(box.center(), 52)
            halo.setColorAt(0, QColor(124, 108, 255, 80))
            halo.setColorAt(1, QColor(124, 108, 255, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(halo)
            p.drawEllipse(box.center(), 52, 52)
            g = QLinearGradient(box.topLeft(), box.bottomRight())
            g.setColorAt(0, QColor(C["accent"]))
            g.setColorAt(1, QColor(C["accent2"]))
            p.setBrush(g)
            p.drawRoundedRect(box, 15, 15)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 70), 1))
            p.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), 15, 15)
            icon = cached_glyph(st["glyph"], "#FFFFFF", 24)
            c = box.center()
            p.drawPixmap(QRectF(c.x() - 12, c.y() - 12, 24, 24), icon, QRectF(icon.rect()))
        elif k == 1:  # antetítulo
            p.setFont(_font(11, QFont.Weight.Bold, spacing=1.6))
            p.setPen(QColor(C["accent2"]))
            p.drawText(QRectF(tx, top, tw, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       self._eyebrow(i).upper())
        elif k == 2:  # título
            p.setFont(_font(25, QFont.Weight.Bold, FONT["display"]))
            p.setPen(QColor(C["text"]))
            p.drawText(QRectF(tx, top + 18, tw, 34), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       st["title"])
        elif k == 3:  # descripción
            p.setFont(_font(14))
            p.setPen(QColor("#A3AABB"))
            p.drawText(QRectF(tx, top + 58, tw, 44),
                       int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
                       st["text"])
        else:  # etiquetas
            y = top + 58 + self._desc_height(i, tw) + 14
            x = tx
            f = _font(12, QFont.Weight.DemiBold)
            fm = QFontMetricsF(f)
            p.setFont(f)
            for glyph, label in st["chips"]:
                text_w = fm.horizontalAdvance(label)
                chip = QRectF(x, y, 12 + 14 + 7 + text_w + 14, 30)
                if chip.right() > tx + tw:
                    break
                p.setPen(QPen(QColor(255, 255, 255, 24), 1))
                p.setBrush(QColor(255, 255, 255, 9))
                p.drawRoundedRect(chip.adjusted(0.5, 0.5, -0.5, -0.5), 15, 15)
                icon = cached_glyph(glyph, C["accent2"], 14)
                p.drawPixmap(QRectF(x + 12, y + 8, 14, 14), icon, QRectF(icon.rect()))
                p.setPen(QColor("#CDD2DE"))
                p.drawText(QRectF(x + 33, y, text_w + 2, 30),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
                x = chip.right() + 8
        p.restore()

    def _paint_hint(self, p: QPainter, alpha: float) -> None:
        """En el último paso, dónde volver a abrir la guía: «Vuelve a verla con [Guía] o [F1]»."""
        if alpha <= 0:
            return
        p.save()
        p.setOpacity(alpha)
        cy = self.height() - self.FOOT_H / 2
        x = 30.0
        text_f, key_f = _font(12), _font(11, QFont.Weight.Bold)
        for kind, label in (("t", "Vuelve a verla con"), ("k", "Guía"), ("t", "o"), ("k", help_key())):
            if kind == "t":
                fm = QFontMetricsF(text_f)
                p.setFont(text_f)
                p.setPen(QColor("#7D859A"))
                wdt = fm.horizontalAdvance(label)
                p.drawText(QRectF(x, cy - 12, wdt + 2, 24), Qt.AlignmentFlag.AlignVCenter, label)
            else:
                fm = QFontMetricsF(key_f)
                wdt = fm.horizontalAdvance(label) + 16
                cap = QRectF(x, cy - 11, wdt, 22)
                p.setPen(QPen(QColor(255, 255, 255, 34), 1))
                p.setBrush(QColor(255, 255, 255, 12))
                p.drawRoundedRect(cap.adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
                p.setFont(key_f)
                p.setPen(QColor(C["text"]))
                p.drawText(cap, Qt.AlignmentFlag.AlignCenter, label)
            x += wdt + 7
        p.restore()


class TourOverlay(QWidget):
    """Visor de la guía sobre la ventana: fondo esmerilado y oscurecido, tarjeta centrada."""
    finished = pyqtSignal(bool)  # True si se llegó al final

    def __init__(self, win: QMainWindow, animate: bool = True):
        super().__init__(win)
        self.win = win
        self.animate = animate
        self.active = False
        self.closing = False
        self._completed = False
        self.t = 0.0
        self.snap: QPixmap | None = None
        self.snap_blur: QPixmap | None = None
        self._shadow: QPixmap | None = None
        self._card_y = 0
        self.setAccessibleName("Guía rápida")
        self.card = TourCard(self)
        self.fade = QVariantAnimation(self)
        self.fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.fade.valueChanged.connect(self._on_fade)
        self.fade.finished.connect(self._on_fade_done)
        keys = ((Qt.Key.Key_Escape, lambda: self.finish(False)), (Qt.Key.Key_Right, self.card.forward),
                (Qt.Key.Key_Left, self.card.back), (Qt.Key.Key_Return, self._enter), (Qt.Key.Key_Enter, self._enter))
        for key, fn in keys:
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
        self.hide()

    def open(self) -> None:
        if self.active and not self.closing:
            return
        self.fade.stop()
        reopen = self.active
        self.active, self.closing = True, False
        if not reopen:
            self.snap = self.win.grab()
            self.snap_blur = frosted(self.snap)
        self.setGeometry(self.win.rect())
        self._layout()
        central = self.win.centralWidget()
        if central is not None:
            central.setEnabled(False)  # el tabulador y los clics se quedan en la guía
        self.show()
        self.raise_()
        if not reopen:
            self.card.start()
        if self.animate:
            self.card.pulse_anim.start()
        self.card.next_btn.setFocus()
        self._fade_to(1.0, 420)

    def finish(self, completed: bool) -> None:
        if not self.active or self.closing:
            return
        self._completed = completed
        self.closing = True
        self._fade_to(0.0, 240)

    def _enter(self) -> None:
        focus = QApplication.focusWidget()
        if isinstance(focus, QPushButton) and self.isAncestorOf(focus):
            focus.click()
        else:
            self.card.next()

    def _fade_to(self, target: float, ms: int) -> None:
        if not self.animate:
            self._on_fade(target)
            self._on_fade_done()
            return
        effect = self.card.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(self.card)
            self.card.setGraphicsEffect(effect)
        effect.setOpacity(self.t)
        self.fade.setStartValue(self.t)
        self.fade.setEndValue(target)
        self.fade.setDuration(ms)
        self.fade.start()

    def _on_fade(self, v) -> None:
        self.t = float(v)
        effect = self.card.graphicsEffect()
        if isinstance(effect, QGraphicsOpacityEffect):
            effect.setOpacity(self.t)
        self.card.move(self.card.x(), int(self._card_y + 18 * (1 - self.t)))
        self.update()

    def _on_fade_done(self) -> None:
        if self.closing:
            self.active = self.closing = False
            self.card.pulse_anim.stop()
            self.card.anim.stop()
            self.hide()
            self.card.setGraphicsEffect(None)
            self.snap = self.snap_blur = None
            central = self.win.centralWidget()
            if central is not None:
                central.setEnabled(True)
            self.finished.emit(self._completed)
        else:
            self.card.setGraphicsEffect(None)  # sin efecto, la tarjeta se pinta directa y nítida

    def _layout(self) -> None:
        self.card.fit(self.width(), self.height())
        self._card_y = (self.height() - self.card.height()) // 2
        self.card.move((self.width() - self.card.width()) // 2, int(self._card_y + 18 * (1 - self.t)))
        self._shadow = None

    def resizeEvent(self, _):  # noqa: N802
        self._layout()

    def mousePressEvent(self, e):  # noqa: N802
        e.accept()  # los clics fuera de la tarjeta no llegan a la ventana

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        if self.snap is not None:
            p.drawPixmap(self.rect(), self.snap)
        else:
            p.fillRect(self.rect(), QColor(C["bg"]))
        e = self.t
        if self.snap_blur is not None and e > 0:
            p.setOpacity(e)
            p.drawPixmap(self.rect(), self.snap_blur)
            p.setOpacity(1.0)
        p.fillRect(self.rect(), QColor(4, 6, 10, int(150 * e)))
        cg = QRectF(self.card.geometry())
        halo = QRadialGradient(cg.center(), cg.width() * 0.8)
        halo.setColorAt(0, QColor(124, 108, 255, int(44 * e)))
        halo.setColorAt(1, QColor(124, 108, 255, 0))
        p.fillRect(self.rect(), halo)
        blur = 36.0
        if self._shadow is None:
            self._shadow = soft_shadow(cg.width(), cg.height(), TourCard.RADIUS, blur, 230)
        pad = blur * 2
        p.setOpacity(e)
        p.drawPixmap(QRectF(cg.x() - pad, cg.y() - pad + 20, cg.width() + 2 * pad, cg.height() + 2 * pad),
                     self._shadow, QRectF(self._shadow.rect()))
        p.setOpacity(1.0)


# ── recordatorios de copia ───────────────────────────────────────────────────

REMIND_OPTIONS = [(1, "1 día"), (2, "2 días"), (3, "3 días"), (7, "1 semana"), (14, "2 semanas")]


def days_text(days: int) -> str:
    return dict(REMIND_OPTIONS).get(days) or f"{days} días"


def short_changes(p: dict | None) -> str:
    """«37 archivos sin copiar» para las listas; «» si no se ha mirado o no hay nada."""
    if not p or p.get("missing"):
        return ""
    files, dirs = p.get("files", 0), p.get("dirs", 0)
    if files:
        return f"{fmt_int(files)} archivo{'s' if files != 1 else ''} sin copiar"
    return "borrados o movidos sin copiar" if dirs else ""


class ChangeChecker(QObject):
    """Mira en segundo plano qué ha cambiado en el origen de cada copia desde la última vez."""
    finished = pyqtSignal(object, bool)  # {id: resultado}, a petición del usuario

    def __init__(self, parent=None):
        super().__init__(parent)
        self.busy = False
        self._cancel = threading.Event()

    def check(self, items: list[dict], manual: bool) -> bool:
        if self.busy or not items:
            return False
        self.busy = True
        self._cancel = threading.Event()
        cancel = self._cancel
        jobs = []
        for it in items:
            since = reminders.parse_time(it.get("last_ok"))
            if since is not None:
                jobs.append((it["id"], it["src"], since.timestamp(), it.get("patterns", []), it.get("excluded", [])))

        def run():
            results = {}
            for pid, src, since, patterns, excluded in jobs:
                if cancel.is_set():
                    break
                try:
                    results[pid] = engine.changes_since(src, since, patterns, excluded, cancel=cancel)
                except Exception:  # noqa: BLE001 - un origen raro no debe tumbar los avisos
                    results[pid] = {"missing": True, "complete": True}
            self.finished.emit(results, manual)

        threading.Thread(target=run, name="recordatorios", daemon=True).start()
        return True

    def cancel(self) -> None:
        self._cancel.set()


class ReminderRow(QFrame):
    """Una copia habitual en la tarjeta de recordatorios: origen → disco, estado y menú."""
    menu_requested = pyqtSignal(str, QPoint)

    def __init__(self, it: dict, status: str, color: str, current: bool):
        super().__init__()
        self.pid = it["id"]
        self.setObjectName("remRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 9, 6, 9)
        lay.setSpacing(10)
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background: {color}; border-radius: 4px;")
        lay.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(1)
        dest = it.get("label") or reminders.short_name(it.get("dst", ""))
        head = QHBoxLayout()
        head.setSpacing(8)
        title = ElideLabel(f"{reminders.short_name(it['src'])}  →  {dest}", Qt.TextElideMode.ElideRight)
        title.setObjectName("remTitle")
        head.addWidget(title, 1)
        if current:
            tag = QLabel("EN USO")
            tag.setObjectName("remTag")
            tag.setToolTip("Es el origen y el destino que tienes elegidos ahora")
            head.addWidget(tag, 0, Qt.AlignmentFlag.AlignVCenter)
        sub = ElideLabel(status, Qt.TextElideMode.ElideRight)
        sub.setObjectName("remSub")
        sub.setStyleSheet(f"color: {color};")
        col.addLayout(head)
        col.addWidget(sub)
        lay.addLayout(col, 1)
        self.more = QPushButton()
        self.more.setObjectName("remMore")
        self.more.setIcon(glyph_icon(G.MORE, C["muted"], 16))
        self.more.setFixedSize(30, 30)
        self.more.setToolTip("Opciones de esta copia")
        self.more.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more.clicked.connect(
            lambda: self.menu_requested.emit(self.pid, self.more.mapToGlobal(QPoint(0, self.more.height()))))
        lay.addWidget(self.more, 0, Qt.AlignmentFlag.AlignVCenter)
        copies = it.get("copies", 0)
        self.setToolTip(f"{it['src']}  →  {it.get('dst', '')}\n"
                        f"{copies} copia{'s' if copies != 1 else ''} completa{'s' if copies != 1 else ''}"
                        f"  ·  última {it.get('last_ok', '').replace('T', ' ')}")

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.menu_requested.emit(self.pid, e.globalPosition().toPoint())


class ReminderBar(QFrame):
    """Aviso en la ventana: hay cambios sin copiar. Se despliega con una animación corta."""

    def __init__(self):
        super().__init__()
        self.setObjectName("remind")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 12, 12)
        lay.setSpacing(14)
        badge = QLabel()
        badge.setObjectName("remBadge")
        badge.setFixedSize(42, 42)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setPixmap(glyph_pixmap(G.BELL, C["warn"], 20))
        lay.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title = ElideLabel("", Qt.TextElideMode.ElideRight)
        self.title.setObjectName("bannerTitle")
        self.text = QLabel()
        self.text.setObjectName("bannerText")
        self.text.setWordWrap(True)
        col.addWidget(self.title)
        col.addWidget(self.text)
        lay.addLayout(col, 1)
        self.go = QPushButton("  Sincronizar ahora")
        self.go.setObjectName("remGo")
        self.go.setIcon(glyph_icon(G.PLAY, "#FFFFFF", 13))
        self.later = QPushButton("Recordar mañana")
        self.later.setObjectName("remLater")
        self.close_btn = QPushButton()
        self.close_btn.setObjectName("remClose")
        self.close_btn.setIcon(glyph_icon(G.CLOSE, C["muted"], 13))
        self.close_btn.setFixedSize(30, 30)
        for b in (self.go, self.later, self.close_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            lay.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(lambda v: self.setMaximumHeight(int(v)))
        self._anim.finished.connect(self._anim_done)
        self._closing = False
        self.hide()

    def reveal(self) -> None:
        if self.isVisible() and not self._closing:
            return
        self._closing = False
        if not ui_animations():
            self.setMaximumHeight(16777215)
            self.show()
            return
        self.setMaximumHeight(0)
        self.show()
        self._anim.stop()
        self._anim.setStartValue(0)
        self._anim.setEndValue(max(60, self.sizeHint().height()))
        self._anim.setDuration(280)
        self._anim.start()

    def conceal(self) -> None:
        if not self.isVisible() or self._closing:
            return
        if not ui_animations():
            self.hide()
            return
        self._closing = True
        self._anim.stop()
        self._anim.setStartValue(self.height())
        self._anim.setEndValue(0)
        self._anim.setDuration(200)
        self._anim.start()

    def _anim_done(self) -> None:
        if self._closing:
            self._closing = False
            self.hide()
        self.setMaximumHeight(16777215)


class MainWindow(QMainWindow):
    def __init__(self, tray_mode: bool = False, updated: bool = False):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1440, 920)
        self.setMinimumSize(1220, 760)
        os.makedirs(DATA_DIR, exist_ok=True)
        self.settings = QSettings(os.path.join(DATA_DIR, "settings.ini"), QSettings.Format.IniFormat)

        self.job: engine.BackupJob | None = None
        self.job_thread: threading.Thread | None = None
        self.taskbar: Taskbar | None = None
        self.tray: QSystemTrayIcon | None = None
        self.dialog: ConnectDialog | None = None
        self._quitting = False
        self._tray_hint_shown = False
        self._state = "idle"
        self._finalized = True
        self._samples: deque = deque()
        self._eta: float | None = None
        self._last_tick = 0.0
        self._last_chart = 0.0
        self._op_start = 0.0
        self._op_wall = datetime.now()
        self._pause_at: float | None = None
        self._paused_total = 0.0
        self._paused_copy = 0.0
        self._copy_seen = False
        self._scrub_seen = False
        self._copy_requested = False
        self._err_seen = 0
        self._last_log = ""
        self.guard = keepawake.Guard()
        self._guard_logged = False
        self._awake_refresh = 0.0
        self._sleeping = False
        self._sleep_paused = False
        self._disk_lost_seen = False
        self.tour: TourOverlay | None = None
        self._tour_pending = True
        self.registry = reminders.Registry()
        self._bar_target: str | None = None

        self._load_settings()
        self._build()
        self._apply_settings()
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._tick)
        self._set_state("idle")
        self._show_idle_banner()

        self.watcher = DiskWatcher(self)
        self.watcher.arrived.connect(self._on_drive_arrived)
        QShortcut(QKeySequence(QKeySequence.StandardKey.HelpContents), self, activated=self.toggle_tour)

        # recordatorios: cada media hora se mira qué copias toca revisar (solo se recorre el origen si toca)
        self.rem_checker = ChangeChecker(self)
        self.rem_checker.finished.connect(self._on_changes_checked)
        self._remind_timer = QTimer(self)
        self._remind_timer.setInterval(30 * 60 * 1000)
        self._remind_timer.timeout.connect(self._reminder_tick)
        self._remind_timer.start()
        QTimer.singleShot(150_000, self._reminder_tick)  # sin prisa al arrancar con la sesión
        self.watcher.arrived.connect(lambda _root: QTimer.singleShot(3000, self._refresh_reminders))
        self._refresh_reminders()

        # apagar, reiniciar o cerrar sesión con una copia en marcha
        app = QApplication.instance()
        app.commitDataRequest.connect(self._on_session_end_query)
        app.aboutToQuit.connect(self._on_about_to_quit)
        if isinstance(app, App):
            app.quit_guard = self._allow_quit
        if IS_WIN:
            self._power_events = PowerEvents(self._on_system_suspend, self._on_system_resume)
            app.installNativeEventFilter(self._power_events)
        if self.watch_box.isChecked() or tray_mode:
            self._ensure_tray()
        # al arrancar con la sesión la ventana no debe abrirse sola aunque la app pase a primer plano
        self.tray_only_start = tray_mode
        if tray_mode:
            QTimer.singleShot(3000, lambda: setattr(self, "tray_only_start", False))
            # si el disco ya está puesto y hace tiempo de la última copia, preguntar
            QTimer.singleShot(4000, self._startup_check)

        # actualizaciones: al poco de arrancar y luego cada 6 h (como mucho una consulta al día)
        self.update_dialog: UpdateDialog | None = None
        self._pending_release: updater.Release | None = None
        self.checker = UpdateChecker(self)
        self.checker.finished.connect(self._on_update_result)
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(6 * 3600 * 1000)
        self._update_timer.timeout.connect(lambda: self._check_updates(manual=False))
        self._update_timer.start()
        QTimer.singleShot(15000, lambda: self._check_updates(manual=False))
        self._refresh_update_status()
        if updated:
            self._log(f"Actualizado a la versión {APP_VERSION}", C["ok"])
            QTimer.singleShot(1500, lambda: self._notify(APP_NAME, f"Actualizado a la versión {APP_VERSION}."))

    # ── actualizaciones ──────────────────────────────────────────────────
    def _on_update_toggled(self, on: bool):
        self._save_settings()
        self._refresh_update_status()
        if on:
            self._check_updates(manual=False)

    def _check_updates(self, manual: bool):
        if not manual:
            if not self.update_box.isChecked():
                return
            try:
                last = datetime.fromisoformat(self.update_checked) if self.update_checked else None
            except ValueError:
                last = None
            if last and (datetime.now() - last).total_seconds() < 20 * 3600:
                return
        self.btn_update.setEnabled(False)
        self.update_status.setText("Consultando GitHub…")
        self.checker.check(manual)

    def _on_update_result(self, rel, error: str, manual: bool):
        self.btn_update.setEnabled(True)
        if rel is None:
            self.update_status.setText(f"No se pudo consultar GitHub: {error}")
            if manual:
                QMessageBox.warning(self, APP_NAME, f"No se pudo consultar si hay versiones nuevas.\n\n{error}")
            return
        self.update_checked = datetime.now().isoformat(timespec="seconds")
        self._save_settings()
        newer = updater.is_newer(rel.version, CURRENT_VERSION)
        if newer and (manual or rel.version != self.update_skip):
            self._pending_release = rel
            self._offer_update()
        elif manual:
            QMessageBox.information(self, APP_NAME, f"Tienes la última versión ({CURRENT_VERSION}).")
        self._refresh_update_status(rel)

    def _offer_update(self):
        rel = self._pending_release
        if rel is None or (self.update_dialog and self.update_dialog.isVisible()):
            return
        if self._state in RUNNING:
            # se ofrece al terminar la sincronización (ver _finish)
            self._notify("Versión nueva disponible", f"{APP_NAME} {rel.version}: te preguntaré al terminar la copia.")
            return
        self._notify("Versión nueva disponible", f"{APP_NAME} {rel.version} está lista para instalar.")
        self._show_window()
        self.update_dialog = UpdateDialog(self, rel)
        self.update_dialog.open()
        QApplication.alert(self.update_dialog, 0)

    def _refresh_update_status(self, rel: updater.Release | None = None):
        if not hasattr(self, "update_status"):
            return
        parts = [f"Versión instalada: {CURRENT_VERSION}"]
        if rel is not None and updater.is_newer(rel.version, CURRENT_VERSION):
            skipped = "  (omitida)" if rel.version == self.update_skip else ""
            parts.append(f"nueva disponible: {rel.version}{skipped}")
        elif self.update_checked:
            parts.append(f"al día · comprobado {fmt_ago(self.update_checked)}")
        if not self.update_box.isChecked():
            parts.append("búsqueda automática desactivada")
        self.update_status.setText("  ·  ".join(parts))
        self._refresh_summaries()

    def quit_for_update(self):
        """Cierra la app para que el instalador pueda sustituirla."""
        self._save_settings()
        self._quitting = True
        if self.tray:
            self.tray.hide()
        self._guard_stop()
        QApplication.quit()

    # ── construcción ─────────────────────────────────────────────────────
    def _build(self):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_header())
        body = QHBoxLayout()
        body.setContentsMargins(22, 6, 22, 18)
        body.setSpacing(18)
        body.addWidget(self._build_sidebar())
        body.addLayout(self._build_main(), 1)
        outer.addLayout(body, 1)
        outer.addWidget(self._build_footer())

    def _build_header(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(22, 18, 22, 12)
        h.setSpacing(14)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(40, 40))
        h.addWidget(logo)
        col = QVBoxLayout()
        col.setSpacing(1)
        trow = QHBoxLayout()
        trow.setSpacing(10)
        title = QLabel(APP_NAME)
        title.setObjectName("h1")
        version = QLabel(f"v{APP_VERSION}")
        version.setObjectName("count")
        trow.addWidget(title)
        trow.addWidget(version, 0, Qt.AlignmentFlag.AlignBottom)
        trow.addStretch(1)
        sub = QLabel("Sincronización de proyectos  ·  detecta cambios, movidos y borrados  ·  guarda versiones")
        sub.setObjectName("muted")
        col.addLayout(trow)
        col.addWidget(sub)
        h.addLayout(col)
        h.addStretch(1)
        self.awake_chip = QFrame()
        self.awake_chip.setObjectName("awake")
        ah = QHBoxLayout(self.awake_chip)
        ah.setContentsMargins(10, 4, 12, 4)
        ah.setSpacing(6)
        self.awake_icon = QLabel()
        self.awake_text = QLabel()
        ah.addWidget(self.awake_icon)
        ah.addWidget(self.awake_text)
        self.awake_chip.hide()
        h.addWidget(self.awake_chip)
        h.addSpacing(8)
        self.pill = QLabel()
        self.pill.setObjectName("pill")
        self.btn_help = QPushButton("  Guía")
        self.btn_help.setObjectName("help")
        self.btn_help.setIcon(glyph_icon(G.HELP, C["accent2"], 15))
        self.btn_help.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_help.setToolTip(f"Guía rápida  ({help_key()})")
        self.btn_help.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)  # alto de la píldora
        self.btn_help.clicked.connect(lambda: self.show_tour())
        h.addWidget(self.btn_help)
        h.addSpacing(4)
        h.addWidget(self.pill)
        return w

    def _build_sidebar(self) -> QWidget:
        """Ajustes en el orden en que se usan: qué y adónde, qué se copia, cómo, lo automático y el sistema."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedWidth(SIDEBAR_W)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 10, 4)
        lay.setSpacing(12)
        self.sections: dict[str, Section] = {}

        def group(text: str, first: bool = False):
            g = QLabel(text.upper())
            g.setObjectName("group")
            if not first:
                lay.addSpacing(6)
            lay.addWidget(g)

        def section(key: str, title: str, glyph: str, collapsible: bool = True) -> Section:
            sec = Section(key, title, glyph, collapsible)
            sec.expanded_changed.connect(self._on_section_toggled)
            self.sections[key] = sec
            lay.addWidget(sec)
            return sec

        def combo_row(title: str, subtitle: str, combo: QComboBox, width: int = 148, tip: str = "") -> SettingRow:
            combo.setFixedWidth(width)
            return SettingRow(title, subtitle, combo, tip)

        # ── copia: origen → destino ──
        group("Copia", first=True)
        pair = Card(margins=(18, 16, 18, 16))
        pair.body.setSpacing(0)
        self.src_card = PathCard("Origen", G.FOLDER, "Carpeta de origen", frameless=True)
        self.dst_card = PathCard("Destino", G.DRIVE, "Carpeta de destino", frameless=True)
        self.src_card.changed.connect(self._on_src_changed)
        self.dst_card.changed.connect(self._on_dst_changed)
        pair.body.addWidget(self.src_card)
        pair.body.addWidget(FlowConnector())
        pair.body.addWidget(self.dst_card)
        self.card_pair = pair
        lay.addWidget(pair)

        # ── qué se copia ──
        group("Qué se copia")
        rules = section("reglas", "Excluir carpetas", G.FILTER)
        hint = QLabel("Se omiten en cualquier nivel de cada proyecto, en origen y destino. Admite comodines "
                      "(*.egg-info).")
        hint.setObjectName("faint")
        hint.setWordWrap(True)
        rules.body.addWidget(hint)
        self.chips_box = QWidget()
        self.chips_box.setToolTip("Clic para activar o desactivar una regla · clic derecho en una regla propia "
                                  "para quitarla")
        self.flow = FlowLayout(self.chips_box, 8)
        rules.body.addWidget(self.chips_box)
        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        self.rule_edit = QLineEdit()
        self.rule_edit.setPlaceholderText("Añadir regla…  (p. ej. Library)")
        self.rule_edit.returnPressed.connect(self._add_rule)
        add_btn = QPushButton()
        add_btn.setObjectName("icon")
        add_btn.setIcon(glyph_icon(G.ADD, C["text"], 14))
        add_btn.setFixedSize(38, 36)
        add_btn.setToolTip("Añadir regla")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.clicked.connect(self._add_rule)
        add_row.addWidget(self.rule_edit, 1)
        add_row.addWidget(add_btn)
        rules.body.addLayout(add_row)
        self.card_rules = rules

        # ── cómo se copia ──
        group("Cómo se copia")
        opts = section("modo", "Modo y versiones", G.GEAR)
        self.mode_combo = QComboBox()
        for key, text in engine.MODES.items():
            self.mode_combo.addItem(text, key)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        opts.body.addWidget(self.mode_combo)
        self.mode_hint = QLabel()
        self.mode_hint.setObjectName("faint")
        self.mode_hint.setWordWrap(True)
        opts.body.addWidget(self.mode_hint)
        self.keep_combo = QComboBox()
        for days, text in KEEP_OPTIONS:
            self.keep_combo.addItem(text, days)
        opts.body.addWidget(combo_row("Conservar versiones", "De lo que modificas o borras", self.keep_combo))
        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(1, 32)
        self.threads_spin.valueChanged.connect(self._update_summary)
        opts.body.addWidget(combo_row("Hilos de copia", "Archivos que se copian a la vez", self.threads_spin, 96))
        self.full_box = Toggle()
        self.full_box.toggled.connect(self._invalidate_plan)
        opts.body.addWidget(SettingRow(
            "Revisar todo el destino", "En el próximo análisis (si no, cada 7 días)", self.full_box,
            "Normalmente el análisis usa el índice de la última copia y no relee el disco. Cambio = distinto "
            "tamaño o fecha de modificación (al nanosegundo en NTFS y APFS)."))

        integ = section("integridad", "Integridad", G.SHIELD)
        self.verify_box = Toggle()
        self.verify_box.toggled.connect(self._invalidate_plan)
        integ.body.addWidget(SettingRow("Verificar cada copia", "Relee el disco y compara su SHA-256",
                                        self.verify_box))
        self.scrub_combo = QComboBox()
        for secs, text in SCRUB_OPTIONS:
            self.scrub_combo.addItem(text, secs)
        integ.body.addWidget(combo_row(
            "Revisar el disco", "Tras sincronizar, busca corrupción silenciosa", self.scrub_combo,
            tip="Comprueba una tanda de archivos ya copiados durante el tiempo elegido: primero los nunca "
                "comprobados y luego los de hace más de 30 días. Si el dañado es el destino, lo repara."))
        self.integ_status = QLabel("")
        self.integ_status.setObjectName("faint")
        self.integ_status.setWordWrap(True)
        integ.body.addWidget(self.integ_status)
        self.btn_scrub = QPushButton("  Comprobar todo el disco ahora")
        self.btn_scrub.setIcon(glyph_icon(G.SHIELD, C["text"], 14))
        self.btn_scrub.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_scrub.clicked.connect(lambda: self._start(copy=True, scrub_all=True))
        integ.body.addWidget(self.btn_scrub)

        # ── automático ──
        group("Automático")
        disk = section("disco", "Disco de copia", G.SYNC)
        self.disk_status = QLabel("")
        self.disk_status.setObjectName("faint")
        self.disk_status.setWordWrap(True)
        disk.body.addWidget(self.disk_status)
        where = "Se inicia con Windows, en la bandeja" if IS_WIN else "Se inicia con la sesión, en la barra de menús"
        self.watch_box = Toggle()
        self.watch_box.toggled.connect(self._on_watch_toggled)
        disk.body.addWidget(SettingRow("Vigilar en segundo plano", where, self.watch_box))
        self.connect_combo = QComboBox()
        for key, text in CONNECT_ACTIONS.items():
            self.connect_combo.addItem(text, key)
        self.connect_combo.currentIndexChanged.connect(lambda _i: self._refresh_summaries())
        disk.body.addWidget(QLabel("Al conectar el disco", objectName="fieldLabel"))
        disk.body.addWidget(self.connect_combo)
        self.card_disk = disk

        rem = section("recordatorios", "Recordatorios", G.BELL)
        self.remind_box = Toggle()
        self.remind_box.toggled.connect(self._on_remind_toggled)
        rem.body.addWidget(SettingRow("Recordarme hacer copia", "Solo si hay cambios sin copiar, una vez por periodo",
                                      self.remind_box))
        self.remind_combo = QComboBox()
        for days, text in REMIND_OPTIONS:
            self.remind_combo.addItem(text, days)
        self.remind_combo.currentIndexChanged.connect(self._on_remind_days_changed)
        rem.body.addWidget(combo_row("Avisar después de", "Días sin copia con cambios", self.remind_combo))
        where = "con Windows" if IS_WIN else "al iniciar sesión"
        link = f'<a href="#" style="color:{C["accent2"]}; text-decoration:none">Iniciarla {where} en segundo plano</a>'
        self.remind_tray_hint = QLabel(f"Con la app cerrada no puede avisarte. {link}")
        self.remind_tray_hint.setObjectName("faint")
        self.remind_tray_hint.setWordWrap(True)
        self.remind_tray_hint.linkActivated.connect(lambda _l: self.watch_box.setChecked(True))
        rem.body.addWidget(self.remind_tray_hint)
        cl = QHBoxLayout()
        cl.addWidget(QLabel("Copias habituales", objectName="fieldLabel"))
        cl.addStretch(1)
        self.btn_remind_check = QPushButton("Comprobar ahora")
        self.btn_remind_check.setObjectName("link")
        self.btn_remind_check.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_remind_check.setToolTip("Mira ya si hay cambios sin copiar en todas las copias habituales")
        self.btn_remind_check.clicked.connect(lambda: self._reminder_tick(manual=True))
        cl.addWidget(self.btn_remind_check)
        rem.body.addLayout(cl)
        rows = QWidget()
        self.remind_rows_box = rows
        self.remind_rows = QVBoxLayout(rows)
        self.remind_rows.setContentsMargins(0, 0, 0, 0)
        self.remind_rows.setSpacing(6)
        rem.body.addWidget(rows)
        self.remind_empty = QLabel("Aún no hay ninguna: cada sincronización completa se añade sola, con su origen "
                                   "y su disco.")
        self.remind_empty.setObjectName("faint")
        self.remind_empty.setWordWrap(True)
        rem.body.addWidget(self.remind_empty)
        self.remind_status = QLabel("")
        self.remind_status.setObjectName("faint")
        rem.body.addWidget(self.remind_status)
        self.card_remind = rem

        # ── sistema ──
        group("Sistema")
        power = section("energia", "Energía", G.POWER)
        self.awake_box = Toggle()
        self.awake_box.toggled.connect(lambda _on: (self._save_settings(), self._refresh_awake(),
                                                    self._refresh_summaries()))
        if IS_MAC:
            how = ("El Mac no reposa por inactividad ni duerme los discos (la pantalla sí puede apagarse), y "
                   "cerrar la app, la sesión o apagar pide confirmación. Si se duerme igualmente, la copia "
                   "espera al disco y sigue sola.")
        else:
            how = ("El equipo no se suspende por inactividad (la pantalla sí puede apagarse), el disco USB no "
                   "se duerme y Windows avisa si intentas apagar o reiniciar. Si se suspende igualmente (tapa, "
                   "botón), la copia se pausa y sigue sola al volver.")
        power.body.addWidget(SettingRow("Equipo y disco despiertos", "Mientras analiza o sincroniza",
                                        self.awake_box, how))
        self.awake_status = QLabel("")
        self.awake_status.setObjectName("faint")
        self.awake_status.setWordWrap(True)
        self.awake_status.setToolTip(how)
        power.body.addWidget(self.awake_status)
        self.card_power = power

        upd = section("actualizaciones", "Actualizaciones", G.DOWNLOAD)
        self.update_box = Toggle()
        self.update_box.toggled.connect(self._on_update_toggled)
        upd.body.addWidget(SettingRow("Buscar versiones nuevas", "En GitHub; pregunta antes de instalar",
                                      self.update_box))
        self.update_status = QLabel("")
        self.update_status.setObjectName("faint")
        self.update_status.setWordWrap(True)
        upd.body.addWidget(self.update_status)
        self.btn_update = QPushButton("  Buscar ahora")
        self.btn_update.setIcon(glyph_icon(G.REFRESH, C["text"], 14))
        self.btn_update.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_update.clicked.connect(lambda: self._check_updates(manual=True))
        upd.body.addWidget(self.btn_update)
        lay.addStretch(1)

        folded = self._cfg.get("folded")
        for key in (DEFAULT_FOLDED if not isinstance(folded, list) else folded):
            if key in self.sections:
                self.sections[key].set_expanded(False, animate=False)
        for w in (self.mode_combo, self.keep_combo, self.threads_spin, self.full_box, self.verify_box,
                  self.scrub_combo, self.remind_combo):
            sig = w.toggled if isinstance(w, Toggle) else (w.valueChanged if isinstance(w, QSpinBox)
                                                          else w.currentIndexChanged)
            sig.connect(lambda *_a: self._refresh_summaries())

        scroll.setWidget(inner)
        self.sidebar = inner
        return scroll

    # ── barra lateral: plegado y resúmenes ───────────────────────────────
    def _on_section_toggled(self, _key: str, _on: bool):
        if hasattr(self, "settings"):
            folded = [k for k, s in self.sections.items() if s.collapsible and not s.expanded]
            self.settings.setValue("folded", json.dumps(folded))

    def _refresh_summaries(self):
        """La línea que enseña cada tarjeta plegada: lo elegido, sin tener que abrirla."""
        if not hasattr(self, "sections"):
            return
        sec = self.sections
        rules = self._active_rules()
        if rules:
            more = f" y {len(rules) - 3} más" if len(rules) > 3 else ""
            sec["reglas"].set_summary(f"{', '.join(rules[:3])}{more}")
        else:
            sec["reglas"].set_summary("Ninguna regla activa: se copia todo")
        mode = {engine.MODE_MIRROR: "Espejo con versiones", engine.MODE_UPDATE: "Solo añadir y actualizar"}.get(
            self.mode_combo.currentData(), self.mode_combo.currentText())
        sec["modo"].set_summary(f"{mode}  ·  versiones {self.keep_combo.currentText().lower()}  ·  "
                                f"{self.threads_spin.value()} hilos")
        scrub = self.scrub_combo.currentData()
        sec["integridad"].set_summary(
            f"{'SHA-256 en cada copia' if self.verify_box.isChecked() else 'Sin verificar al copiar'}  ·  "
            f"{'revisión ' + self.scrub_combo.currentText() if scrub else 'sin revisión'}")
        sec["disco"].set_summary(f"{'Vigilando' if self.watch_box.isChecked() else 'Sin vigilar'}  ·  "
                                 f"{self.connect_combo.currentText().lower()}")
        if self.remind_box.isChecked():
            n = len(self.registry.items)
            alerts = len(self.registry.alerts())
            text = f"Tras {self.remind_combo.currentText()}  ·  {n} copia{'s' if n != 1 else ''} habitual" \
                   f"{'es' if n != 1 else ''}"
            if alerts:
                text += f"  ·  {alerts} con cambios sin copiar"
            sec["recordatorios"].set_summary(text)
        else:
            sec["recordatorios"].set_summary("Desactivados")
        sec["energia"].set_summary("Sin suspensión mientras copia" if self.awake_box.isChecked() else "Desactivado")
        sec["actualizaciones"].set_summary(f"v{APP_VERSION}  ·  "
                                           f"{'automáticas' if self.update_box.isChecked() else 'solo a mano'}")

    def _build_main(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(14)
        top = QHBoxLayout()
        seg = QFrame()
        seg.setObjectName("segbar")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(4, 4, 4, 4)
        sl.setSpacing(4)
        self.seg_group = QButtonGroup(self)
        self.seg_buttons = []
        for i, (text, glyph) in enumerate([("Carpetas", G.TREE), ("Cambios", G.SYNC),
                                           ("Progreso", G.SPEED), ("Incidencias", G.WARN)]):
            b = QPushButton(f"  {text}")
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setIcon(glyph_icon(glyph, C["muted"], 14, C["accent2"]))
            self.seg_group.addButton(b, i)
            self.seg_buttons.append(b)
            sl.addWidget(b)
        top.addWidget(seg)
        top.addStretch(1)
        col.addLayout(top)
        self.remind_bar = ReminderBar()
        self.remind_bar.go.clicked.connect(self._remind_go)
        self.remind_bar.later.clicked.connect(self._remind_later)
        self.remind_bar.close_btn.clicked.connect(self._remind_dismiss)
        col.addWidget(self.remind_bar)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_tree_page())
        self.stack.addWidget(self._build_changes_page())
        self.stack.addWidget(self._build_progress_page())
        self.stack.addWidget(self._build_issues_page())
        self.seg_group.idClicked.connect(self.stack.setCurrentIndex)
        self.seg_buttons[PAGE_TREE].setChecked(True)
        col.addWidget(self.stack, 1)
        return col

    def _build_tree_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        bar = QFrame()
        bar.setObjectName("hint")
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 10, 10, 10)
        h.setSpacing(10)
        h.addWidget(glyph_label(G.INFO, C["accent2"], 16))
        msg = QLabel("Desmarca las carpetas que no quieras copiar. Expande para elegir subcarpetas concretas.")
        msg.setObjectName("muted")
        msg.setWordWrap(True)
        h.addWidget(msg, 1)
        self.excl_count = QLabel("")
        self.excl_count.setObjectName("faint")
        h.addWidget(self.excl_count)
        reset = QPushButton("Restablecer")
        reset.clicked.connect(self._reset_exclusions)
        reload_btn = QPushButton()
        reload_btn.setObjectName("icon")
        reload_btn.setIcon(glyph_icon(G.REFRESH, C["text"], 14))
        reload_btn.setFixedSize(38, 34)
        reload_btn.setToolTip("Volver a leer las carpetas del origen")
        reload_btn.clicked.connect(self._load_tree)
        h.addWidget(reset)
        h.addWidget(reload_btn)
        v.addWidget(bar)
        self.tree = FolderTree()
        self.tree.changed.connect(self._on_tree_changed)
        v.addWidget(self.tree, 1)
        return page

    def _build_changes_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        bar = QFrame()
        bar.setObjectName("hint")
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 8, 10, 8)
        h.setSpacing(8)
        self.kind_group = QButtonGroup(self)
        self.kind_buttons: dict[str | None, QPushButton] = {}
        for key, text in [(None, "Todos"), ("n", "Nuevos"), ("c", "Modificados"),
                          ("m", "Movidos"), ("d", "A versiones")]:
            b = QPushButton(text)
            b.setObjectName("chip")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setProperty("label", text)
            self.kind_group.addButton(b)
            self.kind_buttons[key] = b
            b.clicked.connect(self._filter_changes)
            h.addWidget(b)
        self.kind_buttons[None].setChecked(True)
        h.addStretch(1)
        self.change_search = QLineEdit()
        self.change_search.setPlaceholderText("Buscar ruta…")
        self.change_search.setMinimumWidth(150)
        self.change_search.setMaximumWidth(260)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._filter_changes)
        self.change_search.textChanged.connect(lambda _t: self._search_timer.start())
        h.addWidget(self.change_search)
        v.addWidget(bar)

        self.changes_model = ChangesModel()
        self.changes = QTableView()
        self.changes.setModel(self.changes_model)
        self.changes.verticalHeader().hide()
        self.changes.verticalHeader().setDefaultSectionSize(30)
        self.changes.setShowGrid(False)
        self.changes.setWordWrap(False)
        self.changes.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.changes.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.changes.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self.changes.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.changes.setColumnWidth(0, 130)
        self.changes.setColumnWidth(2, 120)
        self.changes_empty = QLabel("Pulsa «Analizar» para ver exactamente qué se va a copiar, mover o guardar "
                                    "en versiones antes de tocar nada.")
        self.changes_empty.setObjectName("muted")
        self.changes_empty.setWordWrap(True)
        self.changes_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.changes_stack = QStackedWidget()
        self.changes_stack.addWidget(self.changes_empty)
        self.changes_stack.addWidget(self.changes)
        v.addWidget(self.changes_stack, 1)
        return page

    def _build_progress_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 6, 0)
        v.setSpacing(14)

        self.banner = Banner()
        v.addWidget(self.banner)

        self.summary_card = Card("Resumen de la copia", G.DONE)
        self.s_new = Tile("Nuevos", G.ADD, C["ok"])
        self.s_changed = Tile("Modificados", G.REFRESH, C["accent2"])
        self.s_moved = Tile("Movidos", G.NEXT, C["accent"])
        self.s_versions = Tile("A versiones", G.CLOCK, C["warn"])
        self.s_dirs_new = Tile("Carpetas nuevas", G.FOLDER, C["folder"])
        self.s_dirs_gone = Tile("Carpetas quitadas", G.FOLDER, C["muted"])
        self.s_same = Tile("Sin cambios", G.CHECK, C["ok"])
        self.s_checked = Tile("Verificados", G.SHIELD, C["accent2"])
        self.summary_tiles = [self.s_new, self.s_changed, self.s_moved, self.s_versions,
                              self.s_dirs_new, self.s_dirs_gone, self.s_same, self.s_checked]
        self.summary_card.body.addWidget(TileGrid(self.summary_tiles))
        self.summary_line = QLabel("")
        self.summary_line.setObjectName("muted")
        self.summary_line.setWordWrap(True)
        self.summary_card.body.addWidget(self.summary_line)
        self.summary_card.hide()
        v.addWidget(self.summary_card)

        top = QHBoxLayout()
        top.setSpacing(14)
        ring_card = Card(margins=(10, 10, 10, 10))
        ring_card.setFixedWidth(284)
        self.ring = Ring()
        ring_card.body.addWidget(self.ring)
        top.addWidget(ring_card)

        right = QVBoxLayout()
        right.setSpacing(14)
        self.t_elapsed = Tile("Transcurrido", G.TIMER, C["accent2"])
        self.t_left = Tile("Restante", G.CLOCK, C["accent2"])
        self.t_speed = Tile("Velocidad", G.SPEED, C["accent2"])
        self.t_eta = Tile("Fin estimado", G.FLAG, C["accent2"])
        self.t_copied = Tile("Copiado", G.COPY, C["accent"])
        self.t_files = Tile("Archivos", G.DOC, C["accent"])
        self.t_skipped = Tile("Sin cambios", G.CHECK, C["accent"])
        self.t_errors = Tile("Errores", G.WARN, C["accent"])
        self.tiles = [self.t_elapsed, self.t_left, self.t_speed, self.t_eta,
                      self.t_copied, self.t_files, self.t_skipped, self.t_errors]
        right.addWidget(TileGrid(self.tiles))

        bars = Card(margins=(18, 14, 18, 16))
        bars.body.setSpacing(8)
        self.bar_bytes, self.bar_bytes_val = self._bar_row(bars.body, "Datos")
        bars.body.addSpacing(4)
        self.bar_files, self.bar_files_val = self._bar_row(bars.body, "Archivos")
        right.addWidget(bars)
        top.addLayout(right, 1)
        v.addLayout(top)

        mid = QHBoxLayout()
        mid.setSpacing(14)
        chart_card = Card(margins=(18, 14, 18, 14))
        self.chart = SpeedChart()
        chart_card.body.addWidget(self.chart)
        mid.addWidget(chart_card, 3)
        self.active_card = Card("En curso", G.SYNC)
        self.active_rows_box = QVBoxLayout()
        self.active_rows_box.setSpacing(12)
        self.active_card.body.addLayout(self.active_rows_box)
        self.active_card.body.addStretch(1)
        self.active_rows: list[ActiveRow] = []
        mid.addWidget(self.active_card, 2)
        v.addLayout(mid)

        cur = QFrame()
        cur.setObjectName("hint")
        ch = QHBoxLayout(cur)
        ch.setContentsMargins(16, 10, 16, 10)
        ch.setSpacing(10)
        ch.addWidget(glyph_label(G.FOLDER, C["folder"], 14))
        self.cur_label = ElideLabel("—")
        self.cur_label.setObjectName("mono")
        ch.addWidget(self.cur_label, 1)
        v.addWidget(cur)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setMinimumHeight(140)
        self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        v.addWidget(self.log)

        scroll.setWidget(page)
        return scroll

    def _bar_row(self, layout: QVBoxLayout, title: str):
        row = QHBoxLayout()
        lbl = QLabel(title.upper())
        lbl.setObjectName("barLabel")
        val = QLabel("—")
        val.setObjectName("barValue")
        row.addWidget(lbl)
        row.addStretch(1)
        row.addWidget(val)
        layout.addLayout(row)
        bar = thin_bar(8)
        layout.addWidget(bar)
        return bar, val

    def _build_issues_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        bar = QFrame()
        bar.setObjectName("hint")
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 10, 10, 10)
        self.issues_icon = glyph_label(G.DONE, C["ok"], 16)
        h.addWidget(self.issues_icon)
        self.issues_label = QLabel("Sin incidencias")
        self.issues_label.setObjectName("muted")
        h.addWidget(self.issues_label, 1)
        self.btn_export = QPushButton("Exportar…")
        self.btn_export.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self._export_issues)
        h.addWidget(self.btn_export)
        v.addWidget(bar)
        self.issues = QTableWidget(0, 2)
        self.issues.setHorizontalHeaderLabels(["RUTA", "MOTIVO"])
        self.issues.verticalHeader().hide()
        self.issues.setShowGrid(False)
        self.issues.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.issues.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.issues.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        hh = self.issues.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.issues.setColumnWidth(1, 360)
        empty = QLabel("Ningún archivo ha dado problemas.\nSi alguno no se puede leer o copiar, aparece aquí y la "
                       "copia sigue con el resto.")
        empty.setObjectName("muted")
        empty.setWordWrap(True)
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.issues_stack = QStackedWidget()
        self.issues_stack.addWidget(empty)
        self.issues_stack.addWidget(self.issues)
        v.addWidget(self.issues_stack, 1)
        return page

    def _set_issues(self, total: int):
        ok = total == 0
        set_glyph(self.issues_icon, G.DONE if ok else G.WARN, C["ok"] if ok else C["warn"], 16)
        self.issues_label.setText("Sin incidencias" if ok else
                                  f"{fmt_int(total)} incidencia{'s' if total != 1 else ''}: archivos que no se "
                                  "pudieron leer o copiar. El resto sigue adelante.")
        self.btn_export.setEnabled(not ok)
        self.issues_stack.setCurrentIndex(0 if ok else 1)

    def _build_footer(self) -> QWidget:
        f = QFrame()
        f.setObjectName("footer")
        h = QHBoxLayout(f)
        h.setContentsMargins(22, 14, 22, 14)
        h.setSpacing(10)
        self.summary = QLabel()
        self.summary.setObjectName("muted")
        h.addWidget(self.summary, 1)

        def button(text, glyph, name=None, color=C["text"]):
            b = QPushButton(f"  {text}")
            b.setIcon(glyph_icon(glyph, color, 14))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if name:
                b.setObjectName(name)
            h.addWidget(b)
            return b

        self.btn_open = button("Abrir destino", G.OPEN)
        self.btn_scan = button("Analizar", G.SEARCH)
        self.btn_pause = button("Pausar", G.PAUSE)
        self.btn_cancel = button("Cancelar", G.STOP, "danger")
        self.btn_start = button("Sincronizar", G.PLAY, "primary", "#FFFFFF")
        self.btn_start.setMinimumWidth(230)
        self.btn_open.clicked.connect(self._open_dest)
        self.btn_scan.clicked.connect(lambda: self._start(copy=False))
        self.btn_start.clicked.connect(lambda: self._start(copy=True))
        self.btn_pause.clicked.connect(self._toggle_pause)
        self.btn_cancel.clicked.connect(self._cancel)
        return f

    # ── ajustes ──────────────────────────────────────────────────────────
    def _load_settings(self):
        st = self.settings

        def js(key, default):
            try:
                return json.loads(st.value(key, "")) if st.value(key, "") else default
            except (TypeError, ValueError):
                return default

        mode = st.value("mode", engine.MODE_MIRROR)
        self._cfg = {
            "src": st.value("src", DEFAULT_SRC), "dst": st.value("dst", DEFAULT_DST),
            "mode": engine.LEGACY_MODES.get(mode, mode),
            "threads": int(st.value("threads", DEFAULT_THREADS)),
            "keep_days": int(st.value("keep_days", 90)),
            "connect": st.value("connect", "ask"),
            "watch": st.value("watch", "false") == "true",
            "verify": st.value("verify", "true") == "true",
            "scrub": int(st.value("scrub", 300)),
            "auto_update": st.value("auto_update", "true") == "true",
            "awake": st.value("awake", "true") == "true",
            "remind": st.value("remind", "true") == "true",
            "remind_days": int(st.value("remind_days", 3)),
            "folded": js("folded", None),
        }
        self.disk_id = st.value("disk_id", "") or ""
        self.dst_rel = st.value("dst_rel", "") or ""
        self.update_skip = st.value("update_skip", "") or ""
        self.update_checked = st.value("update_checked", "") or ""
        saved = js("rules", {})
        self.rules = {n: saved.get(n, n in DEFAULT_RULES) for n in PRESET_RULES}
        self.custom_rules = [r for r in js("custom_rules", []) if r not in self.rules]
        for r in self.custom_rules:
            self.rules[r] = saved.get(r, True)
        self.excl_map = {k: set(v) for k, v in js("excluded", {}).items()}
        geo = st.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        if st.contains("copias"):
            self.registry = reminders.Registry(js("copias", []))
        else:
            self.registry = self._seed_registry()  # copias hechas antes de existir los recordatorios
            st.setValue("copias", json.dumps(self.registry.to_list(), ensure_ascii=False))
        # copias apuntadas solo por ruta en la letra del disco registrado: pasan a llevar su marca
        if self.disk_id and self._cfg["dst"] and self.registry.adopt_disk(
                self.disk_id, engine.volume_root(self._cfg["dst"]), engine.rel_to_volume):
            st.setValue("copias", json.dumps(self.registry.to_list(), ensure_ascii=False))

    def _save_settings(self):
        st = self.settings
        st.setValue("src", self.src_card.path())
        st.setValue("dst", self.dst_card.path())
        st.setValue("mode", self.mode_combo.currentData())
        st.setValue("threads", self.threads_spin.value())
        st.setValue("keep_days", self.keep_combo.currentData())
        st.setValue("connect", self.connect_combo.currentData())
        st.setValue("watch", "true" if self.watch_box.isChecked() else "false")
        st.setValue("verify", "true" if self.verify_box.isChecked() else "false")
        st.setValue("scrub", self.scrub_combo.currentData())
        st.setValue("auto_update", "true" if self.update_box.isChecked() else "false")
        st.setValue("awake", "true" if self.awake_box.isChecked() else "false")
        st.setValue("remind", "true" if self.remind_box.isChecked() else "false")
        st.setValue("remind_days", self.remind_combo.currentData())
        st.setValue("update_skip", self.update_skip)
        st.setValue("update_checked", self.update_checked)
        st.setValue("disk_id", self.disk_id)
        st.setValue("dst_rel", self.dst_rel)
        st.setValue("rules", json.dumps(self.rules))
        st.setValue("custom_rules", json.dumps(self.custom_rules))
        st.setValue("excluded", json.dumps({k: sorted(v) for k, v in self.excl_map.items() if v}))
        st.setValue("geometry", self.saveGeometry())
        st.sync()

    def _apply_settings(self):
        self.src_card.set_path(self._cfg["src"])
        self.dst_card.set_path(self._cfg["dst"])
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(self._cfg["mode"])))
        self._on_mode_changed()
        self.keep_combo.setCurrentIndex(max(0, self.keep_combo.findData(self._cfg["keep_days"])))
        self.connect_combo.setCurrentIndex(max(0, self.connect_combo.findData(self._cfg["connect"])))
        self.threads_spin.setValue(max(1, min(32, self._cfg["threads"])))
        self.verify_box.setChecked(self._cfg["verify"])
        self.scrub_combo.setCurrentIndex(max(0, self.scrub_combo.findData(self._cfg["scrub"])))
        self.update_box.blockSignals(True)
        self.update_box.setChecked(self._cfg["auto_update"])
        self.update_box.blockSignals(False)
        self.awake_box.blockSignals(True)
        self.awake_box.setChecked(self._cfg["awake"])
        self.awake_box.blockSignals(False)
        self._refresh_awake()
        self.watch_box.blockSignals(True)
        self.watch_box.setChecked(self._cfg["watch"])
        self.watch_box.blockSignals(False)
        for w in (self.remind_box, self.remind_combo):
            w.blockSignals(True)
        self.remind_box.setChecked(self._cfg["remind"])
        self.remind_combo.setCurrentIndex(max(0, self.remind_combo.findData(self._cfg["remind_days"])))
        for w in (self.remind_box, self.remind_combo):
            w.blockSignals(False)
        self._refresh_summaries()
        self._rebuild_chips()
        self._load_tree()
        self._adopt_disk()

    # ── disco de copia ───────────────────────────────────────────────────
    def _adopt_disk(self):
        """Registra como disco de copia el del destino (si ya existe la carpeta)."""
        dst = self.dst_card.path()
        if dst and os.path.isdir(dst):
            found = engine.read_disk_id(engine.volume_root(dst))
            if found:
                self.disk_id = found
                self.dst_rel = engine.rel_to_volume(dst)
            else:
                self._register_disk(dst)
        self._refresh_disk_status()

    def _register_disk(self, dst: str) -> str | None:
        root = engine.volume_root(dst)
        try:
            self.disk_id = engine.ensure_disk_id(root)
        except OSError:
            return None
        self.dst_rel = engine.rel_to_volume(dst)
        self._save_settings()
        self._refresh_disk_status()
        return self.disk_id

    def _refresh_disk_status(self):
        self.dst_card.refresh()
        dst = self.dst_card.path()
        root = engine.volume_root(dst) if dst else ""
        present = (bool(root) and engine.volume_present(dst) and self.disk_id
                   and engine.read_disk_id(root) == self.disk_id)
        if present:
            label = engine.volume_info(root)[0] or "Disco de copia"
            where = f" ({engine.vol_name(root)})" if IS_WIN else ""
            last = engine.last_history(root)
            self.disk_status.setText(f"{label}{where} registrado  ·  última copia "
                                     f"{fmt_ago(last and last.get('fin'))}")
        elif self.disk_id:
            self.disk_status.setText("Disco registrado, ahora desconectado. Al conectarlo se reconoce aunque "
                                     "cambie de letra o de nombre.")
        else:
            self.disk_status.setText("El disco se registra en la primera sincronización (se crea una carpeta "
                                     "oculta .devbackup en su raíz).")
        self.integ_status.setText(self.integrity_text(long=True))

    def _index_path(self) -> str | None:
        return os.path.join(DATA_DIR, "estado", f"{self.disk_id}.db") if self.disk_id else None

    def scrub_seconds(self) -> int:
        return int(self.scrub_combo.currentData())

    def integrity_text(self, long: bool = False) -> str:
        """Cuánto del disco tiene su contenido comprobado, según el índice."""
        path = self._index_path()
        if not path or not os.path.exists(path):
            return "integridad: aún sin comprobar" if not long else \
                "Aún no hay nada comprobado. Se empieza con la próxima sincronización."
        try:
            con = sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)
            try:
                total, covered = con.execute("SELECT count(*), count(verified) FROM files").fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            return "integridad: sin datos"
        frac = covered / total if total else 0.0
        if not long:
            return f"integridad comprobada en el {pct(frac)} del disco"
        rest = "" if covered >= total else (f"  ·  faltan {fmt_int(total - covered)}; se van revisando tras cada "
                                            f"sincronización, empezando por los nunca comprobados")
        return f"Contenido comprobado: {fmt_int(covered)} de {fmt_int(total)} archivos ({pct(frac)}){rest}."

    def _on_watch_toggled(self, on: bool):
        try:
            set_autostart(on)
        except OSError as exc:
            QMessageBox.warning(self, APP_NAME, f"No se pudo configurar el arranque automático:\n{exc}")
        if on:
            self._ensure_tray()
        elif self.tray:
            self.tray.hide()
        self._save_settings()
        self._refresh_reminders()
        self._refresh_summaries()

    def _ensure_tray(self):
        if self.tray is None:
            self.tray = QSystemTrayIcon(app_icon(), self)
            menu = QMenu()
            menu.addAction(glyph_icon(G.OPEN, C["text"], 14), "Abrir", self._show_window)
            menu.addAction(glyph_icon(G.PLAY, C["text"], 14), "Sincronizar ahora", self._tray_sync)
            menu.addAction(glyph_icon(G.HELP, C["text"], 14), "Guía rápida", lambda: self.show_tour())
            menu.addSeparator()
            menu.addAction(glyph_icon(G.CLOSE, C["text"], 14), "Salir", self._quit)
            self._tray_menu = menu
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(self._on_tray_activated)
            self.tray.messageClicked.connect(self._show_window)
        self.tray.setToolTip(f"{APP_NAME} · vigilando el disco de copia")
        self.tray.show()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self._show_window()

    def _tray_sync(self):
        self._show_window()
        if self._state not in RUNNING:
            self._start(copy=True)

    def _notify(self, title: str, text: str):
        if self.tray and self.tray.isVisible():
            self.tray.showMessage(title, text, app_icon(), 6000)

    def _show_window(self):
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def _quit(self):
        self._quitting = True
        self.close()

    def on_instance_message(self, server: QLocalServer):
        sock = server.nextPendingConnection()
        if sock is None:
            return
        sock.waitForReadyRead(300)
        msg = bytes(sock.readAll()).decode(errors="ignore")
        sock.close()
        if msg != "tray":
            self._show_window()

    def _on_drive_arrived(self, root: str, attempt: int = 0):
        if not self.disk_id:
            return
        found = engine.read_disk_id(root)
        if found is None and attempt < 5:
            # la unidad tarda un momento en montarse
            QTimer.singleShot(1500, lambda: self._on_drive_arrived(root, attempt + 1))
            return
        if found == self.disk_id:
            self._on_backup_disk(root)

    def _startup_check(self):
        if not self.disk_id:
            return
        for root in sorted(engine.mounted_volumes()):
            if engine.read_disk_id(root) == self.disk_id:
                last = engine.last_history(root)
                try:
                    age = (datetime.now() - datetime.fromisoformat(last["fin"])).total_seconds() if last else 1e9
                except (KeyError, ValueError):
                    age = 1e9
                if age > 20 * 3600:
                    self._on_backup_disk(root)
                return

    def _on_backup_disk(self, root: str):
        new_dst = os.path.join(root, self.dst_rel) if self.dst_rel else root
        if os.path.normcase(new_dst) != os.path.normcase(self.dst_card.path()):
            self.dst_card.set_path(new_dst)
            self._invalidate_plan()
            self._save_settings()
        self._refresh_disk_status()
        if self._state in RUNNING or (self.dialog and self.dialog.isVisible()):
            return
        action = self.connect_combo.currentData()
        if action == "none":
            self._notify("Disco de copia conectado", f"{new_dst} listo. Abre {APP_NAME} para sincronizar.")
            return
        self._show_window()
        if action == "auto":
            self._notify("Disco de copia conectado", "Sincronizando…")
            self._start(copy=True)
            return
        if not self._start(copy=False, show_progress=False):
            return
        self.dialog = ConnectDialog(self, root)
        self.dialog.open()
        QApplication.alert(self.dialog, 0)

    # ── reglas y árbol ───────────────────────────────────────────────────
    def _active_rules(self) -> list[str]:
        return [n for n, on in self.rules.items() if on]

    def _rebuild_chips(self):
        while self.flow.count():
            item = self.flow.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for name, on in self.rules.items():
            chip = QPushButton(name)
            chip.setObjectName("chip")
            chip.setCheckable(True)
            chip.setChecked(on)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.toggled.connect(lambda checked, n=name: self._toggle_rule(n, checked))
            if name in self.custom_rules:
                chip.setToolTip("Regla propia · clic derecho para quitarla")
                chip.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                chip.customContextMenuRequested.connect(lambda pos, n=name, c=chip: self._rule_menu(n, c, pos))
            self.flow.addWidget(chip)
        self.chips_box.updateGeometry()
        self._refresh_summaries()

    def _toggle_rule(self, name: str, on: bool):
        self.rules[name] = on
        self._refresh_summaries()
        self.tree.set_patterns(self._active_rules())
        self._invalidate_plan()

    def _add_rule(self):
        name = self.rule_edit.text().strip().lower()
        self.rule_edit.clear()
        if not name or any(c in name for c in "\\/:"):
            return
        if name not in self.rules:
            self.custom_rules.append(name)
        self.rules[name] = True
        self._rebuild_chips()
        self.tree.set_patterns(self._active_rules())
        self._invalidate_plan()

    def _rule_menu(self, name: str, chip: QPushButton, pos):
        menu = QMenu(self)
        act = menu.addAction(glyph_icon(G.CLOSE, C["err"], 12), f"Quitar «{name}»")
        if menu.exec(chip.mapToGlobal(pos)) == act:
            self.custom_rules.remove(name)
            self.rules.pop(name, None)
            self._rebuild_chips()
            self.tree.set_patterns(self._active_rules())
            self._invalidate_plan()

    def _excl_key(self) -> str:
        return os.path.normcase(self.src_card.path())

    def _load_tree(self):
        src = self.src_card.path()
        excluded = self.excl_map.setdefault(self._excl_key(), set())
        if os.path.isdir(src):
            n = self.tree.load(src, excluded, self._active_rules())
            self.src_card.info.setText(self.src_card.info.text().split("  ·  ")[0]
                                       + f"  ·  {fmt_int(n)} carpetas de primer nivel")
        else:
            self.tree.clear()
        self._on_tree_changed()

    def _on_src_changed(self):
        self._load_tree()
        self._invalidate_plan()

    def _on_dst_changed(self):
        self._adopt_disk()
        self._invalidate_plan()

    def _on_tree_changed(self):
        n = len(self.tree.excluded)
        self.excl_count.setText(f"{n} excluida{'s' if n != 1 else ''} a mano" if n else "")
        self._invalidate_plan()

    def _reset_exclusions(self):
        self.tree.reset()

    def _on_mode_changed(self):
        self.mode_hint.setText(MODE_HINTS[self.mode_combo.currentData()])
        self._invalidate_plan()

    def _invalidate_plan(self):
        if self.job and self.job.phase == "ready" and self._state == "ready":
            self.job.discard()
            self._set_state("idle")
            self._show_idle_banner()
            self._load_changes(None)
        self._update_summary()

    def _update_summary(self):
        if not hasattr(self, "summary"):
            return
        rules = self._active_rules()
        manual = len(self.tree.excluded) if hasattr(self, "tree") else 0
        parts = [engine.MODES[self.mode_combo.currentData()],
                 f"{len(rules)} regla{'s' if len(rules) != 1 else ''} activa{'s' if len(rules) != 1 else ''}"]
        if manual:
            parts.append(f"{manual} carpeta{'s' if manual != 1 else ''} excluida{'s' if manual != 1 else ''} a mano")
        parts.append(f"{self.threads_spin.value()} hilos")
        self.summary.setText("  ·  ".join(parts))

    # ── cambios ──────────────────────────────────────────────────────────
    def _load_changes(self, job: engine.BackupJob | None):
        rows = change_rows(job) if job else []
        self.changes_model.load(rows)
        counts = {k: 0 for k in KIND_INFO}
        for r in rows:
            counts[r[0]] += 1
        for key, b in self.kind_buttons.items():
            n = len(rows) if key is None else counts[key]
            b.setText(f"{b.property('label')}  {fmt_int(n)}" if job else b.property("label"))
        self.kind_buttons[None].setChecked(True)
        self.change_search.blockSignals(True)
        self.change_search.clear()
        self.change_search.blockSignals(False)
        if job and not rows:
            self.changes_empty.setText("Todo está al día: no hay nada que copiar, mover ni guardar en versiones.")
        elif not job:
            self.changes_empty.setText("Pulsa «Analizar» para ver exactamente qué se va a copiar, mover o guardar "
                                       "en versiones antes de tocar nada.")
        self.changes_stack.setCurrentIndex(1 if rows else 0)

    def _filter_changes(self):
        kind = next((k for k, b in self.kind_buttons.items() if b.isChecked()), None)
        self.changes_model.apply_filter(kind, self.change_search.text())

    # ── estado ───────────────────────────────────────────────────────────
    def _set_state(self, state: str):
        self._state = state
        running = state in RUNNING
        self.sidebar.setEnabled(not running)
        self.tree.setEnabled(not running)
        self.btn_scan.setEnabled(not running)
        self.btn_start.setEnabled(not running)
        self.btn_pause.setVisible(running)
        self.btn_cancel.setVisible(running)
        scrubbing = running and self.job is not None and self.job.stats.step == "verificando"
        self.btn_cancel.setText("  Omitir revisión" if scrubbing else "  Cancelar")
        self.btn_open.setVisible(not running)
        if state == "paused":
            self.btn_pause.setText("  Reanudar")
            self.btn_pause.setIcon(glyph_icon(G.PLAY, C["warn"], 14))
        else:
            self.btn_pause.setText("  Pausar")
            self.btn_pause.setIcon(glyph_icon(G.PAUSE, C["text"], 14))
        if state == "ready" and self.job and self.job.stats.has_changes:
            n = self.job.stats.ops_total
            self.btn_start.setText(f"  Sincronizar {fmt_int(n)} cambio{'s' if n != 1 else ''}")
        else:
            self.btn_start.setText("  Sincronizar")
        text, color = PILL[state]
        if state == "copying" and self.job is not None and self.job.stats.step == "verificando":
            text, color = "Comprobando", C["accent2"]
        self.pill.setText(f"●  {text}")
        self.pill.setStyleSheet(f"QLabel#pill {{ color:{color}; background:{rgba(color, 30)}; "
                                f"border:1px solid {rgba(color, 90)}; }}")
        if self.taskbar:
            self.taskbar.state({"scanning": Taskbar.BUSY, "copying": Taskbar.NORMAL,
                                "paused": Taskbar.PAUSED}.get(state, Taskbar.OFF))
        if self.tray:
            self.tray.setToolTip(f"{APP_NAME} · {text.lower()}")
        if not running:
            self.setWindowTitle(APP_NAME)
        if hasattr(self, "remind_bar"):
            if running:
                self.remind_bar.conceal()
            else:
                self._refresh_remind_bar()

    def _show_idle_banner(self):
        dst = self.dst_card.path() or "el destino"
        self.banner.set("info", "Listo para sincronizar",
                        f"«Analizar» enseña qué ha cambiado sin tocar nada. «Sincronizar» deja {dst} igual que el "
                        f"origen: copia lo nuevo y lo modificado, mueve lo renombrado y guarda en versiones lo que "
                        f"borres.", G.INFO)

    # ── operación ────────────────────────────────────────────────────────
    def _validate(self, src: str, dst: str) -> str | None:
        if not src or not os.path.isdir(src):
            return "La carpeta de origen no existe."
        if not dst:
            return "Elige una carpeta de destino."
        ns, nd = os.path.normcase(os.path.abspath(src)), os.path.normcase(os.path.abspath(dst))
        if ns == nd:
            return "El origen y el destino son la misma carpeta."
        if nd.startswith(ns.rstrip("\\/") + os.sep):
            return "El destino no puede estar dentro del origen."
        if not engine.volume_present(dst):
            return f"El disco de {dst} no está conectado."
        return None

    def _start(self, copy: bool, show_progress: bool = True, scrub_all: bool = False) -> bool:
        src, dst = self.src_card.path(), self.dst_card.path()
        problem = self._validate(src, dst)
        if problem:
            QMessageBox.warning(self, APP_NAME, problem)
            return False
        self.excl_map[self._excl_key()] = self.tree.excluded
        self.rem_checker.cancel()
        if not self.disk_id or engine.read_disk_id(engine.volume_root(dst)) != self.disk_id:
            self._register_disk(dst)
        job = engine.BackupJob(src, dst, self._active_rules(), self.tree.excluded,
                               self.mode_combo.currentData(), self.threads_spin.value(),
                               index_path=self._index_path(), force_full=self.full_box.isChecked(),
                               keep_days=self.keep_combo.currentData(), verify=self.verify_box.isChecked(),
                               scrub_seconds=-1 if scrub_all else self.scrub_seconds())
        reuse = (copy and self.job is not None and self.job.phase == "ready"
                 and self.job.signature() == job.signature())
        if reuse:
            if not self.job.stats.has_changes and not job.scrub_seconds:
                self.seg_buttons[PAGE_PROGRESS].click()
                return True
            # opciones que no cambian el plan: se toman de la pantalla
            for attr in ("threads", "verify", "scrub_seconds"):
                setattr(self.job, attr, getattr(job, attr))
            job = self.job
        else:
            if self.job and self.job.phase == "ready":
                self.job.discard()
            self._reset_progress()
            self._op_start = time.perf_counter()
            self._op_wall = datetime.now()
            self._log(f"Análisis iniciado  ·  {src}  →  {dst}", C["accent2"])
            self._log(f"Modo: {engine.MODES[job.mode].lower()}  ·  reglas: {', '.join(job.patterns) or 'ninguna'}  ·  "
                      f"excluidas a mano: {len(job.excluded)}")
        self.job = job
        self._finalized = False
        self._copy_seen = False
        self._scrub_seen = False
        self.summary_card.hide()
        self._copy_requested = copy
        self._guard_logged = False
        self._sleep_paused = False
        self._disk_lost_seen = False
        self._save_settings()
        self.job_thread = threading.Thread(target=job.run, args=(copy,), name="backup", daemon=True)
        self.job_thread.start()
        self._sync_guard(job)
        self._set_state("copying" if reuse else "scanning")
        if show_progress:
            self.seg_buttons[PAGE_PROGRESS].click()
        self._last_tick = time.perf_counter()
        self.timer.start()
        return True

    def _reset_progress(self):
        self._samples.clear()
        self._eta = None
        self._last_chart = 0.0
        self._pause_at = None
        self._paused_total = 0.0
        self._paused_copy = 0.0
        self._err_seen = 0
        self._last_log = ""
        self.chart.reset()
        self.ring.set_busy(False)
        self.ring.set_value(0, animate=False)
        self.ring.set_text("0 %", "", "PREPARADO")
        self.summary_card.hide()
        self._scrub_seen = False
        for t in self.tiles:
            t.set("—", "")
        for bar in (self.bar_bytes, self.bar_files):
            bar.setValue(0)
        self.bar_bytes_val.setText("—")
        self.bar_files_val.setText("—")
        self.issues.setRowCount(0)
        self._set_issues(0)
        self.seg_buttons[PAGE_ISSUES].setText("  Incidencias")
        self.log.clear()
        self._set_active_rows(0)
        self._load_changes(None)

    def _set_active_rows(self, n: int):
        while self.active_rows:
            row = self.active_rows.pop()
            self.active_rows_box.removeWidget(row)
            row.deleteLater()
        for i in range(min(n, 8)):
            row = ActiveRow(i + 1)
            self.active_rows.append(row)
            self.active_rows_box.addWidget(row)

    def _toggle_pause(self):
        job = self.job
        if not job or self._state not in RUNNING:
            return
        self._sleep_paused = False
        now = time.perf_counter()
        if job.paused:
            job.resume()
            if self._pause_at is not None:
                gap = now - self._pause_at
                self._paused_total += gap
                if job.phase == "copying":
                    self._paused_copy += gap
                self._pause_at = None
            self._samples.clear()
            self._log("Reanudado")
        else:
            job.pause()
            self._pause_at = now
            self._log("En pausa", C["warn"])
        self._tick()

    def _cancel(self):
        if not self.job or self._state not in RUNNING:
            return
        if self.job.phase == "copying" and self.job.stats.step == "verificando":
            # la sincronización ya está completa: omitir la revisión no deshace nada
            self.job.cancel()
            self._log("Revisión de integridad omitida: la sincronización ya estaba completa.", C["warn"])
            return
        what = "la sincronización" if self.job.phase == "copying" else "el análisis"
        ans = QMessageBox.question(self, APP_NAME, f"¿Cancelar {what}?\n\nLo que ya se haya copiado se conserva; "
                                   "el archivo a medias se descarta.")
        if ans == QMessageBox.StandardButton.Yes:
            self.job.cancel()
            self._log("Cancelando…", C["warn"])

    def _open_dest(self):
        dst = self.dst_card.path()
        if dst and os.path.isdir(dst):
            QDesktopServices.openUrl(QUrl.fromLocalFile(dst))

    # ── refresco ─────────────────────────────────────────────────────────
    def _current_pause(self, now: float) -> float:
        return now - self._pause_at if self._pause_at is not None else 0.0

    def _tick(self):
        job = self.job
        if job is None:
            return
        now = time.perf_counter()
        phase = job.phase
        ui = "paused" if phase in ("scanning", "copying") and job.paused else phase
        if phase == "copying" and not self._copy_seen:
            self._copy_seen = True
            self._on_copy_started(job)
        if phase == "copying" and job.stats.step == "verificando" and not self._scrub_seen:
            self._scrub_seen = True
            self._on_scrub_started(job)
        if ui in RUNNING and ui != self._state:
            self._set_state(ui)
        if phase == "scanning":
            self._render_scan(job, now)
        elif phase == "copying":
            self._render_copy(job, now)
        self._drain_errors(job)
        self._watch_disk(job)
        self._sync_guard(job)
        self._last_tick = now
        # al reutilizar un análisis la fase sigue en «ready» hasta que arranca el hilo de copia
        terminal = ("done", "cancelled", "failed") if self._copy_requested else ("ready", "cancelled", "failed")
        if phase in terminal and not self._finalized:
            self._finalized = True
            self.timer.stop()
            self._finish(job, phase)

    def _plan_text(self, s: engine.Stats) -> str:
        return (f"{fmt_int(s.new_files)} nuevos ({fmt_bytes(s.new_bytes)})  ·  "
                f"{fmt_int(s.changed_files)} modificados ({fmt_bytes(s.changed_bytes)})  ·  "
                f"{fmt_int(s.moved_files)} movidos  ·  {fmt_int(s.del_files)} a versiones  ·  "
                f"{fmt_int(s.skip_files)} sin cambios")

    def _render_scan(self, job: engine.BackupJob, now: float):
        s = job.stats
        elapsed = now - self._op_start
        active = max(1e-6, elapsed - self._paused_total - self._current_pause(now))
        self.ring.set_busy(not job.paused)
        self.ring.set_text(fmt_int(s.files_found), "archivos revisados", "ANALIZANDO")
        title = "Analizando el origen con el índice de la última copia…" if s.fast else "Analizando origen y destino…"
        self.banner.set("run", title, f"{fmt_int(s.dirs_scanned)} carpetas  ·  " + self._plan_text(s), G.SEARCH)
        self.t_elapsed.set(fmt_clock(elapsed), f"desde las {self._op_wall:%H:%M:%S}")
        self.t_left.set("—", "se calcula al terminar el análisis")
        self.t_speed.set(f"{fmt_int(s.files_found / active)}/s", "archivos revisados por segundo")
        self.t_eta.set("—", "")
        self.t_copied.set("0 B", f"{fmt_bytes(s.new_bytes + s.changed_bytes)} por copiar")
        self.t_files.set("0", f"{fmt_int(s.new_files + s.changed_files)} por copiar")
        self.t_skipped.set(fmt_int(s.skip_files), f"{fmt_bytes(s.skip_bytes)} ya al día")
        self._render_errors_tile(s)
        self.cur_label.setText(s.current_dir)
        self.setWindowTitle(f"Analizando… · {APP_NAME}")

    def _on_copy_started(self, job: engine.BackupJob):
        s = job.stats
        if s.scan_end:
            self._log_scan_summary(job)
        self._log(f"Sincronización iniciada con {job.threads} hilos  ·  {fmt_int(s.ops_total)} cambios  ·  "
                  f"{fmt_bytes(s.plan_bytes)} por copiar", C["accent"])
        self._set_active_rows(job.threads)
        self.ring.set_busy(False)
        self._samples.clear()
        self.banner.set("run", f"Sincronizando  {job.src}  →  {job.dst}",
                        self._plan_text(s) + f"  ·  análisis en {fmt_clock(s.scan_end - s.scan_start, ms=True)}",
                        G.SYNC)

    def _scrub_left(self, job: engine.BackupJob) -> float | None:
        s = job.stats
        if job.scrub_seconds <= 0 or not s.scrub_start:
            return None
        return max(0.0, job.scrub_seconds - (time.perf_counter() - s.scrub_start))

    def _on_scrub_started(self, job: engine.BackupJob):
        s = job.stats
        limit = (f"como mucho {fmt_human(job.scrub_seconds)}" if job.scrub_seconds > 0
                 else "todo el disco, sin límite de tiempo")
        self._log(f"Sincronización completada. Revisión de integridad: {fmt_int(s.scrub_due)} archivos pendientes "
                  f"de comprobar ({limit}).", C["accent2"])
        self.banner.set("run", "Sincronización completada  ·  revisando la integridad del disco",
                        f"Relee del disco una tanda de archivos ya copiados para detectar daños silenciosos ({limit}). "
                        "La copia ya está completa: puedes terminar ahora con «Omitir revisión».", G.SHIELD)
        self._set_state(self._state)  # píldora «Comprobando» y botón «Omitir revisión»

    def _progress(self, s: engine.Stats) -> float:
        done_b = s.bytes_done + s.bytes_failed
        if s.plan_bytes:
            frac = done_b / s.plan_bytes
        elif s.ops_total:
            frac = s.ops_done / s.ops_total
        else:
            frac = 1.0
        frac = max(0.0, min(1.0, frac))
        if s.ops_done < s.ops_total:
            frac = min(frac, 0.9995)
        return frac

    def _step_text(self, s: engine.Stats) -> str:
        return {
            "limpieza": "Quitando temporales de copias interrumpidas…",
            "carpetas": f"Creando carpetas nuevas en el destino…  {fmt_int(s.dirs_created)} / {fmt_int(s.dirs_to_create)}",
            "moviendo": f"Moviendo archivos renombrados o movidos…  {fmt_int(s.moves_done)} / {fmt_int(s.moved_files)}",
            "versionando": f"Guardando en versiones lo borrado del origen…  "
                           f"{fmt_int(s.deletes_done)} / {fmt_int(s.del_files)}",
            "purgando": "Quitando versiones más antiguas que el plazo elegido…",
            "índice": "Guardando el índice para que el próximo análisis sea rápido…",
            "verificando": self._scrub_text(s),
        }.get(s.step, "")

    def _scrub_text(self, s: engine.Stats) -> str:
        job = self.job
        text = (f"Comprobando la integridad del disco…  {fmt_int(s.scrub_files)} archivos "
                f"({fmt_bytes(s.scrub_bytes)})  ·  {fmt_int(s.scrub_repaired)} reparados  ·  "
                f"{fmt_int(s.scrub_alerts)} avisos")
        if job and job.scrub_seconds > 0 and s.scrub_start:
            left = job.scrub_seconds - (time.perf_counter() - s.scrub_start)
            text += f"  ·  {fmt_human(max(0, left))} de revisión restantes"
        return text

    def _render_copy(self, job: engine.BackupJob, now: float, final: bool = False):
        s = job.stats
        end = s.copy_end if final and s.copy_end else now
        elapsed = end - self._op_start
        t_copy = max(1e-6, end - s.copy_start - self._paused_copy - (0 if final else self._current_pause(now)))
        frac = self._progress(s)
        done_b = s.bytes_done + s.bytes_failed
        paused = job.paused and not final

        work = done_b + s.ops_done * FILE_COST
        work_total = s.plan_bytes + s.ops_total * FILE_COST
        if not paused:
            self._samples.append((t_copy, work, s.bytes_done, s.ops_done))
            while len(self._samples) > 2 and t_copy - self._samples[0][0] > 30:
                self._samples.popleft()

        def rate(window: float, idx: int) -> float:
            if len(self._samples) < 2:
                return 0.0
            last = self._samples[-1]
            first = next((x for x in self._samples if last[0] - x[0] <= window), self._samples[0])
            dt = last[0] - first[0]
            return (last[idx] - first[idx]) / dt if dt > 0.25 else 0.0

        speed = rate(2.0, 2)
        fps = rate(2.0, 3)
        avg = s.bytes_done / t_copy
        work_rate = rate(20.0, 1) if t_copy > 3 else 0.0
        if work_rate <= 0 and t_copy > 1.5:
            work_rate = work / t_copy
        eta = None
        if work_rate > 0:
            raw = min(max(0.0, (work_total - work) / work_rate), 30 * 86400)
            dt = max(0.0, now - self._last_tick)
            self._eta = raw if self._eta is None else max(0.0, self._eta - dt) * 0.88 + raw * 0.12
            eta = self._eta
        if final:
            eta = 0.0

        scrubbing = s.step == "verificando" and not final
        self.ring.set_value(frac)
        if final:
            caption = "COMPLETADO" if job.phase == "done" else "CANCELADO"
        else:
            caption = ("ESPERANDO AL DISCO" if s.disk_lost_at else "EN PAUSA" if paused
                       else "COMPROBANDO" if s.step == "verificando" else "SINCRONIZANDO")
        self.ring.set_text(pct(frac), "sincronizado · revisando integridad" if scrubbing
                           else f"{fmt_bytes(s.bytes_done)} de {fmt_bytes(s.plan_bytes)}", caption)
        self.bar_bytes.setValue(int(frac * 10000))
        self.bar_bytes_val.setText(f"{fmt_bytes(s.bytes_done)}  /  {fmt_bytes(s.plan_bytes)}   ·   {pct(frac)}")
        ffrac = s.ops_done / s.ops_total if s.ops_total else 1.0
        handled = s.files_done + s.moves_done + s.deletes_done
        self.bar_files.setValue(int(ffrac * 10000))
        self.bar_files_val.setText(f"{fmt_int(handled)}  /  {fmt_int(s.ops_total)}   ·   {pct(ffrac)}")

        self.t_elapsed.set(fmt_clock(elapsed), f"desde las {self._op_wall:%H:%M:%S}")
        if final:
            self.t_left.set("00:00:00", "terminado", C["ok"] if job.phase == "done" else None)
            self.t_eta.set(datetime.now().strftime("%H:%M:%S"), "hora de fin real")
        elif scrubbing and not paused and not s.disk_lost_at:
            left = self._scrub_left(job)
            if left is None:
                self.t_left.set(fmt_int(max(0, s.scrub_due - s.scrub_files)),
                                f"archivos por revisar de {fmt_int(s.scrub_due)}")
                self.t_eta.set("—", "revisión sin límite de tiempo")
            else:
                self.t_left.set(fmt_clock(left), "revisión de integridad")
                self.t_eta.set((datetime.now() + timedelta(seconds=left)).strftime("%H:%M:%S"),
                               "fin de la revisión (como mucho)")
        elif paused or s.disk_lost_at:
            why = "esperando al disco" if s.disk_lost_at else "en pausa"
            self.t_left.set("—", why)
            self.t_eta.set("—", why)
        elif eta is None:
            self.t_left.set("…", "midiendo velocidad")
            self.t_eta.set("…", "")
        else:
            self.t_left.set(fmt_clock(eta), f"≈ {fmt_human(eta)}")
            fin = datetime.now() + timedelta(seconds=eta)
            day = fin.date() - datetime.now().date()
            when = "hoy" if day.days == 0 else "mañana" if day.days == 1 else fin.strftime("%d/%m/%Y")
            self.t_eta.set(fin.strftime("%H:%M:%S"), when)
        if final:
            self.t_speed.set(fmt_speed(avg), "media de la copia")
        elif scrubbing:
            span = max(1e-6, time.perf_counter() - s.scrub_start) if s.scrub_start else 0
            self.t_speed.set(fmt_speed(s.scrub_bytes / span) if span else "—", "lectura de comprobación")
        else:
            self.t_speed.set(fmt_speed(speed) if not paused else "—", f"{fmt_int(fps)} archivos/s")
        self.t_copied.set(fmt_bytes(s.bytes_done), f"de {fmt_bytes(s.plan_bytes)}")
        pending = max(0, s.ops_total - s.ops_done)
        self.t_files.set(fmt_int(handled), f"de {fmt_int(s.ops_total)}  ·  {fmt_int(pending)} pendientes")
        self.t_skipped.set(fmt_int(s.skip_files), f"{fmt_bytes(s.skip_bytes)} ya al día")
        self._render_errors_tile(s)

        if not final and not paused and now - self._last_chart >= 0.5:
            self._last_chart = now
            self.chart.push(rate(1.0, 2))
        slots = list(job.active.values())
        for i, row in enumerate(self.active_rows):
            row.show_slot(slots[i] if i < len(slots) and not final else None, job.src)
        if final:
            self.cur_label.setText(job.dst)
        elif slots and s.step != "verificando":
            self.cur_label.setText(slots[0][0] or "")
        elif self._step_text(s):
            self.cur_label.setText(self._step_text(s))

        if not final:
            doing = "En pausa" if paused else "Comprobando integridad" if scrubbing else "Sincronizando"
            self.setWindowTitle(f"{pct(frac)} · {doing} · {APP_NAME}")
            if self.taskbar:
                self.taskbar.value(frac)
            if self.tray:
                self.tray.setToolTip(f"{APP_NAME} · {pct(frac)}")

    def _render_errors_tile(self, s: engine.Stats):
        if s.errors:
            self.t_errors.set(fmt_int(s.errors), "ver pestaña Incidencias", C["err"])
        else:
            self.t_errors.set("0", "sin incidencias", C["ok"])

    def _drain_errors(self, job: engine.BackupJob):
        total = len(job.errors)
        if total <= self._err_seen:
            return
        new = job.errors[self._err_seen:total]
        first = self._err_seen
        self._err_seen = total
        self.issues.setUpdatesEnabled(False)
        row = self.issues.rowCount()
        self.issues.setRowCount(row + len(new))
        for i, (path, msg) in enumerate(new):
            a = QTableWidgetItem(path)
            a.setToolTip(path)
            b = QTableWidgetItem(msg)
            b.setForeground(QColor(C["err"]))
            self.issues.setItem(row + i, 0, a)
            self.issues.setItem(row + i, 1, b)
        self.issues.setUpdatesEnabled(True)
        for i, (path, msg) in enumerate(new):
            if first + i < 100:
                self._log(f"⚠ {path}  —  {msg}", C["err"])
            elif first + i == 100:
                self._log("… más incidencias en la pestaña Incidencias", C["err"])
        self._set_issues(total)
        self.seg_buttons[PAGE_ISSUES].setText(f"  Incidencias  ({fmt_int(total)})")

    def _log_scan_summary(self, job: engine.BackupJob):
        s = job.stats
        self._log(f"Análisis completado en {fmt_clock(s.scan_end - s.scan_start, ms=True)}  ·  "
                  f"{fmt_int(s.files_found)} archivos ({fmt_bytes(s.bytes_found)})  ·  " + self._plan_text(s)
                  + f"  ·  excluidas {fmt_int(s.excluded_rule + s.excluded_manual)}"
                  + (f"  ·  enlaces omitidos {fmt_int(s.links_skipped)}" if s.links_skipped else ""), C["ok"])
        self._log(job.index_note)

    # ── fin ──────────────────────────────────────────────────────────────
    def _finish(self, job: engine.BackupJob, phase: str):
        self._guard_stop()
        s = job.stats
        if self._pause_at is not None:
            self._paused_total += time.perf_counter() - self._pause_at
            self._pause_at = None
        self.ring.set_busy(False)
        if phase == "ready":
            self._finish_scan(job)
        elif phase == "cancelled" and not s.copy_start:
            self.ring.set_text("—", "análisis interrumpido", "CANCELADO")
            self.banner.set("warn", "Análisis cancelado", "No se ha tocado nada. Pulsa Analizar o Sincronizar "
                                                          "para empezar de nuevo.", G.WARN)
            self._log("Análisis cancelado", C["warn"])
        elif phase in ("done", "cancelled"):
            self._finish_copy(job, phase)
        else:
            self.banner.set("err", "La operación se detuvo por un error", job.fatal or "Error desconocido", G.ERROR)
            self._log(f"Error: {job.fatal}", C["err"])
            if s.copy_start:
                self._write_log(job, phase)
        self._set_state(phase)
        if self.taskbar:
            self.taskbar.state(Taskbar.OFF)
        if phase != "ready" or not (self.dialog and self.dialog.isVisible()):
            QApplication.alert(self, 0)
        if self._pending_release is not None and phase != "ready":
            # había una versión nueva esperando a que terminara la copia
            QTimer.singleShot(2500, self._offer_update)

    def _finish_scan(self, job: engine.BackupJob):
        s = job.stats
        self._log_scan_summary(job)
        scan = s.scan_end - s.scan_start
        try:
            free = shutil.disk_usage(engine.volume_root(job.dst)).free
        except OSError:
            free = 0
        detail = (f"{fmt_int(s.files_found)} archivos en origen ({fmt_bytes(s.bytes_found)})  ·  " + self._plan_text(s)
                  + f"  ·  libre en destino {fmt_bytes(free)}\n{job.index_note}")
        if not s.has_changes:
            self.ring.set_value(1)
            self.ring.set_text("Al día", "no hay nada que copiar", "ANÁLISIS")
            self.banner.set("ok", f"Todo está al día  ·  análisis en {fmt_clock(scan, ms=True)}", detail, G.DONE)
        elif free and s.plan_bytes > free:
            self.ring.set_value(0, animate=False)
            self.ring.set_text(fmt_int(s.ops_total), "cambios por aplicar", "ANÁLISIS")
            self.banner.set("warn", "No hay espacio suficiente en el destino",
                            detail + f"  ·  faltan {fmt_bytes(s.plan_bytes - free)}", G.WARN)
        else:
            self.ring.set_value(0, animate=False)
            self.ring.set_text(fmt_int(s.ops_total), "cambios por aplicar", "ANÁLISIS")
            self.banner.set("info", f"Análisis completado en {fmt_clock(scan, ms=True)}  ·  revisa la pestaña "
                                    f"Cambios o pulsa «Sincronizar»", detail, G.CHECK)
        self.t_elapsed.set(fmt_clock(scan, ms=True), "duración del análisis")
        eta = estimate_seconds(engine.volume_root(job.dst), s)
        self.t_left.set(fmt_clock(eta) if eta is not None else "—",
                        "previsto según la última copia" if eta is not None else "se mide al copiar")
        self.t_speed.set("—", "")
        self.t_eta.set("—", "")
        self.t_copied.set("0 B", f"de {fmt_bytes(s.plan_bytes)}")
        self.t_files.set("0", f"de {fmt_int(s.ops_total)}")
        self.t_skipped.set(fmt_int(s.skip_files), f"{fmt_bytes(s.skip_bytes)} ya al día")
        self._render_errors_tile(s)
        self.cur_label.setText(job.src)
        self.bar_bytes_val.setText(f"0 B  /  {fmt_bytes(s.plan_bytes)}")
        self.bar_files_val.setText(f"0  /  {fmt_int(s.ops_total)}")
        self._load_changes(job)
        if not s.has_changes and job.phase == "ready":
            self._record_pair(job, copied=False)  # el destino ya era igual que el origen

    def _finish_copy(self, job: engine.BackupJob, phase: str):
        s = job.stats
        self._render_copy(job, time.perf_counter(), final=True)
        total = s.copy_end - self._op_start
        copy = s.copy_end - s.copy_start
        scan = s.scan_end - s.scan_start
        avg = s.bytes_done / max(1e-6, copy - self._paused_copy)
        if phase == "done":
            self.ring.set_value(1)
            self.ring.set_text("100 %", f"{fmt_bytes(s.bytes_done)} copiados",
                               "COMPLETADO" if not s.errors else "CON INCIDENCIAS")
        self._last_log = self._write_log(job, phase)
        if phase == "done":
            self._record_pair(job, copied=True)
        self._show_summary(job, phase, total, scan, copy, avg)
        parts = [f"{fmt_int(s.files_done)} copiados ({fmt_bytes(s.bytes_done)})",
                 f"{fmt_int(s.moves_done)} movidos", f"{fmt_int(s.versions_saved)} versiones guardadas",
                 f"{fmt_int(s.skip_files)} sin cambios", f"media {fmt_speed(avg)}",
                 f"análisis {fmt_clock(scan, ms=True)}", f"copia {fmt_clock(copy, ms=True)}"]
        if s.versions_purged:
            parts.append(f"{fmt_int(s.versions_purged)} versiones antiguas retiradas")
        if job.verify and s.files_done:
            parts.append(f"{fmt_int(s.verified_copies)} copias verificadas con SHA-256")
        if s.scrub_files or s.scrub_total:
            cover = s.scrub_covered / s.scrub_total if s.scrub_total else 0
            parts.append(f"integridad: {fmt_int(s.scrub_files)} revisados, {fmt_int(s.scrub_repaired)} reparados, "
                         f"{fmt_int(s.scrub_alerts)} avisos · disco comprobado al {pct(cover)}")
        if self._paused_total > 0.5:
            parts.append(f"en pausa {fmt_clock(self._paused_total, ms=True)}")
        issues = f"{fmt_int(s.errors)} incidencia{'s' if s.errors != 1 else ''}"
        if s.errors:
            parts.append(issues)
        action = ("Ver registro", self._open_log) if self._last_log else None
        brief = f"{job.src}  →  {job.dst}  ·  {fmt_bytes(s.bytes_done)} copiados  ·  media {fmt_speed(avg)}"
        if phase == "cancelled":
            self.banner.set("warn", f"Sincronización cancelada tras {fmt_clock(total, ms=True)}",
                            brief + "  ·  lo copiado se conserva; vuelve a pulsar Sincronizar para continuar",
                            G.WARN, action)
            self._log("Sincronización cancelada", C["warn"])
        elif s.errors:
            self.banner.set("warn", f"Sincronización terminada en {fmt_clock(total, ms=True)} con {issues}",
                            brief + "  ·  detalles en la pestaña Incidencias", G.WARN, action)
        else:
            self.banner.set("ok", f"Sincronización completada en {fmt_clock(total, ms=True)}", brief, G.DONE, action)
        self._log(f"Fin  ·  duración total {fmt_clock(total, ms=True)}  ·  " + "  ·  ".join(parts),
                  C["ok"] if phase == "done" and not s.errors else C["warn"])
        if self._last_log:
            self._log(f"Registro guardado en {self._last_log}")
        self.setWindowTitle(f"{'Al día' if phase == 'done' else 'Cancelado'} · {APP_NAME}")
        self._load_changes(None)
        self.full_box.setChecked(False)
        self._refresh_disk_status()
        if phase == "done":
            self._notify("Copia terminada" + (f" con {issues}" if s.errors else ""),
                         f"{fmt_int(s.new_done)} nuevos · {fmt_int(s.changed_done)} modificados · "
                         f"{fmt_int(s.moves_done)} movidos · {fmt_int(s.deletes_done)} a versiones · "
                         f"{fmt_int(s.dirs_created)} carpetas nuevas · {fmt_clock(total)}")
        if os.name == "nt":
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except Exception:  # noqa: BLE001
                pass

    def _show_summary(self, job: engine.BackupJob, phase: str, total: float, scan: float, copy: float,
                      avg: float):
        """Las cifras de la copia que acaba de terminar, de un vistazo."""
        s = job.stats

        def of(done: int, planned: int) -> str:
            return f"de {fmt_int(planned)} previstos" if done != planned else ""

        self.s_new.set(fmt_int(s.new_done), f"{fmt_bytes(s.new_done_bytes)}  {of(s.new_done, s.new_files)}".strip())
        self.s_changed.set(fmt_int(s.changed_done),
                           f"{fmt_bytes(s.changed_done_bytes)}  {of(s.changed_done, s.changed_files)}".strip())
        self.s_moved.set(fmt_int(s.moves_done), "renombrados o movidos, sin copiar")
        self.s_versions.set(fmt_int(s.deletes_done), "borrados del origen, guardados"
                            + (f" · {fmt_int(s.versions_purged)} antiguos retirados" if s.versions_purged else ""))
        self.s_dirs_new.set(fmt_int(s.dirs_created), "creadas en el destino")
        self.s_dirs_gone.set(fmt_int(s.dirs_removed), "ya no estaban en el origen")
        self.s_same.set(fmt_int(s.skip_files), f"{fmt_bytes(s.skip_bytes)} ya al día")
        if job.verify:
            extra = (f" · {fmt_int(s.scrub_files)} revisados, {fmt_int(s.scrub_repaired)} reparados"
                     if s.scrub_files else "")
            self.s_checked.set(fmt_int(s.verified_copies), "SHA-256 releyendo el disco" + extra)
        else:
            self.s_checked.set("—", "verificación desactivada")
        failed = s.files_failed + s.other_failed
        bits = [f"Duración {fmt_clock(total, ms=True)}", f"análisis {fmt_clock(scan, ms=True)}",
                f"copia {fmt_clock(copy, ms=True)}", f"{fmt_bytes(s.bytes_done)} copiados a {fmt_speed(avg)}"]
        if failed:
            bits.append(f"{fmt_int(failed)} sin copiar")
        if s.errors:
            bits.append(f"{fmt_int(s.errors)} incidencia{'s' if s.errors != 1 else ''} (pestaña Incidencias)")
        if s.disk_waits:
            bits.append(f"esperó al disco {fmt_int(s.disk_waits)} vez/veces ({fmt_human(s.disk_wait_s)})")
        if phase == "cancelled":
            bits.append("cancelada: lo copiado se conserva")
        self.summary_line.setText("  ·  ".join(bits))
        self.summary_card.show()

    def _write_log(self, job: engine.BackupJob, phase: str) -> str:
        s = job.stats
        os.makedirs(LOG_DIR, exist_ok=True)
        path = os.path.join(LOG_DIR, f"copia_{self._op_wall:%Y%m%d_%H%M%S}.log")
        end = s.copy_end or time.perf_counter()
        lines = [
            f"{APP_NAME} — informe de sincronización",
            "=" * 60,
            f"Inicio:            {self._op_wall:%Y-%m-%d %H:%M:%S}",
            f"Fin:               {datetime.now():%Y-%m-%d %H:%M:%S}",
            f"Resultado:         {PILL[phase][0]}",
            f"Origen:            {job.src}",
            f"Destino:           {job.dst}",
            f"Reglas:            {', '.join(job.patterns) or 'ninguna'}",
            f"Modo:              {engine.MODES[job.mode]}",
            f"Análisis:          {job.index_note}",
            f"Hilos:             {job.threads}",
            "",
            f"Duración total:    {fmt_clock(end - self._op_start, ms=True)}",
            f"  análisis:        {fmt_clock(s.scan_end - s.scan_start, ms=True)}",
            f"  copia:           {fmt_clock(end - s.copy_start, ms=True) if s.copy_start else '—'}",
            f"  en pausa:        {fmt_clock(self._paused_total, ms=True)}",
            "",
            f"Carpetas analizadas:   {fmt_int(s.dirs_scanned)}",
            f"Archivos en origen:    {fmt_int(s.files_found)}  ({fmt_bytes(s.bytes_found)})",
            f"Sin cambios:           {fmt_int(s.skip_files)}  ({fmt_bytes(s.skip_bytes)})",
            f"Nuevos:                {fmt_int(s.new_files)}  ({fmt_bytes(s.new_bytes)})",
            f"Modificados:           {fmt_int(s.changed_files)}  ({fmt_bytes(s.changed_bytes)})",
            f"Movidos/renombrados:   {fmt_int(s.moved_files)}  ({fmt_bytes(s.moved_bytes)}), hechos {fmt_int(s.moves_done)}",
            f"Borrados → versiones:  {fmt_int(s.del_files)}  ({fmt_bytes(s.del_bytes)}), hechos {fmt_int(s.deletes_done)}",
            f"Copiados:              {fmt_int(s.files_done)}  ({fmt_bytes(s.bytes_done)})",
            f"Fallidos:              {fmt_int(s.files_failed + s.other_failed)}",
            f"Versiones guardadas:   {fmt_int(s.versions_saved)}  (en {getattr(job, 'version_root', '—')})",
            f"Versiones retiradas:   {fmt_int(s.versions_purged)}",
            f"Carpetas creadas:      {fmt_int(s.dirs_created)}",
            f"Carpetas quitadas:     {fmt_int(s.dirs_removed)}",
            f"Carpetas excluidas:    {fmt_int(s.excluded_rule)} por regla, {fmt_int(s.excluded_manual)} a mano",
            f"Enlaces omitidos:      {fmt_int(s.links_skipped)}",
            f"Incidencias:           {fmt_int(s.errors)}",
            "",
            f"Copias verificadas:    {fmt_int(s.verified_copies)} (SHA-256 releyendo el disco), "
            f"reintentos {fmt_int(s.verify_retries)}" if job.verify else "Copias verificadas:    desactivado",
            f"Revisión integridad:   {fmt_int(s.scrub_files)} archivos ({fmt_bytes(s.scrub_bytes)}), "
            f"{fmt_int(s.scrub_repaired)} reparados, {fmt_int(s.scrub_alerts)} avisos",
            f"Cobertura del disco:   {fmt_int(s.scrub_covered)} de {fmt_int(s.scrub_total)} archivos comprobados",
        ]
        if job.errors:
            lines += ["", "INCIDENCIAS", "-" * 60] + [f"{p}\t{m}" for p, m in job.errors]
        cut, dcut = len(job.src), len(job.dst)
        if job.moves:
            lines += ["", "MOVIDOS", "-" * 60] + [f"{old[dcut:]}  →  {item[0][cut:]}" for old, item in job.moves]
        if job.deletes:
            lines += ["", "BORRADOS DEL ORIGEN (GUARDADOS EN VERSIONES)", "-" * 60] + [p[dcut:] for p, _s, _m in job.deletes]
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        except OSError:
            return ""
        return path

    def _open_log(self):
        if self._last_log and os.path.exists(self._last_log):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._last_log))

    def _export_issues(self):
        if not self.job or not self.job.errors:
            QMessageBox.information(self, APP_NAME, "No hay incidencias que exportar.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Exportar incidencias",
                                              os.path.join(LOG_DIR, "incidencias.txt"), "Texto (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(f"{p}\t{m}" for p, m in self.job.errors) + "\n")

    def _log(self, text: str, color: str | None = None):
        if not text:
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log.appendHtml(f'<span style="color:{C["faint"]}">{stamp}</span>&nbsp;&nbsp;'
                            f'<span style="color:{color or C["muted"]}">{html.escape(text)}</span>')

    # ── ventana ──────────────────────────────────────────────────────────
    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if self.taskbar is None:
            set_dark_titlebar(self)
            self.taskbar = Taskbar(int(self.winId()))
        if self._tour_pending and not self._tour_seen():
            self._tour_pending = False
            QTimer.singleShot(700, self._maybe_first_tour)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if self.tour is not None and self.tour.active:
            self.tour.setGeometry(self.rect())

    # ── recordatorios de copia ───────────────────────────────────────────
    def _remind_days(self) -> int:
        return int(self.remind_combo.currentData() or 3) if self.remind_box.isChecked() else 0

    def _save_registry(self):
        self.settings.setValue("copias", json.dumps(self.registry.to_list(), ensure_ascii=False))
        self.settings.sync()

    def _seed_registry(self) -> reminders.Registry:
        """La primera vez, las copias completas de los registros pasan a ser copias habituales."""
        cfg_dst = os.path.normcase(os.path.normpath(self._cfg["dst"])) if self._cfg["dst"] else ""

        def resolve(dst: str) -> tuple[str, str, str]:
            root = engine.volume_root(dst)
            found = engine.read_disk_id(root) if engine.volume_present(dst) else None
            if found:
                return found, engine.rel_to_volume(dst), engine.volume_info(root)[0] or engine.vol_name(root)
            if self.disk_id and os.path.normcase(os.path.normpath(dst)) == cfg_dst:
                return self.disk_id, self.dst_rel or engine.rel_to_volume(dst), ""
            return "", "", ""

        try:
            reg = reminders.seed_from_logs(LOG_DIR, resolve)
        except Exception:  # noqa: BLE001 - un registro raro no debe impedir arrancar
            return reminders.Registry()
        for it in reg.items.values():
            it["excluded"] = sorted(self.excl_map.get(os.path.normcase(it["src"]), set()))
        return reg

    def _disk_roots(self) -> dict[str, str]:
        """Marca de disco → raíz, de los discos conectados ahora."""
        roots: dict[str, str] = {}
        for root in sorted(engine.mounted_volumes()):
            found = engine.read_disk_id(root)
            if found and found not in roots:
                roots[found] = root
        labels = {found: engine.volume_info(root)[0] or engine.vol_name(root) for found, root in roots.items()}
        adopted = sum(self.registry.adopt_disk(found, root, engine.rel_to_volume, labels[found], must_exist=True)
                      for found, root in roots.items())
        for it in self.registry.items.values():
            if it.get("disk_id") in labels and not it.get("label"):
                it["label"] = labels[it["disk_id"]]  # copias recuperadas de los registros, sin nombre de disco
                adopted += 1
        if adopted:
            self._save_registry()
        return roots

    def _pair_dest(self, it: dict, roots: dict | None = None) -> str | None:
        """Dónde está ahora el destino de una copia, o None si su disco no está conectado."""
        if it.get("disk_id"):
            root = (self._disk_roots() if roots is None else roots).get(it["disk_id"])
            if root is None:
                return None
            return os.path.join(root, it["dst_rel"]) if it.get("dst_rel") else root
        return it["dst"] if it.get("dst") and os.path.isdir(it["dst"]) else None

    def _is_current_pair(self, it: dict) -> bool:
        if os.path.normcase(self.src_card.path()) != os.path.normcase(it["src"]):
            return False
        if it.get("disk_id"):
            return it["disk_id"] == self.disk_id and \
                os.path.normcase(it.get("dst_rel", "")) == os.path.normcase(self.dst_rel)
        return os.path.normcase(self.dst_card.path()) == os.path.normcase(it.get("dst", ""))

    @staticmethod
    def _pair_label(it: dict) -> str:
        return it.get("label") or reminders.short_name(it.get("dst", ""))

    def _nb_label(self, it: dict) -> str:
        return self._pair_label(it).replace(" ", "\u00a0")  # «Disco USB» no se parte entre dos líneas

    def _record_pair(self, job: engine.BackupJob, copied: bool):
        """Copia completa (o análisis que encontró el destino al día): ya no hay nada pendiente."""
        root = engine.volume_root(job.dst)
        disk_id = engine.read_disk_id(root) or ""
        label = engine.volume_info(root)[0] or engine.vol_name(root)
        self.registry.record(job.src, job.dst, disk_id, engine.rel_to_volume(job.dst) if disk_id else "", label,
                             job.scan_started_at or datetime.now(), job.patterns, job.excluded, copied=copied,
                             only_existing=not copied)
        self._save_registry()
        self._refresh_reminders()

    def _pair_status(self, it: dict) -> tuple[str, str]:
        p = it.get("pending") or {}
        ago = f"última copia {fmt_ago(it.get('last_ok'))}"
        if not it.get("remind", True):
            return f"{ago}  ·  sin aviso", C["faint"]
        if p.get("missing"):
            return f"origen no disponible  ·  {ago}", C["faint"]
        changes = short_changes(p)
        if changes:
            return f"{changes}  ·  {ago}", C["warn"] if it.get("alert") else C["accent2"]
        if p.get("checked"):
            return f"al día  ·  {ago}", C["ok"]
        return ago, C["muted"]

    def _refresh_reminders(self):
        if not hasattr(self, "remind_rows"):
            return
        enabled = self.remind_box.isChecked()
        self.remind_combo.setEnabled(enabled)
        roots = self._disk_roots()  # antes de pintar: puede completar marcas y nombres de disco
        while self.remind_rows.count():
            w = self.remind_rows.takeAt(0).widget()
            if w is not None:
                w.hide()  # deleteLater llega en la siguiente vuelta del bucle: que no se pinte mientras
                w.deleteLater()
        items = sorted(self.registry.to_list(), key=lambda it: not it.get("alert"))
        for it in items:
            status, color = self._pair_status(it)
            row = ReminderRow(it, status, color, self._is_current_pair(it))
            row.menu_requested.connect(self._reminder_menu)
            self.remind_rows.addWidget(row)
        self.remind_empty.setVisible(not items)
        self.remind_rows_box.setVisible(bool(items))
        self.btn_remind_check.setVisible(bool(items))
        self.remind_tray_hint.setVisible(enabled and not self.watch_box.isChecked())
        checked = max(((it.get("pending") or {}).get("checked", "") for it in items), default="")
        if self.rem_checker.busy:
            self.remind_status.setText("Comprobando cambios…")
        else:
            self.remind_status.setText(f"Comprobado {fmt_ago(checked)}" if checked else "")
        self.remind_status.setVisible(bool(self.remind_status.text()))
        self.btn_remind_check.setEnabled(bool(items) and not self.rem_checker.busy)
        self._refresh_remind_bar(roots)
        self._refresh_summaries()

    def _refresh_remind_bar(self, roots: dict | None = None):
        alerts = self.registry.alerts() if self.remind_box.isChecked() else []
        if self.tray:
            tip = f"{APP_NAME} · {PILL[self._state][0].lower()}"
            if alerts:
                tip += f" · {len(alerts)} copia{'s' if len(alerts) != 1 else ''} con cambios sin copiar"
            self.tray.setToolTip(tip)
        if not alerts or self._state in RUNNING:
            self._bar_target = None
            self.remind_bar.conceal()
            return
        roots = self._disk_roots() if roots is None else roots
        ready = [it for it in alerts if self._pair_dest(it, roots)]
        it = alerts[0]
        if len(alerts) == 1:
            title = f"Tienes cambios sin copiar en {reminders.short_name(it['src'])}"
            what = reminders.changes_text(it.get("pending") or {}, fmt_int, fmt_bytes)
            text = (f"{what[:1].upper()}{what[1:]} desde tu última copia en {self._nb_label(it)}, "
                    f"{fmt_ago(it.get('last_ok'))}.")
        else:
            title = f"Tienes cambios sin copiar en {len(alerts)} copias"
            text = "  ·  ".join(f"{reminders.short_name(a['src'])} → {self._nb_label(a)}: "
                                f"{short_changes(a.get('pending')) or 'con cambios'}" for a in alerts[:3])
        if ready:
            target = ready[0]
            self._bar_target = target["id"]
            text += f"  {self._nb_label(target)} está conectado."
            self.remind_bar.go.setText("  Sincronizar ahora" if len(alerts) == 1
                                       else f"  Sincronizar {reminders.short_name(target['src'])}")
            self.remind_bar.go.show()
        else:
            self._bar_target = None
            text += f"  Conecta {self._nb_label(it)} para copiarlos." if len(alerts) == 1 \
                else "  Conecta el disco de copia para guardarlos."
            self.remind_bar.go.hide()
        self.remind_bar.title.setText(title)
        self.remind_bar.text.setText(text)
        self.remind_bar.close_btn.setToolTip(f"Ocultar el aviso: vuelve dentro de {days_text(self._remind_days())} "
                                             "si sigue sin copia")
        self.remind_bar.reveal()

    def _reminder_tick(self, manual: bool = False):
        if self.rem_checker.busy or self._state in RUNNING:
            return
        if manual:
            items = self.registry.to_list()
        elif self.remind_box.isChecked():
            items = self.registry.due(datetime.now(), self._remind_days())
        else:
            items = []
        if items and self.rem_checker.check(items, manual):
            self._refresh_reminders()
        elif not manual and hasattr(self, "remind_bar"):
            self._refresh_remind_bar()  # «hace 4 días» → «hace 5 días», disco conectado o no

    def _on_changes_checked(self, results: dict, manual: bool):
        self.rem_checker.busy = False
        now = datetime.now()
        fresh = []
        for pid, result in results.items():
            if self.registry.apply_check(pid, result, now, self._remind_days(), self.remind_box.isChecked()) == "new":
                fresh.append(self.registry.get(pid))
        self._save_registry()
        self._refresh_reminders()
        if fresh and not manual:
            self._notify_reminder(fresh)

    def _notify_reminder(self, items: list[dict]):
        """Notificación del sistema, solo si no estás mirando ya la ventana (ahí basta el aviso)."""
        if self.isVisible() and not self.isMinimized() and self.isActiveWindow():
            return
        if len(items) == 1:
            it = items[0]
            p = it.get("pending") or {}
            label = self._pair_label(it)
            what = reminders.changes_text(p, fmt_int, fmt_bytes)
            body = (f"{what[:1].upper()}{what[1:]} en {reminders.short_name(it['src'])} desde tu última copia "
                    f"({fmt_ago(it.get('last_ok'))}). ")
            body += f"{label} está conectado: pulsa para sincronizar." if self._pair_dest(it) \
                else f"Conecta {label} para copiarlos."
        else:
            body = "  ·  ".join(f"{reminders.short_name(it['src'])}: {short_changes(it.get('pending'))}"
                                for it in items[:3]) + ". Pulsa para verlos."
        if self.tray and self.tray.isVisible():
            self.tray.showMessage("Tienes cambios sin copiar", body, app_icon(), 20000)
        else:
            QApplication.alert(self, 0)

    def _on_remind_toggled(self, on: bool):
        self._save_settings()
        self._refresh_reminders()
        if on:
            QTimer.singleShot(1500, self._reminder_tick)

    def _on_remind_days_changed(self):
        self._save_settings()
        self._refresh_reminders()
        QTimer.singleShot(1500, self._reminder_tick)

    def _remind_go(self):
        if self._bar_target:
            self._sync_pair(self._bar_target)

    def _remind_later(self):
        until = datetime.now() + timedelta(days=1)
        for it in self.registry.alerts():
            self.registry.snooze(it["id"], until)
        self._save_registry()
        self._refresh_reminders()

    def _remind_dismiss(self):
        for it in self.registry.alerts():
            self.registry.dismiss(it["id"])
        self._save_registry()
        self._refresh_reminders()

    def _sync_pair(self, pid: str, start: bool = True):
        """Pone esa copia como origen y destino actuales y, si se pide, la sincroniza."""
        it = self.registry.get(pid)
        if it is None or self._state in RUNNING:
            return
        dest = self._pair_dest(it)
        if start and dest is None:
            QMessageBox.information(self, APP_NAME, f"Conecta {self._pair_label(it)} para copiar "
                                                    f"{reminders.short_name(it['src'])}.")
            return
        self.excl_map[self._excl_key()] = self.tree.excluded
        if os.path.normcase(self.src_card.path()) != os.path.normcase(it["src"]):
            self.src_card.set_path(it["src"])
            self._on_src_changed()
        target = dest or it.get("dst", "")
        if target and os.path.normcase(self.dst_card.path()) != os.path.normcase(target):
            self.dst_card.set_path(target)
            self._on_dst_changed()
        self._save_settings()
        if start:
            self._start(copy=True)
        else:
            self._refresh_reminders()

    def _reminder_menu(self, pid: str, pos: QPoint):
        it = self.registry.get(pid)
        if it is None:
            return
        dest = self._pair_dest(it)
        busy = self._state in RUNNING
        menu = QMenu(self)
        a_sync = menu.addAction(glyph_icon(G.PLAY, C["text"], 14),
                                "Sincronizar ahora" if dest else f"Sincronizar  (conecta {self._pair_label(it)})")
        a_sync.setEnabled(dest is not None and not busy)
        a_use = None
        if not self._is_current_pair(it):
            a_use = menu.addAction(glyph_icon(G.SYNC, C["text"], 14), "Usar esta copia")
            a_use.setEnabled(not busy)
        a_check = menu.addAction(glyph_icon(G.SEARCH, C["text"], 14), "Comprobar cambios ahora")
        a_check.setEnabled(not busy and not self.rem_checker.busy)
        menu.addSeparator()
        a_remind = menu.addAction(glyph_icon(G.BELL, C["text"], 14), "Avisarme de esta copia")
        a_remind.setCheckable(True)
        a_remind.setChecked(it.get("remind", True))
        a_forget = menu.addAction(glyph_icon(G.CLOSE, C["text"], 14), "Quitar de la lista")
        a_forget.setToolTip("Vuelve a aparecer si la sincronizas otra vez")
        chosen = menu.exec(pos)
        if chosen is None:
            return
        if chosen is a_sync:
            self._sync_pair(pid)
        elif chosen is a_use:
            self._sync_pair(pid, start=False)
        elif chosen is a_check:
            if self.rem_checker.check([it], manual=True):
                self._refresh_reminders()
        elif chosen is a_remind:
            self.registry.set_remind(pid, a_remind.isChecked())
            self._save_registry()
            self._refresh_reminders()
        elif chosen is a_forget:
            self.registry.remove(pid)
            self._save_registry()
            self._refresh_reminders()

    # ── guía rápida ──────────────────────────────────────────────────────
    def show_tour(self, animate: bool | None = None):
        if self.isHidden() or self.isMinimized():
            self._show_window()
            QTimer.singleShot(250, lambda: self.show_tour(animate))  # que la ventana se pinte antes de la captura
            return
        if self.tour is None:
            self.tour = TourOverlay(self, ui_animations())
            self.tour.finished.connect(self._on_tour_finished)
        if animate is not None:
            self.tour.animate = animate
        self.tour.open()

    def toggle_tour(self):
        if self.tour is not None and self.tour.active and not self.tour.closing:
            self.tour.finish(False)
        else:
            self.show_tour()

    def _tour_seen(self) -> bool:
        try:
            return int(self.settings.value("tour", 0)) >= TOUR_VERSION
        except (TypeError, ValueError):
            return False

    def _on_tour_finished(self, _completed: bool):
        self.settings.setValue("tour", TOUR_VERSION)
        self.settings.sync()

    def _maybe_first_tour(self):
        """La primera vez que se abre la ventana: guía rápida, sin pisar una copia ni otro diálogo."""
        if self._tour_seen() or (self.tour is not None and self.tour.active):
            return
        if not self.isVisible() or self.isMinimized():
            self._tour_pending = True  # al abrir la ventana la próxima vez
            return
        busy = (self._state in RUNNING or QApplication.activeModalWidget() is not None
                or any(d is not None and d.isVisible() for d in (self.dialog, self.update_dialog)))
        if busy:
            QTimer.singleShot(5000, self._maybe_first_tour)
            return
        self.show_tour()

    def closeEvent(self, event):  # noqa: N802
        self.excl_map[self._excl_key()] = self.tree.excluded
        self._save_settings()
        if self.tray and self.tray.isVisible() and not self._quitting:
            # con la vigilancia activa, cerrar la ventana la deja en la bandeja
            event.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                extra = " La sincronización sigue en marcha." if self._state in RUNNING else ""
                self._notify(APP_NAME, "Sigo en la bandeja vigilando tu disco de copia." + extra)
            return
        if self.job and self._state in RUNNING and not self.job.cancelled:
            ans = QMessageBox.question(self, APP_NAME, "Hay una operación en curso. ¿Cancelarla y salir?\n\n"
                                       "Lo ya copiado se conserva.")
            if ans != QMessageBox.StandardButton.Yes:
                self._quitting = False
                event.ignore()
                return
            self.job.cancel()
            if self.job_thread:
                self.job_thread.join(timeout=15)
        self._guard_stop()
        if self.tray:
            self.tray.hide()
        event.accept()
        self._quitting = True
        QApplication.quit()

    # ── energía: equipo y disco despiertos, apagado retenido ─────────────
    def _guard_wanted(self, job: engine.BackupJob) -> bool:
        if not self.awake_box.isChecked() or job.phase not in ("scanning", "copying") or job.cancelled:
            return False
        # en una pausa larga se deja que el equipo se suspenda; al reanudar se vuelve a pedir
        return not (job.paused and self._pause_at is not None
                    and time.perf_counter() - self._pause_at > PAUSE_GRACE)

    def _sync_guard(self, job: engine.BackupJob):
        want = self._guard_wanted(job)
        if want and not self.guard.active:
            self._guard_start(job)
        elif not want and self.guard.active:
            if job.paused and job.phase in ("scanning", "copying"):
                self._log(f"Pausa de más de {PAUSE_GRACE // 60} min: el equipo ya puede suspenderse. Al reanudar "
                          "se vuelve a mantener despierto.", C["warn"])
            self._guard_stop()
        now = time.perf_counter()
        if now - self._awake_refresh >= 1.0:
            self._refresh_awake()

    def _guard_start(self, job: engine.BackupJob):
        s = job.stats

        def activity():
            # cambia mientras la copia escribe en el disco; en el análisis el latido va solo
            if job.phase != "copying" or job.paused:
                return None
            return s.bytes_done, s.files_done, s.moves_done, s.deletes_done, s.scrub_files

        root = job.meta_root if engine.volume_present(job.dst) else None
        self.guard.start(f"{APP_NAME}: copia de seguridad en curso ({job.src} → {job.dst})", root, activity)
        if IS_WIN:
            keepawake.block_shutdown(int(self.winId()),
                                     f"Copia de seguridad en curso ({job.src} → {job.dst}). Si apagas ahora se "
                                     "interrumpe; lo ya copiado se conserva.")
        if not self._guard_logged:
            self._guard_logged = True
            parts = [f"el equipo no se suspende ({self.guard.method or 'no disponible'})"]
            if root:
                parts.append(f"latido al disco {engine.vol_name(root)} si pasa "
                             f"{keepawake.BEAT_SECONDS:.0f} s sin escribir")
            parts.append("apagar o reiniciar pide confirmación" if IS_WIN
                         else "cerrar sesión o apagar pide confirmación")
            self._log("Mientras dure: " + "  ·  ".join(parts), C["muted"])
            if self.guard.error:
                self._log(f"⚠ No se pudo impedir la suspensión: {self.guard.error}", C["warn"])
        self._refresh_awake()

    def _guard_stop(self):
        if self.guard.active:
            self.guard.stop()
        if IS_WIN:
            keepawake.unblock_shutdown(int(self.winId()))
        self._refresh_awake()

    def _refresh_awake(self):
        self._awake_refresh = time.perf_counter()
        st = self.guard.status()
        job = self.job
        lost = bool(job and job.stats.disk_lost_at and self._state in RUNNING)
        if not st["active"] and not lost:
            self.awake_chip.hide()
            if self.awake_box.isChecked():
                self.awake_status.setText("Se activa al analizar o sincronizar y se desactiva al terminar.")
            else:
                self.awake_status.setText("Desactivado: el equipo y el disco se suspenden con normalidad "
                                          "aunque haya una copia en curso.")
            return
        if lost:
            color, text = C["warn"], "Esperando al disco"
        elif st["disk_error"]:
            color, text = C["warn"], "Sin suspensión · disco sin respuesta"
        else:
            color, text = C["ok"], "Sin suspensión" + (" · disco activo" if st["disk"] else "")
        self.awake_icon.setPixmap(glyph_pixmap(G.POWER, color, 13))
        self.awake_text.setText(text)
        self.awake_text.setStyleSheet(f"color:{color};")
        self.awake_chip.setStyleSheet(f"QFrame#awake {{ background:{rgba(color, 26)}; "
                                      f"border:1px solid {rgba(color, 80)}; }}")
        lines = [f"Equipo: no se suspende por inactividad ({st['method'] or 'no disponible'})."]
        beat = ""
        if lost:
            beat = "esperando a que vuelva el disco de copia"
        elif st["disk"]:
            beat = (f"sin respuesta del disco: {st['disk_error']}" if st["disk_error"]
                    else f"último latido hace {fmt_human(time.time() - st['last_beat'])}" if st["last_beat"]
                    else "la copia mantiene el disco activo")
            lines.append(f"Disco de copia ({engine.vol_name(st['root'])}): {beat}.")
        lines.append("Apagar o reiniciar: Windows pide confirmación." if IS_WIN
                     else "Cerrar sesión o apagar: pide confirmación.")
        tip = "\n".join(lines)
        self.awake_chip.setToolTip(tip)
        since = f"Activo desde las {datetime.fromtimestamp(self.guard.since):%H:%M:%S}" if st["active"] else ""
        self.awake_status.setText("  ·  ".join(x for x in (since, beat) if x))
        self.awake_chip.show()

    def _watch_disk(self, job: engine.BackupJob):
        """Avisa cuando el disco de copia desaparece a mitad y cuando vuelve."""
        s = job.stats
        if s.disk_lost_at and not self._disk_lost_seen:
            self._disk_lost_seen = True
            self._log(f"El disco de copia no responde o se ha desconectado ({job.dst}). La copia espera a que "
                      "vuelva y repite el archivo que estaba a medias: no se pierde nada.", C["warn"])
            self._notify(APP_NAME, "El disco de copia se ha desconectado. Vuelve a conectarlo y la copia seguirá.")
            self._refresh_awake()
        elif not s.disk_lost_at and self._disk_lost_seen:
            self._disk_lost_seen = False
            self._log(f"Disco de nuevo disponible: sigue la copia (en total {fmt_human(s.disk_wait_s)} "
                      "esperando).", C["ok"])
            self._refresh_awake()

    def _on_session_end_query(self, manager):
        """Windows pregunta si puede apagar, reiniciar o cerrar la sesión."""
        if self.guard.active and self.job and self._state in RUNNING:
            manager.cancel()
            self._log("Apagado, reinicio o cierre de sesión retenido: hay una copia en curso.", C["warn"])

    def _on_about_to_quit(self):
        # también al apagar de todas formas: se para la copia de forma ordenada (sin archivos a medias)
        self._quitting = True
        if self.job and self._state in RUNNING:
            self.job.cancel()
            if self.job_thread:
                self.job_thread.join(timeout=4)
        self._guard_stop()

    def _allow_quit(self) -> bool:
        """Petición de salir que no viene de la app. En macOS: Cmd+Q, «Salir» del Dock, cerrar sesión o apagar."""
        if not self._quitting:
            if IS_MAC and self.job and self._state in RUNNING and not self.job.cancelled:
                self._show_window()
                ans = QMessageBox.question(
                    self, APP_NAME, "Hay una sincronización en curso. ¿Cancelarla y salir?\n\nLo ya copiado se "
                    "conserva. Si ibas a apagar o cerrar sesión, elige No y espera a que termine.")
                if ans != QMessageBox.StandardButton.Yes:
                    return False
                self.job.cancel()
                if self.job_thread:
                    self.job_thread.join(timeout=15)
            self._quitting = True
        return True

    def _on_system_suspend(self):
        job = self.job
        if self._sleeping or not job or self._state not in RUNNING:
            return
        self._sleeping = True
        if not job.paused:
            job.pause()
            self._pause_at = time.perf_counter()
            self._sleep_paused = True
        self._log("El equipo se va a suspender: copia en pausa. Seguirá sola al volver.", C["warn"])

    def _on_system_resume(self):
        if not self._sleeping:
            return
        self._sleeping = False
        self._log("Equipo reanudado")
        QTimer.singleShot(120_000, self._reminder_tick)
        if self._sleep_paused:
            deadline = time.monotonic() + 180
            QTimer.singleShot(1500, lambda: self._resume_after_sleep(deadline))

    def _resume_after_sleep(self, deadline: float):
        job = self.job
        if not self._sleep_paused or not job or self._state not in RUNNING:
            self._sleep_paused = False
            return
        if not engine.volume_present(job.dst) and time.monotonic() < deadline:
            # el disco USB tarda unos segundos en volver tras la suspensión
            QTimer.singleShot(1000, lambda: self._resume_after_sleep(deadline))
            return
        if job.paused:
            self._toggle_pause()  # si el disco no ha vuelto, la copia lo esperará por su cuenta


_crash_file = None


def install_crash_log() -> None:
    """Registra cualquier fallo en logs/fallos.log.

    Sin un sys.excepthook propio, PyQt6 cierra la aplicación entera ante la
    primera excepción no capturada en un slot, y con pythonw no se ve nada.
    """
    global _crash_file
    path = os.path.join(LOG_DIR, "fallos.log")
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        _crash_file = open(path, "a", encoding="utf-8", buffering=1)
        faulthandler.enable(_crash_file)
    except OSError:
        _crash_file = None
    shown = []

    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        if _crash_file:
            _crash_file.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}]\n{text}")
        if not shown and QApplication.instance() and threading.current_thread() is threading.main_thread():
            shown.append(True)
            QMessageBox.critical(None, APP_NAME, f"Error inesperado en la ventana; la copia sigue en marcha.\n\n"
                                                 f"{exc_type.__name__}: {exc}\n\nDetalle en {path}")

    sys.excepthook = hook
    threading.excepthook = lambda a: hook(a.exc_type, a.exc_value, a.exc_traceback)


def selftest() -> int:
    """Prueba sin ventana: sincroniza, verifica, repara y construye la interfaz.

    La ejecuta GitHub Actions sobre la aplicación ya compilada (Windows y macOS).
    Deja el resultado en selftest.log, en la carpeta actual.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    report = open("selftest.log", "w", encoding="utf-8")

    def say(msg: str) -> None:
        report.write(msg + "\n")
        report.flush()

    try:
        app = App(sys.argv)
        pick_fonts()
        app.setStyleSheet(build_qss(write_assets()))
        say(f"{APP_NAME} {APP_VERSION} · {sys.platform} · Python {sys.version.split()[0]}")
        with tempfile.TemporaryDirectory() as tmp:
            src, dst, meta = (os.path.join(tmp, x) for x in ("src", "dst", "vol"))
            idx = os.path.join(tmp, "estado", "idx.db")
            os.makedirs(meta)
            for i in range(40):
                p = os.path.join(src, "proyecto", "src" if i % 2 else "lib", f"f{i}.txt")
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "wb") as fh:
                    fh.write(os.urandom(2000 + i * 37))
            os.makedirs(os.path.join(src, "proyecto", "node_modules"))
            os.makedirs(os.path.join(src, "$RECYCLE.BIN", "S-1-5-21"))  # como al usar una unidad entera de origen
            with open(os.path.join(src, "$RECYCLE.BIN", "S-1-5-21", "borrado.zip"), "wb") as fh:
                fh.write(b"papelera")
            with open(os.path.join(src, "proyecto", "node_modules", "x.js"), "w") as fh:
                fh.write("x")

            def run(**kw):
                job = engine.BackupJob(src, dst, ["node_modules"], [], threads=4, index_path=idx, meta_root=meta,
                                       scrub_seconds=-1, **kw)
                job.run()
                s = job.stats
                say(f"  {job.phase}: copiados={s.files_done} verificados={s.verified_copies} movidos={s.moves_done} "
                    f"versiones={s.versions_saved} revisados={s.scrub_files} reparados={s.scrub_repaired} "
                    f"errores={s.errors} rápido={s.fast}")
                assert job.phase == "done", job.fatal
                return s

            s = run()
            assert s.files_done == 40 and s.verified_copies == 40 and s.scrub_files == 40
            assert not os.path.exists(os.path.join(dst, "proyecto", "node_modules"))
            assert not os.path.exists(os.path.join(dst, "$RECYCLE.BIN")), "papelera copiada"
            assert s.new_done == 40 and s.changed_done == 0, "contadores del resumen"
            assert updater.is_newer("v1.10.0", "1.9.3") and not updater.is_newer("1.2.0", "1.2.0")
            assert updater.parse_version("v2.0") == (2, 0, 0)
            say("  versiones: comparación correcta")
            os.rename(os.path.join(src, "proyecto", "lib"), os.path.join(src, "proyecto", "libreria"))
            victim = os.path.join(dst, "proyecto", "src", "f1.txt")
            st = os.stat(victim)
            data = bytearray(open(victim, "rb").read())
            data[0] ^= 0xFF
            os.chmod(victim, stat.S_IWRITE | stat.S_IREAD)
            with open(victim, "wb") as fh:
                fh.write(bytes(data))
            os.utime(victim, ns=(st.st_atime_ns, st.st_mtime_ns))
            s = run()
            assert s.fast and s.moves_done == 20 and s.scrub_repaired == 1, "movidos/reparación"
            for root, _dirs, files in os.walk(src):
                if "node_modules" in root or "$RECYCLE.BIN" in root:
                    continue
                for f in files:
                    a = os.path.join(root, f)
                    b = os.path.join(dst, os.path.relpath(a, src))
                    assert open(a, "rb").read() == open(b, "rb").read(), f"distinto: {b}"
            say("  espejo exacto: sí")

            # el disco desaparece a mitad de la copia y vuelve: se espera y no falla nada
            class Flaky(engine.BackupJob):
                calls = 0
                offline_until = 0.0

                def _dst_online(self):
                    return time.monotonic() >= self.offline_until

                def _copy_file(self, *a, **kw):
                    with self._lock:
                        self.calls += 1
                        if self.calls == 6:
                            self.offline_until = time.monotonic() + 1.5
                    if not self._dst_online():
                        raise OSError(2, "disco desconectado (simulado)")
                    return super()._copy_file(*a, **kw)

            settle, engine.DISK_SETTLE = engine.DISK_SETTLE, 0.2
            src2, dst2 = os.path.join(tmp, "src2"), os.path.join(tmp, "dst2")
            for i in range(30):
                os.makedirs(src2, exist_ok=True)
                with open(os.path.join(src2, f"g{i}.bin"), "wb") as fh:
                    fh.write(os.urandom(3000 + i))
            job = Flaky(src2, dst2, [], [], threads=4, meta_root=meta, scrub_seconds=0)
            job.run()
            engine.DISK_SETTLE = settle
            s = job.stats
            say(f"  disco desconectado a mitad: {job.phase} copiados={s.files_done} errores={s.errors} "
                f"esperas={s.disk_waits} ({s.disk_wait_s:.1f} s)")
            assert job.phase == "done" and s.files_done == 30 and s.errors == 0 and s.disk_waits >= 1, \
                "espera al disco"

            # equipo despierto + latido del disco
            beat_s, keepawake.BEAT_SECONDS = keepawake.BEAT_SECONDS, 1.0
            guard = keepawake.Guard()
            guard.start(f"{APP_NAME} selftest", meta)
            try:
                time.sleep(2.6)
                st = guard.status()
                beat_file = os.path.join(meta, ".devbackup", keepawake.BEAT_FILE)
                say(f"  sin suspensión: {st['method'] or '—'} · latidos={st['beats']} {st['disk_error']}")
                assert st["beats"] >= 1 and os.path.exists(beat_file), "latido del disco"
                if IS_WIN or IS_MAC:
                    assert st["system"] and not st["error"], f"retención de energía: {st['error']}"
                if IS_MAC:
                    out = subprocess.run(["pmset", "-g", "assertions"], capture_output=True, text=True).stdout
                    assert f"{APP_NAME} selftest" in out, "la aserción no aparece en pmset"
                    say("  pmset -g assertions: aparece la aserción")
            finally:
                guard.stop()
                keepawake.BEAT_SECONDS = beat_s
            assert not guard.active

            # recordatorios: cambios desde la última copia sin abrir archivos, y un solo aviso por periodo
            rsrc = os.path.join(tmp, "rsrc")
            os.makedirs(os.path.join(rsrc, "p", "node_modules"))
            for i in range(5):
                with open(os.path.join(rsrc, "p", f"a{i}.txt"), "w") as fh:
                    fh.write("x")
            time.sleep(1.1)
            since = time.time()
            time.sleep(1.1)
            changed = (("a0.txt", "cambio"), ("nuevo.txt", "nuevo"), (os.path.join("node_modules", "x.js"), "-"))
            for name, text in changed:
                with open(os.path.join(rsrc, "p", name), "w") as fh:
                    fh.write(text)
            res = engine.changes_since(rsrc, since, ["node_modules"], [])
            assert res["files"] == 2 and not res["missing"], f"cambios pendientes: {res}"
            assert engine.changes_since(os.path.join(tmp, "no-existe"), since)["missing"]
            reg = reminders.Registry()
            now = datetime.now()
            pair = reg.record(rsrc, dst, "disco", "copia", "USB", now - timedelta(days=4), ["node_modules"], [])
            assert reg.due(now, 3) and not reg.due(now, 7), "periodo del recordatorio"
            assert reg.apply_check(pair["id"], res, now, 3) == "new", "aviso"
            assert reg.apply_check(pair["id"], res, now, 3) == "alert" and not reg.due(now, 3), "aviso repetido"
            reg.record(rsrc, dst, "disco", "copia", "USB", now, ["node_modules"], [])
            assert not reg.alerts(), "la copia no quitó el aviso"
            twin = reminders.Registry()
            twin.record(rsrc, dst, "", "", "", now - timedelta(days=9), [], [])  # recuperada de un registro, sin marca
            twin.record(rsrc, dst, "disco", "copia", "USB", now, [], [])
            assert len(twin.items) == 1 and twin.to_list()[0]["copies"] == 2, "copia duplicada por ruta y por disco"
            say(f"  recordatorios: {res['files']} cambios de {res['scanned']} archivos, un aviso por periodo")
        win = MainWindow()
        win._refresh_disk_status()
        say("  interfaz: construida")
        images, spots, _size = tour_assets()
        missing = [k for k, pm in images.items() if pm.isNull()]
        assert not missing, f"faltan ilustraciones de la guía: {missing}"
        tour = TourOverlay(win, animate=False)
        ended: list[bool] = []
        tour.finished.connect(ended.append)
        tour.open()
        steps = len(tour.card.steps)
        assert 4 <= steps <= 6 and tour.card.idx == 0
        for _ in range(steps - 1):
            tour.card.forward()
            tour.card.grab()
        assert tour.card.idx == steps - 1 and tour.card.next_btn.text().strip() == "Empezar"
        tour.card.next()
        assert ended == [True] and not tour.active, "la guía no se cerró al terminar"
        say(f"  guía rápida: {steps} pasos, {len(images)} ilustraciones, "
            f"{sum(len(v) for v in spots.values())} zonas destacadas")
        win.close()
        say("SELFTEST OK")
        return 0
    except BaseException:  # noqa: BLE001 - todo fallo cuenta
        say(traceback.format_exc())
        say("SELFTEST FALLÓ")
        return 1
    finally:
        report.close()


_app_mutex = None


def main() -> int:
    global _app_mutex
    if "--selftest" in sys.argv:
        return selftest()
    if IS_WIN:
        # el instalador (AppMutex) lo usa para pedir que se cierre la app antes de actualizarla
        _app_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "DevBackupStudioMutex")
    try:
        migrate_data()
    except OSError:
        pass
    install_crash_log()
    tray_mode = "--tray" in sys.argv
    if os.name == "nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DevBackupStudio")
            ctypes.windll.kernel32.SetErrorMode(0x0001)  # sin diálogos de «inserte un disco» al mirar unidades
        except Exception:  # noqa: BLE001
            pass
    app = App(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    if send_to_running("tray" if tray_mode else "show"):
        return 0
    app.setStyle("Fusion")
    pick_fonts()
    app.setFont(QFont(FONT["ui"], 10))
    app.setPalette(dark_palette())
    app.setStyleSheet(build_qss(write_assets()))
    server = QLocalServer()
    QLocalServer.removeServer(INSTANCE)
    server.listen(INSTANCE)
    win = MainWindow(tray_mode=tray_mode, updated="--updated" in sys.argv)
    server.newConnection.connect(lambda: win.on_instance_message(server))
    if IS_MAC:
        # clic en el icono del Dock con la ventana escondida en la barra de menús
        app.applicationStateChanged.connect(
            lambda st: win._show_window() if st == Qt.ApplicationState.ApplicationActive and win.isHidden()
            and not win.tray_only_start else None)
    if not tray_mode:
        win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
