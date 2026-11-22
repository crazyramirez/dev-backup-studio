"""Textos para traducir: los saca del código y comprueba los catálogos de locales/.

    python tools/i18n_extract.py           → locales/messages.json (lista para traducir) y lo que falta
    python tools/i18n_extract.py --check   → sale con error si a algún idioma le falta un texto o no
                                             cuadran sus {marcadores}

Se recogen los textos de _("…"), _n("…", "…", n), N_("…") y tr_in(idioma, "…").
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
MODULES = ["backup_studio.py", "engine.py", "versions.py", "reminders.py", "keepawake.py", "updater.py", "gdrive_up.py"]
LOCALES = os.path.join(ROOT, "locales")
PLACEHOLDER = re.compile(r"\{[^{}]*\}")


def extract() -> list[dict]:
    found: dict[str, dict] = {}
    for mod in MODULES:
        path = os.path.join(ROOT, mod)
        src = open(path, encoding="utf-8").read()
        lines = src.splitlines()
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            args = node.args
            msg = plural = None
            if name in ("_", "N_") and args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
                msg = args[0].value
            elif name == "_n" and len(args) >= 2 and all(isinstance(a, ast.Constant) for a in args[:2]):
                msg, plural = args[0].value, args[1].value
            elif name == "tr_in" and len(args) >= 2 and isinstance(args[1], ast.Constant):
                msg = args[1].value
            if msg is None:
                continue
            entry = found.setdefault(msg, {"id": msg, "where": [], "code": lines[node.lineno - 1].strip()[:200]})
            if plural is not None:
                entry["plural"] = plural
            entry["where"].append(f"{mod}:{node.lineno}")
    return list(found.values())


def placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER.findall(text.replace("{{", "").replace("}}", "")))


def check(messages: list[dict], lang: str) -> list[str]:
    path = os.path.join(LOCALES, f"{lang}.json")
    try:
        with open(path, encoding="utf-8") as fh:
            cat = json.load(fh)["messages"]
    except (OSError, ValueError, KeyError) as exc:
        return [f"{lang}: no se pudo leer {path}: {exc}"]
    problems = []
    for m in messages:
        tr = cat.get(m["id"])
        want = placeholders(m["id"]) | placeholders(m.get("plural", ""))
        if tr is None or tr == "" or tr == []:
            problems.append(f"{lang}: falta «{m['id'][:70]}»")
            continue
        if "plural" in m:
            forms = tr if isinstance(tr, list) else [tr]
            if lang not in ("zh", "ja") and len(forms) != 2:
                problems.append(f"{lang}: «{m['id'][:50]}» necesita [singular, plural]")
            for form in forms:
                if not placeholders(form) <= want:
                    problems.append(f"{lang}: marcadores de más en «{form[:60]}»")
            if placeholders(forms[-1]) != want:
                problems.append(f"{lang}: al plural de «{m['id'][:50]}» le faltan marcadores {want}")
        else:
            if not isinstance(tr, str):
                problems.append(f"{lang}: «{m['id'][:50]}» debe ser un texto")
            elif placeholders(tr) != placeholders(m["id"]):
                problems.append(f"{lang}: marcadores distintos en «{m['id'][:50]}» → «{tr[:50]}»")
    extra = set(cat) - {m["id"] for m in messages}
    if extra:
        problems.append(f"{lang}: {len(extra)} textos que ya no se usan (se pueden quitar)")
    return problems


def main() -> int:
    import i18n
    messages = sorted(extract(), key=lambda m: m["where"][0])
    os.makedirs(LOCALES, exist_ok=True)
    if "--check" not in sys.argv:
        with open(os.path.join(LOCALES, "messages.json"), "w", encoding="utf-8") as fh:
            json.dump(messages, fh, ensure_ascii=False, indent=1)
        print(f"{len(messages)} textos ({sum('plural' in m for m in messages)} con plural) → locales/messages.json")
    bad = 0
    for lang in i18n.LANGS:
        if lang == i18n.SOURCE:
            continue
        problems = check(messages, lang)
        serious = [p for p in problems if "ya no se usan" not in p]
        bad += len(serious)
        print(f"{lang}: {'bien' if not problems else f'{len(problems)} avisos'}")
        for p in problems[:12]:
            print("   ", p)
    return 1 if bad and "--check" in sys.argv else 0


if __name__ == "__main__":
    sys.exit(main())
