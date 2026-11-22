"""Imitación de la API de Google Drive v3 con escritura, para la autoprueba (--selftest).

En GitHub Actions no hay cuenta de Google: la copia a Google Drive (gdrive_up) se prueba contra esto.
Admite lo que usa la app: buscar por appProperties y carpeta, crear carpetas, subir en una petición o
por trozos (con un corte de red provocado), mover, borrar, bajar y la cuota de espacio.
"""
import hashlib
import http.server
import itertools
import json
import re
import threading
from urllib.parse import parse_qsl, urlsplit

FOLDER = "application/vnd.google-apps.folder"


class FakeDrive:
    def __init__(self):
        self.files: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.lock = threading.RLock()
        self.ids = itertools.count(1)
        self.limit = 10**12
        self.cut_session_once = False   # corta la conexión en el primer trozo de una subida grande
        self.fail_reason = ""            # 403 con esta razón en las subidas
        self.requests = []
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()

    # ── modelo ──
    def new_id(self):
        return f"f{next(self.ids):05d}"

    def usage(self):
        return sum(len(f.get("data") or b"") for f in self.files.values())

    def meta(self, fid, fields=""):
        f = self.files[fid]
        out = {"id": fid, "name": f["name"], "mimeType": f["mimeType"], "parents": list(f["parents"]),
               "modifiedTime": f["modifiedTime"], "createdTime": f["createdTime"], "trashed": f["trashed"],
               "appProperties": dict(f["appProperties"])}
        if f["mimeType"] != FOLDER:
            out["size"] = str(len(f["data"]))
            out["md5Checksum"] = hashlib.md5(f["data"]).hexdigest()
        return out

    def add(self, name, parents, mime=FOLDER, data=b"", props=None, mtime="2026-01-01T00:00:00.000Z"):
        fid = self.new_id()
        self.files[fid] = {"name": name, "parents": list(parents), "mimeType": mime, "data": data,
                           "appProperties": dict(props or {}), "modifiedTime": mtime,
                           "createdTime": f"2026-01-01T00:00:{len(self.files) % 60:02d}.{fid[1:]}Z", "trashed": False}
        return fid

    def path_of(self, fid):
        parts = []
        cur = fid
        while cur in self.files:
            parts.append(self.files[cur]["name"])
            ps = self.files[cur]["parents"]
            cur = ps[0] if ps else None
        return "/".join(reversed(parts))

    def children(self, fid):
        return [k for k, f in self.files.items() if fid in f["parents"]]

    def delete_tree(self, fid):
        for c in self.children(fid):
            self.delete_tree(c)
        self.files.pop(fid, None)

    def match(self, q, fid):
        f = self.files[fid]
        for term in re.split(r"\s+and\s+(?![^{]*})", q):
            term = term.strip()
            m = re.fullmatch(r"appProperties has \{ key='(.+?)' and value='(.*?)' \}", term)
            if m:
                if f["appProperties"].get(m.group(1)) != m.group(2).replace("\\'", "'"):
                    return False
                continue
            m = re.fullmatch(r"'(.+?)' in parents", term)
            if m:
                if m.group(1) not in f["parents"]:
                    return False
                continue
            if term == "trashed = false":
                if f["trashed"]:
                    return False
                continue
            m = re.fullmatch(r"mimeType = '(.+?)'", term)
            if m:
                if f["mimeType"] != m.group(1):
                    return False
                continue
            m = re.fullmatch(r"name = '(.*)'", term)
            if m:
                if f["name"] != m.group(1).replace("\\'", "'").replace("\\\\", "\\"):
                    return False
                continue
            raise ValueError(f"término no admitido: {term!r}")
        return True

    # ── HTTP ──
    def _handler(self):
        fake = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):
                pass

            def _send(self, status, data=None, headers=None):
                raw = json.dumps(data).encode() if data is not None else b""
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                if data is not None:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def _err(self, status, reason):
                self._send(status, {"error": {"errors": [{"reason": reason}], "code": status}})

            def do_POST(self):  # noqa: N802
                u = urlsplit(self.path)
                q = dict(parse_qsl(u.query))
                body = self._body()
                fake.requests.append(("POST", u.path, q.get("uploadType", "")))
                if u.path == "/token":
                    return self._send(200, {"access_token": "at", "expires_in": 3600})
                with fake.lock:
                    if u.path == "/drive/v3/files":
                        meta = json.loads(body)
                        fid = fake.add(meta["name"], meta.get("parents") or ["root"], meta.get("mimeType", FOLDER),
                                       b"", meta.get("appProperties"))
                        return self._send(200, {"id": fid})
                    if u.path == "/upload/drive/v3/files":
                        if fake.fail_reason:
                            return self._err(403, fake.fail_reason)
                        if q["uploadType"] == "multipart":
                            boundary = self.headers["Content-Type"].split("boundary=")[1]
                            parts = body.split(b"--" + boundary.encode())
                            meta = json.loads(parts[1].split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0])
                            data = parts[2].split(b"\r\n\r\n", 1)[1][:-2]
                            if fake.usage() + len(data) > fake.limit:
                                return self._err(403, "storageQuotaExceeded")
                            fid = fake.add(meta["name"], meta["parents"], meta.get("mimeType", ""), data,
                                           meta.get("appProperties"), meta.get("modifiedTime"))
                            return self._send(200, fake.meta(fid))
                        meta = json.loads(body)
                        sid = f"s{next(fake.ids)}"
                        fake.sessions[sid] = {"meta": meta, "size": int(self.headers["X-Upload-Content-Length"]),
                                              "data": b""}
                        return self._send(200, {}, {"Location": f"{fake.base}/upload/session/{sid}"})
                self._send(404, {})

            def do_PUT(self):  # noqa: N802
                u = urlsplit(self.path)
                body = self._body()
                fake.requests.append(("PUT", u.path, self.headers.get("Content-Range")))
                sid = u.path.rsplit("/", 1)[1]
                with fake.lock:
                    sess = fake.sessions.get(sid)
                    if sess is None:
                        return self._send(404, {})
                    rng = self.headers.get("Content-Range")
                    m = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", rng)
                    if m:
                        start = int(m.group(1))
                        if start != len(sess["data"]):
                            return self._send(400, {})
                        if fake.cut_session_once:
                            fake.cut_session_once = False
                            sess["data"] += body[: len(body) // 2]   # llegó la mitad y se cortó
                            self.close_connection = True
                            self.connection.close()
                            return
                        sess["data"] += body
                    if len(sess["data"]) < sess["size"]:
                        hdr = {"Range": f"bytes=0-{len(sess['data']) - 1}"} if sess["data"] else {}
                        return self._send(308, None, hdr)
                    meta = sess["meta"]
                    if fake.usage() + sess["size"] > fake.limit:
                        return self._err(403, "storageQuotaExceeded")
                    fid = fake.add(meta["name"], meta["parents"], meta.get("mimeType", ""), sess["data"],
                                   meta.get("appProperties"), meta.get("modifiedTime"))
                    fake.sessions.pop(sid)
                    return self._send(200, fake.meta(fid))

            def do_PATCH(self):  # noqa: N802
                u = urlsplit(self.path)
                q = dict(parse_qsl(u.query))
                body = self._body()
                fid = u.path.rsplit("/", 1)[1]
                fake.requests.append(("PATCH", fid, q.get("addParents", "")))
                with fake.lock:
                    f = fake.files.get(fid)
                    if f is None:
                        return self._err(404, "notFound")
                    meta = json.loads(body) if body else {}
                    if "name" in meta:
                        f["name"] = meta["name"]
                    for k, v in (meta.get("appProperties") or {}).items():
                        if v is None:
                            f["appProperties"].pop(k, None)
                        else:
                            f["appProperties"][k] = v
                    if q.get("addParents"):
                        rem = set(q.get("removeParents", "").split(","))
                        f["parents"] = [p for p in f["parents"] if p not in rem] + [q["addParents"]]
                    return self._send(200, {"id": fid})

            def do_DELETE(self):  # noqa: N802
                u = urlsplit(self.path)
                self._body()
                fid = u.path.rsplit("/", 1)[1]
                fake.requests.append(("DELETE", fid, ""))
                with fake.lock:
                    if fid not in fake.files:
                        return self._err(404, "notFound")
                    fake.delete_tree(fid)
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):  # noqa: N802
                u = urlsplit(self.path)
                q = dict(parse_qsl(u.query))
                fake.requests.append(("GET", u.path, q.get("q", "")))
                with fake.lock:
                    if u.path.endswith("/about"):
                        return self._send(200, {"user": {"emailAddress": "prueba@example.com", "permissionId": "p1"},
                                                "storageQuota": {"limit": str(fake.limit),
                                                                 "usage": str(fake.usage())}})
                    if u.path == "/drive/v3/files":
                        found = [fake.meta(fid) for fid in sorted(fake.files) if fake.match(q["q"], fid)]
                        start = int(q.get("pageToken") or 0)
                        size = 3  # páginas cortas: así se prueba la paginación
                        page = found[start:start + size]
                        out = {"files": page}
                        if start + size < len(found):
                            out["nextPageToken"] = str(start + size)
                        return self._send(200, out)
                    fid = u.path.rsplit("/", 1)[1]
                    f = fake.files.get(fid)
                    if f is None:
                        return self._err(404, "notFound")
                    if q.get("alt") == "media":
                        data = f["data"]
                        self.send_response(200)
                        self.send_header("Content-Length", str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                        return
                    return self._send(200, fake.meta(fid))

        return H
