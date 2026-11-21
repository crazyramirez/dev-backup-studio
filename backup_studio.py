"""
DEV Backup Studio — copia de seguridad incremental con progreso en vivo.

    pythonw backup_studio.py
"""

from __future__ import annotations

import ctypes
import faulthandler
import getpass
import hashlib
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
import types
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import (QAbstractNativeEventFilter, QAbstractTableModel, QByteArray, QDate, QDateTime,
                          QEasingCurve, QEvent, QLibraryInfo, QLocale, QModelIndex, QObject, QPoint, QPointF, QProcess, QRect, QRectF, QSettings, QSize, Qt, QTime, QTimer, QTranslator,
                          QUrl, QVariantAnimation, pyqtSignal)
from PyQt6.QtGui import (QBrush, QColor, QConicalGradient, QDesktopServices, QFont,
                         QFontDatabase, QFontMetricsF, QIcon, QImage, QKeySequence, QLinearGradient,
                         QPainter, QPainterPath, QPalette, QPen, QPixmap, QRadialGradient, QShortcut)
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (QAbstractButton, QAbstractItemView, QAbstractScrollArea, QAbstractSpinBox,
                             QApplication, QButtonGroup, QCheckBox, QComboBox,
                             QDialog, QFileDialog, QFrame, QGraphicsBlurEffect, QGraphicsOpacityEffect,
                             QGraphicsPixmapItem, QGraphicsScene, QGridLayout, QHBoxLayout, QHeaderView,
                             QLabel, QLayout, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
                             QMessageBox, QRadioButton, QStyle, QStyledItemDelegate,
                             QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
                             QSpinBox, QStackedWidget, QSystemTrayIcon, QTableView, QTableWidget,
                             QTableWidgetItem, QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                             QWidget)

import cloudlink
import engine
import i18n
import keepawake
import plesklink
import reminders
import updater
import versions
from i18n import N_, _, _n

APP_NAME = "DEV Backup Studio"
APP_VERSION = "2.4.1"
FROZEN = getattr(sys, "frozen", False)
APP_DIR = os.path.dirname(os.path.abspath(__file__))
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


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


def _language_setting() -> str:
    """Idioma guardado en los ajustes: «auto» (el del sistema) o un código (es, en, fr, it, zh, ja)."""
    try:
        value = QSettings(os.path.join(DATA_DIR, "settings.ini"), QSettings.Format.IniFormat).value("language", "auto")
    except Exception:  # noqa: BLE001
        value = "auto"
    return value if value in i18n.LANGS else "auto"


LANGUAGE = _language_setting()
i18n.set_language(i18n.resolve(LANGUAGE))  # antes de las constantes con textos traducidos

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
DEFAULT_FOLDED = ["modo", "integridad", "energia", "idioma", "actualizaciones"]  # tarjetas plegadas al empezar
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
    "history": _p("M3.6 12a8.4 8.4 0 1 0 2.5-6") + _p("M3.5 4.2v4.3h4.3") + _p("M12 7.8v4.6l3 1.9"),
    "restore": _p("M9 14.5L4 9.5l5-5") + _p("M4 9.5h10.5a5.5 5.5 0 0 1 0 11H10.5"),
    "layers": _p("M12 3.5l8.5 4.5-8.5 4.5-8.5-4.5z") + _p("M3.5 12.3l8.5 4.5 8.5-4.5") + _p("M3.5 16.3l8.5 4.5 8.5-4.5"),
    "trash": _p("M4.5 7h15M9.5 7V5.2a1.5 1.5 0 0 1 1.5-1.5h2a1.5 1.5 0 0 1 1.5 1.5V7")
             + _p("M6.5 7l.9 11.6a2 2 0 0 0 2 1.9h5.2a2 2 0 0 0 2-1.9L17.5 7"),
    "eye": _p("M2.5 12s3.5-6.5 9.5-6.5S21.5 12 21.5 12s-3.5 6.5-9.5 6.5S2.5 12 2.5 12z") + '<circle cx="12" cy="12" r="2.8"/>',
    "globe": '<circle cx="12" cy="12" r="9"/>' + _p("M3.2 12h17.6")
             + _p("M12 3c2.4 2.5 3.6 5.5 3.6 9s-1.2 6.5-3.6 9c-2.4-2.5-3.6-5.5-3.6-9S9.6 5.5 12 3z"),
}


class G:
    """Nombres de los iconos de ICONS."""
    ADD, CLOSE, GEAR, SEARCH, REFRESH = "add", "close", "gear", "search", "refresh"
    CHECK, PLAY, PAUSE, STOP, WARN = "check", "play", "pause", "stop", "warn"
    ERROR, FLAG, OPEN, FOLDER, NEXT = "error", "flag", "open", "folder", "next"
    DOC, COPY, TREE, SYNC, TIMER = "doc", "copy", "tree", "sync", "timer"
    DONE, INFO, CLOCK, SPEED, DRIVE, FILTER = "done", "info", "clock", "speed", "drive", "filter"
    SHIELD, DOWNLOAD, POWER = "shield", "download", "power"
    ARROW, BACK, HELP, SPARK, BELL, MORE, GLOBE = "arrow", "back", "help", "spark", "bell", "more", "globe"
    HISTORY, RESTORE, LAYERS, TRASH, EYE = "history", "restore", "layers", "trash", "eye"


# ── formato (es-ES) ──────────────────────────────────────────────────────────

def fmt_int(n) -> str:
    return f"{int(n):,}".replace(",", i18n.numbers()[0])


def fmt_dec(x: float, d: int = 1) -> str:
    thousands, decimal, _pct = i18n.numbers()
    return f"{x:,.{d}f}".replace(",", "\x00").replace(".", decimal).replace("\x00", thousands)


