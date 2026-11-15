"""Extrae de CHANGELOG.md las novedades de una versión a build/notes.md.

    python tools/release_notes.py 1.2.0

Son las notas de la release en GitHub y las que enseña la app al ofrecer la
actualización.
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
version = sys.argv[1].lstrip("vV")

with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as fh:
    text = fh.read()
match = re.search(rf"^## {re.escape(version)}\s*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
if not match:
    sys.exit(f"CHANGELOG.md no tiene una sección «## {version}»")
os.makedirs(os.path.join(ROOT, "build"), exist_ok=True)
with open(os.path.join(ROOT, "build", "notes.md"), "w", encoding="utf-8") as fh:
    fh.write(match[1].strip() + "\n")
print(match[1].strip())
