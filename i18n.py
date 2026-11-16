"""Idiomas de la app: español (el original), inglés, francés, italiano, chino y japonés.

Los textos se escriben en español en el código y se traducen al usarlos:

    _("Sincronizar")                                   → «Sync»
    _("Tienes cambios sin copiar en {name}").format(name=…)
    _n("{n} archivo", "{n} archivos", n).format(n=fmt_int(n))

Las traducciones están en locales/<idioma>.json ({"messages": {"texto en español": "traducción" o
[singular, plural]}}). El idioma se elige en los ajustes («auto» = el del sistema) y se aplica al
arrancar la app. tools/i18n_extract.py saca la lista de textos del código.
"""

from __future__ import annotations

import json
import os
import sys

LANGS = {"es": "Español", "en": "English", "fr": "Français", "it": "Italiano", "zh": "中文（简体）", "ja": "日本語"}
SOURCE = "es"
FALLBACK = "en"  # si el idioma del sistema no está entre los de la app
LOCALES_DIR = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "locales")

# separador de miles, separador decimal y espacio antes de «%»
NUMBERS = {
    "es": (".", ",", " "), "en": (",", ".", ""), "fr": (" ", ",", " "),
    "it": (".", ",", ""), "zh": (",", ".", ""), "ja": (",", ".", ""),
}

_lang = SOURCE
_catalog: dict = {}
_loaded: dict[str, dict] = {}


def system_language() -> str:
    """Primer idioma de la interfaz del sistema que tenga la app; si no, inglés."""
    try:
        from PyQt6.QtCore import QLocale
        names = list(QLocale.system().uiLanguages()) + [QLocale.system().name()]
    except Exception:  # noqa: BLE001
        names = []
    for name in names:
        code = name.replace("_", "-").split("-")[0].lower()
        if code in LANGS:
            return code
    return FALLBACK


def resolve(setting: str | None) -> str:
    """«auto» (o nada) → idioma del sistema; un código válido → ese idioma."""
    return setting if setting in LANGS else system_language()


def catalog(lang: str) -> dict:
    if lang == SOURCE:
        return {}
    if lang not in _loaded:
        try:
            with open(os.path.join(LOCALES_DIR, f"{lang}.json"), encoding="utf-8") as fh:
                _loaded[lang] = json.load(fh).get("messages", {})
        except (OSError, ValueError):
            _loaded[lang] = {}
    return _loaded[lang]


def set_language(lang: str) -> str:
    global _lang, _catalog
    _lang = lang if lang in LANGS else SOURCE
    _catalog = catalog(_lang)
    return _lang


def current() -> str:
    return _lang


def plural_index(lang: str, n: int) -> int:
    if lang in ("zh", "ja"):
        return 0
    if lang == "fr":
        return 0 if n in (0, 1) else 1
    return 0 if n == 1 else 1


def N_(text: str) -> str:
    """Marca un texto para traducir sin traducirlo aún (se traduce al enseñarlo con _())."""
    return text


def _(text: str) -> str:
    found = _catalog.get(text)
    if isinstance(found, list):
        found = found[0] if found else None
    return found or text


def _n(singular: str, plural: str, n: int) -> str:
    found = _catalog.get(singular)
    if isinstance(found, list) and found:
        return found[min(plural_index(_lang, n), len(found) - 1)]
    if isinstance(found, str) and found:
        return found
    return singular if n == 1 else plural


def tr_in(lang: str, text: str) -> str:
    """Un texto en un idioma concreto (p. ej. el aviso de reinicio, en el idioma recién elegido)."""
    found = catalog(lang).get(text)
    if isinstance(found, list):
        found = found[0] if found else None
    return found or text


def numbers() -> tuple[str, str, str]:
    return NUMBERS.get(_lang, NUMBERS[SOURCE])


def all_translations(text: str) -> set[str]:
    """El texto en todos los idiomas (para leer registros escritos en cualquiera de ellos)."""
    out = {text}
    for lang in LANGS:
        found = catalog(lang).get(text)
        if isinstance(found, list):
            out.update(f for f in found if f)
        elif found:
            out.add(found)
    return out