def mid(text: str) -> str:
    """Un texto de etiqueta dentro de una frase: en minúscula, salvo en alemán (los sustantivos van en mayúscula)."""
    return text if i18n.current() == "de" else text.lower()


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
        return f"{int(h):02d}:{int(m):02d}:{whole:02d}{i18n.numbers()[1]}{millis:03d}"
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
    return f"{fmt_dec(x * 100, 1)}{i18n.numbers()[2]}%"


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
        ui=pick("Segoe UI Variable Text", "Segoe UI", "SF Pro Text", "Inter", "Ubuntu", "Noto Sans", "Cantarell", system),
        display=pick("Segoe UI Variable Display", "Segoe UI", "SF Pro Display", "Inter", "Ubuntu", "Noto Sans",
                     "Cantarell", system),
        mono=pick("Cascadia Mono", "Cascadia Code", "SF Mono", "Menlo", "Consolas", "JetBrains Mono", "Ubuntu Mono",
                  "DejaVu Sans Mono", fixed),
    )
    cjk = {"zh": ("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "Source Han Sans SC"),
           "ja": ("Yu Gothic UI", "Meiryo UI", "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Noto Sans CJK JP")}
    if i18n.current() in cjk:  # letra pensada para los caracteres chinos o japoneses
        FONT["ui"] = FONT["display"] = pick(*cjk[i18n.current()], FONT["ui"])


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
QRadioButton { spacing: 10px; background: transparent; }
QRadioButton::indicator { width: 16px; height: 16px; border-radius: 9px; border: 1px solid #3A4258; background: %(bg)s; }
QRadioButton::indicator:hover { border-color: %(accent)s; }
QRadioButton::indicator:checked { border: 5px solid %(accent)s; background: #FFFFFF; width: 8px; height: 8px; }
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
QListWidget#timeline { background: transparent; border: none; padding: 0; }
QListWidget#timeline::item { background: transparent; border: none; }
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

def install_qt_translations(app: QApplication) -> bool:
    """Botones y diálogos estándar de Qt (Sí/No, Cancelar…) en el idioma de la app."""
    lang = i18n.current()
    if lang == "en":
        return True
    name = {"zh": "zh_CN"}.get(lang, lang)
    translator = QTranslator(app)
    for folder in (QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath),
                   os.path.join(getattr(sys, "_MEIPASS", APP_DIR), "PyQt6", "Qt6", "translations")):
        if translator.load(f"qtbase_{name}", folder):
            app.installTranslator(translator)
            app._qt_translator = translator  # que no lo recoja el recolector de basura
            return True
    return False


class WheelGuard(QObject):
    """La rueda del ratón no cambia desplegables ni contadores: al pasar por encima
    se desplaza la página, como en el resto de la ventana."""

    def eventFilter(self, obj, e):
        if e.type() != QEvent.Type.Wheel or not isinstance(obj, (QComboBox, QAbstractSpinBox)):
            return False
        w = obj.parentWidget()
        while w is not None and not isinstance(w, QAbstractScrollArea):
            w = w.parentWidget()
        if w is not None:
            QApplication.sendEvent(w.viewport(), e)
        return True


class App(QApplication):
    """Deja que la ventana decida si se puede salir cuando lo pide el sistema."""
    quit_guard = None  # función que devuelve True si se puede salir

    def __init__(self, argv):
        super().__init__(argv)
        self._wheel_guard = WheelGuard(self)
        self.installEventFilter(self._wheel_guard)

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
        self._px = self.VALUE_PX

    VALUE_PX = 23  # tamaño de la cifra (QSS QLabel#tileValue)

    def set(self, value: str, sub: str = "", color: str | None = None) -> None:
        self.value.setText(value)
        self.sub.setText(sub)
        if color != self._color:
            self._color = color
            self._style()
        self._fit()

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._fit()

    def _fit(self) -> None:
        """La cifra se encoge lo justo para caber (letras más anchas, como la china, o cifras largas)."""
        avail = self.width() - 32
        if avail <= 0:
            return
        f = QFont(FONT["display"])
        f.setWeight(QFont.Weight.DemiBold)
        px = self.VALUE_PX
        while px > 15:
            f.setPixelSize(px)
            if QFontMetricsF(f).horizontalAdvance(self.value.text()) <= avail:
                break
            px -= 1
        if px != self._px:
            self._px = px
            self._style()

    def _style(self) -> None:
        css = "" if self._px == self.VALUE_PX else f"font-size:{self._px}px;"
        self.value.setStyleSheet(css + (f"color:{self._color};" if self._color else ""))


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
        self.big, self.small, self.caption = pct(0), "", _("PREPARADO")
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

    def paintEvent(self, _e):  # noqa: N802
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

    def paintEvent(self, _e):  # noqa: N802
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
        p.drawText(QRectF(2, 0, w / 2, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, _("Velocidad").upper())
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
            p.drawText(area, Qt.AlignmentFlag.AlignCenter, _("La gráfica aparece al empezar la copia"))
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
        p.drawText(QRectF(area.left() + 4, py - 17, 200, 15), Qt.AlignmentFlag.AlignLeft, _("pico {speed}").format(speed=fmt_speed(peak)))
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
        self.name = ElideLabel(_("en espera…"))
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
            self.name.setText(_("en espera…"))
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
        self.setHeaderLabels([_("CARPETA"), _("ESTADO")])
        self.setUniformRowHeights(True)
        self.setAnimated(True)
        self.setIndentation(20)
        self.setIconSize(QSize(16, 16))
        # varias a la vez con Ctrl o Mayús: «Marcar» y «Desmarcar» actúan solo sobre la selección
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
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

    # No puede llamarse «reset»: QTreeWidget.clear() llama al reset() de Qt, y con ese nombre se
    # borraban las exclusiones a mano cada vez que se recargaba el árbol (al abrir la app, al
    # cambiar el origen…).
    def clear_exclusions(self) -> None:
        self.excluded.clear()
        self.refresh()
        self.changed.emit()

    # ── marcar y desmarcar en bloque ──

    def _under(self, key: str, rel: str) -> bool:
        return key == rel or key.startswith(rel + os.sep)

    def scope(self) -> list[QTreeWidgetItem]:
        """Carpetas seleccionadas que se pueden marcar (sin las que ya cuelgan de otra seleccionada)."""
        picked = [(it, engine.norm_rel(it.data(0, self.REL))) for it in self.selectedItems()
                  if it.flags() & Qt.ItemFlag.ItemIsUserCheckable]
        return [it for it, rel in picked if not any(o != rel and self._under(rel, o) for _it, o in picked)]

    def can_check(self, items: list[QTreeWidgetItem]) -> bool:
        """¿Queda algo desmarcado (la carpeta o algo dentro)?"""
        if not items:
            return bool(self.excluded)
        rels = [engine.norm_rel(it.data(0, self.REL)) for it in items]
        return any(self._under(k, r) for k in self.excluded for r in rels)

    def can_uncheck(self, items: list[QTreeWidgetItem]) -> bool:
        """¿Queda alguna marcada?"""
        if not items:
            root = self.invisibleRootItem()
            items = [root.child(i) for i in range(root.childCount())
                     if root.child(i).flags() & Qt.ItemFlag.ItemIsUserCheckable]
        return any(it.checkState(0) == Qt.CheckState.Checked for it in items)

    def set_checked(self, items: list[QTreeWidgetItem], checked: bool) -> None:
        """Marca o desmarca esas carpetas con todo lo que tienen dentro; sin ninguna, el árbol entero."""
        if not items:
            root = self.invisibleRootItem()
            items = [root.child(i) for i in range(root.childCount())
                     if root.child(i).flags() & Qt.ItemFlag.ItemIsUserCheckable]
            if checked:
                self.excluded.clear()
                items = []
        for it in items:
            rel = engine.norm_rel(it.data(0, self.REL))
            # lo de dentro sigue a la carpeta: no quedan exclusiones sueltas debajo
            self.excluded -= {k for k in self.excluded if self._under(k, rel)}
            if not checked:
                self.excluded.add(rel)
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
                status, color = _("Regla «{rule}»").format(rule=rule), C["warn"]
            else:
                item.setFlags(base | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Unchecked if manual else Qt.CheckState.Checked)
                status, color = (_("Excluida"), C["err"]) if manual else ("", C["text"])
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

    def enterEvent(self, _e):  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, _e):  # noqa: N802
        self._hover = False
        self.update()

    def focusInEvent(self, e):  # noqa: N802
        self._kbd = e.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
        super().focusInEvent(e)

    def focusOutEvent(self, e):  # noqa: N802
        self._kbd = False
        super().focusOutEvent(e)

    def paintEvent(self, _e):  # noqa: N802
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
            self.head.setToolTip(_("Plegar o desplegar"))
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

    def paintEvent(self, _e):  # noqa: N802
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
        self.btn.setToolTip(_("Elegir carpeta…"))
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
            self.info.setText(_("Sin carpeta"))
            self.disk.setValue(0)
            return
        root = engine.volume_root(path)
        try:
            if not engine.volume_present(path):
                raise OSError
            du = shutil.disk_usage(root)
        except OSError:
            self.info.setText(_("Servidor de red no disponible") if engine.is_network(path) else _("Unidad no conectada"))
            self.disk.setValue(0)
            return
        self.disk.setValue(int(du.used / du.total * 10000) if du.total else 0)
        note = "" if os.path.isdir(path) else ("  ·  " + _("se creará"))
        self.info.setText(_("{root}  ·  {free} libres de {total}{note}").format(root=engine.vol_name(root), free=fmt_bytes(du.free), total=fmt_bytes(du.total), note=note))


# ── ventana principal ────────────────────────────────────────────────────────

PILL = {
    "idle": (_("Listo"), C["muted"]), "ready": (_("Análisis listo"), C["accent2"]),
    "scanning": (_("Analizando"), C["accent2"]), "copying": (_("Sincronizando"), C["accent"]),
    "paused": (_("En pausa"), C["warn"]), "done": (_("Al día"), C["ok"]),
    "cancelled": (_("Cancelado"), C["warn"]), "failed": (_("Error"), C["err"]),
}
RUNNING = ("scanning", "copying", "paused")
PAGE_TREE, PAGE_CHANGES, PAGE_PROGRESS, PAGE_ISSUES, PAGE_RECOVER, PAGE_PLESK = range(6)

# Las versiones ocupan sitio en el disco de copia: se guardan un plazo y luego se quitan solas
# (y antes, si falta espacio para copiar). 30 días por defecto; 60 como mucho.
KEEP_OPTIONS = [(7, _("7 días")), (30, _("30 días")), (60, _("60 días"))]
KEEP_DEFAULT = 30


def keep_days_setting(value) -> int:
    """Plazo guardado en los ajustes. Los de antes (90 días, 1 año, siempre) pasan a 60 días."""
    try:
        days = int(value)
    except (TypeError, ValueError):
        return KEEP_DEFAULT
    if days in dict(KEEP_OPTIONS):
        return days
    return max(d for d, _t in KEEP_OPTIONS)
CONNECT_ACTIONS = {"ask": _("Preguntar si sincronizar"), "auto": _("Sincronizar sin preguntar"), "none": _("Solo avisar")}
KIND_INFO = {"n": (_("Nuevo"), C["ok"]), "c": (_("Modificado"), C["accent2"]),
             "m": (_("Movido"), C["accent"]), "d": (_("A versiones"), C["warn"])}
MODE_HINTS = {
    engine.MODE_MIRROR: _("El destino queda idéntico al origen. Lo modificado o borrado no se pierde: la versión "
                        "anterior se guarda en .devbackup/versiones del disco de copia."),
    engine.MODE_UPDATE: _("Copia lo nuevo y lo modificado pero nunca quita nada del destino, aunque lo borres en "
                        "el origen."),
}

SCRUB_OPTIONS = [(0, _("Desactivada")), (120, _("2 min")), (300, _("5 min")), (900, _("15 min")), (3600, _("1 hora")),
                 (-1, _("Hasta revisar todo"))]

INSTANCE = "DevBackupStudio-" + getpass.getuser()
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "DEV Backup Studio"
LAUNCH_AGENT = os.path.expanduser("~/Library/LaunchAgents/com.crazyramirez.devbackupstudio.plist")
# Linux (freedesktop): arranque con la sesión, entrada del menú de aplicaciones e icono
DESKTOP_ID = "dev-backup-studio"
_XDG_CONFIG = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
_XDG_DATA = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
AUTOSTART_DESKTOP = os.path.join(_XDG_CONFIG, "autostart", DESKTOP_ID + ".desktop")
MENU_DESKTOP = os.path.join(_XDG_DATA, "applications", DESKTOP_ID + ".desktop")
MENU_ICON = os.path.join(_XDG_DATA, "icons", "hicolor", "256x256", "apps", DESKTOP_ID + ".png")


def fmt_ago(iso: str | None) -> str:
    if not iso:
        return _("nunca")
    try:
        sec = (datetime.now() - datetime.fromisoformat(iso)).total_seconds()
    except ValueError:
        return _("nunca")
    if sec < 90:
        return _("hace un momento")
    if sec < 3600:
        return _("hace {sec} min").format(sec=int(sec // 60))
    if sec < 86400:
        return _("hace {sec} h").format(sec=int(sec // 3600))
    days = int(sec // 86400)
    return _("ayer") if days == 1 else _n("hace {n} día", "hace {n} días", days).format(n=days)


def app_command() -> list[str]:
    """Orden que abre la app: el .AppImage en Linux (sys.executable está en un montaje temporal)."""
    if FROZEN:
        return [updater.appimage_path() or sys.executable]
    exe = sys.executable
    if IS_WIN:
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        exe = pyw if os.path.exists(pyw) else exe
    return [exe, os.path.join(APP_DIR, "backup_studio.py")]


def autostart_command() -> list[str]:
    return app_command() + ["--tray"]


def _desktop_exec(args: list[str]) -> str:
    """Línea Exec= de un archivo .desktop: entre comillas lo que lo necesite (especificación freedesktop)."""
    special = set(" \t\n\"'\\><~|&;$*?#()`")
    out = []
    for a in args:
        a = a.replace("%", "%%")
        if a and not special.intersection(a):
            out.append(a)
            continue
        inner = "".join("\\" + c if c in "\"`$\\" else c for c in a)
        out.append(('"' + inner + '"').replace("\\", "\\\\"))  # y la barra se escapa otra vez en el valor
    return " ".join(out)


def _desktop_entry(args: list[str], **extra: str) -> str:
    lines = ["[Desktop Entry]", "Type=Application", f"Name={APP_NAME}",
             "Comment=" + _("Copia de seguridad y sincronización de carpetas"),
             f"Exec={_desktop_exec(args)}", f"Icon={DESKTOP_ID}", "Terminal=false",
             "Categories=Utility;System;Archiving;", f"StartupWMClass={APP_NAME}"]
    lines += [f"{k.replace('_', '-')}={v}" for k, v in extra.items()]
    return "\n".join(lines) + "\n"


def _write_if_changed(path: str, text: str) -> None:
    try:
        with open(path, encoding="utf-8") as fh:
            if fh.read() == text:
                return
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def install_linux_desktop() -> None:
    """Linux: entrada en el menú de aplicaciones con su icono, apuntando a donde está ahora el
    .AppImage (si se mueve, se corrige al abrirlo). También pone al día el arranque con la sesión."""
    if not IS_LINUX:
        return
    try:
        if not os.path.exists(MENU_ICON):
            os.makedirs(os.path.dirname(MENU_ICON), exist_ok=True)
            app_icon_pixmap(256).save(MENU_ICON, "PNG")
        if FROZEN and updater.appimage_path():
            _write_if_changed(MENU_DESKTOP, _desktop_entry(app_command(), Keywords="backup;sync;copia;"))
        if os.path.exists(AUTOSTART_DESKTOP):
            set_autostart(True)
    except OSError:
        pass


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
    elif IS_LINUX:
        if on:
            _write_if_changed(AUTOSTART_DESKTOP, _desktop_entry(
                autostart_command(), NoDisplay="true", X_GNOME_Autostart_enabled="true"))
        else:
            try:
                os.remove(AUTOSTART_DESKTOP)
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
    """Avisa cuando se conecta una unidad o vuelve a responder una carpeta de red.

    Consultar los discos cada 2 s no cuesta nada; las carpetas de red se comprueban con una conexión
    de un segundo como mucho, y por eso todo va en un hilo aparte: la ventana nunca espera a la red.
    """
    arrived = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._extra: frozenset[str] = frozenset()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="discos", daemon=True)
        self._thread.start()

    def set_network_roots(self, roots) -> None:
        """Carpetas de red sin letra de unidad («\\\\nube\\DEV_Backup_Studio\\») que también hay que vigilar."""
        self._extra = frozenset(roots)

    def stop(self) -> None:
        self._stop.set()

    def _run(self):
        extra = self._extra
        known = engine.mounted_volumes(extra=extra)
        while not self._stop.wait(2.0):
            try:
                if IS_LINUX:
                    # la carpeta de la nube no sigue montada tras reiniciar: se monta al responder
                    for root in self._extra:
                        cloudlink.ensure_mounted(root)
                now = engine.mounted_volumes(extra=self._extra)
            except Exception:  # noqa: BLE001 - el vigilante no se puede morir
                continue
            # una carpeta de red que se acaba de elegir no es un disco que se acaba de conectar
            fresh = self._extra - extra
            extra = self._extra
            for root in sorted(now - known - fresh):
                self.arrived.emit(root)
            known = now


class ChangesModel(QAbstractTableModel):
    HEAD = [_("TIPO"), _("RUTA"), _("TAMAÑO")]

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
            return _("{path}\nantes en: {extra}").format(path=path, extra=extra) if extra else path
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
    rows += [("d", p[dcut:].lstrip("\\/"), size, "") for p, size, _m in job.deletes]
    return rows


def estimate_seconds(root: str, s: engine.Stats) -> float | None:
    """Tiempo previsto con el modelo aprendido en la última copia a este disco (datos + archivos)."""
    last = engine.last_history(root)
    if last and isinstance(last.get("modelo"), dict):
        est = engine.Estimator(last["modelo"], network=engine.is_network(root))
        return est.remaining(s.plan_bytes, s.plan_files, s.moved_files + s.del_files)
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
        self.title = QLabel(_("Tu disco de copia está conectado"))
        self.title.setObjectName("h1")
        label = engine.volume_info(self.root)[0] or _("Disco de copia")
        last = engine.last_history(self.root)
        where = f" ({engine.vol_name(root)})" if IS_WIN else ""
        sub = QLabel(_("{label}{where}  ·  última copia {v}  ·  {integrity_text}").format(label=label, where=where, v=fmt_ago(last and last.get('fin')), integrity_text=win.integrity_text()))
        sub.setObjectName("muted")
        col.addWidget(self.title)
        col.addWidget(sub)
        head.addLayout(col, 1)
        lay.addLayout(head)

        self.status = QLabel(_("Buscando cambios en {path}…").format(path=win.src_card.path()))
        self.status.setObjectName("muted")
        lay.addWidget(self.status)
        self.busy = thin_bar(4)
        self.busy.setRange(0, 0)
        lay.addWidget(self.busy)

        grid = QGridLayout()
        grid.setSpacing(10)
        self.t_new = Tile(_("Nuevos"), G.ADD, C["ok"])
        self.t_chg = Tile(_("Modificados"), G.SYNC, C["accent2"])
        self.t_mov = Tile(_("Movidos"), G.NEXT, C["accent"])
        self.t_del = Tile(_("A versiones"), G.FLAG, C["warn"])
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
        self.b_later = QPushButton(_("Ahora no"))
        self.b_view = QPushButton(("  " + _("Ver cambios")))
        self.b_view.setIcon(glyph_icon(G.TREE, C["text"], 14))
        self.b_sync = QPushButton(("  " + _("Sincronizar ahora")))
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
        self.t_mov.set(fmt_int(s.moved_files), fmt_bytes(s.moved_bytes) + (" " + _("sin recopiar")))
        self.t_del.set(fmt_int(s.del_files), _("borrados del origen"))

    def _poll(self):
        job = self.win.job
        if job is None:
            return
        s = job.stats
        if job.phase == "scanning":
            self._fill(s)
            self.status.setText(_("Buscando cambios…  {files_found} archivos revisados").format(files_found=fmt_int(s.files_found)))
        elif job.phase == "ready":
            self._timer.stop()
            self.busy.hide()
            self._fill(s)
            note = job.index_note
            if not s.has_changes:
                self.title.setText(_("Todo está al día"))
                self.status.setText(_("No hay nada que copiar desde la última vez  ·  {files_found} archivos revisados en "
                                      "{scan_end}").format(files_found=fmt_int(s.files_found), scan_end=fmt_clock(s.scan_end - s.scan_start, ms=True)))
                self.b_view.hide()
                if self.win.scrub_seconds():
                    # sin cambios, el botón principal sirve para avanzar la revisión de integridad
                    self.b_sync.setText(("  " + _("Comprobar integridad")))
                    self.b_sync.setIcon(glyph_icon(G.SHIELD, "#FFFFFF", 14))
                    self.b_sync.setEnabled(True)
                    self.b_sync.setDefault(True)
                    self.b_later.setText(_("Cerrar"))
                else:
                    self.b_sync.hide()
                    self.b_later.setText(_("Cerrar"))
            else:
                self.status.setText(_("{ops_total} cambios listos  ·  {plan_bytes} por copiar  ·  {skip_files} archivos sin "
                                      "cambios").format(ops_total=fmt_int(s.ops_total), plan_bytes=fmt_bytes(s.plan_bytes), skip_files=fmt_int(s.skip_files)))
                eta = estimate_seconds(self.root, s)
                if eta is not None:
                    note += ("  ·  " + _("tiempo previsto ≈ {v}").format(v=fmt_human(max(1, eta))))
                self.b_sync.setEnabled(True)
                self.b_view.setEnabled(True)
                self.b_sync.setDefault(True)
                self.b_sync.setFocus()
                mass = job.mass_deletion()
                if mass:
                    warn = (_("El origen está vacío: sincronizar dejaría la copia vacía") if mass["empty"] else
                            _n("Atención: se apartarían {n} archivo de la copia ({pct})",
                               "Atención: se apartarían {n} archivos de la copia ({pct})", mass["files"]).format(
                                n=fmt_int(mass["files"]), pct=pct(mass["ratio"])))
                    note = warn + "  ·  " + note
                    self.note.setStyleSheet(f"color:{C['warn']};")
            self.note.setText(note)
        elif job.phase in ("cancelled", "failed"):
            self._timer.stop()
            self.busy.hide()
            self.status.setText((_("No se pudo completar el análisis.") + " ") + (job.fatal or ""))
            self.b_later.setText(_("Cerrar"))

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


class MassDeleteDialog(QDialog):
    """Antes de apartar a versiones una parte grande de la copia: qué pasaría y qué hacer.

    «choice» queda en «cancel» (no sincronizar), «view» (ver la lista) o «go» (sincronizar igualmente).
    """

    def __init__(self, win: "MainWindow", job: engine.BackupJob, info: dict):
        super().__init__(win)
        self.choice = "cancel"
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.setMinimumWidth(660)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 24, 26, 22)
        lay.setSpacing(16)

        head = QHBoxLayout()
        head.setSpacing(16)
        head.addWidget(glyph_label(G.WARN, C["warn"], 34), 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(6)
        keep = mid(win.keep_combo.currentText())
        if info["empty"]:
            title = _("El origen está vacío")
            text = _("No hay ningún archivo en {src}. Si el disco o la carpeta de origen no está bien conectado, "
                     "sincronizar dejaría la copia vacía.").format(src=job.src)
        else:
            title = _n("Esta sincronización apartaría {n} archivo de la copia",
                       "Esta sincronización apartaría {n} archivos de la copia", info["files"]).format(n=fmt_int(info["files"]))
            text = _("Es el {pct} de lo que hay en el destino ({size}). Pasaría a versiones: se puede recuperar "
                     "durante {keep} desde «Recuperar», pero la copia dejaría de tenerlo.").format(
                pct=pct(info["ratio"]), size=fmt_bytes(info["bytes"]), keep=keep)
        t = QLabel(title)
        t.setObjectName("h1")
        t.setWordWrap(True)
        sub = QLabel(text)
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        col.addWidget(t)
        col.addWidget(sub)
        head.addLayout(col, 1)
        lay.addLayout(head)

        folders = info["folders"]
        if folders:
            box = QFrame()
            box.setObjectName("hint")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(16, 12, 16, 12)
            bl.setSpacing(8)
            cap = QLabel(_("Ya no están en el origen").upper())
            cap.setObjectName("cardTitle")
            bl.addWidget(cap)
            for name, n, size in folders[:6]:
                row = QHBoxLayout()
                row.setSpacing(10)
                row.addWidget(glyph_label(G.FOLDER if name else G.DOC, C["folder"] if name else C["muted"], 15))
                label = ElideLabel(name or _("Archivos sueltos de la raíz"))
                row.addWidget(label, 1)
                count = QLabel(_n("{n} archivo", "{n} archivos", n).format(n=fmt_int(n)) + "  ·  " + fmt_bytes(size))
                count.setObjectName("faint")
                row.addWidget(count)
                bl.addLayout(row)
            if len(folders) > 6:
                more = QLabel(_n("y {n} carpeta más", "y {n} carpetas más", len(folders) - 6).format(n=fmt_int(len(folders) - 6)))
                more.setObjectName("faint")
                bl.addWidget(more)
            lay.addWidget(box)

        tip = QLabel(_("Si no lo has borrado tú a propósito, no sincronices: comprueba que el origen está bien "
                       "conectado. Con el modo «Solo añadir y actualizar» nunca se quita nada del destino."))
        tip.setObjectName("faint")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.b_view = QPushButton("  " + _("Ver la lista"))
        self.b_view.setIcon(glyph_icon(G.TREE, C["text"], 14))
        self.b_no = QPushButton(_("No sincronizar"))
        self.b_go = QPushButton("  " + _("Sincronizar igualmente"))
        self.b_go.setObjectName("danger")
        self.b_go.setIcon(glyph_icon(G.PLAY, C["text"], 14))
        buttons.addWidget(self.b_view)
        buttons.addStretch(1)
        buttons.addWidget(self.b_no)
        buttons.addWidget(self.b_go)
        for b in (self.b_view, self.b_no, self.b_go):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setAutoDefault(False)
        self.b_no.setDefault(True)  # lo seguro es lo que pasa con Intro
        self.b_no.setFocus()
        lay.addLayout(buttons)
        self.b_view.clicked.connect(lambda: self._pick("view"))
        self.b_no.clicked.connect(lambda: self._pick("cancel"))
        self.b_go.clicked.connect(lambda: self._pick("go"))

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        set_dark_titlebar(self)

    def _pick(self, choice: str):
        self.choice = choice
        if choice == "go":
            self.accept()
        else:
            self.reject()


# ── recuperar: lo apartado a versiones, por fecha ───────────────────────────

def qlocale() -> QLocale:
    return QLocale(i18n.current())


def qdatetime(d: datetime) -> QDateTime:
    return QDateTime(QDate(d.year, d.month, d.day), QTime(d.hour, d.minute, d.second))


def day_label(d: datetime) -> str:
    """«Hoy», «Ayer» o la fecha larga en el idioma de la app."""
    today = datetime.now().date()
    if d.date() == today:
        return _("Hoy")
    if d.date() == today - timedelta(days=1):
        return _("Ayer")
    text = qlocale().toString(QDate(d.year, d.month, d.day), QLocale.FormatType.LongFormat)
    return text[:1].upper() + text[1:]


def group_label(d: datetime) -> str:
    """Grupo de la línea de tiempo: hoy, ayer, esta semana o el mes."""
    days = (datetime.now().date() - d.date()).days
    if days <= 0:
        return _("Hoy")
    if days == 1:
        return _("Ayer")
    if days < 7:
        return _("Esta semana")
    if i18n.current() in ("zh", "ja"):
        return f"{d.year}年{d.month}月"
    month = qlocale().standaloneMonthName(d.month)
    return f"{month[:1].upper()}{month[1:]} {d.year}"


def short_day(d: datetime) -> str:
    """Día corto con el de la semana: «lun 16 nov», «Mon 16 Nov», «11月16日 (月)»."""
    q = QDate(d.year, d.month, d.day)
    if i18n.current() in ("zh", "ja"):
        return f"{d.month}月{d.day}日 ({qlocale().toString(q, 'ddd')})"
    if i18n.current() == "de":
        return qlocale().toString(q, "ddd, d. MMM")
    return qlocale().toString(q, "ddd d MMM")


def short_stamp(d: datetime) -> str:
    return qlocale().toString(qdatetime(d), QLocale.FormatType.ShortFormat)


def _show_items_linux(path: str) -> bool:
    """Linux: pide al gestor de archivos (Nautilus, Dolphin, Nemo…) que enseñe el archivo seleccionado."""
    try:
        from PyQt6.QtDBus import QDBus, QDBusConnection, QDBusMessage
    except ImportError:
        return False
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return False
    msg = QDBusMessage.createMethodCall("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                                        "org.freedesktop.FileManager1", "ShowItems")
    msg.setArguments([[QUrl.fromLocalFile(path).toString()], ""])
    reply = bus.call(msg, QDBus.CallMode.Block, 3000)
    return reply.type() == QDBusMessage.MessageType.ReplyMessage


def reveal_path(path: str) -> None:
    """Abre la carpeta con el archivo seleccionado (Explorador o Finder)."""
    try:
        if IS_WIN:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            return
        if IS_MAC:
            subprocess.Popen(["open", "-R", path])
            return
        if IS_LINUX and _show_items_linux(path):
            return
    except OSError:
        pass
    QDesktopServices.openUrl(QUrl.fromLocalFile(path if os.path.isdir(path) else os.path.dirname(path)))


class TimelineDelegate(QStyledItemDelegate):
    """Fechas como una línea de tiempo: punto, hora, qué hay y cuánto le queda."""
    KIND, TITLE, SUB, SIDE = (Qt.ItemDataRole.UserRole + 10 + i for i in range(4))

    def sizeHint(self, option, index):  # noqa: N802
        return QSize(200, 34 if index.data(self.KIND) == "head" else 60)

    def paint(self, p: QPainter, option, index):
        kind = index.data(self.KIND)
        r = QRectF(option.rect)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if kind == "head":
            f = QFont(FONT["ui"])
            f.setPixelSize(10)
            f.setWeight(QFont.Weight.ExtraBold)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.6)
            p.setFont(f)
            p.setPen(QColor(C["faint"]))
            p.drawText(r.adjusted(16, 13, -8, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                       (index.data(self.TITLE) or "").upper())
            p.restore()
            return
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        card = r.adjusted(4, 3, -4, -3)
        if selected or hover:
            path = QPainterPath()
            path.addRoundedRect(card, 10, 10)
            fill = QColor(C["accent"])
            fill.setAlpha(46 if selected else 0)
            if not selected:
                fill = QColor(255, 255, 255, 10)
            p.fillPath(path, fill)
            if selected:
                edge = QColor(C["accent"])
                edge.setAlpha(130)
                p.setPen(QPen(edge, 1))
                p.drawPath(path)
        x = card.left() + 20
        cy = card.top() + 21
        if kind == "all":
            p.drawPixmap(QPointF(x - 9, cy - 9), glyph_pixmap(G.LAYERS, C["accent2"] if selected else C["muted"], 18))
        else:
            line = QColor(C["line2"])
            p.setPen(QPen(line, 2))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom() + 1))
            if selected:
                halo = QColor(C["accent2"])
                halo.setAlpha(55)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(halo)
                p.drawEllipse(QPointF(x, cy), 9, 9)
                p.setBrush(QColor(C["accent2"]))
                p.drawEllipse(QPointF(x, cy), 5, 5)
            else:
                p.setPen(QPen(QColor(C["muted"] if hover else C["line2"]), 2))
                p.setBrush(QColor(C["card"]))
                p.drawEllipse(QPointF(x, cy), 5, 5)
        left = x + 18
        side = index.data(self.SIDE) or ""
        fs = QFont(FONT["ui"])
        fs.setPixelSize(11)
        side_w = QFontMetricsF(fs).horizontalAdvance(side) + 6 if side else 0
        width = card.right() - 12 - left - side_w
        ft = QFont(FONT["ui"])
        ft.setPixelSize(13)
        ft.setWeight(QFont.Weight.DemiBold)
        p.setFont(ft)
        p.setPen(QColor(C["text"]))
        title = QFontMetricsF(ft).elidedText(index.data(self.TITLE) or "", Qt.TextElideMode.ElideRight, width)
        p.drawText(QRectF(left, card.top() + 9, width, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        p.setFont(fs)
        p.setPen(QColor(C["muted"]))
        sub = QFontMetricsF(fs).elidedText(index.data(self.SUB) or "", Qt.TextElideMode.ElideRight,
                                           card.right() - 12 - left)
        p.drawText(QRectF(left, card.top() + 30, card.right() - 12 - left, 18),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, sub)
        if side:
            p.setPen(QColor(C["faint"]))
            p.drawText(QRectF(card.right() - 12 - side_w, card.top() + 9, side_w, 20),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, side)
        p.restore()


class EmptyState(QWidget):
    """Mensaje centrado con icono: sin versiones, sin disco, cargando…"""

    def __init__(self, glyph: str, busy: bool = False):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(30, 30, 30, 30)
        v.setSpacing(10)
        v.addStretch(1)
        self.icon = glyph_label(glyph, C["faint"], 46)
        v.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addSpacing(6)
        self.title = QLabel()
        self.title.setObjectName("bannerTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setWordWrap(True)
        self.text = QLabel()
        self.text.setObjectName("muted")
        self.text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text.setWordWrap(True)
        self.text.setMaximumWidth(520)
        v.addWidget(self.title)
        # centrado con márgenes elásticos y no con AlignHCenter: con la alineación, Qt le da al texto
        # su ancho preferido y no el alto que necesita al partirse en varias líneas, y se corta
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.text, 100)
        row.addStretch(1)
        v.addLayout(row)
        if busy:
            bar = thin_bar(4)
            bar.setRange(0, 0)
            bar.setFixedWidth(220)
            v.addSpacing(6)
            v.addWidget(bar, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addStretch(2)
        for lbl in (self.title, self.text):
            lbl.installEventFilter(self)

    def eventFilter(self, obj, e):  # noqa: N802
        # el alto que necesita el texto partido en líneas con su ancho de verdad (los layouts de Qt, y
        # más dentro de un QStackedWidget, a veces le dan el de una sola línea y lo cortan)
        if e.type() == QEvent.Type.Resize and isinstance(obj, QLabel):
            self._fit(obj)
        return False

    @staticmethod
    def _fit(lbl: QLabel) -> None:
        need = lbl.heightForWidth(lbl.width()) if lbl.width() > 0 else -1
        if need > 0 and lbl.minimumHeight() != need:
            lbl.setMinimumHeight(need)

    def set(self, title: str, text: str = "") -> "EmptyState":
        self.title.setText(title)
        self.text.setText(text)
        self.text.setVisible(bool(text))
        for lbl in (self.title, self.text):
            self._fit(lbl)
        return self


class RecoverPage(QWidget):
    """«Recuperar»: lo que la sincronización apartó a versiones, por fecha, como una máquina del tiempo.

    Recuperar copia la versión de vuelta (no la mueve) y nunca sobrescribe: si ya hay un archivo
    distinto con ese nombre, la versión se guarda al lado con «(recuperado …)» en el nombre.
    """
    loaded = pyqtSignal(int, object, object)  # generación, fechas (None = sin disco), {ruta: ¿existe en el origen?}
    restore_progress = pyqtSignal(int, int)
    restored = pyqtSignal(object, bool)       # versions.RestoreResult, ¿al origen?
    snapshot_removed = pyqtSignal(str)         # error, o "" si fue bien
    ALL = "\x00todo"
    ROLE = Qt.ItemDataRole.UserRole
    PREVIEW_MAX = 512 * 1024 * 1024

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.snaps: list[versions.Snapshot] = []
        self.by_name: dict[str, versions.Snapshot] = {}
        self.exists: dict[str, bool] = {}
        self.stale = True
        self.busy = False
        self._gen = 0
        self._key = None
        self._kind: str | None = None
        self._all: list[list] = []      # lo de la fecha elegida: [ruta, tamaño, fecha_ns, Snapshot, nº de fechas]
        self._rows: list[list] = []     # lo que enseña el árbol (tras filtrar)
        self._cancel = threading.Event()
        self._ic_folder = glyph_icon(G.FOLDER, C["folder"], 16)
        self._ic_gone = glyph_icon(G.DOC, C["warn"], 16)
        self._ic_prev = glyph_icon(G.DOC, C["accent2"], 16)
        shutil.rmtree(self.preview_dir(), ignore_errors=True)  # vistas previas de otra sesión
        self._build()
        self.loaded.connect(self._on_loaded)
        self.restore_progress.connect(self._on_progress)
        self.restored.connect(self._on_restored)
        self.snapshot_removed.connect(self._on_snapshot_removed)

    @staticmethod
    def preview_dir() -> str:
        return os.path.join(tempfile.gettempdir(), APP_NAME, "vista")

    # ── construcción ──
    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        bar = QFrame()
        bar.setObjectName("hint")
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 10, 10, 10)
        h.setSpacing(10)
        h.addWidget(glyph_label(G.HISTORY, C["accent2"], 16))
        msg = QLabel(_("Lo que la sincronización apartó de la copia, por fecha: lo que borraste del origen y la "
                       "versión anterior de lo que cambiaste. Recuperar lo devuelve al origen sin sobrescribir nada."))
        msg.setObjectName("muted")
        msg.setWordWrap(True)
        h.addWidget(msg, 1)
        self.b_reload = QPushButton()
        self.b_reload.setObjectName("icon")
        self.b_reload.setIcon(glyph_icon(G.REFRESH, C["text"], 14))
        self.b_reload.setFixedSize(38, 34)
        self.b_reload.setToolTip(_("Volver a leer las versiones del disco de copia"))
        self.b_reload.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_reload.clicked.connect(lambda: self.reload(force=True))
        h.addWidget(self.b_reload)
        v.addWidget(bar)

        self.result = Banner()
        self.result.hide()
        v.addWidget(self.result)

        body = QHBoxLayout()
        body.setSpacing(14)
        left = Card(_("Fechas"), G.CLOCK, margins=(8, 14, 8, 8))
        left.body.setSpacing(6)
        left.setFixedWidth(278)
        self.timeline = QListWidget()
        self.timeline.setObjectName("timeline")
        self.timeline.setItemDelegate(TimelineDelegate(self.timeline))
        self.timeline.setMouseTracking(True)
        self.timeline.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.timeline.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.timeline.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.timeline.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.timeline.customContextMenuRequested.connect(self._timeline_menu)
        self.timeline.currentItemChanged.connect(lambda *_a: self._show_selected())
        left.body.addWidget(self.timeline, 1)
        self.usage = QLabel("")
        self.usage.setObjectName("faint")
        self.usage.setWordWrap(True)
        self.usage.setContentsMargins(10, 4, 10, 2)
        left.body.addWidget(self.usage)
        body.addWidget(left)

        right = QVBoxLayout()
        right.setSpacing(10)
        head = QVBoxLayout()
        head.setSpacing(2)
        head.setContentsMargins(4, 2, 0, 0)
        self.title = ElideLabel("", Qt.TextElideMode.ElideRight)
        self.title.setObjectName("bannerTitle")
        self.sub = ElideLabel("", Qt.TextElideMode.ElideRight)
        self.sub.setObjectName("faint")
        head.addWidget(self.title)
        head.addWidget(self.sub)
        right.addLayout(head)

        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.kind_group = QButtonGroup(self)
        self.kind_buttons: dict[str | None, QPushButton] = {}
        for key, text in ((None, _("Todo")), ("d", _("Borrados")), ("p", _("Versiones anteriores"))):
            b = QPushButton(text)
            b.setObjectName("chip")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setProperty("label", text)
            b.setToolTip({None: "", "d": _("Ya no están en el origen"),
                          "p": _("Siguen en el origen, pero con otro contenido: esto es como eran antes")}[key])
            self.kind_group.addButton(b)
            self.kind_buttons[key] = b
            b.clicked.connect(lambda _c=False, k=key: self._set_kind(k))
            filters.addWidget(b)
        self.kind_buttons[None].setChecked(True)
        filters.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText(_("Buscar archivo…"))
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(170)
        self.search.setMaximumWidth(280)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(220)
        self._search_timer.timeout.connect(self._apply_filter)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())
        filters.addWidget(self.search)
        right.addLayout(filters)

        self.stack = QStackedWidget()
        self.loading = EmptyState(G.HISTORY, busy=True).set(_("Leyendo versiones…"))
        self.empty = EmptyState(G.HISTORY)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([_("NOMBRE"), _("ESTADO"), _("TAMAÑO"), _("MODIFICADO")])
        self.tree.setUniformRowHeights(True)
        self.tree.setAnimated(True)
        self.tree.setIndentation(20)
        self.tree.setIconSize(QSize(16, 16))
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        self.tree.itemSelectionChanged.connect(self.update_actions)
        self.tree.itemDoubleClicked.connect(lambda it, _c: self._open() if it.data(0, self.ROLE)[0] == "file" else None)
        hdr = self.tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col, width in ((1, 190), (2, 92), (3, 150)):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            self.tree.setColumnWidth(col, width)
        for w in (self.loading, self.empty, self.tree):
            self.stack.addWidget(w)
        right.addWidget(self.stack, 1)

        actions = QFrame()
        actions.setObjectName("hint")
        ah = QHBoxLayout(actions)
        ah.setContentsMargins(16, 8, 10, 8)
        ah.setSpacing(8)
        self.sel_label = ElideLabel("", Qt.TextElideMode.ElideRight)
        self.sel_label.setObjectName("muted")
        ah.addWidget(self.sel_label, 1)
        self.progress = thin_bar(6)
        self.progress.setFixedWidth(170)
        self.progress.hide()
        ah.addWidget(self.progress)
        self.b_cancel = QPushButton(_("Detener"))
        self.b_cancel.hide()
        self.b_cancel.clicked.connect(self._cancel.set)
        ah.addWidget(self.b_cancel)

        def button(text, glyph, tip, slot, name=None, color=C["text"]):
            b = QPushButton(f"  {text}" if text else "")
            b.setIcon(glyph_icon(glyph, color, 14))
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if name:
                b.setObjectName(name)
            if not text:
                b.setFixedSize(38, 36)
                b.setAccessibleName(tip)
            b.clicked.connect(slot)
            ah.addWidget(b)
            return b

        self.b_open = button("", G.EYE, _("Ver: abre una copia temporal; la versión guardada no cambia"), self._open,
                             "icon")
        self.b_reveal = button("", G.OPEN, _("Mostrar la versión en su carpeta del disco de copia"), self._reveal,
                               "icon")
        self.b_to = button(_("Recuperar en…"), G.FOLDER, _("Elige otra carpeta para dejar ahí una copia"),
                           lambda: self._restore(ask=True))
        self.b_restore = button(_("Recuperar"), G.RESTORE, _("Devuelve lo seleccionado a su sitio en el origen"),
                                lambda: self._restore(), "primary", "#FFFFFF")
        self.b_restore.setMinimumWidth(150)
        right.addWidget(actions)
        body.addLayout(right, 1)
        v.addLayout(body, 1)
        self.update_actions()

    # ── carga ──
    def target(self) -> tuple[str, str, str] | None:
        dst = self.win.dst_card.path()
        if not dst:
            return None
        return engine.volume_root(dst), os.path.normpath(dst), self.win.src_card.path()

    def show_page(self):
        if self.stale or self._key != self.target():
            self.reload()
        else:
            self.update_actions()

    def mark_stale(self):
        self.stale = True
        if self.isVisible():
            self.reload()

    @staticmethod
    def cache_path(dst: str) -> str:
        """Lista de archivos de cada fecha ya leída: lo apartado en una fecha no cambia."""
        key = hashlib.sha1(os.path.normcase(os.path.normpath(dst)).encode("utf-8")).hexdigest()[:16]
        return os.path.join(DATA_DIR, "estado", f"versiones-{key}.json")

    def reload(self, force: bool = False):
        """Vuelve a leer las fechas. Con «force», también las ya conocidas (no se fía de la caché)."""
        target = self.target()
        self.stale = False
        if target != self._key:
            self.result.hide()  # el resultado de recuperar era de otra copia
        self._key = target
        self._gen += 1
        gen = self._gen
        if target is None:
            self._on_loaded(gen, None, None)
            return
        if not self.snaps:
            self.stack.setCurrentWidget(self.loading)
        self.b_reload.setEnabled(False)

        running = self.win._state in RUNNING  # la fecha más reciente puede estar escribiéndose

        def work():
            meta, dst, src = target
            try:
                if not engine.volume_present(dst):
                    self.loaded.emit(gen, None, None)
                    return
                cache_file = self.cache_path(dst)
                cache: dict = {}
                if not force:
                    try:
                        with open(cache_file, encoding="utf-8") as fh:
                            data = json.load(fh)
                        cache = data if isinstance(data, dict) else {}
                    except (OSError, ValueError):
                        cache = {}
                snaps = versions.load(meta, dst, cache=cache)
                if running and cache:
                    cache.pop(max(cache), None)
                try:
                    os.makedirs(os.path.dirname(cache_file), exist_ok=True)
                    with open(cache_file + ".tmp", "w", encoding="utf-8") as fh:
                        json.dump(cache, fh, ensure_ascii=False, separators=(",", ":"))
                    os.replace(cache_file + ".tmp", cache_file)
                except OSError:
                    pass
                exists: dict[str, bool] = {}
                if src and os.path.isdir(src):
                    for snap in snaps:
                        for rel, _size, _m in snap.files:
                            if rel not in exists:
                                exists[rel] = os.path.lexists(engine.lp(os.path.join(src, rel)))
                self.loaded.emit(gen, snaps, exists)
            except Exception:  # noqa: BLE001 - la página no se puede quedar cargando
                self.loaded.emit(gen, [], {})

        threading.Thread(target=work, name="versiones", daemon=True).start()

    def _current_key(self) -> str | None:
        it = self.timeline.currentItem()
        return it.data(self.ROLE) if it is not None else None

    def _on_loaded(self, gen: int, snaps, exists):
        if gen != self._gen:
            return
        self.b_reload.setEnabled(True)
        keep = self._current_key()
        if snaps is None:
            self.snaps, self.by_name, self.exists = [], {}, {}
            self.timeline.clear()
            self.usage.setText("")
            self._all, self._rows = [], []
            self.title.setText(_("Recuperar"))
            self.sub.setText("")
            if self.target() is None:
                self.empty.set(_("Elige una carpeta de destino"),
                               _("Las versiones se guardan en el disco de copia, junto al destino."))
            else:
                self.empty.set(_("Conecta el disco de copia"),
                               _("Las versiones se guardan en el propio disco de copia. Conéctalo (o enciende tu "
                                 "nube) para verlas y recuperarlas."))
            self.stack.setCurrentWidget(self.empty)
            self.update_actions()
            return
        self.snaps, self.exists = snaps, exists
        self.by_name = {s.name: s for s in snaps}
        total = sum(s.size for s in snaps)
        keep_text = mid(self.win.keep_combo.currentText())
        self.usage.setText(_("{size} en versiones · se guardan {keep}").format(size=fmt_bytes(total), keep=keep_text)
                           if snaps else _("Se guardan {keep}").format(keep=keep_text))
        self._fill_timeline()
        if not snaps:
            self._all, self._rows = [], []
            self.tree.clear()
            self.title.setText(_("Recuperar"))
            self.sub.setText("")
            self.empty.set(_("Aún no hay nada que recuperar"),
                           _("Cuando sincronices y algo se borre del origen o cambie, la versión de la copia se aparta "
                             "aquí, por fecha, durante {keep}. Desde aquí la recuperas con un clic.").format(keep=keep_text))
            self.stack.setCurrentWidget(self.empty)
            self.update_actions()
            return
        row = 0
        for i in range(self.timeline.count()):
            if self.timeline.item(i).data(self.ROLE) == keep and keep is not None:
                row = i
                break
        self.timeline.setCurrentRow(row)
        self._show_selected()

    def _fill_timeline(self):
        d = TimelineDelegate
        self.timeline.blockSignals(True)
        self.timeline.clear()
        files = len({rel for s in self.snaps for rel, _size, _m in s.files})
        if self.snaps:
            it = QListWidgetItem()
            it.setData(self.ROLE, self.ALL)
            it.setData(d.KIND, "all")
            it.setData(d.TITLE, _("Todas las fechas"))
            it.setData(d.SUB, _n("{n} archivo", "{n} archivos", files).format(n=fmt_int(files)) + "  ·  "
                       + fmt_bytes(sum(s.size for s in self.snaps)))
            it.setToolTip(_("La versión más reciente de cada archivo apartado"))
            self.timeline.addItem(it)
        keep = self.win.keep_combo.currentData() or KEEP_DEFAULT
        last_group = None
        now = datetime.now()
        for s in self.snaps:
            group = group_label(s.stamp)
            if group != last_group:
                last_group = group
                head = QListWidgetItem()
                head.setData(d.KIND, "head")
                head.setData(d.TITLE, group)
                head.setFlags(Qt.ItemFlag.NoItemFlags)
                self.timeline.addItem(head)
            it = QListWidgetItem()
            it.setData(self.ROLE, s.name)
            it.setData(d.KIND, "snap")
            days = (now.date() - s.stamp.date()).days
            when = s.stamp.strftime("%H:%M")
            if days >= 2:
                when = short_day(s.stamp) + "  ·  " + when
            it.setData(d.TITLE, when)
            it.setData(d.SUB, _n("{n} archivo", "{n} archivos", len(s.files)).format(n=fmt_int(len(s.files)))
                       + "  ·  " + fmt_bytes(s.size))
            left = keep - (now - s.stamp).days
            it.setData(d.SIDE, _("{n} d").format(n=max(0, left)))
            it.setToolTip(_n("Se quita sola dentro de {n} día (plazo de {keep} días)",
                             "Se quita sola dentro de {n} días (plazo de {keep} días)", max(0, left)).format(
                n=max(0, left), keep=keep))
            self.timeline.addItem(it)
        self.timeline.blockSignals(False)

    # ── lo que se enseña ──
    def _status(self, rel: str) -> str:
        return "p" if self.exists.get(rel) else "d"

    def _show_selected(self):
        key = self._current_key()
        if key is None or not self.snaps:
            return
        if key == self.ALL:
            seen: dict[str, list] = {}
            for s in self.snaps:  # de la más reciente a la más antigua
                for rel, size, mtime in s.files:
                    if rel in seen:
                        seen[rel][4] += 1
                    else:
                        seen[rel] = [rel, size, mtime, s, 1]
            self._all = list(seen.values())
            self.title.setText(_("Todas las fechas"))
            self.sub.setText(_("La versión más reciente de cada archivo · {n} · {size}").format(
                n=_n("{n} archivo", "{n} archivos", len(self._all)).format(n=fmt_int(len(self._all))),
                size=fmt_bytes(sum(e[1] for e in self._all))))
            self.tree.headerItem().setText(3, _("APARTADO"))
        else:
            s = self.by_name[key]
            self._all = [[rel, size, mtime, s, 1] for rel, size, mtime in s.files]
            self.title.setText(day_label(s.stamp) + "  ·  " + s.stamp.strftime("%H:%M"))
            self.sub.setText(_("Apartado al sincronizar · {n} · {size}").format(
                n=_n("{n} archivo", "{n} archivos", len(s.files)).format(n=fmt_int(len(s.files))),
                size=fmt_bytes(s.size)))
            self.tree.headerItem().setText(3, _("MODIFICADO"))
        counts = {"d": 0, "p": 0}
        for e in self._all:
            counts[self._status(e[0])] += 1
        for key2, b in self.kind_buttons.items():
            n = len(self._all) if key2 is None else counts[key2]
            b.setText(f"{b.property('label')}  {fmt_int(n)}")
        self._apply_filter()

    def _set_kind(self, kind: str | None):
        self._kind = kind
        self._apply_filter()

    def _apply_filter(self):
        text = self.search.text().strip().lower()
        kind = self._kind
        rows = [e for e in self._all
                if (not text or text in e[0].lower()) and (kind is None or self._status(e[0]) == kind)]
        self._rows = rows
        if not self._all:
            return
        if not rows:
            self.tree.clear()
            self.empty.set(_("Nada coincide"), _("Prueba con otra búsqueda u otro filtro."))
            self.stack.setCurrentWidget(self.empty)
        else:
            self._build_tree(rows, expand=bool(text) or kind is not None)
            self.stack.setCurrentWidget(self.tree)
        self.update_actions()

    def _build_tree(self, rows: list[list], expand: bool):
        all_dates = self._current_key() == self.ALL
        tree = self.tree
        tree.setUpdatesEnabled(False)
        tree.clear()
        root: dict = {}
        for e in rows:
            parts = e[0].split(os.sep)
            node = root
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node.setdefault("\x00", []).append(e)
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        gone_c, prev_c, faint_c, muted_c = QColor(C["warn"]), QColor(C["accent2"]), QColor(C["faint"]), QColor(C["muted"])

        def add(parent: QTreeWidgetItem, node: dict, prefix: str) -> tuple[int, int, int]:
            size = gone = prev = 0
            for name in sorted((k for k in node if k != "\x00"), key=str.casefold):
                rel = os.path.join(prefix, name) if prefix else name
                it = QTreeWidgetItem(parent, [name, "", "", ""])
                it.setIcon(0, self._ic_folder)
                it.setData(0, self.ROLE, ("dir", rel))
                a, b, c = add(it, node[name], rel)
                parts = []
                if b:
                    parts.append(_n("{n} borrado", "{n} borrados", b).format(n=fmt_int(b)))
                if c:
                    parts.append(_n("{n} anterior", "{n} anteriores", c).format(n=fmt_int(c)))
                it.setText(1, "  ·  ".join(parts))
                it.setForeground(1, faint_c)
                it.setText(2, fmt_bytes(a))
                it.setTextAlignment(2, right)
                it.setForeground(2, muted_c)
                size, gone, prev = size + a, gone + b, prev + c
            for e in sorted(node.get("\x00", []), key=lambda e: os.path.basename(e[0]).casefold()):
                rel, fsize, mtime, snap, count = e
                st = self._status(rel)
                label = _("Borrado") if st == "d" else _("Versión anterior")
                if count > 1:
                    label += "  ·  " + _n("{n} fecha", "{n} fechas", count).format(n=count)
                when = short_stamp(snap.stamp) if all_dates else short_stamp(datetime.fromtimestamp(mtime / 1e9))
                it = QTreeWidgetItem(parent, [os.path.basename(rel), label, fmt_bytes(fsize), when])
                it.setIcon(0, self._ic_gone if st == "d" else self._ic_prev)
                it.setForeground(1, gone_c if st == "d" else prev_c)
                it.setTextAlignment(2, right)
                it.setForeground(2, muted_c)
                it.setForeground(3, muted_c)
                it.setToolTip(0, rel)
                it.setData(0, self.ROLE, ("file", e))
                size += fsize
                gone += st == "d"
                prev += st == "p"
            return size, gone, prev

        add(tree.invisibleRootItem(), root, "")
        if expand and len(rows) <= 3000 or len(rows) <= 300:
            tree.expandAll()
        else:
            for i in range(tree.topLevelItemCount()):
                tree.topLevelItem(i).setExpanded(True)
        tree.setUpdatesEnabled(True)

    # ── selección y acciones ──
    def _selected(self) -> list[tuple[list, str]]:
        """Archivos seleccionados (las carpetas, con todo lo suyo) y la carpeta desde la que cuelgan."""
        items = sorted(self.tree.selectedItems(), key=lambda it: (it.data(0, self.ROLE)[0] != "dir",
                                                                  str(it.data(0, self.ROLE)[1]).count(os.sep)))
        out, seen = [], set()
        for it in items:
            kind, val = it.data(0, self.ROLE)
            if kind == "file":
                found = [(val, os.path.dirname(val[0]))]
            else:
                prefix = val + os.sep
                found = [(e, os.path.dirname(val)) for e in self._rows if e[0].startswith(prefix)]
            for e, base in found:
                if e[0] not in seen:
                    seen.add(e[0])
                    out.append((e, base))
        return out

    def update_actions(self):
        if not hasattr(self, "b_restore"):
            return
        picked = self._selected()
        n = len(picked)
        items = self.tree.selectedItems()
        running = self.win._state in RUNNING if hasattr(self.win, "_state") else False
        src = self.win.src_card.path()
        src_ok = bool(src) and os.path.isdir(src)
        if self.busy:
            pass
        elif n:
            self.sel_label.setText(_n("{n} archivo seleccionado · {size}", "{n} archivos seleccionados · {size}", n).format(
                n=fmt_int(n), size=fmt_bytes(sum(e[1] for e, _b in picked))))
        else:
            self.sel_label.setText(_("Elige qué recuperar  ·  Ctrl o Mayús + clic para varios"))
        one_file = len(items) == 1 and items[0].data(0, self.ROLE)[0] == "file"
        self.b_open.setEnabled(one_file and not self.busy)
        self.b_reveal.setEnabled(len(items) == 1 and not self.busy)
        self.b_to.setEnabled(bool(n) and not self.busy)
        self.b_restore.setEnabled(bool(n) and src_ok and not running and not self.busy)
        if running:
            self.b_restore.setToolTip(_("Espera a que termine la sincronización"))
        elif not src_ok:
            self.b_restore.setToolTip(_("La carpeta de origen no está disponible: usa «Recuperar en…»"))
        else:
            self.b_restore.setToolTip(_("Devuelve lo seleccionado a su sitio en el origen"))

    def _version_file(self, e: list) -> str:
        return os.path.join(e[3].path, e[0])

    def _open(self):
        items = self.tree.selectedItems()
        if len(items) != 1 or items[0].data(0, self.ROLE)[0] != "file":
            return
        e = items[0].data(0, self.ROLE)[1]
        path = self._version_file(e)
        if e[1] > self.PREVIEW_MAX:
            reveal_path(path)
            return
        folder = os.path.join(self.preview_dir(), e[3].name)
        target = os.path.join(folder, os.path.basename(e[0]))
        try:
            os.makedirs(folder, exist_ok=True)
            if os.path.exists(target):
                os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
                os.remove(target)
            shutil.copy2(engine.lp(path), target)
            os.chmod(target, stat.S_IREAD)  # es una vista: de solo lectura
        except OSError as exc:
            QMessageBox.warning(self, APP_NAME, _("No se pudo abrir la versión.\n\n{error}").format(error=exc.strerror or exc))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(target))

    def _reveal(self):
        items = self.tree.selectedItems()
        if len(items) != 1:
            return
        kind, val = items[0].data(0, self.ROLE)
        if kind == "file":
            reveal_path(self._version_file(val))
            return
        prefix = val + os.sep
        e = next((e for e in self._rows if e[0].startswith(prefix)), None)
        if e is not None:
            reveal_path(os.path.join(e[3].path, val))

    def _restore(self, ask: bool = False):
        picked = self._selected()
        if not picked or self.busy:
            return
        src = self.win.src_card.path()
        to_dir = None
        if ask:
            start = src if src and os.path.isdir(src) else os.path.expanduser("~")
            to_dir = QFileDialog.getExistingDirectory(self, _("Recuperar en…"), start)
            if not to_dir:
                return
        items = []
        for (rel, size, mtime, snap, _c), base in picked:
            if to_dir is None:
                target = os.path.join(src, rel)
            else:
                target = os.path.join(to_dir, os.path.relpath(rel, base) if base else rel)
            items.append((os.path.join(snap.path, rel), target, size, mtime))
        self.busy = True
        self._cancel.clear()
        self.progress.setRange(0, max(1, len(items)))
        self.progress.setValue(0)
        self.progress.show()
        self.b_cancel.setVisible(len(items) > 20)
        self.sel_label.setText(_("Recuperando…"))
        self.result.hide()
        self.update_actions()
        to_origin = to_dir is None
        self._restore_dir = to_dir or src

        def work():
            step = max(1, len(items) // 200)

            def progress(i, n, _target):
                if i % step == 0 or i == n:
                    self.restore_progress.emit(i, n)
            try:
                res = versions.restore(items, cancel=self._cancel, progress=progress)
            except Exception as exc:  # noqa: BLE001
                res = versions.RestoreResult(failed=[("", str(exc))])
            self.restored.emit(res, to_origin)

        threading.Thread(target=work, name="recuperar", daemon=True).start()

    def _on_progress(self, i: int, n: int):
        self.progress.setRange(0, max(1, n))
        self.progress.setValue(i)
        self.sel_label.setText(_("Recuperando…  {i} / {n}").format(i=fmt_int(i), n=fmt_int(n)))

    def _on_restored(self, res: versions.RestoreResult, to_origin: bool):
        self.busy = False
        self.progress.hide()
        self.b_cancel.hide()
        total = res.done + res.same
        where = _("en el origen") if to_origin else _("en {dir}").format(dir=self._restore_dir)
        details = []
        if res.renamed:
            details.append(_n("{n} se guardó al lado, con «({tag} …)» en el nombre, porque ya había otro archivo "
                              "distinto con ese nombre", "{n} se guardaron al lado, con «({tag} …)» en el nombre, "
                              "porque ya había otros archivos distintos con ese nombre", res.renamed).format(
                n=fmt_int(res.renamed), tag=_("recuperado")))
        if res.same:
            details.append(_n("{n} ya estaba igual", "{n} ya estaban iguales", res.same).format(n=fmt_int(res.same)))
        if to_origin and res.done:
            details.append(_("pasarán a la copia en la próxima sincronización"))
        if res.failed:
            details.append(_n("{n} no se pudo recuperar", "{n} no se pudieron recuperar", len(res.failed)).format(
                n=fmt_int(len(res.failed))) + ": " + "; ".join(
                f"{os.path.basename(p) or p}: {m}" for p, m in res.failed[:3]))
        title = _n("{n} archivo recuperado {where}", "{n} archivos recuperados {where}", total).format(
            n=fmt_int(total), where=where)
        kind = "warn" if res.failed or self._cancel.is_set() else "ok"
        if self._cancel.is_set():
            title += "  ·  " + _("detenido")
        text = ". ".join(d[:1].upper() + d[1:] for d in details) + ("." if details else "")
        first = res.targets[0] if res.targets else None
        self.result.set(kind, title, text, G.DONE if kind == "ok" else G.WARN,
                        (_("Mostrar"), lambda: reveal_path(first)) if first else None)
        self.result.show()
        self.win._log(title + (("  ·  " + text) if text else ""), C["ok"] if kind == "ok" else C["warn"])
        if to_origin and res.done:
            self.win._invalidate_plan()
        self.reload()

    # ── menús ──
    def _tree_menu(self, pos: QPoint):
        if not self.tree.itemAt(pos):
            return
        menu = QMenu(self)
        names = {self.b_restore: _("Recuperar"), self.b_to: _("Recuperar en…"), self.b_open: _("Ver"),
                 self.b_reveal: _("Mostrar en su carpeta")}
        for b, text in names.items():
            a = menu.addAction(b.icon(), text)
            a.setEnabled(b.isEnabled())
            a.triggered.connect(b.click)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _timeline_menu(self, pos: QPoint):
        it = self.timeline.itemAt(pos)
        key = it.data(self.ROLE) if it is not None else None
        snap = self.by_name.get(key) if key else None
        if snap is None:
            return
        menu = QMenu(self)
        a_open = menu.addAction(glyph_icon(G.OPEN, C["text"], 14), _("Abrir la carpeta de esta fecha"))
        menu.addSeparator()
        a_del = menu.addAction(glyph_icon(G.TRASH, C["err"], 14), _("Eliminar estas versiones…"))
        a_del.setEnabled(not self.busy and self.win._state not in RUNNING)
        chosen = menu.exec(self.timeline.viewport().mapToGlobal(pos))
        if chosen is a_open:
            QDesktopServices.openUrl(QUrl.fromLocalFile(snap.path))
        elif chosen is a_del:
            self._delete_snapshot(snap)

    def _delete_snapshot(self, snap: versions.Snapshot):
        when = day_label(snap.stamp) + " " + snap.stamp.strftime("%H:%M")
        box = QMessageBox(QMessageBox.Icon.Warning, APP_NAME,
                          _("¿Eliminar para siempre las versiones de {when}?").format(when=when),
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
        box.setInformativeText(_("Son {n} ({size}). Libera ese espacio en el disco de copia, pero ya no se podrán "
                                 "recuperar. No se puede deshacer.").format(
            n=_n("{n} archivo", "{n} archivos", len(snap.files)).format(n=fmt_int(len(snap.files))),
            size=fmt_bytes(snap.size)))
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        meta = self._key[0] if self._key else engine.volume_root(self.win.dst_card.path())
        self.busy = True
        self.update_actions()
        self.stack.setCurrentWidget(self.loading)

        def work():
            try:
                versions.delete_snapshot(snap, meta)
                self.snapshot_removed.emit("")
            except OSError as exc:
                self.snapshot_removed.emit(exc.strerror or str(exc))

        threading.Thread(target=work, name="quitar-versiones", daemon=True).start()

    def _on_snapshot_removed(self, error: str):
        self.busy = False
        if error:
            QMessageBox.warning(self, APP_NAME, _("No se pudieron eliminar todas las versiones.\n\n{error}").format(error=error))
        self.reload(force=bool(error))  # a medias: lo que queda hay que leerlo de nuevo


class PairDialog(QDialog):
    """«Conectar con mi nube»: encuentra Personal Cloud Storage en la red y se conecta con un código.

    La web de la nube da un código de 6 cifras (Ajustes → «Conectar un ordenador»). Con él, la nube
    entrega la carpeta de red y sus credenciales; se guardan en el sistema y el destino queda puesto.
    """
    found = pyqtSignal(list)
    # «linked» y no «done»: un atributo «done» taparía QDialog.done(), que Qt llama al cerrar
    linked = pyqtSignal(str)
    failed = pyqtSignal(str, str)

    def __init__(self, win: "MainWindow"):
        super().__init__(win)
        self.win = win
        self.result_path = ""
        self._link: cloudlink.Link | None = None  # credenciales ya recibidas (el código se gasta)
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 24, 26, 22)
        lay.setSpacing(14)

        title = QLabel(_("Conectar con tu nube"))
        title.setObjectName("h1")
        lay.addWidget(title)
        intro = QLabel(_("En la web de tu nube abre Ajustes → «Copias de tus ordenadores» y pulsa «Conectar un "
                         "ordenador». Escribe aquí el código que aparece."))
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        lab_cloud = QLabel(_("Nube"))
        lab_cloud.setObjectName("faint")
        lay.addWidget(lab_cloud)
        self.cloud = QComboBox()
        self.cloud.setEditable(True)
        self.cloud.lineEdit().setPlaceholderText(_("Buscando tu nube en la red…"))
        lay.addWidget(self.cloud)

        lab_code = QLabel(_("Código"))
        lab_code.setObjectName("faint")
        lay.addWidget(lab_code)
        self.code = QLineEdit()
        self.code.setInputMask("999 999;_")
        self.code.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = self.code.font()
        font.setPointSize(26)
        font.setBold(True)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 4)
        self.code.setFont(font)
        self.code.setMinimumHeight(64)
        lay.addWidget(self.code)

        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.busy = thin_bar(4)
        self.busy.setRange(0, 0)
        self.busy.hide()
        lay.addWidget(self.busy)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.b_cancel = QPushButton(_("Cancelar"))
        self.b_go = QPushButton("  " + _("Conectar"))
        self.b_go.setObjectName("primary")
        self.b_go.setIcon(glyph_icon(G.CHECK, "#FFFFFF", 14))
        self.b_go.setDefault(True)
        for b in (self.b_cancel, self.b_go):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            buttons.addWidget(b)
        lay.addLayout(buttons)
        self.b_cancel.clicked.connect(self.reject)
        self.b_go.clicked.connect(self._go)
        self.code.textChanged.connect(self._update)
        self.cloud.editTextChanged.connect(self._update)
        self.found.connect(self._on_found)
        self.linked.connect(self._on_done)
        self.failed.connect(self._on_failed)
        self._update()
        self.code.setFocus()
        saved = str(win.settings.value("nube_host", "") or "")
        if saved:
            self.cloud.addItem(f"{saved.split('.')[0]}  ({saved})", saved)
            self.cloud.setCurrentIndex(0)
        def search():
            clouds = cloudlink.discover(known=[saved] if saved else [])
            try:
                self.found.emit(clouds)
            except RuntimeError:  # el diálogo se cerró antes de terminar la búsqueda
                pass

        threading.Thread(target=search, name="buscar-nube", daemon=True).start()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        set_dark_titlebar(self)

    def _digits(self) -> str:
        return "".join(c for c in self.code.text() if c.isdigit())

    def _host(self) -> str:
        data = self.cloud.currentData()
        if data and self.cloud.currentText() == self.cloud.itemText(self.cloud.currentIndex()):
            return data
        return cloudlink.normalize_host(self.cloud.currentText())

    def _update(self, *_args):
        ready = len(self._digits()) == 6 or (self._link is not None and self._link.host == self._host())
        self.b_go.setEnabled(ready and bool(self._host()) and not self.busy.isVisible())

    def _on_found(self, clouds: list):
        self.cloud.lineEdit().setPlaceholderText(_("Dirección de tu nube (p. ej. nube.local)"))
        if not clouds:
            if not self.cloud.currentText():
                self.status.setText(_("No encuentro tu nube en la red. Escribe su dirección: la que usas en el "
                                      "navegador, sin «https://»."))
            return
        typed = self.cloud.currentText()
        have = {str(self.cloud.itemData(i) or "").lower() for i in range(self.cloud.count())}
        for c in clouds:
            if c.host.lower() not in have:
                self.cloud.addItem(f"{c.name}  ({c.host})", c.host)
        if not typed:
            self.cloud.setCurrentIndex(0)
        self._update()

    def _go(self):
        host, code = self._host(), self._digits()
        self.busy.show()
        self.b_go.setEnabled(False)
        self.code.setEnabled(False)
        self.cloud.setEnabled(False)
        self.status.setText(_("Conectando con {host}…").format(host=host))

        def work():
            try:
                # el código se gasta al canjearlo: si lo que falló fue abrir la carpeta, se reintenta
                # con lo ya recibido, sin pedir otro código
                link = self._link if self._link and self._link.host == host else None
                if link is None:
                    link = cloudlink.claim(host, code, cloudlink.device_name())
                    self._link = link
                self.linked.emit(cloudlink.connect(link))
            except cloudlink.LinkError as exc:
                self.failed.emit(exc.kind, exc.detail)
            except Exception as exc:  # noqa: BLE001 - se enseña y se puede repetir
                self.failed.emit("connect", str(exc))

        threading.Thread(target=work, name="conectar-nube", daemon=True).start()

    def _on_failed(self, kind: str, detail: str):
        self.busy.hide()
        self.code.setEnabled(True)
        self.cloud.setEnabled(True)
        text = {
            "code": _("El código no es correcto o ha caducado. Pide uno nuevo en la web y vuelve a probar."),
            "unreachable": _("No se puede llegar a la nube. Comprueba la dirección y que este ordenador está en la "
                             "misma red."),
            "unavailable": _("La carpeta de red no está activada en tu nube."),
            "busy": _("Demasiados intentos. Espera unos minutos y vuelve a probar."),
            "connect": _("El código es correcto, pero no se pudo abrir la carpeta de red. Pulsa «Conectar» para "
                         "reintentar: no hace falta otro código."),
            "nogvfs": _("Este escritorio no puede abrir carpetas de red. Instala el paquete gvfs-backends "
                        "(Ubuntu, Debian) o gvfs-smb (Fedora, Arch) y pulsa «Conectar»: no hace falta otro código."),
        }.get(kind, kind)
        self.status.setText(text + (f"  ({detail})" if detail and kind in ("unreachable", "connect") else ""))
        if kind == "code":
            self.code.clear()
            self.code.setFocus()
        self._update()

    @property
    def link_host(self) -> str:
        return self._link.host if self._link else ""

    def _on_done(self, path: str):
        self.result_path = path
        self.accept()


# ── servidores Plesk ─────────────────────────────────────────────────────────

PLESK_BLUE = "#52BBE6"
PLESK_EXT_URL = f"https://github.com/{updater.REPO}/releases/latest"
PLESK_SCHEDULES = {"manual": N_("Solo a mano"), "daily": N_("Cada día"), "weekly": N_("Cada semana")}


def plesk_pixmap(px: int, dpr: float = 2.0) -> QPixmap:
    """El icono de Plesk (assets/plesk/plesk.png); si faltara, un cuadro azul con su «P»."""
    pm = QPixmap(int(px * dpr), int(px * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    src = QPixmap(os.path.join(plesklink.ASSETS, "plesk.png"))
    if not src.isNull():
        p.drawPixmap(QRectF(0, 0, px, px), src, QRectF(src.rect()))
    else:
        p.setBrush(QColor(PLESK_BLUE))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(0, 0, px, px), px * 0.22, px * 0.22)
        p.setPen(QColor("#FFFFFF"))
        p.setFont(_font(int(px * 0.62), QFont.Weight.Bold))
        p.drawText(QRectF(0, 0, px, px), Qt.AlignmentFlag.AlignCenter, "P")
    p.end()
    return pm


def plesk_icon(px: int = 16) -> QIcon:
    icon = QIcon()
    icon.addPixmap(plesk_pixmap(px), QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(plesk_pixmap(px), QIcon.Mode.Normal, QIcon.State.On)
    return icon


def plesk_error_text(exc: plesklink.PleskError | None) -> str:
    if exc is None:
        return ""
    d = exc.detail
    try:  # solo «space» y «serverspace» traen un número; los demás, texto
        size = fmt_bytes(int(float(d)))
    except (TypeError, ValueError):
        size = "—"
    return {
        "code": _("El código no es correcto o ha caducado. Pide uno nuevo en Plesk y vuelve a probar."),
        "unreachable": _("No se puede llegar al servidor ({detail}). Comprueba la dirección y que el puerto del panel "
                         "(8443) está abierto.").format(detail=d or "—"),
        "cert": _("El certificado del servidor no es el que se aceptó al conectarlo. Si lo has cambiado tú, vuelve a "
                  "conectar el servidor para aceptar el nuevo."),
        "auth": _("El servidor ya no reconoce este ordenador (se desconectó desde Plesk). Vuelve a conectarlo con un "
                  "código nuevo."),
        "noext": _("La extensión DEV Backup Studio no está instalada en ese Plesk, o está desactivada."),
        "old": _("La extensión de Plesk es de una versión antigua: actualízala en Extensiones → Mis extensiones."),
        "busy": _("Demasiados intentos. Espera unos minutos y vuelve a probar."),
        "server": _("Plesk no pudo terminar: {detail}").format(detail=d or "—"),
        "space": _("No cabe en el disco de copia: faltan {size}.").format(size=size),
        "serverspace": _("No hay sitio en el servidor Plesk para recibir la copia: faltan {size}.").format(size=size),
        "running": _("Ya hay una copia o restauración en marcha en ese servidor. Espera a que termine."),
        "signature": _("El Gestor de copias de Plesk no admite una copia hecha en otro servidor. Elige «Restaurar todo "
                       "el servidor», o súbela a mano en Plesk marcando «Subir archivos de copia sin firma válida»."),
        "verify": _("Lo descargado no coincide con el original (SHA-256 distinto). No se ha guardado nada; vuelve a "
                    "intentarlo."),
        "missing": _("Esa copia ya no está en el servidor."),
        "cancelled": _("Detenido."),
    }.get(exc.kind, d or exc.kind)


class PleskPairDialog(QDialog):
    """«Conectar un servidor Plesk»: dirección del panel + el código de 6 cifras que da la extensión."""
    cert_checked = pyqtSignal(bool, str)      # ¿de una autoridad conocida?, huella
    paired = pyqtSignal(object, str)          # plesklink.Server, token
    failed = pyqtSignal(str, str)             # tipo de error, detalle

    def __init__(self, parent: QWidget, url: str = ""):
        super().__init__(parent)
        self.server: plesklink.Server | None = None
        self.token = ""
        self._url = ""
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 24, 26, 22)
        lay.setSpacing(14)
        head = QHBoxLayout()
        head.setSpacing(12)
        logo = QLabel()
        logo.setPixmap(plesk_pixmap(34))
        head.addWidget(logo)
        title = QLabel(_("Conectar un servidor Plesk"))
        title.setObjectName("h1")
        head.addWidget(title, 1)
        lay.addLayout(head)
        steps = QLabel(
            "<ol style='margin-left:-22px'>"
            f"<li>{html.escape(_('En Plesk, abre Extensiones → Mis extensiones → Subir extensión y sube la extensión DEV Backup Studio.'))} "
            f"<a href='{PLESK_EXT_URL}' style='color:{C['accent2']}; text-decoration:none'>"
            f"{html.escape(_('Descargar la extensión'))}</a><br>"
            f"<span style='color:{C['faint']}'>"
            + html.escape(_("¿No ves «Subir extensión»? Plesk lo esconde: añade a su panel.ini (o con la extensión "
                            "Panel.ini Editor) [ext-catalog] y extensionUpload = true."))
            + "</span></li>"
            f"<li>{html.escape(_('Ábrela y pulsa «Conectar un ordenador».'))}</li>"
            f"<li>{html.escape(_('Escribe aquí la dirección del panel y el código que aparece.'))}</li></ol>")
        steps.setObjectName("muted")
        steps.setWordWrap(True)
        steps.setOpenExternalLinks(True)
        lay.addWidget(steps)

        lab = QLabel(_("Servidor"))
        lab.setObjectName("faint")
        lay.addWidget(lab)
        self.url = QLineEdit(url)
        self.url.setPlaceholderText(_("plesk.tudominio.com  (el puerto 8443 se pone solo)"))
        lay.addWidget(self.url)
        lab = QLabel(_("Código"))
        lab.setObjectName("faint")
        lay.addWidget(lab)
        self.code = QLineEdit()
        self.code.setInputMask("999 999;_")
        self.code.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = self.code.font()
        font.setPointSize(26)
        font.setBold(True)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 4)
        self.code.setFont(font)
        self.code.setMinimumHeight(64)
        lay.addWidget(self.code)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.status)
        self.busy = thin_bar(4)
        self.busy.setRange(0, 0)
        self.busy.hide()
        lay.addWidget(self.busy)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.b_cancel = QPushButton(_("Cancelar"))
        self.b_go = QPushButton("  " + _("Conectar"))
        self.b_go.setObjectName("primary")
        self.b_go.setIcon(glyph_icon(G.CHECK, "#FFFFFF", 14))
        self.b_go.setDefault(True)
        for b in (self.b_cancel, self.b_go):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            buttons.addWidget(b)
        lay.addLayout(buttons)
        self.b_cancel.clicked.connect(self.reject)
        self.b_go.clicked.connect(self._go)
        self.code.textChanged.connect(self._update)
        self.url.textChanged.connect(self._update)
        self.cert_checked.connect(self._on_cert)
        self.paired.connect(self._on_paired)
        self.failed.connect(self._on_failed)
        self._update()
        (self.code if url else self.url).setFocus()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        set_dark_titlebar(self)

    def _digits(self) -> str:
        return "".join(c for c in self.code.text() if c.isdigit())

    def _update(self, *_args):
        ok = len(self._digits()) == 6 and bool(plesklink.normalize_url(self.url.text()))
        self.b_go.setEnabled(ok and not self.busy.isVisible())

    def _set_busy(self, on: bool, text: str = ""):
        self.busy.setVisible(on)
        self.url.setEnabled(not on)
        self.code.setEnabled(not on)
        if text:
            self.status.setText(text)
        self._update()

    def _go(self):
        self._url = plesklink.normalize_url(self.url.text())
        self._set_busy(True, _("Conectando con {host}…").format(host=plesklink.url_host(self._url)))
        url = self._url

        def work():
            try:
                trusted, fp = plesklink.check_certificate(url)
                self.cert_checked.emit(trusted, fp)
            except plesklink.PleskError as exc:
                self.failed.emit(exc.kind, exc.detail)

        threading.Thread(target=work, name="plesk-certificado", daemon=True).start()

    def _on_cert(self, trusted: bool, fp: str):
        pin = ""
        if not trusted:
            if not fp:
                self._on_failed("unreachable", _("sin certificado"))
                return
            box = QMessageBox(QMessageBox.Icon.Question, APP_NAME, "", parent=self)
            box.setText(_("El certificado de {host} no es de una autoridad reconocida (lo normal con el certificado "
                          "propio de Plesk).").format(host=plesklink.url_host(self._url)))
            box.setInformativeText(_("Comprueba que esta huella es la misma que enseña la extensión junto al código:")
                                   + f"\n\n{plesklink.fmt_fingerprint(fp)}\n\n" + _("¿Confiar en este servidor?"))
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            if box.exec() != QMessageBox.StandardButton.Yes:
                self._set_busy(False, _("No se ha conectado: el certificado no se aceptó."))
                return
            pin = fp
        url, code, device = self._url, self._digits(), cloudlink.device_name()

        def work():
            try:
                srv, token = plesklink.pair(url, code, device, pin)
                self.paired.emit(srv, token)
            except plesklink.PleskError as exc:
                self.failed.emit(exc.kind, exc.detail)
            except Exception as exc:  # noqa: BLE001 - se enseña y se puede repetir
                self.failed.emit("server", str(exc))

        threading.Thread(target=work, name="plesk-emparejar", daemon=True).start()

    def _on_failed(self, kind: str, detail: str):
        self._set_busy(False, plesk_error_text(plesklink.PleskError(kind, detail)))
        if kind == "code":
            self.code.clear()
            self.code.setFocus()

    def _on_paired(self, srv: plesklink.Server, token: str):
        self.server, self.token = srv, token
        self.accept()


class PleskRestoreDialog(QDialog):
    """Elegir adónde y cómo restaurar una copia de Plesk guardada en el disco."""

    def __init__(self, parent: QWidget, backup: plesklink.StoredBackup, servers: list[plesklink.Server],
                 default: plesklink.Server | None, password: str):
        super().__init__(parent)
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.setMinimumWidth(600)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 24, 26, 22)
        lay.setSpacing(12)
        head = QHBoxLayout()
        head.setSpacing(12)
        logo = QLabel()
        logo.setPixmap(plesk_pixmap(34))
        head.addWidget(logo)
        col = QVBoxLayout()
        col.setSpacing(2)
        title = QLabel(_("Restaurar una copia de Plesk"))
        title.setObjectName("h1")
        origin = backup.info.get("server") or os.path.basename(os.path.dirname(backup.path))
        sub = QLabel(_("{server}  ·  {date}  ·  {size}").format(server=origin, date=short_stamp(backup.when),
                                                                size=fmt_bytes(backup.size)))
        sub.setObjectName("muted")
        col.addWidget(title)
        col.addWidget(sub)
        head.addLayout(col, 1)
        lay.addLayout(head)

        lab = QLabel(_("Servidor de destino"))
        lab.setObjectName("fieldLabel")
        lay.addWidget(lab)
        self.target = QComboBox()
        for s in servers:
            self.target.addItem(plesk_icon(16), f"{s.label}  ({plesklink.url_host(s.url)})", s.id)
        if default is not None:
            self.target.setCurrentIndex(max(0, self.target.findData(default.id)))
        lay.addWidget(self.target)

        lab = QLabel(_("Qué hacer"))
        lab.setObjectName("fieldLabel")
        lay.addWidget(lab)
        self.mode_import = QRadioButton(_("Subirla al Gestor de copias de seguridad de Plesk"))
        hint_import = QLabel(_("No cambia nada en el servidor. Después, en Plesk (Herramientas y configuración → Gestor "
                               "de copias de seguridad) eliges qué restaurar: todo, una suscripción, una base de "
                               "datos…"))
        self.mode_server = QRadioButton(_("Restaurar todo el servidor ahora"))
        hint_server = QLabel(_("Con «pleskrestore», la restauración oficial de Plesk: ajustes del servidor, "
                               "suscripciones, webs, bases de datos y correo. Lo que ya existe se sustituye por lo de "
                               "la copia. El panel puede dejar de responder un rato mientras tanto."))
        for w in (hint_import, hint_server):
            w.setObjectName("faint")
            w.setWordWrap(True)
            w.setContentsMargins(28, 0, 0, 4)
        lay.addWidget(self.mode_import)
        lay.addWidget(hint_import)
        lay.addWidget(self.mode_server)
        lay.addWidget(hint_server)
        self.mode_import.setChecked(True)
        self.confirm = QCheckBox(_("Entiendo que se sustituirá lo que hay ahora en el servidor"))
        self.confirm.setContentsMargins(28, 0, 0, 0)
        lay.addWidget(self.confirm)

        lab = QLabel(_("Contraseña de la copia"))
        lab.setObjectName("fieldLabel")
        lay.addWidget(lab)
        self.password = QLineEdit(password)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText(_("La que se usó al hacerla (para recuperar las contraseñas guardadas)"))
        lay.addWidget(self.password)
        if backup.info.get("password") is False:
            self.password.setPlaceholderText(_("Esta copia se hizo sin contraseña"))

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        b_cancel = QPushButton(_("Cancelar"))
        self.b_go = QPushButton("  " + _("Restaurar"))
        self.b_go.setObjectName("primary")
        self.b_go.setIcon(glyph_icon(G.RESTORE, "#FFFFFF", 14))
        for b in (b_cancel, self.b_go):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            buttons.addWidget(b)
        lay.addLayout(buttons)
        b_cancel.clicked.connect(self.reject)
        self.b_go.clicked.connect(self.accept)
        for w in (self.mode_import, self.mode_server, self.confirm):
            w.toggled.connect(self._update)
        self.target.currentIndexChanged.connect(self._update)
        self._update()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        set_dark_titlebar(self)

    def _update(self, *_a):
        server_mode = self.mode_server.isChecked()
        self.confirm.setVisible(server_mode)
        self.b_go.setText("  " + (_("Restaurar todo el servidor") if server_mode else _("Subir a Plesk")))
        self.b_go.setEnabled(self.target.count() > 0 and (not server_mode or self.confirm.isChecked()))

    @property
    def mode(self) -> str:
        return "server" if self.mode_server.isChecked() else "import"

    @property
    def target_id(self) -> str:
        return self.target.currentData() or ""


class PleskPage(QWidget):
    """«Plesk»: copias completas de servidores Plesk con su herramienta oficial, en PLESK/ del disco de copia.

    Una sola operación a la vez (copia o restauración), en un hilo; la página la lee con un temporizador.
    Las copias automáticas se miran cada 10 minutos y al conectar el disco de copia.
    """
    op_changed = pyqtSignal()      # desde el hilo: hay que guardar los ajustes del servidor
    unpaired = pyqtSignal(str, str)   # servidor, error al avisar a Plesk ("" si fue bien)
    ROLE = Qt.ItemDataRole.UserRole

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.secrets = plesklink.Secrets(os.path.join(DATA_DIR, "plesk.json"))
        self.servers: list[plesklink.Server] = []
        self.op: plesklink.Operation | None = None
        self.thread: threading.Thread | None = None
        self.guard = keepawake.Guard()
        self._finalized = True
        self._samples: deque = deque(maxlen=40)
        self._selected = ""       # id del servidor, o «disk:<carpeta>» para copias de servidores ya no conectados
        self._load()
        self._build()
        self.op_changed.connect(self._save)
        self.unpaired.connect(self._on_unpaired)
        self.timer = QTimer(self)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self._tick)
        self.due_timer = QTimer(self)
        self.due_timer.setInterval(10 * 60 * 1000)
        self.due_timer.timeout.connect(self.check_due)
        self.due_timer.start()
        QTimer.singleShot(90_000, self.check_due)
        self.refresh()

    # ── datos ──
    def _load(self):
        try:
            data = json.loads(self.win.settings.value("plesk_servers", "") or "[]")
        except (TypeError, ValueError):
            data = []
        self.servers = [s for s in (plesklink.Server.from_dict(d) for d in data) if s]

    def _save(self):
        self.win.settings.setValue("plesk_servers", json.dumps([s.to_dict() for s in self.servers], ensure_ascii=False))
        self.win.settings.sync()

    def server(self, sid: str) -> plesklink.Server | None:
        return next((s for s in self.servers if s.id == sid), None)

    def _password(self, folder: str, create: bool = False) -> str:
        """La contraseña de las copias va con la carpeta del servidor: si se quita el servidor, se conserva
        para poder restaurar sus copias."""
        key = f"plesk/pw/{folder}"
        pw = self.secrets.get(key)
        if not pw and create:
            pw = plesklink.new_password()
            self.secrets.set(key, pw)
        return pw

    def disk_root(self) -> str | None:
        """Raíz del disco de copia (donde va la carpeta PLESK), si está conectado."""
        dst = self.win.dst_card.path()
        if not dst or not engine.volume_present(dst):
            return None
        root = engine.volume_root(dst)
        return root if os.path.isdir(root) else None

    def _folder_of(self, key: str) -> str | None:
        root = self.disk_root()
        if not root:
            return None
        if key.startswith("disk:"):
            return os.path.join(plesklink.plesk_root(root), key[5:])
        srv = self.server(key)
        return os.path.join(plesklink.plesk_root(root), srv.folder_name) if srv else None

    @property
    def busy(self) -> bool:
        return self.op is not None and self.op.running

    # ── construcción ──
    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        bar = QFrame()
        bar.setObjectName("hint")
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 10, 10, 10)
        h.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(plesk_pixmap(18))
        h.addWidget(logo)
        msg = QLabel(_("Copia completa de tus servidores Plesk con su herramienta oficial: ajustes, suscripciones, "
                       "webs, bases de datos y correo. Se guarda en la carpeta PLESK del disco de copia y se restaura "
                       "como Plesk manda."))
        msg.setObjectName("muted")
        msg.setWordWrap(True)
        h.addWidget(msg, 1)
        self.b_add = QPushButton("  " + _("Conectar servidor…"))
        self.b_add.setIcon(glyph_icon(G.ADD, C["text"], 14))
        self.b_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_add.clicked.connect(lambda: self.pair())
        h.addWidget(self.b_add)
        v.addWidget(bar)

        self.pages = QStackedWidget()
        self.empty = EmptyState(G.GLOBE)
        self.empty.icon.setPixmap(plesk_pixmap(54))
        self.empty.icon.setFixedSize(54, 54)
        self.empty.set(_("Ningún servidor Plesk conectado"),
                       _("Instala la extensión DEV Backup Studio en tu Plesk, pulsa «Conectar un ordenador» y escribe "
                         "aquí el código que te da. Sin contraseñas ni acceso por SSH."))
        b = QPushButton("  " + _("Conectar un servidor Plesk…"))
        b.setObjectName("primary")
        b.setIcon(glyph_icon(G.ADD, "#FFFFFF", 14))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(lambda: self.pair())
        self.empty.layout().insertWidget(self.empty.layout().count() - 1, b, 0, Qt.AlignmentFlag.AlignHCenter)
        self.pages.addWidget(self.empty)

        main = QWidget()
        body = QHBoxLayout(main)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(14)
        left = Card(_("Servidores"), G.GLOBE, margins=(8, 14, 8, 8))
        left.setFixedWidth(278)
        self.list = QListWidget()
        self.list.setObjectName("timeline")
        self.list.setIconSize(QSize(26, 26))
        self.list.setSpacing(2)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.currentItemChanged.connect(lambda cur, _p: self._select(cur.data(self.ROLE) if cur else ""))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._list_menu)
        left.body.addWidget(self.list, 1)
        self.disk_note = QLabel("")
        self.disk_note.setObjectName("faint")
        self.disk_note.setWordWrap(True)
        self.disk_note.setContentsMargins(10, 4, 10, 2)
        left.body.addWidget(self.disk_note)
        body.addWidget(left)

        right = QVBoxLayout()
        right.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(10)
        head.setContentsMargins(4, 2, 0, 0)
        names = QVBoxLayout()
        names.setSpacing(2)
        self.title = ElideLabel("", Qt.TextElideMode.ElideRight)
        self.title.setObjectName("bannerTitle")
        self.sub = ElideLabel("", Qt.TextElideMode.ElideRight)
        self.sub.setObjectName("faint")
        names.addWidget(self.title)
        names.addWidget(self.sub)
        head.addLayout(names, 1)
        self.b_disconnect = QPushButton("  " + _("Desconectar…"))
        self.b_disconnect.setObjectName("danger")
        self.b_disconnect.setIcon(glyph_icon(G.CLOSE, C["muted"], 13))
        self.b_disconnect.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_disconnect.setToolTip(_("Este ordenador deja de estar conectado a ese servidor Plesk. Las copias ya "
                                       "guardadas se conservan."))
        self.b_disconnect.clicked.connect(self._disconnect_selected)
        head.addWidget(self.b_disconnect, 0, Qt.AlignmentFlag.AlignVCenter)
        right.addLayout(head)
        self.banner = Banner()
        right.addWidget(self.banner)
        self.progress = thin_bar(6)
        self.progress.hide()
        right.addWidget(self.progress)

        self.opts = QFrame()
        self.opts.setObjectName("hint")
        oh = QHBoxLayout(self.opts)
        oh.setContentsMargins(16, 8, 10, 8)
        oh.setSpacing(10)
        oh.addWidget(QLabel(_("Copia automática"), objectName="fieldLabel"))
        self.schedule = QComboBox()
        for key, text in PLESK_SCHEDULES.items():
            self.schedule.addItem(_(text), key)
        self.schedule.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.schedule.setMinimumWidth(130)
        self.schedule.setToolTip(_("Con la app abierta (o en la bandeja) y el disco de copia conectado"))
        oh.addWidget(self.schedule)
        oh.addSpacing(10)
        oh.addWidget(QLabel(_("Conservar"), objectName="fieldLabel"))
        self.keep = QComboBox()
        for n in plesklink.KEEP_OPTIONS:
            self.keep.addItem(_n("{n} copia", "{n} copias", n).format(n=n), n)
        self.keep.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.keep.setMinimumWidth(110)
        self.keep.setToolTip(_("Al terminar una copia se quitan las más antiguas"))
        oh.addWidget(self.keep)
        oh.addStretch(1)
        self.b_password = QPushButton()
        self.b_password.setObjectName("icon")
        self.b_password.setIcon(glyph_icon(G.SHIELD, C["text"], 14))
        self.b_password.setFixedSize(38, 36)
        self.b_password.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_password.setAccessibleName(_("Contraseña…"))
        self.b_password.setToolTip(_("Contraseña…") + "  ·  "
                                   + _("Plesk cifra con ella los datos sensibles de la copia y la pide al restaurar"))
        self.b_password.clicked.connect(self._show_password)
        oh.addWidget(self.b_password)
        for combo in (self.schedule, self.keep):  # el QSS añade relleno y flecha que sizeHint no cuenta
            fm = combo.fontMetrics()
            combo.setMinimumWidth(max(fm.horizontalAdvance(combo.itemText(i)) for i in range(combo.count())) + 64)
        self.schedule.currentIndexChanged.connect(self._on_options)
        self.keep.currentIndexChanged.connect(self._on_options)
        right.addWidget(self.opts)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([_("COPIA"), _("TAMAÑO"), _("PLESK"), _("COMPROBACIÓN")])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setIconSize(QSize(16, 16))
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        self.tree.itemSelectionChanged.connect(self._update_actions)
        self.tree.itemDoubleClicked.connect(lambda *_a: self._reveal())
        hdr = self.tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col, width in ((1, 100), (2, 130), (3, 190)):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            self.tree.setColumnWidth(col, width)
        self.tree_stack = QStackedWidget()
        self.no_backups = EmptyState(G.DRIVE)
        self.tree_stack.addWidget(self.tree)
        self.tree_stack.addWidget(self.no_backups)
        right.addWidget(self.tree_stack, 1)

        actions = QFrame()
        actions.setObjectName("hint")
        ah = QHBoxLayout(actions)
        ah.setContentsMargins(16, 8, 10, 8)
        ah.setSpacing(8)
        self.sel_label = ElideLabel("", Qt.TextElideMode.ElideRight)
        self.sel_label.setObjectName("muted")
        ah.addWidget(self.sel_label, 1)

        def button(text, glyph, tip, slot, name=None, color=C["text"]):
            b = QPushButton(f"  {text}" if text else "")
            b.setIcon(glyph_icon(glyph, color, 14))
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if name:
                b.setObjectName(name)
            if not text:
                b.setFixedSize(38, 36)
                b.setAccessibleName(tip)
            b.clicked.connect(slot)
            ah.addWidget(b)
            return b

        self.b_more = button("", G.MORE, _("Más acciones"), self._more_menu, "icon")
        self.b_reveal = button("", G.OPEN, _("Mostrar la copia en su carpeta del disco de copia"), self._reveal, "icon")
        self.b_stop = button(_("Detener"), G.STOP, _("Detiene la operación; lo ya descargado se aprovecha la próxima vez"),
                             self._stop)
        self.b_restore = button(_("Restaurar…"), G.RESTORE, _("Sube la copia elegida a un servidor Plesk y la restaura"),
                                self._restore)
        self.b_backup = button(_("Copiar ahora"), G.PLAY, _("Copia completa del servidor con pleskbackup"),
                               lambda: self.start_backup(self._selected), "primary", "#FFFFFF")
        self.b_backup.setMinimumWidth(160)
        right.addWidget(actions)
        body.addLayout(right, 1)
        self.pages.addWidget(main)
        v.addWidget(self.pages, 1)

    # ── lista de servidores ──
    def refresh(self):
        """Vuelve a pintar la lista (servidores conectados y carpetas del disco sin servidor) y lo elegido."""
        root = self.disk_root()
        on_disk = plesklink.list_servers_on_disk(plesklink.plesk_root(root)) if root else []
        known = {s.folder_name.lower() for s in self.servers}
        orphans = [f for f in on_disk if f.lower() not in known]
        self.pages.setCurrentIndex(1 if self.servers or orphans else 0)
        current = self._selected
        self.list.blockSignals(True)
        self.list.clear()
        for s in self.servers:
            it = QListWidgetItem(plesk_icon(26), f"{s.label}\n{self._status_line(s)}")
            it.setData(self.ROLE, s.id)
            it.setSizeHint(QSize(240, 52))
            self.list.addItem(it)
        for f in orphans:
            it = QListWidgetItem(glyph_icon(G.DRIVE, C["muted"], 22), f"{f}\n{_('Solo en el disco de copia')}")
            it.setData(self.ROLE, f"disk:{f}")
            it.setSizeHint(QSize(240, 52))
            self.list.addItem(it)
        keys = [self.list.item(i).data(self.ROLE) for i in range(self.list.count())]
        if current not in keys:
            current = keys[0] if keys else ""
        if current:
            self.list.setCurrentRow(keys.index(current))
        self.list.blockSignals(False)
        if root:
            self.disk_note.setText(_("Copias en {path}").format(path=plesklink.plesk_root(root)))
        else:
            self.disk_note.setText(_("Conecta el disco de copia para guardar o ver las copias."))
        self._select(current)

    def _status_line(self, s: plesklink.Server) -> str:
        if self.busy and self.op.server is s:
            return _("Restaurando…") if self.op.kind == "restore" else _("Copiando…")
        if s.last_error and (not s.last_ok or s.last_try > s.last_ok):
            return _("⚠ Falló la última copia")
        if s.last_ok:
            return _("Última copia {when}").format(when=fmt_ago(s.last_ok))
        return _("Aún sin copias")

    def _select(self, key: str):
        self._selected = key or ""
        srv = self.server(self._selected)
        if srv:
            self.title.setText(srv.label)
            bits = [srv.url]
            if srv.plesk_version:
                bits.append(f"Plesk {srv.plesk_version}")
            if srv.os:
                bits.append(srv.os)
            if srv.fingerprint:
                bits.append(_("certificado fijado"))
            self.sub.setText("  ·  ".join(bits))
            for w, val in ((self.schedule, srv.schedule), (self.keep, srv.keep)):
                w.blockSignals(True)
                w.setCurrentIndex(max(0, w.findData(val)))
                w.blockSignals(False)
            self.opts.setVisible(True)
        elif self._selected.startswith("disk:"):
            self.title.setText(self._selected[5:])
            self.sub.setText(_("Ya no está conectado: sus copias se pueden restaurar en otro servidor Plesk"))
            self.opts.setVisible(False)
        else:
            self.title.setText("")
            self.sub.setText("")
            self.opts.setVisible(False)
        self._load_backups()
        self._render()

    def _load_backups(self):
        self.tree.clear()
        folder = self._folder_of(self._selected) if self._selected else None
        backups = plesklink.list_backups(folder) if folder else []
        for b in backups:
            it = QTreeWidgetItem([f"{day_label(b.when)}  ·  {b.when.strftime('%H:%M')}", fmt_bytes(b.size),
                                  str(b.info.get("plesk_version") or "—"),
                                  _("SHA-256 comprobado") if b.sha256 else _("sin comprobar")])
            it.setIcon(0, plesk_icon(16))
            it.setIcon(3, glyph_icon(G.SHIELD if b.sha256 else G.WARN, C["ok"] if b.sha256 else C["warn"], 14))
            it.setData(0, self.ROLE, b)
            it.setToolTip(0, b.path)
            self.tree.addTopLevelItem(it)
        if backups:
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
            self.tree_stack.setCurrentWidget(self.tree)
        else:
            if folder is None and self._selected:
                self.no_backups.set(_("Disco de copia no conectado"),
                                    _("Conéctalo para ver las copias guardadas y para hacer copias nuevas."))
            else:
                self.no_backups.set(_("Aún no hay copias de este servidor"),
                                    _("Pulsa «Copiar ahora»: Plesk prepara la copia completa y se descarga aquí."))
            self.tree_stack.setCurrentWidget(self.no_backups)
        self._update_actions()

    def _chosen_backup(self) -> plesklink.StoredBackup | None:
        it = self.tree.currentItem()
        return it.data(0, self.ROLE) if it is not None and it.isSelected() else None

    def _update_actions(self):
        srv = self.server(self._selected)
        b = self._chosen_backup()
        busy = self.busy
        self.b_stop.setVisible(busy)
        self.b_backup.setVisible(not busy)
        self.b_backup.setEnabled(srv is not None)
        self.b_restore.setEnabled(not busy and b is not None and bool(self.servers))
        self.b_reveal.setEnabled(b is not None)
        self.b_more.setEnabled(bool(self._selected))
        self.b_add.setEnabled(not busy)
        self.b_disconnect.setVisible(srv is not None)
        self.b_disconnect.setEnabled(srv is not None and not (busy and self.op.server is srv))
        self.schedule.setEnabled(srv is not None)
        self.keep.setEnabled(srv is not None)
        n = self.tree.topLevelItemCount()
        total = sum(self.tree.topLevelItem(i).data(0, self.ROLE).size for i in range(n))
        self.sel_label.setText(_n("{n} copia guardada · {size}", "{n} copias guardadas · {size}", n).format(
            n=n, size=fmt_bytes(total)) if n else "")

    def _on_options(self, *_a):
        srv = self.server(self._selected)
        if srv is None:
            return
        srv.schedule = self.schedule.currentData()
        srv.keep = int(self.keep.currentData())
        self._save()
        self.refresh_summary()

    def refresh_summary(self):
        self.win._refresh_summaries()

    # ── conectar y quitar ──
    def pair(self, url: str = "", replace: plesklink.Server | None = None):
        if self.busy:
            return
        dlg = PleskPairDialog(self.win, url)
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.server is None:
            return
        srv = dlg.server
        old = replace or next((s for s in self.servers if s.url == srv.url), None)
        if old is not None:  # volver a conectar: se conserva lo elegido y lo hecho
            for name in ("schedule", "keep", "last_ok", "last_size", "pending_job", "pending_stamp"):
                setattr(srv, name, getattr(old, name))
            self.secrets.delete_prefix(f"plesk/{old.id}/")
            self.servers[self.servers.index(old)] = srv
        else:
            self.servers.append(srv)
        self.secrets.set(plesklink.token_key(srv.id), dlg.token)
        self._password(srv.folder_name, create=True)
        self._save()
        self._selected = srv.id
        self.refresh()
        self.win._log(_("Servidor Plesk conectado: {name} ({url})").format(name=srv.label, url=srv.url), C["ok"])
        if old is None and not plesklink.list_backups(self._folder_of(srv.id) or ""):
            ans = QMessageBox.question(self.win, APP_NAME, _("{name} está conectado. ¿Hacer ya su primera copia "
                                                              "completa?").format(name=srv.label))
            if ans == QMessageBox.StandardButton.Yes:
                self.start_backup(srv.id)

    def remove(self, srv: plesklink.Server, ask: bool = True):
        """Desconectar: Plesk borra la llave de este ordenador y la app olvida el servidor. Las copias
        del disco (y la contraseña para restaurarlas) se quedan."""
        if self.busy and self.op.server is srv:
            QMessageBox.information(self.win, APP_NAME, _("Espera a que termine la operación en curso con {name}, o "
                                                          "detenla.").format(name=srv.label))
            return
        if ask:
            box = QMessageBox(QMessageBox.Icon.Question, APP_NAME, "", parent=self.win)
            box.setText(_("¿Desconectar {name}?").format(name=srv.label))
            box.setInformativeText(_("Este ordenador deja de estar conectado a ese Plesk: no hará más copias de él y "
                                     "desaparece de la extensión. Las copias ya guardadas en el disco se conservan y se "
                                     "pueden seguir restaurando. Para volver a conectarlo hará falta un código nuevo."))
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
            box.button(QMessageBox.StandardButton.Yes).setText(_("Desconectar"))
            box.setDefaultButton(QMessageBox.StandardButton.Cancel)
            if box.exec() != QMessageBox.StandardButton.Yes:
                return
        token = self.secrets.get(plesklink.token_key(srv.id))
        name = srv.label

        def work():
            error = ""
            try:
                if token:
                    plesklink.Client(srv.url, token, srv.fingerprint, timeout=10).unpair()
            except plesklink.PleskError as exc:
                # si Plesk ya no lo reconocía, allí ya estaba desconectado
                error = "" if exc.kind == "auth" else plesk_error_text(exc)
            try:
                self.unpaired.emit(name, error)
            except RuntimeError:  # la ventana ya se cerró
                pass

        threading.Thread(target=work, name="plesk-desconectar", daemon=True).start()
        self.servers.remove(srv)
        self.secrets.delete_prefix(f"plesk/{srv.id}/")
        self._save()
        self.refresh()
        self.refresh_summary()

    def _disconnect_selected(self):
        srv = self.server(self._selected)
        if srv is not None:
            self.remove(srv)

    def _on_unpaired(self, name: str, error: str):
        if not error:
            self.win._log(_("Plesk: {name} desconectado").format(name=name), C["muted"])
            return
        self.win._log(_("Plesk: {name} desconectado aquí, pero no se pudo avisar al servidor: {error}").format(
            name=name, error=error), C["warn"])
        QMessageBox.warning(self.win, APP_NAME, _("{name} se ha desconectado en este ordenador, pero el servidor no "
                                                  "respondió. Quita también este ordenador en Plesk: extensión DEV "
                                                  "Backup Studio → Ordenadores conectados → Desconectar.").format(name=name))

    def _list_menu(self, pos):
        it = self.list.itemAt(pos)
        if it is None:
            return
        self.list.setCurrentItem(it)
        srv = self.server(it.data(self.ROLE))
        if srv is None:
            return
        menu = QMenu(self)
        menu.addAction(glyph_icon(G.GLOBE, C["text"], 14), _("Abrir Plesk en el navegador"),
                       lambda: QDesktopServices.openUrl(QUrl(srv.url)))
        menu.addAction(glyph_icon(G.REFRESH, C["text"], 14), _("Volver a conectar con un código…"),
                       lambda: self.pair(srv.url, replace=srv))
        menu.addSeparator()
        act = menu.addAction(glyph_icon(G.CLOSE, C["err"], 14), _("Desconectar…"), lambda: self.remove(srv))
        act.setEnabled(not (self.busy and self.op.server is srv))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def _show_password(self):
        srv = self.server(self._selected)
        if srv is None:
            return
        pw = self._password(srv.folder_name, create=True)
        box = QMessageBox(QMessageBox.Icon.Information, APP_NAME, "", parent=self.win)
        box.setText(_("Contraseña de las copias de {name}").format(name=srv.label))
        box.setInformativeText(_("Plesk cifra con ella las contraseñas que guarda la copia (de buzones, bases de "
                                 "datos, FTP…). La app la recuerda, pero anótala en un sitio seguro: si restauras desde "
                                 "otro ordenador o subes la copia a mano en Plesk, te la pedirá.") + f"\n\n{pw}")
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        copy_btn = box.addButton(_("Copiar"), QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is copy_btn:
            QApplication.clipboard().setText(pw)

    def _more_menu(self):
        srv = self.server(self._selected)
        menu = QMenu(self)
        if srv is not None:
            menu.addAction(glyph_icon(G.GLOBE, C["text"], 14), _("Abrir Plesk en el navegador"),
                           lambda: QDesktopServices.openUrl(QUrl(srv.url)))
            menu.addAction(glyph_icon(G.REFRESH, C["text"], 14), _("Volver a conectar con un código…"),
                           lambda: self.pair(srv.url, replace=srv))
            menu.addAction(glyph_icon(G.SHIELD, C["text"], 14), _("Contraseña…"), self._show_password)
        folder = self._folder_of(self._selected)
        if folder and os.path.isdir(folder):
            menu.addAction(glyph_icon(G.OPEN, C["text"], 14), _("Abrir la carpeta de las copias"),
                           lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(folder)))
        b = self._chosen_backup()
        if b is not None and not self.busy:
            menu.addSeparator()
            menu.addAction(glyph_icon(G.TRASH, C["err"], 14), _("Eliminar esta copia del disco…"),
                           lambda: self._delete_backup(b))
        if srv is not None and not self.busy:
            menu.addSeparator()
            menu.addAction(glyph_icon(G.CLOSE, C["err"], 14), _("Desconectar…"), lambda: self.remove(srv))
        menu.exec(self.b_more.mapToGlobal(QPoint(0, self.b_more.height() + 4)))

    def _tree_menu(self, pos):
        it = self.tree.itemAt(pos)
        if it is None:
            return
        it.setSelected(True)
        self.tree.setCurrentItem(it)
        b = it.data(0, self.ROLE)
        menu = QMenu(self)
        menu.addAction(glyph_icon(G.OPEN, C["text"], 14), _("Mostrar en su carpeta"), self._reveal)
        if not self.busy:
            menu.addAction(glyph_icon(G.RESTORE, C["text"], 14), _("Restaurar…"), self._restore)
            menu.addSeparator()
            menu.addAction(glyph_icon(G.TRASH, C["err"], 14), _("Eliminar esta copia del disco…"),
                           lambda: self._delete_backup(b))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _reveal(self):
        b = self._chosen_backup()
        if b is not None:
            reveal_path(b.path)

    def _delete_backup(self, b: plesklink.StoredBackup):
        ans = QMessageBox.question(self.win, APP_NAME, _("¿Eliminar la copia del {date} ({size}) del disco de copia? "
                                                          "No se puede deshacer.").format(date=short_stamp(b.when),
                                                                                          size=fmt_bytes(b.size)))
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            plesklink.delete_backup(b)
        except OSError as exc:
            QMessageBox.warning(self.win, APP_NAME, _("No se pudo eliminar:\n{exc}").format(exc=exc))
        self._load_backups()

    # ── copiar y restaurar ──
    def start_backup(self, sid: str, auto: bool = False) -> bool:
        srv = self.server(sid)
        if srv is None or self.busy:
            return False
        root = self.disk_root()
        if not root:
            if not auto:
                QMessageBox.information(self.win, APP_NAME, _("Conecta el disco de copia: las copias de Plesk se guardan en "
                                                              "su carpeta PLESK."))
            return False
        token = self.secrets.get(plesklink.token_key(srv.id))
        if not token:
            if not auto:
                self.pair(srv.url, replace=srv)
            return False
        folder = os.path.join(plesklink.plesk_root(root), srv.folder_name)
        op = plesklink.BackupRun(srv, token, self._password(srv.folder_name, create=True), folder, srv.keep)
        srv.last_try = datetime.now().isoformat(timespec="seconds")
        self._save()
        self._launch(op)
        self.win._log(_("Plesk: copia completa de {name}{auto}").format(
            name=srv.label, auto=(" " + _("(automática)")) if auto else ""), C["accent2"])
        if auto:
            self.win._notify(_("Copia de Plesk"), _("Copiando {name}…").format(name=srv.label))
        return True

    def _restore(self):
        b = self._chosen_backup()
        if b is None or self.busy or not self.servers:
            return
        folder = os.path.basename(os.path.dirname(b.path))
        default = self.server(self._selected) or next((s for s in self.servers if s.folder_name == folder), None)
        dlg = PleskRestoreDialog(self.win, b, self.servers, default, self._password(folder))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        srv = self.server(dlg.target_id)
        if srv is None:
            return
        if dlg.mode == "server":
            box = QMessageBox(QMessageBox.Icon.Warning, APP_NAME, "", parent=self.win)
            box.setText(_("¿Restaurar todo {name} con la copia del {date}?").format(name=srv.label,
                                                                                    date=short_stamp(b.when)))
            box.setInformativeText(_("Las suscripciones, webs, bases de datos, correo y ajustes que hay ahora en el "
                                     "servidor se sustituirán por los de la copia. No se puede deshacer desde aquí."))
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(QMessageBox.StandardButton.Cancel)
            if box.exec() != QMessageBox.StandardButton.Yes:
                return
        token = self.secrets.get(plesklink.token_key(srv.id))
        if not token:
            self.pair(srv.url, replace=srv)
            return
        password = dlg.password.text()
        if password and not self._password(folder):
            self.secrets.set(f"plesk/pw/{folder}", password)
        op = plesklink.RestoreRun(srv, token, b, password, dlg.mode)
        self._selected = srv.id
        self._launch(op)
        self.win._log(_("Plesk: restaurando en {name} la copia del {date} ({how})").format(
            name=srv.label, date=short_stamp(b.when),
            how=_("todo el servidor") if dlg.mode == "server" else _("al Gestor de copias")), C["accent2"])

    def _launch(self, op: plesklink.Operation):
        self.op = op
        self._finalized = False
        self._samples.clear()
        op.on_change = lambda _op: self.op_changed.emit()
        self.thread = threading.Thread(target=op.run, name=f"plesk-{op.kind}", daemon=True)
        self.thread.start()
        if self.win.awake_box.isChecked():
            self.guard.start(_("{app_name}: copia de Plesk en curso ({name})").format(app_name=APP_NAME,
                                                                                    name=op.server.label))
        self.timer.start()
        self.refresh()

    def _stop(self):
        if self.busy:
            self.op.cancel()
            self.b_stop.setEnabled(False)

    def stop_and_wait(self, timeout: float = 10.0):
        if self.busy:
            self.op.cancel()
            if self.thread:
                self.thread.join(timeout=timeout)
        self.guard.stop()

    def _tick(self):
        op = self.op
        if op is None:
            self.timer.stop()
            return
        now = time.monotonic()
        if op.phase in ("download", "upload"):
            self._samples.append((now, op.bytes_done))
        self._render()
        if not op.running and not self._finalized:
            self._finish(op)

    def _speed(self) -> float:
        if len(self._samples) < 2:
            return 0.0
        (t0, b0), (t1, b1) = self._samples[0], self._samples[-1]
        return (b1 - b0) / (t1 - t0) if t1 > t0 else 0.0

    def _render(self):
        op = self.op
        srv = self.server(self._selected)
        mine = op is not None and srv is not None and op.server is srv
        self.b_stop.setEnabled(bool(op and op.running and not op.stop.is_set()))
        if mine and op.running:
            elapsed = fmt_clock(time.monotonic() - op.started)
            frac = op.bytes_done / op.bytes_total if op.bytes_total else 0.0
            if op.phase == "connect":
                title, text, bar = _("Conectando con {name}…").format(name=srv.label), "", None
            elif op.phase == "server":
                title = _("Plesk está haciendo la copia completa del servidor")
                text, bar = (op.message or _("Preparando…")), None
            elif op.phase == "restore":
                title = (_("Plesk está restaurando el servidor") if getattr(op, "mode", "") == "server"
                         else _("Plesk está añadiendo la copia a su Gestor de copias"))
                text, bar = (op.message or _("Preparando…")), None
            elif op.phase == "verify":
                title = _("Comprobando el SHA-256")
                text, bar = f"{fmt_bytes(op.bytes_done)} / {fmt_bytes(op.bytes_total)}", frac
            else:
                speed = self._speed()
                left = (op.bytes_total - op.bytes_done) / speed if speed > 0 else None
                title = _("Descargando la copia") if op.phase == "download" else _("Subiendo la copia al servidor")
                text = "  ·  ".join(x for x in (
                    f"{fmt_bytes(op.bytes_done)} / {fmt_bytes(op.bytes_total)}  ({pct(frac)})",
                    fmt_speed(speed) if speed > 0 else "",
                    _("quedan {t}").format(t=fmt_clock(left)) if left is not None else "",
                    _("seguido desde {size}").format(size=fmt_bytes(op.resumed_from)) if op.resumed_from else "") if x)
                bar = frac
            self.banner.set("run", title, f"{text}\n" + _("Transcurrido {t}").format(t=elapsed), G.SYNC)
            self.progress.setVisible(True)
            if bar is None:
                self.progress.setRange(0, 0)
            else:
                self.progress.setRange(0, 10000)
                self.progress.setValue(int(bar * 10000))
        else:
            self.progress.hide()
            if op is not None and mine and not op.running:
                self._result_banner(op)
            elif srv is not None:
                if srv.last_error and (not srv.last_ok or srv.last_try > srv.last_ok):
                    self.banner.set("err", _("Falló la última copia ({when})").format(when=fmt_ago(srv.last_try)),
                                    srv.last_error, G.ERROR)
                elif srv.last_ok:
                    nxt = self._next_text(srv)
                    self.banner.set("ok", _("Última copia completa {when}").format(when=fmt_ago(srv.last_ok)),
                                    _("{size} · comprobada con SHA-256").format(size=fmt_bytes(srv.last_size)) + nxt,
                                    G.DONE)
                else:
                    self.banner.set("info", _("Listo para la primera copia"),
                                    _("La copia completa la hace Plesk con «pleskbackup server»; después se descarga y "
                                      "se comprueba aquí.") + self._next_text(srv), G.INFO)
            elif self._selected.startswith("disk:"):
                self.banner.set("info", _("Copias de un servidor que ya no está conectado"),
                                _("Elige una y pulsa «Restaurar…» para subirla a uno de tus servidores Plesk."), G.INFO)
            self.banner.setVisible(srv is not None or self._selected.startswith("disk:"))
        self._update_actions()

    def _next_text(self, srv: plesklink.Server) -> str:
        days = plesklink.SCHEDULE_DAYS.get(srv.schedule, 0)
        if not days:
            return ""
        return "  ·  " + (_("copia automática cada día") if days == 1 else _("copia automática cada semana"))

    def _result_banner(self, op: plesklink.Operation):
        if op.phase == "done" and op.kind == "backup":
            r = op.result
            extra = _("  ·  quitadas {n} antiguas").format(n=len(op.pruned)) if getattr(op, "pruned", None) else ""
            self.banner.set("ok", _("Copia completa guardada"),
                            _("{size} · SHA-256 comprobado · {path}").format(size=fmt_bytes(r.size), path=r.path) + extra,
                            G.DONE, (_("Mostrar"), lambda: reveal_path(r.path)))
        elif op.phase == "done":
            if op.warning == "auth":
                self.banner.set("warn", _("Restauración en marcha en Plesk"),
                                _("La copia restaurada trae otras conexiones de la extensión, así que este ordenador ya no "
                                  "puede seguirla. Mira el resultado en Plesk y, si hace falta, vuelve a conectarlo."),
                                G.WARN, (_("Abrir Plesk"), lambda: QDesktopServices.openUrl(QUrl(op.server.url))))
            elif getattr(op, "mode", "") == "server":
                self.banner.set("ok", _("Servidor restaurado"),
                                _("Plesk terminó la restauración. Revisa en el panel que todo está como esperabas."),
                                G.DONE, (_("Abrir Plesk"), lambda: QDesktopServices.openUrl(QUrl(op.server.url))))
            else:
                self.banner.set("ok", _("Copia subida a Plesk"),
                                _("Está en Herramientas y configuración → Gestor de copias de seguridad. Elige allí qué "
                                  "restaurar."),
                                G.DONE, (_("Abrir Plesk"), lambda: QDesktopServices.openUrl(QUrl(op.server.url))))
        elif op.phase == "cancelled":
            self.banner.set("warn", _("Detenido"), _("No se ha cambiado nada en el disco de copia. Lo ya descargado se "
                                                     "aprovecha la próxima vez.") if op.kind == "backup"
                            else _("La copia no se ha restaurado."), G.WARN)
        else:
            err = op.error
            self.banner.set("err", _("No se pudo copiar el servidor") if op.kind == "backup"
                            else _("No se pudo restaurar"), plesk_error_text(err), G.ERROR,
                            (_("Volver a conectar…"), lambda: self.pair(op.server.url, replace=op.server))
                            if err and err.kind in ("auth", "cert") else None)

    def _finish(self, op: plesklink.Operation):
        self._finalized = True
        self.timer.stop()
        self.guard.stop()
        srv = op.server
        name = srv.label
        if op.phase == "done":
            if op.kind == "backup":
                self.win._log(_("Plesk: copia de {name} guardada ({size}, SHA-256 comprobado) en {path}").format(
                    name=name, size=fmt_bytes(op.result.size), path=op.result.path), C["ok"])
                for b in getattr(op, "pruned", []):
                    self.win._log(_("Plesk: quitada la copia antigua {path}").format(path=b.path), C["muted"])
                self.win._notify(_("Copia de Plesk terminada"), _("{name}: {size} guardados en el disco de copia.").format(
                    name=name, size=fmt_bytes(op.result.size)))
            else:
                self.win._log(_("Plesk: restauración en {name} terminada").format(name=name), C["ok"])
                self.win._notify(_("Restauración de Plesk terminada"), name)
        elif op.phase == "failed":
            text = plesk_error_text(op.error)
            if op.kind == "backup":
                srv.last_error = text
            self.win._log(_("Plesk: {name}: {text}").format(name=name, text=text), C["err"])
            for line in op.log[-15:]:
                self.win._log("    " + line, C["muted"])
            self.win._notify(_("Copia de Plesk") if op.kind == "backup" else _("Restauración de Plesk"),
                             f"{name}: {text}")
        else:
            self.win._log(_("Plesk: {name}: detenido").format(name=name), C["warn"])
        self._save()
        self.refresh()
        self.refresh_summary()
        QTimer.singleShot(5000, self.check_due)

    # ── copias automáticas ──
    def due(self, srv: plesklink.Server, now: datetime | None = None) -> bool:
        days = plesklink.SCHEDULE_DAYS.get(srv.schedule, 0)
        if not days:
            return False
        now = now or datetime.now()
        last = reminders.parse_time(srv.last_ok)
        tried = reminders.parse_time(srv.last_try)
        if tried and (now - tried) < timedelta(hours=2):
            return False  # tras un fallo, no se reintenta enseguida
        return last is None or (now - last) >= timedelta(days=days) - timedelta(hours=2)

    def check_due(self):
        if self.busy or not self.disk_root():
            return
        for srv in self.servers:
            if self.due(srv) and self.start_backup(srv.id, auto=True):
                return

    def summary(self) -> str:
        if not self.servers:
            return _("Ningún servidor conectado")
        if self.busy:
            return _("Copiando {name}…").format(name=self.op.server.label)
        return _n("{n} servidor conectado", "{n} servidores conectados", len(self.servers)).format(n=len(self.servers))


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
        title = QLabel(_("Hay una versión nueva: {version}").format(version=rel.version))
        title.setObjectName("h1")
        when = ""
        if rel.published:
            try:
                # GitHub da la hora en UTC; fmt_ago compara con la hora local
                local = datetime.fromisoformat(rel.published.replace("Z", "+00:00")).astimezone()
                when = ("  ·  " + _("publicada {isoformat}").format(isoformat=fmt_ago(local.replace(tzinfo=None).isoformat())))
            except ValueError:
                pass
        size = f"  ·  {fmt_bytes(rel.asset_size)}" if rel.asset_size else ""
        sub = QLabel(_("Tienes la {current_version}{when}{size}").format(current_version=CURRENT_VERSION, when=when, size=size))
        sub.setObjectName("muted")
        col.addWidget(title)
        col.addWidget(sub)
        head.addLayout(col, 1)
        lay.addLayout(head)

        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(rel.notes.strip() or _("Sin notas de versión."))
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
        self.b_skip = QPushButton(_("Omitir esta versión"))
        self.b_skip.setObjectName("link")
        buttons.addWidget(self.b_skip)
        buttons.addStretch(1)
        self.b_later = QPushButton(_("Más tarde"))
        self.b_go = QPushButton()
        self.b_go.setObjectName("primary")
        for b in (self.b_skip, self.b_later, self.b_go):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        buttons.addWidget(self.b_later)
        buttons.addWidget(self.b_go)
        lay.addLayout(buttons)

        if updater.can_self_update() and rel.asset_url:
            self.b_go.setText(("  " + _("Actualizar ahora")))
            self.b_go.setIcon(glyph_icon(G.DOWNLOAD, "#FFFFFF", 14))
            self.b_go.clicked.connect(self._update)
            how = (_("se descarga el instalador, se comprueba su SHA-256 y se instala; la app se cierra y vuelve a "
                   "abrirse sola."))
            self.status.setText(_("Al actualizar {how} Tus ajustes y copias no se tocan.").format(how=how))
        else:
            self.b_go.setText(("  " + _("Ver en GitHub")))
            self.b_go.setIcon(glyph_icon(G.OPEN, "#FFFFFF", 14))
            self.b_go.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(rel.page)))
            self.status.setText(_("Esta copia se ejecuta desde el código: actualízala con «git pull».")
                                if not FROZEN else _("No hay instalador para este sistema en esta versión."))
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
            self.status.setText(_("Hay una sincronización en marcha: podrás actualizar en cuanto termine."))

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
        self.b_later.setText(_("Cancelar"))
        self.bar.show()
        self.bar.setRange(0, 0)
        self.status.setText(_("Descargando {asset_name}…").format(asset_name=self.rel.asset_name))

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
            self.status.setText(_("Descargando {asset_name}…  {done} de {total}").format(asset_name=self.rel.asset_name, done=fmt_bytes(done), total=fmt_bytes(total)))

    def _on_downloaded(self, path: str, error: str):
        if error:
            if self._stop.is_set():
                return
            self.bar.hide()
            self.status.setText(_("No se pudo descargar la actualización: {error}").format(error=error))
            self.b_go.setEnabled(True)
            self.b_skip.setEnabled(True)
            self.b_later.setText(_("Más tarde"))
            return
        self.bar.setRange(0, 10000)
        self.bar.setValue(10000)
        self.status.setText(_("Descarga comprobada con SHA-256. Instalando: la app se cerrará y volverá a abrirse…"))
        try:
            updater.launch_install(path)
        except OSError as exc:
            self.status.setText(_("No se pudo lanzar el instalador: {exc}").format(exc=exc))
            return
        QTimer.singleShot(600, self.win.quit_for_update)

    def reject(self):
        self._stop.set()
        super().reject()


