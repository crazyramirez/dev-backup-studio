"""
Actualizaciones desde GitHub Releases.

1. Consulta la última versión publicada en el repositorio (API de GitHub).
2. Si es más nueva, la UI pregunta. Al aceptar se descarga el instalador de
   Windows o el .dmg de macOS y se comprueba su SHA-256 con el que publica
   GitHub para ese archivo.
3. Se lanza la instalación y la app se cierra:
   - Windows: el instalador corre en modo /SILENT (solo la barra de progreso) y
     con /AUTOUPDATE vuelve a abrir la app al terminar.
   - macOS: un pequeño script espera a que la app se cierre, sustituye la .app
     por la del .dmg y la vuelve a abrir; si algo falla, abre el .dmg.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import ssl
import subprocess
import sys
import tempfile
import threading
import urllib.request

from i18n import _
from dataclasses import dataclass

REPO = "crazyramirez/dev-backup-studio"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases"
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
FROZEN = getattr(sys, "frozen", False)


@dataclass
class Release:
    version: str
    tag: str
    notes: str
    page: str
    published: str
    asset_name: str = ""
    asset_url: str = ""
    asset_size: int = 0
    sha256: str = ""


def parse_version(text: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", text.split("-")[0])
    return tuple(int(n) for n in nums[:3]) + (0,) * (3 - min(3, len(nums)))


def is_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


def _ssl_context() -> ssl.SSLContext:
    # la app de macOS no trae los certificados del sistema: se usan los de certifi
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _open(url: str, user_agent: str, timeout: float = 20):
    req = urllib.request.Request(url, headers={
        "User-Agent": user_agent,
        "Accept": "application/vnd.github+json" if "api.github.com" in url else "application/octet-stream",
    })
    return urllib.request.urlopen(req, timeout=timeout, context=_ssl_context())


def _wanted_asset(name: str) -> bool:
    if IS_WIN:
        return name.startswith("DEV-Backup-Studio-Setup-") and name.endswith(".exe")
    if IS_MAC:
        return name.endswith("-macOS.dmg")
    return False


def fetch_latest(current: str) -> Release:
    """Última versión publicada. Lanza OSError/ValueError si no se puede consultar."""
    with _open(LATEST_API, f"DEV-Backup-Studio/{current}") as resp:
        data = json.loads(resp.read().decode("utf-8"))
    tag = data.get("tag_name") or ""
    rel = Release(version=tag.lstrip("vV"), tag=tag, notes=data.get("body") or "",
                  page=data.get("html_url") or RELEASES_URL, published=data.get("published_at") or "")
    for asset in data.get("assets", []):
        if _wanted_asset(asset.get("name", "")):
            rel.asset_name = asset["name"]
            rel.asset_url = asset["browser_download_url"]
            rel.asset_size = int(asset.get("size") or 0)
            digest = asset.get("digest") or ""
            rel.sha256 = digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else ""
            break
    return rel


def can_self_update() -> bool:
    """Solo la app instalada se puede sustituir sola; desde el código hay que hacer git pull."""
    return FROZEN and (IS_WIN or IS_MAC)


def download(rel: Release, current: str, progress=None, stop: threading.Event | None = None) -> str:
    """Descarga el instalador y comprueba su SHA-256. Devuelve la ruta."""
    folder = os.path.join(tempfile.gettempdir(), "DEV Backup Studio", "actualizacion")
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, rel.asset_name)
    part = target + ".part"
    h = hashlib.sha256()
    done = 0
    with _open(rel.asset_url, f"DEV-Backup-Studio/{current}", timeout=60) as resp, open(part, "wb") as fh:
        total = int(resp.headers.get("Content-Length") or rel.asset_size or 0)
        while True:
            if stop is not None and stop.is_set():
                raise InterruptedError(_("descarga cancelada"))
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            fh.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if rel.sha256 and h.hexdigest() != rel.sha256:
        os.remove(part)
        raise ValueError(_("el archivo descargado no coincide con el SHA-256 publicado en GitHub"))
    os.replace(part, target)
    return target


def launch_install(path: str) -> None:
    """Lanza la instalación; quien llama debe cerrar la app justo después."""
    if IS_WIN:
        quoted = path.replace("'", "''")
        script = (f"Start-Sleep -Seconds 2; Start-Process -FilePath '{quoted}' "
                  "-ArgumentList '/SILENT','/SUPPRESSMSGBOXES','/NORESTART','/AUTOUPDATE'")
        flags = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-Command", script]
        try:
            # que el instalador sobreviva al cierre de la app aunque esta corra dentro de un «job»
            subprocess.Popen(cmd, creationflags=flags | 0x01000000, close_fds=True)
        except OSError:
            subprocess.Popen(cmd, creationflags=flags, close_fds=True)
        return
    if IS_MAC:
        app = os.path.abspath(os.path.join(os.path.dirname(sys.executable), "..", ".."))
        q_app, q_dmg = shlex.quote(app), shlex.quote(path)
        script = f"""#!/bin/sh
while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.5; done
MNT=$(mktemp -d /tmp/devbackup.XXXXXX)
if hdiutil attach -nobrowse -quiet -mountpoint "$MNT" {q_dmg}; then
  NEW="$MNT/DEV Backup Studio.app"
  if [ -d "$NEW" ] && rm -rf {q_app}.old && mv {q_app} {q_app}.old && ditto "$NEW" {q_app}; then
    rm -rf {q_app}.old
    xattr -dr com.apple.quarantine {q_app} 2>/dev/null
    hdiutil detach -quiet "$MNT"
    open {q_app} --args --updated
    exit 0
  fi
  [ -d {q_app}.old ] && [ ! -d {q_app} ] && mv {q_app}.old {q_app}
  hdiutil detach -quiet "$MNT"
fi
open {q_dmg}
"""
        fd, script_path = tempfile.mkstemp(prefix="devbackup-update-", suffix=".sh")
        with os.fdopen(fd, "w") as fh:
            fh.write(script)
        subprocess.Popen(["/bin/sh", script_path], start_new_session=True, close_fds=True)
        return
    raise OSError(_("actualización automática no disponible en este sistema"))
