"""
Mantiene despiertos el equipo y el disco de copia mientras dura una operación.

- Equipo. Windows: solicitud de energía con motivo (PowerCreateRequest), visible en
  `powercfg /requests`. macOS: aserciones de IOKit, como `caffeinate -i -m -s`, visibles
  en `pmset -g assertions`. Linux: inhibición de systemd-logind (`systemd-inhibit`), visible
  en `systemd-inhibit --list`. La pantalla sí puede apagarse.
- Disco. Ni el sistema ni el firmware de un disco USB saben que una copia sigue en marcha
  si el disco lleva un rato sin recibir escrituras (p. ej. mientras se analiza el origen),
  y lo apagan. Un latido escribe unos bytes en `.devbackup/latido` y los fuerza hasta el
  disco cuando lleva BEAT_SECONDS sin actividad.
- Apagado. En Windows el motivo que se registra aquí es el que enseña la pantalla de
  «Esta aplicación impide apagar»; la decisión de retenerlo la toma la interfaz.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import threading
import time

from i18n import N_

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

BEAT_SECONDS = 30.0   # sin actividad en el disco durante este tiempo → latido
BEAT_FILE = "latido"
META_DIR = ".devbackup"


# ── equipo: Windows ──────────────────────────────────────────────────────────

if IS_WIN:
    from ctypes import wintypes

    class _Detailed(ctypes.Structure):
        _fields_ = [("LocalizedReasonModule", wintypes.HMODULE), ("LocalizedReasonId", wintypes.ULONG),
                    ("ReasonStringCount", wintypes.ULONG), ("ReasonStrings", ctypes.POINTER(wintypes.LPWSTR))]

    class _Reason(ctypes.Union):
        _fields_ = [("Detailed", _Detailed), ("SimpleReasonString", wintypes.LPWSTR)]

    class _ReasonContext(ctypes.Structure):
        _fields_ = [("Version", wintypes.ULONG), ("Flags", wintypes.DWORD), ("Reason", _Reason)]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.PowerCreateRequest.restype = wintypes.HANDLE
    _k32.PowerCreateRequest.argtypes = [ctypes.POINTER(_ReasonContext)]
    _k32.PowerSetRequest.argtypes = [wintypes.HANDLE, ctypes.c_int]
    _k32.PowerClearRequest.argtypes = [wintypes.HANDLE, ctypes.c_int]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.SetThreadExecutionState.restype = wintypes.DWORD
    _k32.SetThreadExecutionState.argtypes = [wintypes.DWORD]
    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _u32.ShutdownBlockReasonCreate.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    _u32.ShutdownBlockReasonDestroy.argtypes = [wintypes.HWND]

    _REQUEST_SYSTEM = 1          # PowerRequestSystemRequired
    _CONTEXT_SIMPLE_STRING = 0x1
    _INVALID_HANDLE = ctypes.c_void_p(-1).value
    _ES_CONTINUOUS, _ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001


class _WindowsSystem:
    def __init__(self, reason: str):
        self._reason = ctypes.create_unicode_buffer(reason[:250])  # vivo mientras dure la solicitud
        self._handle = None
        ctx = _ReasonContext(0, _CONTEXT_SIMPLE_STRING)
        ctx.Reason.SimpleReasonString = ctypes.cast(self._reason, wintypes.LPWSTR)
        handle = _k32.PowerCreateRequest(ctypes.byref(ctx))
        if handle and handle != _INVALID_HANDLE and _k32.PowerSetRequest(handle, _REQUEST_SYSTEM):
            self._handle = handle
            self.method = N_("solicitud de energía de Windows")
        else:
            if handle and handle != _INVALID_HANDLE:
                _k32.CloseHandle(handle)
            # respaldo: vale para el hilo que lo llama, que es el de la interfaz
            _k32.SetThreadExecutionState(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED)
            self.method = "SetThreadExecutionState"

    def release(self) -> None:
        if self._handle:
            _k32.PowerClearRequest(self._handle, _REQUEST_SYSTEM)
            _k32.CloseHandle(self._handle)
            self._handle = None
        else:
            _k32.SetThreadExecutionState(_ES_CONTINUOUS)


def block_shutdown(hwnd: int, reason: str) -> bool:
    """Texto que Windows enseña si se intenta apagar o reiniciar con la copia en marcha."""
    return bool(IS_WIN and hwnd and _u32.ShutdownBlockReasonCreate(hwnd, reason[:250]))


def unblock_shutdown(hwnd: int) -> None:
    if IS_WIN and hwnd:
        _u32.ShutdownBlockReasonDestroy(hwnd)


# ── equipo: macOS ────────────────────────────────────────────────────────────

_MAC_ASSERTIONS = ("PreventUserIdleSystemSleep",  # sin reposo por inactividad (caffeinate -i)
                   "PreventDiskIdle",             # sin reposo de los discos (caffeinate -m)
                   "PreventSystemSleep")          # tampoco el del sistema con corriente (caffeinate -s)


class _MacSystem:
    def __init__(self, reason: str):
        self._ids: list[int] = []
        self._proc: subprocess.Popen | None = None
        try:
            self._iokit_assertions(reason)
            self.method = N_("aserciones de energía de macOS")
        except (OSError, AttributeError):
            self._release_ids()
            self._proc = subprocess.Popen(["caffeinate", "-i", "-m", "-s", "-w", str(os.getpid())],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.method = "caffeinate"

    def _iokit_assertions(self, reason: str) -> None:
        cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        iokit = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        iokit.IOPMAssertionCreateWithName.restype = ctypes.c_int32
        iokit.IOPMAssertionCreateWithName.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                                                      ctypes.POINTER(ctypes.c_uint32)]
        iokit.IOPMAssertionRelease.restype = ctypes.c_int32
        iokit.IOPMAssertionRelease.argtypes = [ctypes.c_uint32]
        self._iokit = iokit
        utf8, level_on = 0x08000100, 255

        def cfstr(text: str):
            return cf.CFStringCreateWithCString(None, text.encode("utf-8"), utf8)

        name = cfstr(reason)
        try:
            for kind in _MAC_ASSERTIONS:
                ref = cfstr(kind)
                aid = ctypes.c_uint32(0)
                rc = iokit.IOPMAssertionCreateWithName(ref, level_on, name, ctypes.byref(aid))
                cf.CFRelease(ref)
                if rc != 0:
                    raise OSError(rc, f"IOPMAssertionCreateWithName({kind})")
                self._ids.append(aid.value)
        finally:
            cf.CFRelease(name)

    def _release_ids(self) -> None:
        for aid in self._ids:
            self._iokit.IOPMAssertionRelease(aid)
        self._ids.clear()

    def release(self) -> None:
        self._release_ids()
        if self._proc is not None:
            self._proc.terminate()
            self._proc = None


# ── equipo: Linux ────────────────────────────────────────────────────────────

class _LinuxSystem:
    """Sin suspensión por inactividad ni manual mientras viva el proceso; si la app muere, el
    `tail --pid` termina y systemd quita la inhibición solo."""

    def __init__(self, reason: str):
        tool = shutil.which("systemd-inhibit")
        if not tool:
            raise OSError("systemd-inhibit no está disponible")
        # Bloquear la suspensión solo se permite a la sesión activa del escritorio (polkit); si se
        # niega (sesión remota, WSL…), al menos que la inactividad no cuente.
        err = ""
        for what in ("sleep:idle", "idle"):
            self._proc = subprocess.Popen(
                [tool, f"--what={what}", "--who=DEV Backup Studio", f"--why={reason[:200]}", "--mode=block",
                 "tail", f"--pid={os.getpid()}", "-f", "/dev/null"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
            try:
                self._proc.wait(timeout=0.5)  # si no se puede (sin permiso, sin logind), sale enseguida
            except subprocess.TimeoutExpired:
                self._proc.stderr.close()
                self.method = (N_("inhibición de systemd") if what == "sleep:idle"
                               else N_("inhibición de systemd, solo inactividad"))
                return
            err = (self._proc.stderr.read() or b"").decode("utf-8", "replace").strip()
            self._proc.stderr.close()
        raise OSError(err or f"systemd-inhibit terminó con {self._proc.returncode}")

    def release(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()


# ── disco: latido ────────────────────────────────────────────────────────────

class _Heartbeat(threading.Thread):
    def __init__(self, root: str, activity=None):
        super().__init__(name="latido", daemon=True)
        self.path = os.path.join(root, META_DIR, BEAT_FILE)
        self.activity = activity      # función que cambia de valor cuando la copia escribe en el disco
        self.stop_event = threading.Event()
        self.last_io = time.monotonic()
        self.last_beat = 0.0          # time.time() del último latido
        self.beats = 0
        self.error = ""

    def run(self) -> None:
        mark = None
        while not self.stop_event.wait(1.0):
            now = time.monotonic()
            if self.activity is not None:
                try:
                    current = self.activity()
                except Exception:  # noqa: BLE001 - la sonda nunca debe parar el latido
                    current = None
                if current != mark:
                    mark = current
                    self.last_io = now
                    continue
            if now - self.last_io >= BEAT_SECONDS:
                self.beat()
                self.last_io = time.monotonic()

    def beat(self) -> None:
        """Escribe y obliga al disco a guardarlo (no basta con la caché del sistema)."""
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o644)
            try:
                os.write(fd, f"{time.time():.3f} {os.getpid()}\n".encode())
                if IS_MAC:
                    import fcntl
                    fcntl.fcntl(fd, fcntl.F_FULLFSYNC)  # fsync en macOS no vacía la caché del disco
                else:
                    os.fsync(fd)                         # FlushFileBuffers en Windows
            finally:
                os.close(fd)
            self.last_beat = time.time()
            self.beats += 1
            self.error = ""
        except OSError as exc:
            self.error = exc.strerror or str(exc)


# ── conjunto ─────────────────────────────────────────────────────────────────

class Guard:
    """Equipo despierto + latido del disco. Se arranca y se para desde la interfaz."""

    def __init__(self):
        self._system = None
        self._beat: _Heartbeat | None = None
        self.root = ""
        self.since = 0.0
        self.error = ""

    @property
    def active(self) -> bool:
        return self._system is not None or self._beat is not None

    @property
    def method(self) -> str:
        return getattr(self._system, "method", "")

    def start(self, reason: str, disk_root: str | None = None, activity=None) -> None:
        if self.active:
            return
        self.error = ""
        try:
            if IS_WIN:
                self._system = _WindowsSystem(reason)
            elif IS_MAC:
                self._system = _MacSystem(reason)
            elif IS_LINUX:
                self._system = _LinuxSystem(reason)
        except OSError as exc:
            self._system = None
            self.error = str(exc)
        if disk_root:
            self.root = disk_root
            self._beat = _Heartbeat(disk_root, activity)
            self._beat.start()
        self.since = time.time()

    def stop(self) -> None:
        if self._system is not None:
            try:
                self._system.release()
            except OSError:
                pass
            self._system = None
        if self._beat is not None:
            self._beat.stop_event.set()
            # espera a que suelte el archivo del latido (un disco colgado no bloquea más de 2 s)
            if self._beat.is_alive() and self._beat is not threading.current_thread():
                self._beat.join(timeout=2.0)
            self._beat = None

    def status(self) -> dict:
        b = self._beat
        return {
            "active": self.active, "system": self._system is not None, "method": self.method,
            "error": self.error, "disk": bool(b), "root": self.root,
            "last_beat": b.last_beat if b else 0.0, "beats": b.beats if b else 0,
            "disk_error": b.error if b else "",
        }