# ── guía rápida ──────────────────────────────────────────────────────────────

TOUR_VERSION = 2  # al subirla, quien actualiza vuelve a ver la guía (una vez) con lo nuevo
TOUR_DIR = os.path.join(getattr(sys, "_MEIPASS", APP_DIR), "assets", "tour")


def tour_steps() -> list[dict]:
    """Pasos de la guía. Las ilustraciones salen de tools/make_tour.py (capturas reales de la app)."""
    tray = _("la barra de menús") if IS_MAC else _("la bandeja")
    return [
        {"image": "1-bienvenida", "glyph": G.SPARK, "eyebrow": _("Guía rápida"),
         "title": _("Tus proyectos, siempre a salvo"),
         "text": _("Copia tu carpeta de proyectos a un disco externo o a tu nube: rápido, comprobado y con "
                 "un historial para recuperar lo que borres."),
         "chips": [(G.SHIELD, _("Verificado con SHA-256")), (G.CLOCK, _("Versiones de lo que cambias")),
                   (G.NEXT, _("Detecta movidos"))]},
        {"image": "2-origen-destino", "glyph": G.FOLDER, "title": _("Elige origen y destino"),
         "text": _("El origen es tu carpeta de proyectos; el destino, una carpeta del disco externo o de tu "
                   "nube en casa."),
         "chips": [(G.FOLDER, _("Una carpeta o un disco entero")), (G.DRIVE, _("Lo reconoce aunque cambie de letra")),
                   (G.GLOBE, _("Tu nube, con un código"))]},
        {"image": "3-que-copiar", "glyph": G.FILTER, "title": _("Decide qué se copia"),
         "text": _("Desmarca carpetas en el árbol o exclúyelas por nombre, en cualquier nivel de cada proyecto."),
         "chips": [(G.FILTER, _("node_modules, .venv, dist…")), (G.TREE, _("Se recuerda para la próxima"))]},
        {"image": "4-sincroniza", "glyph": G.PLAY, "title": _("Analiza y sincroniza"),
         "text": _("Analizar enseña lo nuevo, modificado y movido sin tocar nada. Sincronizar lo copia, lo "
                   "comprueba y, si fuera a apartar media copia de golpe, se para a preguntarte."),
         "chips": [(G.SEARCH, _("Revisa antes de copiar")), (G.SHIELD, _("Cada archivo verificado")),
                   (G.WARN, _("Frena los borrados masivos"))]},
        {"image": "5-recupera", "glyph": G.HISTORY, "title": _("Recupera lo que borraste"),
         "text": _("Lo que borras o cambias pasa a versiones. En Recuperar eliges una fecha, buscas el archivo y "
                   "lo devuelves a su sitio, sin sobrescribir nada."),
         "chips": [(G.HISTORY, _("Línea de tiempo por fechas")), (G.RESTORE, _("Nunca sobrescribe")),
                   (G.CLOCK, _("7, 30 o 60 días"))]},
        {"image": "6-conecta", "glyph": G.BELL, "title": _("Conecta el disco y listo"),
         "text": _("Desde {tray} vigila el disco y te recuerda copiar si llevas días con cambios sin guardar. "
                   "Al enchufarlo, sincronizas con un clic.").format(tray=tray),
         "chips": [(G.SYNC, _("Vigila en segundo plano")), (G.BELL, _("Te recuerda copiar")),
                   (G.POWER, _("Sin suspensión al copiar"))]},
    ]


def tour_folder(lang: str | None = None) -> str:
    """assets/tour/<idioma>: las capturas de la guía están hechas con la app en cada idioma."""
    for code in (lang or i18n.current(), i18n.SOURCE):
        folder = os.path.join(TOUR_DIR, code)
        if os.path.isfile(os.path.join(folder, "tour.json")):
            return folder
    return TOUR_DIR


def tour_assets(lang: str | None = None) -> tuple[dict[str, QPixmap], dict[str, list], tuple[float, float]]:
    """Ilustraciones de la guía en el idioma de la app y zonas que se destacan en cada una (tour.json)."""
    folder = tour_folder(lang)
    spots: dict[str, list] = {}
    size = (920.0, 400.0)
    try:
        with open(os.path.join(folder, "tour.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        spots = data.get("spots", {})
        size = (float(data["size"][0]), float(data["size"][1]))
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        pass
    images = {s["image"]: QPixmap(os.path.join(folder, s["image"] + ".jpg")) for s in tour_steps()}
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

    def paintEvent(self, _e):  # noqa: N802
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

    def leaveEvent(self, _e):  # noqa: N802
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
        self.setAccessibleName(_("Guía rápida"))

        self.close_btn = QPushButton(self)
        self.close_btn.setObjectName("tourClose")
        self.close_btn.setIcon(glyph_icon(G.CLOSE, C["text"], 14))
        self.close_btn.setFixedSize(34, 34)
        self.close_btn.setToolTip(_("Cerrar la guía  (Esc)"))
        self.close_btn.setAccessibleName(_("Cerrar la guía"))
        self.skip_btn = QPushButton(_("Saltar la guía"), self)
        self.skip_btn.setObjectName("tourSkip")
        self.dots = TourDots(len(self.steps), self)
        self.back_btn = QPushButton(("  " + _("Atrás")), self)
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
        self.next_btn.setText((_("Empezar") + "  ") if last else (_("Siguiente") + "  "))
        self.next_btn.setIcon(glyph_icon(G.CHECK if last else G.ARROW, "#FFFFFF", 15))
        self.dots.set_step(self.idx, self.prev)
        st = self.steps[self.idx]
        self.setAccessibleDescription(f"{self._eyebrow(self.idx)}. {st['title']}. {st['text']}")
        self._place()
        if not self.next_btn.hasFocus() and not self.back_btn.hasFocus():
            self.next_btn.setFocus()

    def _eyebrow(self, i: int) -> str:
        return self.steps[i].get("eyebrow") or _("Paso {i} de {last}").format(i=i, last=self.last)

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

    def paintEvent(self, _e):  # noqa: N802
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
        for kind, label in (("t", _("Vuelve a verla con")), ("k", _("Guía")), ("t", _("o")), ("k", help_key())):
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
        self.setAccessibleName(_("Guía rápida"))
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

    def resizeEvent(self, _e):  # noqa: N802
        self._layout()

    def mousePressEvent(self, e):  # noqa: N802
        e.accept()  # los clics fuera de la tarjeta no llegan a la ventana

    def paintEvent(self, _e):  # noqa: N802
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

REMIND_OPTIONS = [(1, _("1 día")), (2, _("2 días")), (3, _("3 días")), (7, _("1 semana")), (14, _("2 semanas"))]


def days_text(days: int) -> str:
    return dict(REMIND_OPTIONS).get(days) or _("{days} días").format(days=days)


def short_changes(p: dict | None) -> str:
    """«37 archivos sin copiar» para las listas; «» si no se ha mirado o no hay nada."""
    if not p or p.get("missing"):
        return ""
    files, dirs = p.get("files", 0), p.get("dirs", 0)
    if files:
        return _n("{n} archivo sin copiar", "{n} archivos sin copiar", files).format(n=fmt_int(files))
    return _("borrados o movidos sin copiar") if dirs else ""


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
            tag = QLabel(_("EN USO"))
            tag.setObjectName("remTag")
            tag.setToolTip(_("Es el origen y el destino que tienes elegidos ahora"))
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
        self.more.setToolTip(_("Opciones de esta copia"))
        self.more.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more.clicked.connect(
            lambda: self.menu_requested.emit(self.pid, self.more.mapToGlobal(QPoint(0, self.more.height()))))
        lay.addWidget(self.more, 0, Qt.AlignmentFlag.AlignVCenter)
        copies = it.get("copies", 0)
        self.setToolTip(f"{it['src']}  →  {it.get('dst', '')}\n"
                        + _n("{n} copia completa", "{n} copias completas", copies).format(n=copies)
                        + "  ·  " + _("última {when}").format(when=it.get("last_ok", "").replace("T", " ")))

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
        self.go = QPushButton(("  " + _("Sincronizar ahora")))
        self.go.setObjectName("remGo")
        self.go.setIcon(glyph_icon(G.PLAY, "#FFFFFF", 13))
        self.later = QPushButton(_("Recordar mañana"))
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
    # comprobación de la nube en segundo plano: (servidor, destino corregido o "", ok | down | none)
    cloud_checked = pyqtSignal(str, str, str)

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
        self._watch_network_roots()
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
        self.cloud_checked.connect(self._on_cloud_checked)
        QTimer.singleShot(1500, self._check_cloud)
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
            self._log(_("Actualizado a la versión {app_version}").format(app_version=APP_VERSION), C["ok"])
            QTimer.singleShot(1500, lambda: self._notify(APP_NAME, _("Actualizado a la versión {app_version}.").format(app_version=APP_VERSION)))

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
        self.update_status.setText(_("Consultando GitHub…"))
        self.checker.check(manual)

    def _on_update_result(self, rel, error: str, manual: bool):
        self.btn_update.setEnabled(True)
        if rel is None:
            self.update_status.setText(_("No se pudo consultar GitHub: {error}").format(error=error))
            if manual:
                QMessageBox.warning(self, APP_NAME, _("No se pudo consultar si hay versiones nuevas.\n\n{error}").format(error=error))
            return
        self.update_checked = datetime.now().isoformat(timespec="seconds")
        self._save_settings()
        newer = updater.is_newer(rel.version, CURRENT_VERSION)
        if newer and (manual or rel.version != self.update_skip):
            self._pending_release = rel
            self._offer_update()
        elif manual:
            QMessageBox.information(self, APP_NAME, _("Tienes la última versión ({current_version}).").format(current_version=CURRENT_VERSION))
        self._refresh_update_status(rel)

    def _offer_update(self):
        rel = self._pending_release
        if rel is None or (self.update_dialog and self.update_dialog.isVisible()):
            return
        if self._state in RUNNING:
            # se ofrece al terminar la sincronización (ver _finish)
            self._notify(_("Versión nueva disponible"), _("{app_name} {version}: te preguntaré al terminar la copia.").format(app_name=APP_NAME, version=rel.version))
            return
        self._notify(_("Versión nueva disponible"), _("{app_name} {version} está lista para instalar.").format(app_name=APP_NAME, version=rel.version))
        self._show_window()
        self.update_dialog = UpdateDialog(self, rel)
        self.update_dialog.open()
        QApplication.alert(self.update_dialog, 0)

    def _refresh_update_status(self, rel: updater.Release | None = None):
        if not hasattr(self, "update_status"):
            return
        parts = [_("Versión instalada: {current_version}").format(current_version=CURRENT_VERSION)]
        if rel is not None and updater.is_newer(rel.version, CURRENT_VERSION):
            skipped = ("  " + _("(omitida)")) if rel.version == self.update_skip else ""
            parts.append(_("nueva disponible: {version}{skipped}").format(version=rel.version, skipped=skipped))
        elif self.update_checked:
            parts.append(_("al día · comprobado {update_checked}").format(update_checked=fmt_ago(self.update_checked)))
        if not self.update_box.isChecked():
            parts.append(_("búsqueda automática desactivada"))
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
        sub = QLabel(_("Sincronización de proyectos  ·  detecta cambios, movidos y borrados  ·  guarda versiones"))
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
        self.btn_help = QPushButton(("  " + _("Guía")))
        self.btn_help.setObjectName("help")
        self.btn_help.setIcon(glyph_icon(G.HELP, C["accent2"], 15))
        self.btn_help.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_help.setToolTip(_("Guía rápida  ({help_key})").format(help_key=help_key()))
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
        group(_("Copia"), first=True)
        pair = Card(margins=(18, 16, 18, 16))
        pair.body.setSpacing(0)
        self.src_card = PathCard(_("Origen"), G.FOLDER, _("Carpeta de origen"), frameless=True)
        self.dst_card = PathCard(_("Destino"), G.DRIVE, _("Carpeta de destino"), frameless=True)
        self.src_card.changed.connect(self._on_src_changed)
        self.dst_card.changed.connect(self._on_dst_changed)
        pair.body.addWidget(self.src_card)
        pair.body.addWidget(FlowConnector())
        pair.body.addWidget(self.dst_card)
        self.cloud_btn = QPushButton("  " + _("Conectar con mi nube…"))
        self.cloud_btn.setObjectName("link")
        self.cloud_btn.setIcon(glyph_icon(G.GLOBE, C["accent"], 14))
        self.cloud_btn.setToolTip(_("Guarda las copias en Personal Cloud Storage, en tu red de casa, con el código "
                                    "que te da su web. Sin contraseñas."))
        self.cloud_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cloud_btn.clicked.connect(self._pair_cloud)
        cloud_row = QHBoxLayout()
        cloud_row.setSpacing(10)
        self.cloud_state = QLabel("")
        self.cloud_state.hide()
        cloud_row.addWidget(self.cloud_state)
        cloud_row.addWidget(self.cloud_btn)
        cloud_row.addStretch(1)
        pair.body.addLayout(cloud_row)
        self.card_pair = pair
        lay.addWidget(pair)

        # ── qué se copia ──
        group(_("Qué se copia"))
        rules = section("reglas", _("Excluir carpetas"), G.FILTER)
        hint = QLabel(_("Se omiten en cualquier nivel de cada proyecto, en origen y destino. Admite comodines "
                      "(*.egg-info)."))
        hint.setObjectName("faint")
        hint.setWordWrap(True)
        rules.body.addWidget(hint)
        self.chips_box = QWidget()
        self.chips_box.setToolTip(_("Clic para activar o desactivar una regla · clic derecho en una regla propia "
                                  "para quitarla"))
        self.flow = FlowLayout(self.chips_box, 8)
        rules.body.addWidget(self.chips_box)
        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        self.rule_edit = QLineEdit()
        self.rule_edit.setPlaceholderText(_("Añadir regla…  (p. ej. Library)"))
        self.rule_edit.returnPressed.connect(self._add_rule)
        add_btn = QPushButton()
        add_btn.setObjectName("icon")
        add_btn.setIcon(glyph_icon(G.ADD, C["text"], 14))
        add_btn.setFixedSize(38, 36)
        add_btn.setToolTip(_("Añadir regla"))
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.clicked.connect(self._add_rule)
        add_row.addWidget(self.rule_edit, 1)
        add_row.addWidget(add_btn)
        rules.body.addLayout(add_row)
        self.card_rules = rules

        # ── cómo se copia ──
        group(_("Cómo se copia"))
        opts = section("modo", _("Modo y versiones"), G.GEAR)
        self.mode_combo = QComboBox()
        for key, text in engine.MODES.items():
            self.mode_combo.addItem(_(text), key)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        opts.body.addWidget(self.mode_combo)
        self.mode_hint = QLabel()
        self.mode_hint.setObjectName("faint")
        self.mode_hint.setWordWrap(True)
        opts.body.addWidget(self.mode_hint)
        self.keep_combo = QComboBox()
        for days, text in KEEP_OPTIONS:
            self.keep_combo.addItem(text, days)
        opts.body.addWidget(combo_row(_("Conservar versiones"), _("Luego se quitan solas; antes si falta sitio"), self.keep_combo))
        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(1, 32)
        self.threads_spin.valueChanged.connect(self._update_summary)
        opts.body.addWidget(combo_row(_("Hilos de copia"), _("Archivos que se copian a la vez"), self.threads_spin, 96))
        self.full_box = Toggle()
        self.full_box.toggled.connect(self._invalidate_plan)
        opts.body.addWidget(SettingRow(
            _("Revisar todo el destino"), _("En el próximo análisis (si no, cada 7 días)"), self.full_box,
            _("Normalmente el análisis usa el índice de la última copia y no relee el disco. Cambio = distinto "
            "tamaño o fecha de modificación (al nanosegundo en NTFS y APFS).")))

        integ = section("integridad", _("Integridad"), G.SHIELD)
        self.verify_box = Toggle()
        self.verify_box.toggled.connect(self._invalidate_plan)
        integ.body.addWidget(SettingRow(_("Verificar cada copia"), _("Relee el disco y compara su SHA-256"),
                                        self.verify_box))
        self.scrub_combo = QComboBox()
        for secs, text in SCRUB_OPTIONS:
            self.scrub_combo.addItem(text, secs)
        integ.body.addWidget(combo_row(
            _("Revisar el disco"), _("Tras sincronizar, busca corrupción silenciosa"), self.scrub_combo,
            tip=_("Comprueba una tanda de archivos ya copiados durante el tiempo elegido: primero los nunca "
                "comprobados y luego los de hace más de 30 días. Si el dañado es el destino, lo repara.")))
        self.integ_status = QLabel("")
        self.integ_status.setObjectName("faint")
        self.integ_status.setWordWrap(True)
        integ.body.addWidget(self.integ_status)
        self.btn_scrub = QPushButton(("  " + _("Comprobar todo el disco ahora")))
        self.btn_scrub.setIcon(glyph_icon(G.SHIELD, C["text"], 14))
        self.btn_scrub.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_scrub.clicked.connect(lambda: self._start(copy=True, scrub_all=True))
        integ.body.addWidget(self.btn_scrub)

        # ── automático ──
        group(_("Automático"))
        disk = section("disco", _("Disco de copia"), G.SYNC)
        self.disk_status = QLabel("")
        self.disk_status.setObjectName("faint")
        self.disk_status.setWordWrap(True)
        disk.body.addWidget(self.disk_status)
        where = (_("Se inicia con Windows, en la bandeja") if IS_WIN else
                 _("Se inicia con la sesión, en la barra de menús") if IS_MAC else
                 _("Se inicia con la sesión, en la bandeja"))
        self.watch_box = Toggle()
        self.watch_box.toggled.connect(self._on_watch_toggled)
        disk.body.addWidget(SettingRow(_("Vigilar en segundo plano"), where, self.watch_box))
        self.connect_combo = QComboBox()
        for key, text in CONNECT_ACTIONS.items():
            self.connect_combo.addItem(text, key)
        self.connect_combo.currentIndexChanged.connect(lambda _i: self._refresh_summaries())
        disk.body.addWidget(QLabel(_("Al conectar el disco"), objectName="fieldLabel"))
        disk.body.addWidget(self.connect_combo)
        self.card_disk = disk

        rem = section("recordatorios", _("Recordatorios"), G.BELL)
        self.remind_box = Toggle()
        self.remind_box.toggled.connect(self._on_remind_toggled)
        rem.body.addWidget(SettingRow(_("Recordarme hacer copia"), _("Solo si hay cambios sin copiar, una vez por periodo"),
                                      self.remind_box))
        self.remind_combo = QComboBox()
        for days, text in REMIND_OPTIONS:
            self.remind_combo.addItem(text, days)
        self.remind_combo.currentIndexChanged.connect(self._on_remind_days_changed)
        rem.body.addWidget(combo_row(_("Avisar después de"), _("Días sin copia con cambios"), self.remind_combo))
        where = _("con Windows") if IS_WIN else _("al iniciar sesión")
        link_text = html.escape(_("Iniciarla {where} en segundo plano").format(where=where))
        link = f'<a href="#" style="color:{C["accent2"]}; text-decoration:none">{link_text}</a>'
        self.remind_tray_hint = QLabel(html.escape(_("Con la app cerrada no puede avisarte.")) + " " + link)
        self.remind_tray_hint.setObjectName("faint")
        self.remind_tray_hint.setWordWrap(True)
        self.remind_tray_hint.linkActivated.connect(lambda _l: self.watch_box.setChecked(True))
        rem.body.addWidget(self.remind_tray_hint)
        cl = QHBoxLayout()
        cl.addWidget(QLabel(_("Copias habituales"), objectName="fieldLabel"))
        cl.addStretch(1)
        self.btn_remind_check = QPushButton(_("Comprobar ahora"))
        self.btn_remind_check.setObjectName("link")
        self.btn_remind_check.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_remind_check.setToolTip(_("Mira ya si hay cambios sin copiar en todas las copias habituales"))
        self.btn_remind_check.clicked.connect(lambda: self._reminder_tick(manual=True))
        cl.addWidget(self.btn_remind_check)
        rem.body.addLayout(cl)
        rows = QWidget()
        self.remind_rows_box = rows
        self.remind_rows = QVBoxLayout(rows)
        self.remind_rows.setContentsMargins(0, 0, 0, 0)
        self.remind_rows.setSpacing(6)
        rem.body.addWidget(rows)
        self.remind_empty = QLabel(_("Aún no hay ninguna: cada sincronización completa se añade sola, con su origen "
                                   "y su disco."))
        self.remind_empty.setObjectName("faint")
        self.remind_empty.setWordWrap(True)
        rem.body.addWidget(self.remind_empty)
        self.remind_status = QLabel("")
        self.remind_status.setObjectName("faint")
        rem.body.addWidget(self.remind_status)
        self.card_remind = rem

        # ── sistema ──
        group(_("Sistema"))
        power = section("energia", _("Energía"), G.POWER)
        self.awake_box = Toggle()
        self.awake_box.toggled.connect(lambda _on: (self._save_settings(), self._refresh_awake(),
                                                    self._refresh_summaries()))
        if IS_MAC:
            how = (_("El Mac no reposa por inactividad ni duerme los discos (la pantalla sí puede apagarse), y "
                   "cerrar la app, la sesión o apagar pide confirmación. Si se duerme igualmente, la copia "
                   "espera al disco y sigue sola."))
        elif IS_LINUX:
            how = (_("El equipo no se suspende por inactividad (la pantalla sí puede apagarse) y el disco USB no "
                   "se duerme. Si se suspende igualmente, la copia espera al disco y sigue sola."))
        else:
            how = (_("El equipo no se suspende por inactividad (la pantalla sí puede apagarse), el disco USB no "
                   "se duerme y Windows avisa si intentas apagar o reiniciar. Si se suspende igualmente (tapa, "
                   "botón), la copia se pausa y sigue sola al volver."))
        power.body.addWidget(SettingRow(_("Equipo y disco despiertos"), _("Mientras analiza o sincroniza"),
                                        self.awake_box, how))
        self.awake_status = QLabel("")
        self.awake_status.setObjectName("faint")
        self.awake_status.setWordWrap(True)
        self.awake_status.setToolTip(how)
        power.body.addWidget(self.awake_status)
        self.card_power = power

        lang = section("idioma", _("Idioma"), G.GLOBE)
        self.lang_combo = QComboBox()
        self.lang_combo.addItem(_("Automático ({name})").format(name=i18n.LANGS[i18n.system_language()]), "auto")
        for code, name in i18n.LANGS.items():
            self.lang_combo.addItem(name, code)
        self.lang_combo.setCurrentIndex(max(0, self.lang_combo.findData(LANGUAGE)))
        lang.body.addWidget(combo_row(_("Idioma de la app"), _("Automático: el del sistema"), self.lang_combo, 170))
        self.lang_note = QLabel("")
        self.lang_note.setObjectName("faint")
        self.lang_note.setWordWrap(True)
        self.lang_note.hide()
        lang.body.addWidget(self.lang_note)
        self.btn_restart = QPushButton()
        self.btn_restart.setObjectName("primary")
        self.btn_restart.setIcon(glyph_icon(G.REFRESH, "#FFFFFF", 14))
        self.btn_restart.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_restart.clicked.connect(self._restart_app)
        self.btn_restart.hide()
        lang.body.addWidget(self.btn_restart)
        self.lang_combo.currentIndexChanged.connect(self._on_language_changed)

        upd = section("actualizaciones", _("Actualizaciones"), G.DOWNLOAD)
        self.update_box = Toggle()
        self.update_box.toggled.connect(self._on_update_toggled)
        upd.body.addWidget(SettingRow(_("Buscar versiones nuevas"), _("En GitHub; pregunta antes de instalar"),
                                      self.update_box))
        self.update_status = QLabel("")
        self.update_status.setObjectName("faint")
        self.update_status.setWordWrap(True)
        upd.body.addWidget(self.update_status)
        self.btn_update = QPushButton(("  " + _("Buscar ahora")))
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

    # ── idioma ───────────────────────────────────────────────────────────
    def _on_language_changed(self):
        choice = self.lang_combo.currentData()
        self.settings.setValue("language", choice)
        self.settings.sync()
        target = i18n.resolve(choice)
        pending = target != i18n.current()
        if pending:  # el aviso, ya en el idioma elegido
            self.lang_note.setText(i18n.tr_in(target, "El idioma cambia al reiniciar la app. Tus ajustes y copias "
                                                      "se conservan."))
            self.btn_restart.setText("  " + i18n.tr_in(target, "Reiniciar ahora"))
        self.lang_note.setVisible(pending)
        self.btn_restart.setVisible(pending)
        self._refresh_summaries()

    def _restart_app(self):
        """Vuelve a abrir la app (para aplicar el idioma). Con una copia en marcha, espera a que termine."""
        if self._state in RUNNING:
            QMessageBox.information(self, APP_NAME, _("Hay una operación en curso: reinicia la app cuando termine."))
            return
        self._save_settings()
        args = [a for a in sys.argv[1:] if a not in ("--tray", "--updated")]
        program, arguments = (sys.executable, args) if FROZEN else (sys.executable, [os.path.abspath(sys.argv[0])] + args)
        server = getattr(QApplication.instance(), "instance_server", None)
        if server is not None:
            server.close()  # la nueva instancia no debe creer que ya hay otra abierta
        started = QProcess.startDetached(program, arguments)
        if not (started[0] if isinstance(started, tuple) else started):
            if server is not None:
                server.listen(INSTANCE)
            QMessageBox.warning(self, APP_NAME, _("No se pudo reiniciar la app. Ciérrala y vuelve a abrirla."))
            return
        self.quit_for_update()

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
            more = (" " + _("y {rules} más").format(rules=len(rules) - 3)) if len(rules) > 3 else ""
            sec["reglas"].set_summary(f"{', '.join(rules[:3])}{more}")
        else:
            sec["reglas"].set_summary(_("Ninguna regla activa: se copia todo"))
        mode = {engine.MODE_MIRROR: _("Espejo con versiones"), engine.MODE_UPDATE: _("Solo añadir y actualizar")}.get(
            self.mode_combo.currentData(), self.mode_combo.currentText())
        sec["modo"].set_summary(_("{mode}  ·  versiones {lower}  ·  {value} hilos").format(mode=mode, lower=mid(self.keep_combo.currentText()), value=self.threads_spin.value()))
        scrub = self.scrub_combo.currentData()
        sec["integridad"].set_summary(
            (_("SHA-256 en cada copia") if self.verify_box.isChecked() else _("Sin verificar al copiar")) + "  ·  "
            + (_("revisión {period}").format(period=self.scrub_combo.currentText()) if scrub else _("sin revisión")))
        sec["disco"].set_summary((_("Vigilando") if self.watch_box.isChecked() else _("Sin vigilar"))
                                 + "  ·  " + mid(self.connect_combo.currentText()))
        if self.remind_box.isChecked():
            n = len(self.registry.items)
            alerts = len(self.registry.alerts())
            text = (_("Tras {period}").format(period=self.remind_combo.currentText()) + "  ·  "
                    + _n("{n} copia habitual", "{n} copias habituales", n).format(n=n))
            if alerts:
                text += ("  ·  " + _("{alerts} con cambios sin copiar").format(alerts=alerts))
            sec["recordatorios"].set_summary(text)
        else:
            sec["recordatorios"].set_summary(_("Desactivados"))
        sec["energia"].set_summary(_("Sin suspensión mientras copia") if self.awake_box.isChecked() else _("Desactivado"))
        choice = self.lang_combo.currentData()
        name = i18n.LANGS[i18n.resolve(choice)]
        sec["idioma"].set_summary(_("Automático · {name}").format(name=name) if choice == "auto" else name)
        sec["actualizaciones"].set_summary(f"v{APP_VERSION}  ·  " + (_("automáticas") if self.update_box.isChecked()
                                                                       else _("solo a mano")))

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
        for i, (text, glyph) in enumerate([(_("Carpetas"), G.TREE), (_("Cambios"), G.SYNC),
                                           (_("Progreso"), G.SPEED), (_("Incidencias"), G.WARN),
                                           (_("Recuperar"), G.HISTORY)]):
            b = QPushButton(f"  {text}")
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setIcon(glyph_icon(glyph, C["muted"], 14, C["accent2"]))
            self.seg_group.addButton(b, i)
            self.seg_buttons.append(b)
            sl.addWidget(b)
        b = QPushButton("  Plesk")
        b.setObjectName("seg")
        b.setCheckable(True)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setIcon(plesk_icon(14))
        b.setToolTip(_("Copias completas de tus servidores Plesk"))
        self.seg_group.addButton(b, PAGE_PLESK)
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
        self.recover = RecoverPage(self)
        self.stack.addWidget(self.recover)
        self.plesk = PleskPage(self)
        self.stack.addWidget(self.plesk)
        self.seg_group.idClicked.connect(self.stack.setCurrentIndex)
        self.stack.currentChanged.connect(lambda i: self.recover.show_page() if i == PAGE_RECOVER else None)
        self.stack.currentChanged.connect(lambda i: self.plesk.refresh() if i == PAGE_PLESK else None)
        self.keep_combo.currentIndexChanged.connect(lambda *_a: self.recover.mark_stale())
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
        msg = QLabel(_("Desmarca las carpetas que no quieras copiar. Expande para elegir subcarpetas concretas, o "
                       "selecciona varias (Ctrl o Mayús + clic) para marcarlas o desmarcarlas a la vez."))
        msg.setObjectName("muted")
        msg.setWordWrap(True)
        h.addWidget(msg, 1)
        self.excl_count = QLabel("")
        self.excl_count.setObjectName("faint")
        h.addWidget(self.excl_count)
        self.b_check_all = QPushButton()
        self.b_uncheck_all = QPushButton()
        for b, on in ((self.b_check_all, True), (self.b_uncheck_all, False)):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, on=on: self._check_scope(on))
        reload_btn = QPushButton()
        reload_btn.setObjectName("icon")
        reload_btn.setIcon(glyph_icon(G.REFRESH, C["text"], 14))
        reload_btn.setFixedSize(38, 34)
        reload_btn.setToolTip(_("Volver a leer las carpetas del origen"))
        reload_btn.clicked.connect(self._load_tree)
        h.addWidget(self.b_check_all)
        h.addWidget(self.b_uncheck_all)
        h.addWidget(reload_btn)
        v.addWidget(bar)
        self.tree = FolderTree()
        self.tree.changed.connect(self._on_tree_changed)
        self.tree.itemSelectionChanged.connect(self._update_check_buttons)
        self._update_check_buttons()
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
        for key, text in [(None, _("Todos")), ("n", _("Nuevos")), ("c", _("Modificados")),
                          ("m", _("Movidos")), ("d", _("A versiones"))]:
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
        self.change_search.setPlaceholderText(_("Buscar ruta…"))
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
        self.changes_empty = QLabel(_("Pulsa «Analizar» para ver exactamente qué se va a copiar, mover o guardar "
                                    "en versiones antes de tocar nada."))
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

        self.summary_card = Card(_("Resumen de la copia"), G.DONE)
        self.s_new = Tile(_("Nuevos"), G.ADD, C["ok"])
        self.s_changed = Tile(_("Modificados"), G.REFRESH, C["accent2"])
        self.s_moved = Tile(_("Movidos"), G.NEXT, C["accent"])
        self.s_versions = Tile(_("A versiones"), G.CLOCK, C["warn"])
        self.s_dirs_new = Tile(_("Carpetas nuevas"), G.FOLDER, C["folder"])
        self.s_dirs_gone = Tile(_("Carpetas quitadas"), G.FOLDER, C["muted"])
        self.s_same = Tile(_("Sin cambios"), G.CHECK, C["ok"])
        self.s_checked = Tile(_("Verificados"), G.SHIELD, C["accent2"])
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
        self.t_elapsed = Tile(_("Transcurrido"), G.TIMER, C["accent2"])
        self.t_left = Tile(_("Restante"), G.CLOCK, C["accent2"])
        self.t_speed = Tile(_("Velocidad"), G.SPEED, C["accent2"])
        self.t_eta = Tile(_("Fin estimado"), G.FLAG, C["accent2"])
        self.t_copied = Tile(_("Copiado"), G.COPY, C["accent"])
        self.t_files = Tile(_("Archivos"), G.DOC, C["accent"])
        self.t_skipped = Tile(_("Sin cambios"), G.CHECK, C["accent"])
        self.t_errors = Tile(_("Errores"), G.WARN, C["accent"])
        self.tiles = [self.t_elapsed, self.t_left, self.t_speed, self.t_eta,
                      self.t_copied, self.t_files, self.t_skipped, self.t_errors]
        right.addWidget(TileGrid(self.tiles))

        bars = Card(margins=(18, 14, 18, 16))
        bars.body.setSpacing(8)
        self.bar_bytes, self.bar_bytes_val = self._bar_row(bars.body, _("Datos"))
        bars.body.addSpacing(4)
        self.bar_files, self.bar_files_val = self._bar_row(bars.body, _("Archivos"))
        right.addWidget(bars)
        top.addLayout(right, 1)
        v.addLayout(top)

        mid = QHBoxLayout()
        mid.setSpacing(14)
        chart_card = Card(margins=(18, 14, 18, 14))
        self.chart = SpeedChart()
        chart_card.body.addWidget(self.chart)
        mid.addWidget(chart_card, 3)
        self.active_card = Card(_("En curso"), G.SYNC)
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
        self.issues_label = QLabel(_("Sin incidencias"))
        self.issues_label.setObjectName("muted")
        h.addWidget(self.issues_label, 1)
        self.btn_export = QPushButton(_("Exportar…"))
        self.btn_export.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self._export_issues)
        h.addWidget(self.btn_export)
        v.addWidget(bar)
        self.issues = QTableWidget(0, 2)
        self.issues.setHorizontalHeaderLabels([_("RUTA"), _("MOTIVO")])
        self.issues.verticalHeader().hide()
        self.issues.setShowGrid(False)
        self.issues.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.issues.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.issues.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        hh = self.issues.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.issues.setColumnWidth(1, 360)
        empty = QLabel(_("Ningún archivo ha dado problemas.\nSi alguno no se puede leer o copiar, aparece aquí y la "
                       "copia sigue con el resto."))
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
        self.issues_label.setText(_("Sin incidencias") if ok else
                                  _n("{n} incidencia: archivos que no se pudieron leer o copiar. El resto sigue "
                                     "adelante.", "{n} incidencias: archivos que no se pudieron leer o copiar. El "
                                     "resto sigue adelante.", total).format(n=fmt_int(total)))
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

        self.btn_open = button(_("Abrir destino"), G.OPEN)
        self.btn_scan = button(_("Analizar"), G.SEARCH)
        self.btn_pause = button(_("Pausar"), G.PAUSE)
        self.btn_cancel = button(_("Cancelar"), G.STOP, "danger")
        self.btn_start = button(_("Sincronizar"), G.PLAY, "primary", "#FFFFFF")
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
            "keep_days": keep_days_setting(st.value("keep_days", KEEP_DEFAULT)),
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
        # norm_rel: las guardadas en macOS antes de 1.9.6 conservaban las mayúsculas
        self.excl_map = {k: {engine.norm_rel(x) for x in v} for k, v in js("excluded", {}).items()}
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

    def _network_roots(self) -> set[str]:
        """Carpetas de red sin letra de unidad donde hay copias: el destino actual y las habituales."""
        dsts = [self.dst_card.path()] + [it.get("dst", "") for it in self.registry.items.values()]
        return {engine.volume_root(d) for d in dsts if d and (engine.is_unc(d) or engine.gvfs_share(d))}

    def _watch_network_roots(self):
        if getattr(self, "watcher", None) is not None:
            self.watcher.set_network_roots(self._network_roots())

    def _save_settings(self):
        self._watch_network_roots()
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
            label = engine.volume_info(root)[0] or _("Disco de copia")
            where = f" ({engine.vol_name(root)})" if IS_WIN else ""
            last = engine.last_history(root)
            self.disk_status.setText(_("{label}{where} registrado  ·  última copia {v}").format(label=label, where=where, v=fmt_ago(last and last.get('fin'))))
        elif self.disk_id:
            self.disk_status.setText(_("Disco registrado, ahora desconectado. Al conectarlo se reconoce aunque "
                                     "cambie de letra o de nombre."))
        else:
            self.disk_status.setText(_("El disco se registra en la primera sincronización (se crea una carpeta "
                                     "oculta .devbackup en su raíz)."))
        self.integ_status.setText(self.integrity_text(long=True))

    def _index_path(self) -> str | None:
        return os.path.join(DATA_DIR, "estado", f"{self.disk_id}.db") if self.disk_id else None

    def scrub_seconds(self) -> int:
        return int(self.scrub_combo.currentData())

    def integrity_text(self, long: bool = False) -> str:
        """Cuánto del disco tiene su contenido comprobado, según el índice."""
        path = self._index_path()
        if not path or not os.path.exists(path):
            return _("integridad: aún sin comprobar") if not long else \
                _("Aún no hay nada comprobado. Se empieza con la próxima sincronización.")
        try:
            con = sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)
            try:
                total, covered = con.execute("SELECT count(*), count(verified) FROM files").fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            return _("integridad: sin datos")
        frac = covered / total if total else 0.0
        if not long:
            return _("integridad comprobada en el {frac} del disco").format(frac=pct(frac))
        rest = "" if covered >= total else (("  ·  " + _("faltan {total}; se van revisando tras cada sincronización, empezando por los nunca "
                                              "comprobados").format(total=fmt_int(total - covered))))
        return _("Contenido comprobado: {covered} de {total} archivos ({frac}){rest}.").format(covered=fmt_int(covered), total=fmt_int(total), frac=pct(frac), rest=rest)

    def _on_watch_toggled(self, on: bool):
        try:
            set_autostart(on)
        except OSError as exc:
            QMessageBox.warning(self, APP_NAME, _("No se pudo configurar el arranque automático:\n{exc}").format(exc=exc))
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
            menu.addAction(glyph_icon(G.OPEN, C["text"], 14), _("Abrir"), self._show_window)
            menu.addAction(glyph_icon(G.PLAY, C["text"], 14), _("Sincronizar ahora"), self._tray_sync)
            menu.addAction(glyph_icon(G.HELP, C["text"], 14), _("Guía rápida"), lambda: self.show_tour())
            menu.addSeparator()
            menu.addAction(glyph_icon(G.CLOSE, C["text"], 14), _("Salir"), self._quit)
            self._tray_menu = menu
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(self._on_tray_activated)
            self.tray.messageClicked.connect(self._show_window)
        self.tray.setToolTip(_("{app_name} · vigilando el disco de copia").format(app_name=APP_NAME))
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
        for root in sorted(engine.mounted_volumes(extra=self._network_roots())):
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
        self.plesk.refresh()
        QTimer.singleShot(30_000, self.plesk.check_due)  # copias de Plesk pendientes, sin pisar el aviso del disco
        if self._state in RUNNING or (self.dialog and self.dialog.isVisible()):
            return
        action = self.connect_combo.currentData()
        if action == "none":
            self._notify(_("Disco de copia conectado"), _("{new_dst} listo. Abre {app_name} para sincronizar.").format(new_dst=new_dst, app_name=APP_NAME))
            return
        self._show_window()
        if action == "auto":
            self._notify(_("Disco de copia conectado"), _("Sincronizando…"))
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
                chip.setToolTip(_("Regla propia · clic derecho para quitarla"))
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
        act = menu.addAction(glyph_icon(G.CLOSE, C["err"], 12), _("Quitar «{name}»").format(name=name))
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
                                       + ("  ·  " + _("{n} carpetas de primer nivel").format(n=fmt_int(n))))
        else:
            self.tree.clear()
        self._on_tree_changed()

    def _on_src_changed(self):
        self._load_tree()
        self._invalidate_plan()
        if hasattr(self, "recover"):
            self.recover.mark_stale()

    def _on_dst_changed(self):
        self._adopt_disk()
        self._invalidate_plan()
        if hasattr(self, "recover"):
            self.recover.mark_stale()
        if hasattr(self, "plesk"):
            self.plesk.refresh()
        if hasattr(self, "cloud_state"):
            self._check_cloud()

    def _pair_cloud(self):
        if self._state in RUNNING:
            QMessageBox.information(self, APP_NAME, _("Espera a que termine la copia en curso."))
            return
        dlg = PairDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.result_path:
            return
        self.dst_card.set_path(dlg.result_path)
        if dlg.link_host:
            self.settings.setValue("nube_host", dlg.link_host)
        self._on_dst_changed()
        self._save_settings()
        QMessageBox.information(self, APP_NAME, _("Conectado. Las copias se guardarán en {path}.").format(path=dlg.result_path))

    def _check_cloud(self):
        """¿El destino es una carpeta de Personal Cloud Storage? ¿Responde? Todo fuera de la ventana."""
        dst = self.dst_card.path()
        saved = str(self.settings.value("nube_host", "") or "")
        self._cloud_seq = getattr(self, "_cloud_seq", 0) + 1
        seq = self._cloud_seq

        def work():
            cloudlink.ensure_mounted(dst)  # Linux: tras reiniciar, la vuelve a montar
            parts = cloudlink.unc_parts(dst)
            if parts is None:
                state, host, new_dst = "none", "", ""
            else:
                host = parts[0]
                ok = cloudlink.probe(host)
                state = "ok" if ok else ("down" if host.lower() == saved.lower() else "none")
                new_dst = cloudlink.current_share_path(dst) if ok else ""
            if seq == self._cloud_seq:  # el destino no ha cambiado entretanto
                try:
                    self.cloud_checked.emit(host, new_dst or "", state)
                except RuntimeError:  # la ventana ya se cerró
                    pass

        threading.Thread(target=work, name="comprobar-nube", daemon=True).start()

    def _on_cloud_checked(self, host: str, new_dst: str, state: str):
        name = host.split(".")[0]
        if state == "ok":
            if host and str(self.settings.value("nube_host", "") or "").lower() != host.lower():
                self.settings.setValue("nube_host", host)
            if new_dst and os.path.normcase(new_dst) != os.path.normcase(self.dst_card.path()) and self._state not in RUNNING:
                # la carpeta de la nube cambió de nombre (p. ej. «Copias» → «DEV_Backup_Studio»)
                self.dst_card.set_path(new_dst)
                self._on_dst_changed()
                self._save_settings()
            self.cloud_state.setText("✓  " + _("Conectado a tu nube «{name}»").format(name=name))
            self.cloud_state.setStyleSheet(f"color: {C['ok']};")
            self.cloud_state.show()
            self.cloud_btn.setText("  " + _("Volver a conectar…"))
        elif state == "down":
            self.cloud_state.setText(_("Tu nube «{name}» no responde ahora").format(name=name))
            self.cloud_state.setStyleSheet(f"color: {C['warn']};")
            self.cloud_state.show()
            self.cloud_btn.setText("  " + _("Volver a conectar…"))
        else:
            self.cloud_state.hide()
            self.cloud_btn.setText("  " + _("Conectar con mi nube…"))

    def _on_tree_changed(self):
        n = len(self.tree.excluded)
        self.excl_count.setText(_n("{n} excluida a mano", "{n} excluidas a mano", n).format(n=n) if n else "")
        self._update_check_buttons()
        self._invalidate_plan()

    def _reset_exclusions(self):
        self.tree.clear_exclusions()

    def _check_scope(self, checked: bool):
        self.tree.set_checked(self.tree.scope(), checked)

    def _update_check_buttons(self):
        """Los botones dicen sobre qué actúan (todo o la selección) y se apagan si no hay nada que hacer."""
        items = self.tree.scope()
        n = len(items)
        if n:
            self.b_check_all.setText(_n("Marcar {n} seleccionada", "Marcar {n} seleccionadas", n).format(n=n))
            self.b_uncheck_all.setText(_n("Desmarcar {n} seleccionada", "Desmarcar {n} seleccionadas", n).format(n=n))
            tip_on = _("Incluye las carpetas seleccionadas y todo lo que tienen dentro.")
            tip_off = _("Excluye las carpetas seleccionadas y todo lo que tienen dentro.")
        else:
            self.b_check_all.setText(_("Marcar todo"))
            self.b_uncheck_all.setText(_("Desmarcar todo"))
            tip_on = _("Incluye todas las carpetas (quita las exclusiones hechas a mano; las reglas se mantienen).")
            tip_off = _("Excluye todas las carpetas. Los archivos sueltos de la raíz del origen se siguen copiando.")
        self.b_check_all.setToolTip(tip_on)
        self.b_uncheck_all.setToolTip(tip_off)
        self.b_check_all.setEnabled(self.tree.can_check(items))
        self.b_uncheck_all.setEnabled(self.tree.can_uncheck(items))

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
        parts = [_(engine.MODES[self.mode_combo.currentData()]),
                 _n("{n} regla activa", "{n} reglas activas", len(rules)).format(n=len(rules))]
        if manual:
            parts.append(_n("{n} carpeta excluida a mano", "{n} carpetas excluidas a mano", manual).format(n=manual))
        parts.append(_n("{n} hilo", "{n} hilos", self.threads_spin.value()).format(n=self.threads_spin.value()))
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
            self.changes_empty.setText(_("Todo está al día: no hay nada que copiar, mover ni guardar en versiones."))
        elif not job:
            self.changes_empty.setText(_("Pulsa «Analizar» para ver exactamente qué se va a copiar, mover o guardar "
                                       "en versiones antes de tocar nada."))
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
        self.btn_cancel.setText(("  " + _("Omitir revisión")) if scrubbing else ("  " + _("Cancelar")))
        self.btn_open.setVisible(not running)
        if hasattr(self, "recover"):
            self.recover.update_actions()
        if state == "paused":
            self.btn_pause.setText(("  " + _("Reanudar")))
            self.btn_pause.setIcon(glyph_icon(G.PLAY, C["warn"], 14))
        else:
            self.btn_pause.setText(("  " + _("Pausar")))
            self.btn_pause.setIcon(glyph_icon(G.PAUSE, C["text"], 14))
        if state == "ready" and self.job and self.job.stats.has_changes:
            n = self.job.stats.ops_total
            self.btn_start.setText("  " + _n("Sincronizar {n} cambio", "Sincronizar {n} cambios", n).format(n=fmt_int(n)))
        else:
            self.btn_start.setText(("  " + _("Sincronizar")))
        text, color = PILL[state]
        if state == "copying" and self.job is not None and self.job.stats.step == "verificando":
            text, color = _("Comprobando"), C["accent2"]
        self.pill.setText(f"●  {text}")
        self.pill.setStyleSheet(f"QLabel#pill {{ color:{color}; background:{rgba(color, 30)}; "
                                f"border:1px solid {rgba(color, 90)}; }}")
        if self.taskbar:
            self.taskbar.state({"scanning": Taskbar.BUSY, "copying": Taskbar.NORMAL,
                                "paused": Taskbar.PAUSED}.get(state, Taskbar.OFF))
        if self.tray:
            self.tray.setToolTip(f"{APP_NAME} · {mid(text)}")
        if not running:
            self.setWindowTitle(APP_NAME)
        if hasattr(self, "remind_bar"):
            if running:
                self.remind_bar.conceal()
            else:
                self._refresh_remind_bar()

    def _show_idle_banner(self):
        dst = self.dst_card.path() or _("el destino")
        self.banner.set("info", _("Listo para sincronizar"),
                        _("«Analizar» enseña qué ha cambiado sin tocar nada. «Sincronizar» deja {dst} igual que el "
                          "origen: copia lo nuevo y lo modificado, mueve lo renombrado y guarda en versiones lo que "
                          "borres.").format(dst=dst), G.INFO)

    # ── operación ────────────────────────────────────────────────────────
    def _validate(self, src: str, dst: str) -> str | None:
        if not src or not os.path.isdir(src):
            return _("La carpeta de origen no existe.")
        if not dst:
            return _("Elige una carpeta de destino.")
        ns, nd = os.path.normcase(os.path.abspath(src)), os.path.normcase(os.path.abspath(dst))
        if ns == nd:
            return _("El origen y el destino son la misma carpeta.")
        if nd.startswith(ns.rstrip("\\/") + os.sep):
            return _("El destino no puede estar dentro del origen.")
        if not engine.volume_present(dst):
            if engine.is_network(dst):
                return _("No se llega a la carpeta de red {dst}. Comprueba que el servidor está encendido y "
                         "en la misma red.").format(dst=dst)
            return _("El disco de {dst} no está conectado.").format(dst=dst)
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
            for attr in ("threads", "verify", "scrub_seconds", "keep_days"):
                setattr(self.job, attr, getattr(job, attr))
            job = self.job
            job.held = False  # antes de arrancar el hilo: la ventana no debe ver la retención anterior
        else:
            if self.job and self.job.phase == "ready":
                self.job.discard()
            self._reset_progress()
            self._op_start = time.perf_counter()
            self._op_wall = datetime.now()
            self._log(_("Análisis iniciado  ·  {src}  →  {dst}").format(src=src, dst=dst), C["accent2"])
            self._log(_("Modo: {lower}  ·  reglas: {v}  ·  excluidas a mano: {excluded}").format(lower=mid(_(engine.MODES[job.mode])), v=', '.join(job.patterns) or _("ninguna"), excluded=len(job.excluded)))
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
        self.ring.set_text(pct(0), "", _("PREPARADO"))
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
        self.seg_buttons[PAGE_ISSUES].setText(("  " + _("Incidencias")))
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
            self._log(_("Reanudado"))
        else:
            job.pause()
            self._pause_at = now
            self._log(_("En pausa"), C["warn"])
        self._tick()

    def _cancel(self):
        if not self.job or self._state not in RUNNING:
            return
        if self.job.phase == "copying" and self.job.stats.step == "verificando":
            # la sincronización ya está completa: omitir la revisión no deshace nada
            self.job.cancel()
            self._log(_("Revisión de integridad omitida: la sincronización ya estaba completa."), C["warn"])
            return
        what = _("la sincronización") if self.job.phase == "copying" else _("el análisis")
        ans = QMessageBox.question(self, APP_NAME, _("¿Cancelar {what}?\n\nLo que ya se haya copiado se conserva; el archivo a medias se descarta.").format(what=what))
        if ans == QMessageBox.StandardButton.Yes:
            self.job.cancel()
            self._log(_("Cancelando…"), C["warn"])

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
        # detenida antes de copiar por un borrado masivo: espera a que se confirme
        held = self._copy_requested and phase == "ready" and job.held
        if (phase in terminal or held) and not self._finalized:
            self._finalized = True
            self.timer.stop()
            self._finish(job, phase)

    def _plan_text(self, s: engine.Stats) -> str:
        text = self._plan_counts(s)
        if s.case_renames:
            text += "  ·  " + _n("{n} con otras mayúsculas", "{n} con otras mayúsculas",
                                 s.case_renames).format(n=fmt_int(s.case_renames))
        return text

    def _plan_counts(self, s: engine.Stats) -> str:
        return (_("{new_files} nuevos ({new_bytes})  ·  {changed_files} modificados ({changed_bytes})  ·  "
                  "{moved_files} movidos  ·  {del_files} a versiones  ·  {skip_files} sin cambios").format(new_files=fmt_int(s.new_files), new_bytes=fmt_bytes(s.new_bytes), changed_files=fmt_int(s.changed_files), changed_bytes=fmt_bytes(s.changed_bytes), moved_files=fmt_int(s.moved_files), del_files=fmt_int(s.del_files), skip_files=fmt_int(s.skip_files)))

    def _render_scan(self, job: engine.BackupJob, now: float):
        s = job.stats
        elapsed = now - self._op_start
        active = max(1e-6, elapsed - self._paused_total - self._current_pause(now))
        self.ring.set_busy(not job.paused)
        self.ring.set_text(fmt_int(s.files_found), _("archivos revisados"), _("ANALIZANDO"))
        title = _("Analizando el origen con el índice de la última copia…") if s.fast else _("Analizando origen y destino…")
        self.banner.set("run", title, (_("{dirs_scanned} carpetas  ·").format(dirs_scanned=fmt_int(s.dirs_scanned)) + "  ") + self._plan_text(s), G.SEARCH)
        self.t_elapsed.set(fmt_clock(elapsed), _("desde las {op_wall:%H:%M:%S}").format(op_wall=self._op_wall))
        self.t_left.set("—", _("se calcula al terminar el análisis"))
        self.t_speed.set(f"{fmt_int(s.files_found / active)}/s", _("archivos revisados por segundo"))
        self.t_eta.set("—", "")
        self.t_copied.set("0 B", _("{new_bytes} por copiar").format(new_bytes=fmt_bytes(s.new_bytes + s.changed_bytes)))
        self.t_files.set("0", _("{new_files} por copiar").format(new_files=fmt_int(s.new_files + s.changed_files)))
        self.t_skipped.set(fmt_int(s.skip_files), _("{skip_bytes} ya al día").format(skip_bytes=fmt_bytes(s.skip_bytes)))
        self._render_errors_tile(s)
        self.cur_label.setText(s.current_dir)
        self.setWindowTitle(_("Analizando… · {app_name}").format(app_name=APP_NAME))

    def _on_copy_started(self, job: engine.BackupJob):
        s = job.stats
        if s.scan_end:
            self._log_scan_summary(job)
        self._log(_("Sincronización iniciada con {threads} hilos  ·  {ops_total} cambios  ·  {plan_bytes} por "
                    "copiar").format(threads=job.threads, ops_total=fmt_int(s.ops_total), plan_bytes=fmt_bytes(s.plan_bytes)), C["accent"])
        self._set_active_rows(job.threads)
        self.ring.set_busy(False)
        self.banner.set("run", _("Sincronizando  {src}  →  {dst}").format(src=job.src, dst=job.dst),
                        self._plan_text(s) + ("  ·  " + _("análisis en {scan_end}").format(scan_end=fmt_clock(s.scan_end - s.scan_start, ms=True))),
                        G.SYNC)

    def _scrub_left(self, job: engine.BackupJob) -> float | None:
        s = job.stats
        if job.scrub_seconds <= 0 or not s.scrub_start:
            return None
        return max(0.0, job.scrub_seconds - (time.perf_counter() - s.scrub_start))

    def _on_scrub_started(self, job: engine.BackupJob):
        s = job.stats
        limit = (_("como mucho {scrub_seconds}").format(scrub_seconds=fmt_human(job.scrub_seconds)) if job.scrub_seconds > 0
                 else _("todo el disco, sin límite de tiempo"))
        self._log(_("Sincronización completada. Revisión de integridad: {scrub_due} archivos pendientes de "
                    "comprobar ({limit}).").format(scrub_due=fmt_int(s.scrub_due), limit=limit), C["accent2"])
        self.banner.set("run", _("Sincronización completada  ·  revisando la integridad del disco"),
                        _("Relee del disco una tanda de archivos ya copiados para detectar daños silenciosos "
                          "({limit}). La copia ya está completa: puedes terminar ahora con «Omitir revisión».").format(limit=limit), G.SHIELD)
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
            "limpieza": _("Quitando temporales de copias interrumpidas…"),
            "carpetas": _("Creando carpetas nuevas en el destino…  {dirs_created} / {dirs_to_create}").format(dirs_created=fmt_int(s.dirs_created), dirs_to_create=fmt_int(s.dirs_to_create)),
            "moviendo": _("Moviendo archivos renombrados o movidos…  {moves_done} / {moved_files}").format(moves_done=fmt_int(s.moves_done), moved_files=fmt_int(s.moved_files)),
            "versionando": _("Guardando en versiones lo borrado del origen…  {deletes_done} / {del_files}").format(deletes_done=fmt_int(s.deletes_done), del_files=fmt_int(s.del_files)),
            "purgando": _("Quitando versiones más antiguas que el plazo elegido…"),
            "haciendo sitio": _("Falta espacio: quitando las versiones más antiguas para hacer sitio…"),
            "índice": _("Guardando el índice para que el próximo análisis sea rápido…"),
            "verificando": self._scrub_text(s),
        }.get(s.step, "")

    def _scrub_text(self, s: engine.Stats) -> str:
        job = self.job
        text = (_("Comprobando la integridad del disco…  {scrub_files} archivos ({scrub_bytes})  ·  "
                  "{scrub_repaired} reparados  ·  {scrub_alerts} avisos").format(scrub_files=fmt_int(s.scrub_files), scrub_bytes=fmt_bytes(s.scrub_bytes), scrub_repaired=fmt_int(s.scrub_repaired), scrub_alerts=fmt_int(s.scrub_alerts)))
        if job and job.scrub_seconds > 0 and s.scrub_start:
            left = job.scrub_seconds - (time.perf_counter() - s.scrub_start)
            text += ("  ·  " + _("{v} de revisión restantes").format(v=fmt_human(max(0, left))))
        return text

    def _render_copy(self, job: engine.BackupJob, now: float, final: bool = False):
        s = job.stats
        end = s.copy_end if final and s.copy_end else now
        elapsed = end - self._op_start
        t_copy = max(1e-6, end - s.copy_start - self._paused_copy - (0 if final else self._current_pause(now)))
        frac = self._progress(s)
        done_b = s.bytes_done + s.bytes_failed
        paused = job.paused and not final

        # Velocidad: media de los últimos ~6 s de copia (sin pausas), no un instante suelto.
        # Tiempo restante: modelo de coste aprendido de esta copia (datos + coste fijo por archivo,
        # engine.Estimator), más lo que falta por mover o pasar a versiones.
        est = job.estimator
        speed, fps = est.speed() if est else (0.0, 0.0)
        avg = s.bytes_done / t_copy
        threads = len(job.active)
        eta = scrub_budget = None
        confidence = 0.0
        info = job.eta()
        if info is not None and (est.active >= est.SPAN or est.has_prior):
            raw, scrub_budget, confidence = info
            raw = min(raw, 30 * 86400)
            dt = max(0.0, now - self._last_tick)
            # cuenta atrás suave: el valor nuevo entra poco a poco, sin saltos de un segundo a otro
            self._eta = raw if self._eta is None else max(0.0, self._eta - dt) * 0.8 + raw * 0.2
            eta = self._eta
        if final:
            eta = 0.0

        scrubbing = s.step == "verificando" and not final
        self.ring.set_value(frac)
        if final:
            caption = _("COMPLETADO") if job.phase == "done" else _("CANCELADO")
        else:
            caption = (_("ESPERANDO AL DISCO") if s.disk_lost_at else _("EN PAUSA") if paused
                       else _("COMPROBANDO") if s.step == "verificando" else _("SINCRONIZANDO"))
        self.ring.set_text(pct(frac), _("sincronizado · revisando integridad") if scrubbing
                           else _("{done} de {total}").format(done=fmt_bytes(s.bytes_done),
                                                             total=fmt_bytes(s.plan_bytes)), caption)
        self.bar_bytes.setValue(int(frac * 10000))
        self.bar_bytes_val.setText(f"{fmt_bytes(s.bytes_done)}  /  {fmt_bytes(s.plan_bytes)}   ·   {pct(frac)}")
        ffrac = s.ops_done / s.ops_total if s.ops_total else 1.0
        handled = s.files_done + s.moves_done + s.deletes_done
        self.bar_files.setValue(int(ffrac * 10000))
        self.bar_files_val.setText(f"{fmt_int(handled)}  /  {fmt_int(s.ops_total)}   ·   {pct(ffrac)}")

        self.t_elapsed.set(fmt_clock(elapsed), _("desde las {op_wall:%H:%M:%S}").format(op_wall=self._op_wall))
        if final:
            self.t_left.set("00:00:00", _("terminado"), C["ok"] if job.phase == "done" else None)
            self.t_eta.set(datetime.now().strftime("%H:%M:%S"), _("hora de fin real"))
        elif scrubbing and not paused and not s.disk_lost_at:
            left = self._scrub_left(job)
            if left is None:
                self.t_left.set(fmt_int(max(0, s.scrub_due - s.scrub_files)),
                                _("archivos por revisar de {scrub_due}").format(scrub_due=fmt_int(s.scrub_due)))
                self.t_eta.set("—", _("revisión sin límite de tiempo"))
            else:
                self.t_left.set(fmt_clock(left), _("revisión de integridad"))
                self.t_eta.set((datetime.now() + timedelta(seconds=left)).strftime("%H:%M:%S"),
                               _("fin de la revisión (como mucho)"))
        elif paused or s.disk_lost_at:
            why = _("esperando al disco") if s.disk_lost_at else _("en pausa")
            self.t_left.set("—", why)
            self.t_eta.set("—", why)
        elif eta is None:
            self.t_left.set("…", _("midiendo velocidad"))
            self.t_eta.set("…", "")
        else:
            note = f"≈ {fmt_human(eta)}"
            if confidence < 0.6:
                note += "  ·  " + _("afinando")
            self.t_left.set(fmt_clock(eta), note)
            fin = datetime.now() + timedelta(seconds=eta + (scrub_budget or 0))
            day = fin.date() - datetime.now().date()
            when = _("hoy") if day.days == 0 else _("mañana") if day.days == 1 else fin.strftime(_("%d/%m/%Y"))
            if scrub_budget:
                when += "  ·  " + _("con ≤ {v} de revisión").format(v=fmt_human(scrub_budget))
            self.t_eta.set(fin.strftime("%H:%M:%S"), when)
        if final:
            self.t_speed.set(fmt_speed(avg), _("media de la copia"))
        elif scrubbing:
            span = max(1e-6, time.perf_counter() - s.scrub_start) if s.scrub_start else 0
            self.t_speed.set(fmt_speed(s.scrub_bytes / span) if span else "—", _("lectura de comprobación"))
        else:
            sub = _("{n} archivos/s").format(n=fmt_dec(fps, 1) if 0 < fps < 10 else fmt_int(fps))
            if threads:
                sub += "  ·  " + _n("{n} hilo copiando", "{n} hilos copiando", threads).format(n=threads)
            self.t_speed.set(fmt_speed(speed) if not paused and (speed or fps) else "—" if paused else "…", sub)
        self.t_copied.set(fmt_bytes(s.bytes_done), _("de {total}").format(total=fmt_bytes(s.plan_bytes)))
        pending = max(0, s.ops_total - s.ops_done)
        self.t_files.set(fmt_int(handled), _("de {ops_total}  ·  {pending} pendientes").format(ops_total=fmt_int(s.ops_total), pending=fmt_int(pending)))
        self.t_skipped.set(fmt_int(s.skip_files), _("{skip_bytes} ya al día").format(skip_bytes=fmt_bytes(s.skip_bytes)))
        self._render_errors_tile(s)

        if not final and not paused and now - self._last_chart >= 0.5:
            self._last_chart = now
            self.chart.push(speed)
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
            doing = _("En pausa") if paused else _("Comprobando integridad") if scrubbing else _("Sincronizando")
            self.setWindowTitle(f"{pct(frac)} · {doing} · {APP_NAME}")
            if self.taskbar:
                self.taskbar.value(frac)
            if self.tray:
                self.tray.setToolTip(f"{APP_NAME} · {pct(frac)}")

    def _render_errors_tile(self, s: engine.Stats):
        if s.errors:
            self.t_errors.set(fmt_int(s.errors), _("ver pestaña Incidencias"), C["err"])
        else:
            self.t_errors.set("0", _("sin incidencias"), C["ok"])

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
                self._log(_("… más incidencias en la pestaña Incidencias"), C["err"])
        self._set_issues(total)
        self.seg_buttons[PAGE_ISSUES].setText(("  " + _("Incidencias  ({total})").format(total=fmt_int(total))))

    def _log_scan_summary(self, job: engine.BackupJob):
        s = job.stats
        self._log((_("Análisis completado en {scan_end}  ·  {files_found} archivos ({bytes_found})  ·").format(scan_end=fmt_clock(s.scan_end - s.scan_start, ms=True), files_found=fmt_int(s.files_found), bytes_found=fmt_bytes(s.bytes_found)) + "  ") + self._plan_text(s)
                  + ("  ·  " + _("excluidas {excluded_rule}").format(excluded_rule=fmt_int(s.excluded_rule + s.excluded_manual)))
                  + (("  ·  " + _("enlaces omitidos {links_skipped}").format(links_skipped=fmt_int(s.links_skipped))) if s.links_skipped else ""), C["ok"])
        self._log(job.index_note)

    # ── fin ──────────────────────────────────────────────────────────────
    def _apply_renamed_exclusions(self, job: engine.BackupJob):
        """Las exclusiones a mano de una carpeta renombrada pasan a su nombre nuevo (el motor ya lo aplicó)."""
        moved = job.renamed_exclusions
        if not moved or os.path.normcase(job.src) != self._excl_key():
            return
        excluded = self.tree.excluded
        for old, new in moved:
            excluded.discard(old)
            excluded.add(new)
            self._log(_("Exclusión «{old}» → «{new}»: la carpeta se renombró").format(old=old, new=new), C["accent2"])
        self.excl_map[self._excl_key()] = excluded
        self.tree.refresh()  # sin «changed»: el análisis recién hecho sigue valiendo
        n = len(excluded)
        self.excl_count.setText(_n("{n} excluida a mano", "{n} excluidas a mano", n).format(n=n) if n else "")
        self._update_check_buttons()
        self._save_settings()

    def _finish(self, job: engine.BackupJob, phase: str):
        self._guard_stop()
        self._apply_renamed_exclusions(job)
        s = job.stats
        if self._pause_at is not None:
            self._paused_total += time.perf_counter() - self._pause_at
            self._pause_at = None
        self.ring.set_busy(False)
        if phase == "ready":
            self._finish_scan(job)
            if job.held:
                QTimer.singleShot(120, lambda: self._confirm_mass(job))
        elif phase == "cancelled" and not s.copy_start:
            self.ring.set_text("—", _("análisis interrumpido"), _("CANCELADO"))
            self.banner.set("warn", _("Análisis cancelado"), _("No se ha tocado nada. Pulsa Analizar o Sincronizar "
                                                          "para empezar de nuevo."), G.WARN)
            self._log(_("Análisis cancelado"), C["warn"])
        elif phase in ("done", "cancelled"):
            self._finish_copy(job, phase)
        else:
            self.banner.set("err", _("La operación se detuvo por un error"), job.fatal or _("Error desconocido"), G.ERROR)
            self._log(_("Error: {fatal}").format(fatal=job.fatal), C["err"])
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
        detail = ((_("{files_found} archivos en origen ({bytes_found})  ·").format(files_found=fmt_int(s.files_found), bytes_found=fmt_bytes(s.bytes_found)) + "  ") + self._plan_text(s)
                  + ("  ·  " + _("libre en destino {free}\n{index_note}").format(free=fmt_bytes(free), index_note=job.index_note)))
        if not s.has_changes:
            self.ring.set_value(1)
            self.ring.set_text(_("Al día"), _("no hay nada que copiar"), _("ANÁLISIS"))
            self.banner.set("ok", _("Todo está al día  ·  análisis en {scan}").format(scan=fmt_clock(scan, ms=True)), detail, G.DONE)
        elif free and s.plan_bytes > free:
            self.ring.set_value(0, animate=False)
            self.ring.set_text(fmt_int(s.ops_total), _("cambios por aplicar"), _("ANÁLISIS"))
            self.banner.set("warn", _("No hay espacio suficiente en el destino"),
                            detail + ("  ·  " + _("faltan {plan_bytes}").format(plan_bytes=fmt_bytes(s.plan_bytes - free))), G.WARN)
        else:
            self.ring.set_value(0, animate=False)
            self.ring.set_text(fmt_int(s.ops_total), _("cambios por aplicar"), _("ANÁLISIS"))
            self.banner.set("info", _("Análisis completado en {scan}  ·  revisa la pestaña Cambios o pulsa «Sincronizar»").format(scan=fmt_clock(scan, ms=True)), detail, G.CHECK)
        mass = job.mass_deletion() if s.has_changes else None
        if mass:
            what = (_("El origen está vacío: sincronizar dejaría la copia vacía") if mass["empty"] else
                    _n("Atención: se apartarían {n} archivo de la copia ({pct})",
                       "Atención: se apartarían {n} archivos de la copia ({pct})", mass["files"]).format(
                        n=fmt_int(mass["files"]), pct=pct(mass["ratio"])))
            self.banner.set("warn", what, _("Revisa la lista en Cambios → A versiones. Antes de apartar nada se pide "
                                            "confirmación.") + "\n" + detail, G.WARN,
                            (_("Ver la lista"), self._show_mass_list))
        self.t_elapsed.set(fmt_clock(scan), _("duración del análisis"))
        eta = estimate_seconds(engine.volume_root(job.dst), s)
        self.t_left.set(fmt_clock(eta) if eta is not None else "—",
                        _("previsto según la última copia") if eta is not None else _("se mide al copiar"))
        self.t_speed.set("—", "")
        self.t_eta.set("—", "")
        self.t_copied.set("0 B", _("de {total}").format(total=fmt_bytes(s.plan_bytes)))
        self.t_files.set("0", _("de {total}").format(total=fmt_int(s.ops_total)))
        self.t_skipped.set(fmt_int(s.skip_files), _("{skip_bytes} ya al día").format(skip_bytes=fmt_bytes(s.skip_bytes)))
        self._render_errors_tile(s)
        self.cur_label.setText(job.src)
        self.bar_bytes_val.setText(f"0 B  /  {fmt_bytes(s.plan_bytes)}")
        self.bar_files_val.setText(f"0  /  {fmt_int(s.ops_total)}")
        self._load_changes(job)
        if not s.has_changes and job.phase == "ready":
            self._record_pair(job, copied=False)  # el destino ya era igual que el origen

    def _show_mass_list(self):
        self.seg_buttons[PAGE_CHANGES].click()
        self.kind_buttons["d"].click()

    def _confirm_mass(self, job: engine.BackupJob):
        """La sincronización se paró antes de apartar mucho de la copia: se pregunta qué hacer."""
        if job is not self.job or job.phase != "ready" or not job.held:
            return
        info = job.mass_deletion()
        if not info:
            return
        self._log(_("Sincronización detenida antes de copiar: apartaría {n} archivos ({pct} de la copia). "
                    "Se pide confirmación.").format(n=fmt_int(info["files"]), pct=pct(info["ratio"])), C["warn"])
        if self.isHidden() or self.isMinimized():
            self._show_window()
        self._notify(_("Sincronización en espera"), _("Iba a apartar muchos archivos de la copia. Confírmalo en la "
                                                       "ventana."))
        dlg = MassDeleteDialog(self, job, info)
        dlg.exec()
        if job is not self.job or job.phase != "ready":
            return
        if dlg.choice == "go":
            job.allow_mass_delete = True
            self._log(_("Confirmado: se sincroniza y lo borrado del origen pasa a versiones."), C["warn"])
            self._start(copy=True)
        elif dlg.choice == "view":
            self._show_mass_list()
        else:
            self._log(_("No se ha sincronizado: la copia sigue como estaba."), C["muted"])

    def _finish_copy(self, job: engine.BackupJob, phase: str):
        s = job.stats
        self.recover.mark_stale()
        self._render_copy(job, time.perf_counter(), final=True)
        total = s.copy_end - self._op_start
        copy = s.copy_end - s.copy_start
        scan = s.scan_end - s.scan_start
        avg = s.bytes_done / max(1e-6, copy - self._paused_copy)
        if phase == "done":
            self.ring.set_value(1)
            self.ring.set_text(f"100{i18n.numbers()[2]}%", _("{size} copiados").format(size=fmt_bytes(s.bytes_done)),
                               _("COMPLETADO") if not s.errors else _("CON INCIDENCIAS"))
        self._last_log = self._write_log(job, phase)
        if phase == "done":
            self._record_pair(job, copied=True)
        self._show_summary(job, phase, total, scan, copy, avg)
        parts = [_("{files_done} copiados ({bytes_done})").format(files_done=fmt_int(s.files_done), bytes_done=fmt_bytes(s.bytes_done)),
                 _("{n} movidos").format(n=fmt_int(s.moves_done)), _("{versions_saved} versiones guardadas").format(versions_saved=fmt_int(s.versions_saved)),
                 _("{skip_files} sin cambios").format(skip_files=fmt_int(s.skip_files)), _("media {speed}").format(speed=fmt_speed(avg)),
                 _("análisis {scan}").format(scan=fmt_clock(scan, ms=True)), _("copia {time}").format(time=fmt_clock(copy, ms=True))]
        if s.versions_purged:
            parts.append(_("{versions_purged} versiones antiguas retiradas").format(versions_purged=fmt_int(s.versions_purged)))
        if s.space_freed:
            parts.append(_("{size} liberados quitando versiones antiguas por falta de espacio").format(size=fmt_bytes(s.space_freed)))
        if job.verify and s.files_done:
            parts.append(_("{verified_copies} copias verificadas con SHA-256").format(verified_copies=fmt_int(s.verified_copies)))
        if s.scrub_files or s.scrub_total:
            cover = s.scrub_covered / s.scrub_total if s.scrub_total else 0
            parts.append(_("integridad: {scrub_files} revisados, {scrub_repaired} reparados, {scrub_alerts} avisos · "
                           "disco comprobado al {cover}").format(scrub_files=fmt_int(s.scrub_files), scrub_repaired=fmt_int(s.scrub_repaired), scrub_alerts=fmt_int(s.scrub_alerts), cover=pct(cover)))
        if self._paused_total > 0.5:
            parts.append(_("en pausa {paused_total}").format(paused_total=fmt_clock(self._paused_total, ms=True)))
        issues = _n("{n} incidencia", "{n} incidencias", s.errors).format(n=fmt_int(s.errors))
        if s.errors:
            parts.append(issues)
        action = (_("Ver registro"), self._open_log) if self._last_log else None
        brief = _("{src}  →  {dst}  ·  {bytes_done} copiados  ·  media {avg}").format(src=job.src, dst=job.dst, bytes_done=fmt_bytes(s.bytes_done), avg=fmt_speed(avg))
        if phase == "cancelled":
            self.banner.set("warn", _("Sincronización cancelada tras {total}").format(total=fmt_clock(total, ms=True)),
                            brief + ("  ·  " + _("lo copiado se conserva; vuelve a pulsar Sincronizar para continuar")),
                            G.WARN, action)
            self._log(_("Sincronización cancelada"), C["warn"])
        elif s.errors:
            self.banner.set("warn", _("Sincronización terminada en {total} con {issues}").format(total=fmt_clock(total, ms=True), issues=issues),
                            brief + ("  ·  " + _("detalles en la pestaña Incidencias")), G.WARN, action)
        else:
            self.banner.set("ok", _("Sincronización completada en {total}").format(total=fmt_clock(total, ms=True)), brief, G.DONE, action)
        self._log((_("Fin  ·  duración total {total}  ·").format(total=fmt_clock(total, ms=True)) + "  ") + "  ·  ".join(parts),
                  C["ok"] if phase == "done" and not s.errors else C["warn"])
        if self._last_log:
            self._log(_("Registro guardado en {last_log}").format(last_log=self._last_log))
        self.setWindowTitle(f"{_('Al día') if phase == 'done' else _('Cancelado')} · {APP_NAME}")
        self._load_changes(None)
        self.full_box.setChecked(False)
        self._refresh_disk_status()
        if phase == "done":
            self._notify(_("Copia terminada con {issues}").format(issues=issues) if s.errors else _("Copia terminada"),
                         _("{new_done} nuevos · {changed_done} modificados · {moves_done} movidos · {deletes_done} a "
                           "versiones · {dirs_created} carpetas nuevas · {total}").format(new_done=fmt_int(s.new_done), changed_done=fmt_int(s.changed_done), moves_done=fmt_int(s.moves_done), deletes_done=fmt_int(s.deletes_done), dirs_created=fmt_int(s.dirs_created), total=fmt_clock(total)))
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
            return _("de {planned} previstos").format(planned=fmt_int(planned)) if done != planned else ""

        self.s_new.set(fmt_int(s.new_done), f"{fmt_bytes(s.new_done_bytes)}  {of(s.new_done, s.new_files)}".strip())
        self.s_changed.set(fmt_int(s.changed_done),
                           f"{fmt_bytes(s.changed_done_bytes)}  {of(s.changed_done, s.changed_files)}".strip())
        self.s_moved.set(fmt_int(s.moves_done), _("renombrados o movidos, sin copiar"))
        self.s_versions.set(fmt_int(s.deletes_done), _("borrados del origen, guardados")
                            + ((" · " + _("{versions_purged} antiguos retirados").format(versions_purged=fmt_int(s.versions_purged))) if s.versions_purged else ""))
        self.s_dirs_new.set(fmt_int(s.dirs_created), _("creadas en el destino"))
        self.s_dirs_gone.set(fmt_int(s.dirs_removed), _("ya no estaban en el origen"))
        self.s_same.set(fmt_int(s.skip_files), _("{skip_bytes} ya al día").format(skip_bytes=fmt_bytes(s.skip_bytes)))
        if job.verify:
            extra = ((" · " + _("{scrub_files} revisados, {scrub_repaired} reparados").format(scrub_files=fmt_int(s.scrub_files), scrub_repaired=fmt_int(s.scrub_repaired)))
                     if s.scrub_files else "")
            self.s_checked.set(fmt_int(s.verified_copies), _("SHA-256 releyendo el disco") + extra)
        else:
            self.s_checked.set("—", _("verificación desactivada"))
        failed = s.files_failed + s.other_failed
        bits = [_("Duración {total}").format(total=fmt_clock(total, ms=True)), _("análisis {scan}").format(scan=fmt_clock(scan, ms=True)),
                _("copia {time}").format(time=fmt_clock(copy, ms=True)), _("{bytes_done} copiados a {avg}").format(bytes_done=fmt_bytes(s.bytes_done), avg=fmt_speed(avg))]
        if failed:
            bits.append(_("{failed} sin copiar").format(failed=fmt_int(failed)))
        if s.errors:
            bits.append(_n("{n} incidencia (pestaña Incidencias)", "{n} incidencias (pestaña Incidencias)",
                           s.errors).format(n=fmt_int(s.errors)))
        if s.disk_waits:
            bits.append(_n("esperó al disco {n} vez ({time})", "esperó al disco {n} veces ({time})",
                           s.disk_waits).format(n=fmt_int(s.disk_waits), time=fmt_human(s.disk_wait_s)))
        if phase == "cancelled":
            bits.append(_("cancelada: lo copiado se conserva"))
        self.summary_line.setText("  ·  ".join(bits))
        self.summary_card.show()

    def _write_log(self, job: engine.BackupJob, phase: str) -> str:
        s = job.stats
        os.makedirs(LOG_DIR, exist_ok=True)
        path = os.path.join(LOG_DIR, f"copia_{self._op_wall:%Y%m%d_%H%M%S}.log")
        end = s.copy_end or time.perf_counter()
        width = 23

        def row(label: str, value) -> str:
            return f"{label + ':':<{width}}{value}"

        rules = ", ".join(job.patterns) or _("ninguna")
        lines = [
            _("{app_name} — informe de sincronización").format(app_name=APP_NAME),
            "=" * 60,
            row(_("Inicio"), f"{self._op_wall:%Y-%m-%d %H:%M:%S}"),
            row(_("Fin"), f"{datetime.now():%Y-%m-%d %H:%M:%S}"),
            row(_("Resultado"), PILL[phase][0]),
            row(_("Origen"), job.src),
            row(_("Destino"), job.dst),
            row(_("Reglas"), rules),
            row(_("Modo"), _(engine.MODES[job.mode])),
            row(_("Análisis"), job.index_note),
            row(_("Hilos"), job.threads),
            "",
            row(_("Duración total"), fmt_clock(end - self._op_start, ms=True)),
            row("  " + _("análisis"), fmt_clock(s.scan_end - s.scan_start, ms=True)),
            row("  " + _("copia"), fmt_clock(end - s.copy_start, ms=True) if s.copy_start else "—"),
            row("  " + _("en pausa"), fmt_clock(self._paused_total, ms=True)),
            "",
            row(_("Carpetas analizadas"), fmt_int(s.dirs_scanned)),
            row(_("Archivos en origen"), f"{fmt_int(s.files_found)}  ({fmt_bytes(s.bytes_found)})"),
            row(_("Sin cambios"), f"{fmt_int(s.skip_files)}  ({fmt_bytes(s.skip_bytes)})"),
            row(_("Nuevos"), f"{fmt_int(s.new_files)}  ({fmt_bytes(s.new_bytes)})"),
            row(_("Modificados"), f"{fmt_int(s.changed_files)}  ({fmt_bytes(s.changed_bytes)})"),
            row(_("Movidos/renombrados"), _("{n} ({size}), hechos {done}").format(
                n=fmt_int(s.moved_files), size=fmt_bytes(s.moved_bytes), done=fmt_int(s.moves_done))),
            row(_("Borrados → versiones"), _("{n} ({size}), hechos {done}").format(
                n=fmt_int(s.del_files), size=fmt_bytes(s.del_bytes), done=fmt_int(s.deletes_done))),
            row(_("Copiados"), f"{fmt_int(s.files_done)}  ({fmt_bytes(s.bytes_done)})"),
            row(_("Fallidos"), fmt_int(s.files_failed + s.other_failed)),
            row(_("Versiones guardadas"), _("{n}  (en {path})").format(
                n=fmt_int(s.versions_saved), path=getattr(job, "version_root", "—"))),
            row(_("Versiones retiradas"), fmt_int(s.versions_purged)),
            row(_("Carpetas creadas"), fmt_int(s.dirs_created)),
            row(_("Carpetas quitadas"), fmt_int(s.dirs_removed)),
            row(_("Carpetas excluidas"), _("{rule} por regla, {manual} a mano").format(
                rule=fmt_int(s.excluded_rule), manual=fmt_int(s.excluded_manual))),
            row(_("Enlaces omitidos"), fmt_int(s.links_skipped)),
            row(_("Incidencias"), fmt_int(s.errors)),
            "",
            row(_("Copias verificadas"), _("{n} (SHA-256 releyendo el disco), reintentos {retries}").format(
                n=fmt_int(s.verified_copies), retries=fmt_int(s.verify_retries)) if job.verify else _("desactivado")),
            row(_("Revisión integridad"), _("{n} archivos ({size}), {repaired} reparados, {alerts} avisos").format(
                n=fmt_int(s.scrub_files), size=fmt_bytes(s.scrub_bytes), repaired=fmt_int(s.scrub_repaired),
                alerts=fmt_int(s.scrub_alerts))),
            row(_("Cobertura del disco"), _("{n} de {total} archivos comprobados").format(
                n=fmt_int(s.scrub_covered), total=fmt_int(s.scrub_total))),
        ]
        if job.errors:
            lines += ["", _("INCIDENCIAS"), "-" * 60] + [f"{p}\t{m}" for p, m in job.errors]
        cut, dcut = len(job.src), len(job.dst)
        if job.moves:
            lines += ["", _("MOVIDOS"), "-" * 60] + [f"{old[dcut:]}  →  {item[0][cut:]}" for old, item in job.moves]
        if job.deletes:
            lines += ["", _("BORRADOS DEL ORIGEN (GUARDADOS EN VERSIONES)"), "-" * 60] + [p[dcut:] for p, _s, _m in job.deletes]
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
            QMessageBox.information(self, APP_NAME, _("No hay incidencias que exportar."))
            return
        path, _flt = QFileDialog.getSaveFileName(self, _("Exportar incidencias"),
                                              os.path.join(LOG_DIR, "incidencias.txt"), _("Texto (*.txt)"))
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
        self._watch_network_roots()
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
        for root in sorted(engine.mounted_volumes(extra=self._network_roots())):
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
        ago = _("última copia {v}").format(v=fmt_ago(it.get('last_ok')))
        if not it.get("remind", True):
            return _("{ago}  ·  sin aviso").format(ago=ago), C["faint"]
        if p.get("missing"):
            return _("origen no disponible  ·  {ago}").format(ago=ago), C["faint"]
        changes = short_changes(p)
        if changes:
            return f"{changes}  ·  {ago}", C["warn"] if it.get("alert") else C["accent2"]
        if p.get("checked"):
            return _("al día  ·  {ago}").format(ago=ago), C["ok"]
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
            self.remind_status.setText(_("Comprobando cambios…"))
        else:
            self.remind_status.setText(_("Comprobado {checked}").format(checked=fmt_ago(checked)) if checked else "")
        self.remind_status.setVisible(bool(self.remind_status.text()))
        self.btn_remind_check.setEnabled(bool(items) and not self.rem_checker.busy)
        self._refresh_remind_bar(roots)
        self._refresh_summaries()

    def _refresh_remind_bar(self, roots: dict | None = None):
        alerts = self.registry.alerts() if self.remind_box.isChecked() else []
        if self.tray:
            tip = f"{APP_NAME} · {mid(PILL[self._state][0])}"
            if alerts:
                tip += " · " + _n("{n} copia con cambios sin copiar", "{n} copias con cambios sin copiar",
                                  len(alerts)).format(n=len(alerts))
            self.tray.setToolTip(tip)
        if not alerts or self._state in RUNNING:
            self._bar_target = None
            self.remind_bar.conceal()
            return
        roots = self._disk_roots() if roots is None else roots
        ready = [it for it in alerts if self._pair_dest(it, roots)]
        it = alerts[0]
        if len(alerts) == 1:
            title = _("Tienes cambios sin copiar en {it}").format(it=reminders.short_name(it['src']))
            what = reminders.changes_text(it.get("pending") or {}, fmt_int, fmt_bytes)
            text = _("{changes} desde tu última copia en {disk}, {ago}.").format(
                changes=what[:1].upper() + what[1:], disk=self._nb_label(it), ago=fmt_ago(it.get("last_ok")))
        else:
            title = _n("Tienes cambios sin copiar en {n} copia", "Tienes cambios sin copiar en {n} copias",
                       len(alerts)).format(n=len(alerts))
            text = "  ·  ".join(f"{reminders.short_name(a['src'])} → {self._nb_label(a)}: "
                                f"{short_changes(a.get('pending')) or _('con cambios')}" for a in alerts[:3])
        if ready:
            target = ready[0]
            self._bar_target = target["id"]
            text += ("  " + _("{target} está conectado.").format(target=self._nb_label(target)))
            self.remind_bar.go.setText(("  " + _("Sincronizar ahora")) if len(alerts) == 1
                                       else ("  " + _("Sincronizar {target}").format(target=reminders.short_name(target['src']))))
            self.remind_bar.go.show()
        else:
            self._bar_target = None
            text += ("  " + _("Conecta {it} para copiarlos.").format(it=self._nb_label(it))) if len(alerts) == 1 \
                else ("  " + _("Conecta el disco de copia para guardarlos."))
            self.remind_bar.go.hide()
        self.remind_bar.title.setText(title)
        self.remind_bar.text.setText(text)
        self.remind_bar.close_btn.setToolTip(_("Ocultar el aviso: vuelve dentro de {remind_days} si sigue sin copia").format(remind_days=days_text(self._remind_days())))
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
            body = _("{changes} en {folder} desde tu última copia ({ago}).").format(
                changes=what[:1].upper() + what[1:], folder=reminders.short_name(it["src"]),
                ago=fmt_ago(it.get("last_ok"))) + " "
            body += _("{label} está conectado: pulsa para sincronizar.").format(label=label) if self._pair_dest(it) \
                else _("Conecta {label} para copiarlos.").format(label=label)
        else:
            body = "  ·  ".join(f"{reminders.short_name(it['src'])}: {short_changes(it.get('pending'))}"
                                for it in items[:3]) + ". " + _("Pulsa para verlos.")
        if self.tray and self.tray.isVisible():
            self.tray.showMessage(_("Tienes cambios sin copiar"), body, app_icon(), 20000)
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
            QMessageBox.information(self, APP_NAME, _("Conecta {it} para copiar {it2}.").format(it=self._pair_label(it), it2=reminders.short_name(it['src'])))
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
                                _("Sincronizar ahora") if dest else _("Sincronizar  (conecta {it})").format(it=self._pair_label(it)))
        a_sync.setEnabled(dest is not None and not busy)
        a_use = None
        if not self._is_current_pair(it):
            a_use = menu.addAction(glyph_icon(G.SYNC, C["text"], 14), _("Usar esta copia"))
            a_use.setEnabled(not busy)
        a_check = menu.addAction(glyph_icon(G.SEARCH, C["text"], 14), _("Comprobar cambios ahora"))
        a_check.setEnabled(not busy and not self.rem_checker.busy)
        menu.addSeparator()
        a_remind = menu.addAction(glyph_icon(G.BELL, C["text"], 14), _("Avisarme de esta copia"))
        a_remind.setCheckable(True)
        a_remind.setChecked(it.get("remind", True))
        a_forget = menu.addAction(glyph_icon(G.CLOSE, C["text"], 14), _("Quitar de la lista"))
        a_forget.setToolTip(_("Vuelve a aparecer si la sincronizas otra vez"))
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
        if self.tray and self.tray.isVisible() and QSystemTrayIcon.isSystemTrayAvailable() and not self._quitting:
            # con la vigilancia activa, cerrar la ventana la deja en la bandeja
            event.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                extra = (" " + _("La sincronización sigue en marcha.")) if self._state in RUNNING else ""
                self._notify(APP_NAME, _("Sigo en la bandeja vigilando tu disco de copia.") + extra)
            return
        if self.job and self._state in RUNNING and not self.job.cancelled:
            ans = QMessageBox.question(self, APP_NAME, _("Hay una operación en curso. ¿Cancelarla y salir?\n\n"
                                       "Lo ya copiado se conserva."))
            if ans != QMessageBox.StandardButton.Yes:
                self._quitting = False
                event.ignore()
                return
            self.job.cancel()
            if self.job_thread:
                self.job_thread.join(timeout=15)
        if self.plesk.busy:
            ans = QMessageBox.question(self, APP_NAME, _("Hay una copia o restauración de Plesk en curso. ¿Detenerla y "
                                                         "salir?\n\nLo ya descargado se aprovecha la próxima vez."))
            if ans != QMessageBox.StandardButton.Yes:
                self._quitting = False
                event.ignore()
                return
        self.plesk.stop_and_wait()
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
                self._log(_("Pausa de más de {pause_grace} min: el equipo ya puede suspenderse. Al reanudar se vuelve "
                            "a mantener despierto.").format(pause_grace=PAUSE_GRACE // 60), C["warn"])
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

        # el latido mantiene despierto un disco USB; a una carpeta de red no le hace falta
        root = job.meta_root if not job.network and engine.volume_present(job.dst) else None
        self.guard.start(_("{app_name}: copia de seguridad en curso ({src} → {dst})").format(app_name=APP_NAME, src=job.src, dst=job.dst), root, activity)
        if IS_WIN:
            keepawake.block_shutdown(int(self.winId()),
                                     _("Copia de seguridad en curso ({src} → {dst}). Si apagas ahora se interrumpe; lo ya copiado "
                                       "se conserva.").format(src=job.src, dst=job.dst))
        if not self._guard_logged:
            self._guard_logged = True
            parts = [_("el equipo no se suspende ({v})").format(v=_(self.guard.method) if self.guard.method else _("no disponible"))]
            if root:
                parts.append(_("latido al disco {root} si pasa {beat_seconds:.0f} s sin escribir").format(root=engine.vol_name(root), beat_seconds=keepawake.BEAT_SECONDS))
            if not IS_LINUX:
                parts.append(_("apagar o reiniciar pide confirmación") if IS_WIN
                             else _("cerrar sesión o apagar pide confirmación"))
            self._log((_("Mientras dure:") + " ") + "  ·  ".join(parts), C["muted"])
            if self.guard.error:
                self._log(_("⚠ No se pudo impedir la suspensión: {error}").format(error=self.guard.error), C["warn"])
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
                self.awake_status.setText(_("Se activa al analizar o sincronizar y se desactiva al terminar."))
            else:
                self.awake_status.setText(_("Desactivado: el equipo y el disco se suspenden con normalidad "
                                          "aunque haya una copia en curso."))
            return
        if lost:
            color, text = C["warn"], _("Esperando al disco")
        elif st["disk_error"]:
            color, text = C["warn"], _("Sin suspensión · disco sin respuesta")
        else:
            color, text = C["ok"], _("Sin suspensión") + ((" · " + _("disco activo")) if st["disk"] else "")
        self.awake_icon.setPixmap(glyph_pixmap(G.POWER, color, 13))
        self.awake_text.setText(text)
        self.awake_text.setStyleSheet(f"color:{color};")
        self.awake_chip.setStyleSheet(f"QFrame#awake {{ background:{rgba(color, 26)}; "
                                      f"border:1px solid {rgba(color, 80)}; }}")
        lines = [_("Equipo: no se suspende por inactividad ({v}).").format(v=_(st['method']) if st['method'] else _("no disponible"))]
        beat = ""
        if lost:
            beat = _("esperando a que vuelva el disco de copia")
        elif st["disk"]:
            beat = (_("sin respuesta del disco: {st}").format(st=st['disk_error']) if st["disk_error"]
                    else _("último latido hace {time}").format(time=fmt_human(time.time() - st['last_beat'])) if st["last_beat"]
                    else _("la copia mantiene el disco activo"))
            lines.append(_("Disco de copia ({st}): {beat}.").format(st=engine.vol_name(st['root']), beat=beat))
        if not IS_LINUX:
            lines.append(_("Apagar o reiniciar: Windows pide confirmación.") if IS_WIN
                         else _("Cerrar sesión o apagar: pide confirmación."))
        tip = "\n".join(lines)
        self.awake_chip.setToolTip(tip)
        since = _("Activo desde las {since:%H:%M:%S}").format(since=datetime.fromtimestamp(self.guard.since)) if st["active"] else ""
        self.awake_status.setText("  ·  ".join(x for x in (since, beat) if x))
        self.awake_chip.show()

    def _watch_disk(self, job: engine.BackupJob):
        """Avisa cuando el disco de copia desaparece a mitad y cuando vuelve."""
        s = job.stats
        if s.disk_lost_at and not self._disk_lost_seen:
            self._disk_lost_seen = True
            self._log(_("El disco de copia no responde o se ha desconectado ({dst}). La copia espera a que vuelva "
                        "y repite el archivo que estaba a medias: no se pierde nada.").format(dst=job.dst), C["warn"])
            self._notify(APP_NAME, _("El disco de copia se ha desconectado. Vuelve a conectarlo y la copia seguirá."))
            self._refresh_awake()
        elif not s.disk_lost_at and self._disk_lost_seen:
            self._disk_lost_seen = False
            self._log(_("Disco de nuevo disponible: sigue la copia (en total {disk_wait_s} esperando).").format(disk_wait_s=fmt_human(s.disk_wait_s)), C["ok"])
            self._refresh_awake()

    def _on_session_end_query(self, manager):
        """Windows pregunta si puede apagar, reiniciar o cerrar la sesión."""
        if self.guard.active and self.job and self._state in RUNNING:
            manager.cancel()
            self._log(_("Apagado, reinicio o cierre de sesión retenido: hay una copia en curso."), C["warn"])

    def _on_about_to_quit(self):
        # también al apagar de todas formas: se para la copia de forma ordenada (sin archivos a medias)
        self._quitting = True
        if self.job and self._state in RUNNING:
            self.job.cancel()
            if self.job_thread:
                self.job_thread.join(timeout=4)
        self.plesk.stop_and_wait(timeout=4)
        self._guard_stop()

    def _allow_quit(self) -> bool:
        """Petición de salir que no viene de la app. En macOS: Cmd+Q, «Salir» del Dock, cerrar sesión o apagar."""
        if not self._quitting:
            if IS_MAC and self.job and self._state in RUNNING and not self.job.cancelled:
                self._show_window()
                ans = QMessageBox.question(
                    self, APP_NAME, _("Hay una sincronización en curso. ¿Cancelarla y salir?\n\nLo ya copiado se "
                    "conserva. Si ibas a apagar o cerrar sesión, elige No y espera a que termine."))
                if ans != QMessageBox.StandardButton.Yes:
                    return False
                self.job.cancel()
                if self.job_thread:
                    self.job_thread.join(timeout=15)
            if IS_MAC and self.plesk.busy:
                self._show_window()
                ans = QMessageBox.question(self, APP_NAME, _("Hay una copia o restauración de Plesk en curso. ¿Detenerla "
                                                             "y salir?\n\nLo ya descargado se aprovecha la próxima vez."))
                if ans != QMessageBox.StandardButton.Yes:
                    return False
                self.plesk.stop_and_wait()
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
        self._log(_("El equipo se va a suspender: copia en pausa. Seguirá sola al volver."), C["warn"])

    def _on_system_resume(self):
        if not self._sleeping:
            return
        self._sleeping = False
        self._log(_("Equipo reanudado"))
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


def selftest_plesk(win: "MainWindow", say) -> None:
    """Copias de Plesk contra una imitación de la API de la extensión (en CI no hay Plesk ni PHP):
    emparejar, copiar con un corte a mitad de descarga, conservar N, restaurar, la carpeta PLESK con su
    icono y que la sincronización espejo no la toque."""
    import http.server

    state = {"jobs": {}, "uploads": {}, "devices": set(), "calls": []}
    payload = os.urandom(3_000_000)

    class Fake(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def _json(self, data, status=200):
            raw = json.dumps({**data, "protocol": plesklink.PROTOCOL}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _route(self):
            from urllib.parse import parse_qs, urlsplit
            u = urlsplit(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            a = q.get("a")
            state["calls"].append(a)
            if u.path != plesklink.API_PATH:
                return self._json({}, 404)
            if a == "pair":
                if json.loads(body)["code"] != "123456":
                    return self._json({"error": "code"}, 403)
                state["devices"].add("t" * 64)
                return self._json({"token": "t" * 64, "device": "d1", "server": {"hostname": "plesk.test",
                                                                                  "plesk_version": "18.0.70"}})
            if self.headers.get("X-DBS-Token") not in state["devices"]:
                return self._json({"error": "auth"}, 401)
            if a == "info":
                return self._json({"server": {"hostname": "plesk.test", "plesk_version": "18.0.70"}})
            if a == "backup":
                jid = uuid_hex()
                state["jobs"][jid] = {"id": jid, "kind": "backup", "state": "done", "size": len(payload),
                                      "sha256": hashlib.sha256(payload).hexdigest(), "log": ["Backup finished"]}
                return self._json({"job": state["jobs"][jid]})
            if a == "job":
                return self._json({"job": state["jobs"][q["id"]]})
            if a == "download":
                start = int((self.headers.get("Range") or "bytes=0-")[6:].rstrip("-"))
                cut = state.pop("cut", None)  # la primera descarga se corta a mitad
                data = payload[start:]
                self.send_response(206 if start else 200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data[:cut] if cut else data)
                if cut:
                    self.close_connection = True
                return None
            if a in ("release", "cancel"):
                state["jobs"].pop(q["id"], None)
                return self._json({})
            if a == "upload_start":
                meta = json.loads(body)
                state["uploads"]["u1"] = {"meta": meta, "data": b""}
                return self._json({"upload": {"id": "u1", "received": 0}})
            if a == "upload_chunk":
                up = state["uploads"][q["id"]]
                if int(q["offset"]) == len(up["data"]):
                    up["data"] += body
                return self._json({"upload": {"id": "u1", "received": len(up["data"])}})
            if a == "restore":
                req = json.loads(body)
                up = state["uploads"][req["upload"]]
                ok = hashlib.sha256(up["data"]).hexdigest() == up["meta"]["sha256"]
                state["restored"] = (req["mode"], ok)
                state["jobs"]["r1"] = {"id": "r1", "kind": "restore", "state": "done" if ok else "failed"}
                return self._json({"job": state["jobs"]["r1"]})
            return self._json({"error": "server"}, 400)

        do_GET = do_POST = _route

    def uuid_hex() -> str:
        return os.urandom(16).hex()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = plesklink.normalize_url(f"http://127.0.0.1:{server.server_address[1]}")
    saved_poll, plesklink.POLL_S = plesklink.POLL_S, 0.05
    try:
        with tempfile.TemporaryDirectory() as vol:
            try:
                plesklink.pair(url, "000000", "PC")
                raise AssertionError("Plesk: aceptó un código erróneo")
            except plesklink.PleskError as exc:
                assert exc.kind == "code", exc.kind
            for kind in ("code", "unreachable", "cert", "auth", "noext", "old", "busy", "running", "server", "space",
                         "serverspace", "signature", "verify", "missing", "cancelled", "otro"):
                for detail in ("", "[WinError 10061] rechazada", "1234567"):
                    assert plesk_error_text(plesklink.PleskError(kind, detail)), f"Plesk: sin texto para {kind}"
            srv, token = plesklink.pair(url, "123 456", "PC")
            assert srv.label == "plesk.test" and token == "t" * 64, "Plesk: emparejar"
            folder = os.path.join(plesklink.plesk_root(vol), srv.folder_name)
            state["cut"] = 1_000_000
            op = plesklink.BackupRun(srv, token, "clave", folder, keep=2)
            op.run()
            assert op.phase == "done", f"Plesk: copia {op.phase} {op.error}"
            with open(op.result.path, "rb") as fh:
                assert fh.read() == payload, "Plesk: la copia descargada no es igual"
            assert "release" in state["calls"] and not state["jobs"], "Plesk: no se borró del servidor"
            for i in range(2):
                time.sleep(1.05)
                again = plesklink.BackupRun(srv, token, "clave", folder, keep=2)
                again.run()
                assert again.phase == "done", again.error
            kept = plesklink.list_backups(folder)
            assert len(kept) == 2 and kept[0].sha256 == hashlib.sha256(payload).hexdigest(), "Plesk: conservar 2"
            if IS_WIN:
                root = plesklink.plesk_root(vol)
                assert os.path.exists(os.path.join(root, "desktop.ini")), "Plesk: carpeta sin icono"
                assert ctypes.windll.kernel32.GetFileAttributesW(root) & 0x1, "Plesk: desktop.ini no se aplicará"
            rest = plesklink.RestoreRun(srv, token, kept[0], "clave", "server")
            rest.run()
            assert rest.phase == "done" and state.get("restored") == ("server", True), f"Plesk: restaurar {rest.error}"
            # la sincronización espejo con el destino en la raíz del disco no toca la carpeta PLESK
            with tempfile.TemporaryDirectory() as side:
                src, meta = os.path.join(side, "src"), os.path.join(side, "meta")
                os.makedirs(os.path.join(src, "PLESK"))
                os.makedirs(meta)
                with open(os.path.join(src, "a.txt"), "w") as fh:
                    fh.write("a")
                with open(os.path.join(src, "PLESK", "otro.txt"), "w") as fh:
                    fh.write("no se copia encima")
                job = engine.BackupJob(src, vol, [], [], meta_root=meta, scrub_seconds=0)
                job.dst_is_root = True
                job.allow_mass_delete = True
                job.run()
                assert job.phase == "done" and len(plesklink.list_backups(folder)) == 2, "Plesk: la sincronización tocó PLESK"
                assert not os.path.exists(os.path.join(folder, "..", "otro.txt")), "Plesk: se copió encima de PLESK"
                assert os.path.exists(os.path.join(vol, "a.txt")), "Plesk: la sincronización no copió"
            # la página: conectar y quitar
            page = win.plesk
            before = list(page.servers)
            page.servers.append(srv)
            page.refresh()
            assert page.list.count() >= 1 and page.summary()
            page.servers[:] = before
            page.refresh()
        say("  Plesk: emparejar con código, copia con corte a mitad y SHA-256, conservar 2, restaurar, "
            "carpeta PLESK con icono y a salvo de la sincronización")
    finally:
        plesklink.POLL_S = saved_poll
        server.shutdown()


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
                beat_file = os.path.join(meta, ".devbackup", keepawake.BEAT_FILE)
                deadline = time.monotonic() + 10.0   # el runner de CI puede ir lento: no basta un tiempo fijo
                while time.monotonic() < deadline and not guard.status()["beats"]:
                    time.sleep(0.2)
                st = guard.status()
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
        # los diálogos se cierran (QDialog.done) sin errores: ningún atributo tapa sus métodos
        for cls in (PairDialog, ConnectDialog, UpdateDialog):
            clash = [n for n in vars(cls) if n in ("done", "accept", "reject", "exec", "open", "result")
                     and isinstance(vars(cls)[n], pyqtSignal)]
            assert not clash, f"{cls.__name__}: {clash} tapa un método de QDialog"
        # recargar el árbol de carpetas conserva las exclusiones a mano (y no tapa métodos de Qt)
        with tempfile.TemporaryDirectory() as tree_root:
            os.makedirs(os.path.join(tree_root, "a", "b"))
            kept = {engine.norm_rel(os.path.join("a", "b"))}
            tree = FolderTree()
            tree.load(tree_root, kept, [])
            tree.load(tree_root, kept, [])
            assert kept == {engine.norm_rel(os.path.join("a", "b"))}, "recargar el árbol borra las exclusiones"
            assert "reset" not in vars(FolderTree), "FolderTree.reset tapa QAbstractItemView.reset"
        say("  árbol de carpetas: recargar conserva las exclusiones")
        # renombrar: carpeta entera (con archivos repetidos), exclusión que la sigue y solo mayúsculas
        with tempfile.TemporaryDirectory() as ren:
            rsrc, rdst = os.path.join(ren, "src"), os.path.join(ren, "dst")
            for rel in ["P/a/__init__.py", "P/b/__init__.py", "P/a/m.py", "P/build/o.bin", "P/lib/readme.md"]:
                os.makedirs(os.path.dirname(os.path.join(rsrc, rel)), exist_ok=True)
                with open(os.path.join(rsrc, rel), "wb") as fh:
                    fh.write(b"" if rel.endswith("__init__.py") else os.urandom(200))
            excl = [engine.norm_rel(os.path.join("P", "build"))]

            def ren_run():
                j = engine.BackupJob(rsrc, rdst, [], excl, meta_root=rdst, scrub_seconds=0)
                j.run(copy=True)
                assert j.phase == "done" and not j.errors, j.errors
                return j

            ren_run()
            os.rename(os.path.join(rsrc, "P"), os.path.join(rsrc, "Q"))
            j = ren_run()
            assert (j.stats.new_files, j.stats.moved_files, j.stats.del_files) == (0, 4, 0), \
                f"renombrar carpeta: {j.stats.new_files} nuevos, {j.stats.moved_files} movidos, {j.stats.del_files} a versiones"
            assert j.renamed_exclusions and not os.path.exists(os.path.join(rdst, "Q", "build")), "la exclusión no siguió a la carpeta"
            excl = [new for _old, new in j.renamed_exclusions]
            os.rename(os.path.join(rsrc, "Q", "lib"), os.path.join(rsrc, "Q", "Lib"))
            j = ren_run()
            names = os.listdir(os.path.join(rdst, "Q"))
            if engine._case_sensitive:  # Linux con ext4/btrfs: «lib» y «Lib» son carpetas distintas
                assert "Lib" in names and "lib" not in names, f"cambio de mayúsculas no aplicado: {names}"
            else:
                assert j.stats.case_done == 1 and "Lib" in names, "cambio de mayúsculas no aplicado"
            assert not ren_run().stats.has_changes, "tras renombrar quedan cambios pendientes"
            # mover dos subcarpetas hermanas a sitios distintos, una solo con archivos repetidos
            for rel in ["Q/x/__init__.py", "Q/x/n.py", "Q/y/__init__.py", "Q/y/k/__init__.py"]:
                os.makedirs(os.path.dirname(os.path.join(rsrc, rel)), exist_ok=True)
                with open(os.path.join(rsrc, rel), "wb") as fh:
                    fh.write(os.urandom(120) if rel.endswith("n.py") else b"")
            ren_run()
            os.makedirs(os.path.join(rsrc, "R", "S"))
            os.rename(os.path.join(rsrc, "Q", "x"), os.path.join(rsrc, "R", "x"))
            os.rename(os.path.join(rsrc, "Q", "y"), os.path.join(rsrc, "R", "S", "y"))
            j = ren_run()
            assert (j.stats.new_files, j.stats.moved_files, j.stats.del_files) == (0, 4, 0), \
                f"mover subcarpetas: {j.stats.new_files} nuevos, {j.stats.moved_files} movidos, {j.stats.del_files} a versiones"
        say("  renombrar y mover carpetas: enteras, a otra subcarpeta, exclusiones y mayúsculas, sin recopiar nada")
        # borrado masivo: se detiene antes de copiar y pide confirmación; versiones: se recuperan sin pisar nada
        assert [d for d, _t in KEEP_OPTIONS] == [7, 30, 60] and keep_days_setting(0) == 60             and keep_days_setting("365") == 60 and keep_days_setting(None) == KEEP_DEFAULT, "plazos de versiones"
        with tempfile.TemporaryDirectory() as mass:
            msrc, mdst, mvol = (os.path.join(mass, x) for x in ("src", "dst", "vol"))
            os.makedirs(mvol)
            for d in ("a", "b"):
                for i in range(60):
                    p = os.path.join(msrc, d, f"f{i}.txt")
                    os.makedirs(os.path.dirname(p), exist_ok=True)
                    with open(p, "w") as fh:
                        fh.write(d * (i + 1))

            def mass_run(allow=False):
                j = engine.BackupJob(msrc, mdst, [], [], meta_root=mvol, scrub_seconds=0)
                j.allow_mass_delete = allow
                j.run()
                return j

            assert mass_run().phase == "done"
            shutil.rmtree(os.path.join(msrc, "a"))
            time.sleep(1.1)  # otra fecha de versiones
            j = mass_run()
            info = j.mass_deletion()
            assert j.held and j.phase == "ready" and info and info["folders"][0][:2] == ("a", 60), "borrado masivo sin parar"
            assert len(os.listdir(os.path.join(mdst, "a"))) == 60, "se apartó sin confirmar"
            j.allow_mass_delete = True
            j.run()
            assert j.phase == "done" and j.stats.deletes_done == 60 and not os.path.exists(os.path.join(mdst, "a"))
            with open(os.path.join(msrc, "b", "f1.txt"), "w") as fh:
                fh.write("cambiado")
            time.sleep(1.1)
            assert mass_run().stats.versions_saved == 1
            snaps = versions.load(mvol, mdst)
            assert [len(x.files) for x in snaps] == [1, 60], [len(x.files) for x in snaps]
            items = [(os.path.join(x.path, rel), os.path.join(msrc, rel), size, mt)
                     for x in snaps for rel, size, mt in x.files]
            res = versions.restore(items)
            assert (res.restored, res.renamed, res.same, res.failed) == (60, 1, 0, []), vars(res)
            with open(os.path.join(msrc, "b", "f1.txt")) as fh:
                assert fh.read() == "cambiado", "recuperar sobrescribió el archivo actual"
            assert versions.restore(items).same == 60, "recuperar dos veces duplica"
            assert all(os.path.exists(i[0]) for i in items), "recuperar movió la versión"
            # la página «Recuperar» lee las fechas y recupera desde la ventana
            win.recover.target = lambda: (mvol, os.path.normpath(mdst), msrc)
            user_src = win.src_card.path()  # al cerrar se guardan los ajustes: se deja como estaba
            win.src_card.set_path(msrc)
            win.recover.reload()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and win.recover.stack.currentWidget() is win.recover.loading:
                app.processEvents()
                time.sleep(0.05)
            app.processEvents()
            assert win.recover.timeline.count() == 4 and win.recover.tree.topLevelItemCount() == 2,                 f"página Recuperar: {win.recover.timeline.count()} filas"
            shutil.rmtree(os.path.join(msrc, "a"))
            win.recover.reload()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not win.recover.kind_buttons["d"].text().endswith("60"):
                app.processEvents()
                time.sleep(0.05)
            folder = next(win.recover.tree.topLevelItem(i) for i in range(2)
                          if win.recover.tree.topLevelItem(i).text(0) == "a")
            folder.setSelected(True)
            win.recover._restore()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and win.recover.busy:
                app.processEvents()
                time.sleep(0.05)
            assert len(os.listdir(os.path.join(msrc, "a"))) == 60, "la página no recuperó la carpeta"
            win.recover.target = types.MethodType(RecoverPage.target, win.recover)
            win.src_card.set_path(user_src)
            # origen vacío: también se detiene
            shutil.rmtree(msrc)
            os.makedirs(msrc)
            j = mass_run()
            assert j.held and j.mass_deletion()["empty"], "origen vacío sin parar"
        say("  borrado masivo: se detiene y pide confirmación · recuperar: sin sobrescribir, sin duplicar")
        pair = PairDialog(win)
        pair.accept()  # pasa por QDialog.done(): si algo lo tapa, el resultado no cambia
        assert pair.result() == QDialog.DialogCode.Accepted, "el diálogo «Conectar con mi nube» no se cierra"
        say("  conectar con la nube: el diálogo abre y se cierra")
        selftest_plesk(win, say)
        if IS_LINUX:
            # montaje con GVFS: un «gio» de mentira con las mismas preguntas que el de verdad
            with tempfile.TemporaryDirectory() as fake:
                gvfs = os.path.join(fake, "gvfs")
                os.makedirs(gvfs)
                secret = 's3cr€t "x y$'
                with open(os.path.join(fake, "gio"), "w", encoding="utf-8") as fh:
                    fh.write("#!/bin/bash\n"
                             "for i in 1 2; do\n"
                             "  echo 'Authentication Required'\n"
                             "  read -r -p 'User [x]: ' u; read -r -p 'Domain [WORKGROUP]: ' d\n"
                             "  read -r -s -p 'Password: ' p; echo\n"
                             f"  [ \"$u\" = pc ] && [ \"$p\" = '{secret.replace(chr(39), '')}' ] && "
                             f"mkdir -p '{gvfs}/smb-share:server=nube.local,share=dev_backup_studio' && exit 0\n"
                             "done\nexit 2\n")
                os.chmod(os.path.join(fake, "gio"), 0o755)
                secret = secret.replace("'", "")
                saved = (os.environ.get("PATH", ""), os.environ.get("XDG_CONFIG_HOME"), cloudlink._gvfs_dir)
                os.environ["PATH"] = fake + os.pathsep + saved[0]
                os.environ["XDG_CONFIG_HOME"] = os.path.join(fake, "cfg")
                cloudlink._gvfs_dir = lambda: gvfs
                try:
                    bad = cloudlink.Link("nube.local", "DEV_Backup_Studio", "pc", "otra", "PC")
                    try:
                        cloudlink.connect(bad)
                        raise AssertionError("gio: aceptó una contraseña errónea")
                    except cloudlink.LinkError as exc:
                        assert exc.kind == "connect", exc.kind
                    dst = cloudlink.connect(cloudlink.Link("nube.local", "DEV_Backup_Studio", "pc", secret, "PC"))
                    assert os.path.isdir(dst) and cloudlink.unc_parts(dst)[2] == "PC", dst
                    mode = stat.S_IMODE(os.stat(cloudlink.credentials_file()).st_mode)
                    assert mode == 0o600, f"credenciales con permisos {oct(mode)}"
                    shutil.rmtree(os.path.dirname(dst))  # como tras reiniciar
                    reach, engine.host_reachable = engine.host_reachable, lambda h, port=445: True
                    try:
                        assert cloudlink.ensure_mounted(dst) and os.path.isdir(os.path.dirname(dst)), "no se volvió a montar"
                    finally:
                        engine.host_reachable = reach
                finally:
                    os.environ["PATH"] = saved[0]
                    if saved[1] is None:
                        os.environ.pop("XDG_CONFIG_HOME", None)
                    else:
                        os.environ["XDG_CONFIG_HOME"] = saved[1]
                    cloudlink._gvfs_dir = saved[2]
            say("  nube en Linux: gio mount por terminal virtual, contraseña errónea, credencial 0600, volver a montar")
        for code in i18n.LANGS:
            images, spots, _size = tour_assets(code)
            missing = [k for k, pm in images.items() if pm.isNull()]
            assert not missing and tour_folder(code).endswith(code), f"faltan ilustraciones de la guía ({code})"
        here = i18n.current()
        for code in i18n.LANGS:
            if code == i18n.SOURCE:
                continue
            assert len(i18n.catalog(code)) > 500, f"traducción incompleta: {code}"
            i18n.set_language(code)
            assert _("Sincronizar") != "Sincronizar" and "{n}" not in _n("{n} hilo", "{n} hilos", 3).format(n=3)
        i18n.set_language(here)
        say(f"  idiomas: {', '.join(i18n.LANGS)} con traducciones e ilustraciones · "
            f"diálogos de Qt en {i18n.current()}: {'sí' if install_qt_translations(app) else 'no'}")
        images, spots, _size = tour_assets()
        tour = TourOverlay(win, animate=False)
        ended: list[bool] = []
        tour.finished.connect(ended.append)
        tour.open()
        steps = len(tour.card.steps)
        assert 4 <= steps <= 6 and tour.card.idx == 0
        for _i in range(steps - 1):
            tour.card.forward()
            tour.card.grab()
        assert tour.card.idx == steps - 1 and tour.card.next_btn.text().strip() == _("Empezar")
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


def restore_child_env() -> None:
    """Linux: la app compilada pone sus bibliotecas en LD_LIBRARY_PATH; los programas que abre
    (gestor de archivos, navegador, xdg-open) tienen que usar las del sistema."""
    if not (IS_LINUX and FROZEN):
        return
    orig = os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
    if orig is not None:
        os.environ["LD_LIBRARY_PATH"] = orig
    else:
        os.environ.pop("LD_LIBRARY_PATH", None)


def main() -> int:
    global _app_mutex
    restore_child_env()
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
    app.setDesktopFileName(DESKTOP_ID)  # Wayland: une la ventana con su entrada del menú y su icono
    app.setQuitOnLastWindowClosed(False)
    install_qt_translations(app)
    if send_to_running("tray" if tray_mode else "show"):
        return 0
    install_linux_desktop()
    app.setStyle("Fusion")
    pick_fonts()
    app.setFont(QFont(FONT["ui"], 10))
    app.setPalette(dark_palette())
    app.setStyleSheet(build_qss(write_assets()))
    server = QLocalServer()
    QLocalServer.removeServer(INSTANCE)
    server.listen(INSTANCE)
    app.instance_server = server  # se cierra al reiniciar la app (cambio de idioma)
    win = MainWindow(tray_mode=tray_mode, updated="--updated" in sys.argv)
    server.newConnection.connect(lambda: win.on_instance_message(server))
    if IS_MAC:
        # clic en el icono del Dock con la ventana escondida en la barra de menús
        app.applicationStateChanged.connect(
            lambda st: win._show_window() if st == Qt.ApplicationState.ApplicationActive and win.isHidden()
            and not win.tray_only_start else None)
    if not tray_mode:
        win.show()
    elif IS_LINUX:
        # GNOME sin extensión de bandeja: sin icono no habría forma de volver a la ventana. Se mira
        # pasados unos segundos, que al iniciar sesión el panel puede tardar en aparecer.
        QTimer.singleShot(8000, lambda: win.showMinimized()
                          if win.isHidden() and not QSystemTrayIcon.isSystemTrayAvailable() else None)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
