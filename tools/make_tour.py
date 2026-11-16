"""Genera las ilustraciones de la guía rápida (assets/tour/) con capturas reales de la app.

    python tools/make_tour.py            # captura con datos de ejemplo y compone
    python tools/make_tour.py --compose  # solo recompone a partir de la última captura

Solo Windows: monta los datos de ejemplo como S:\\Proyectos y X:\\Copia con «subst» para que las
rutas de las capturas queden limpias. Las capturas se hacen a 2× para pantallas de alta densidad.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import stat
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "assets", "tour")
WORK = os.path.join(tempfile.gettempdir(), "devbackup_tour")
RAW = os.path.join(WORK, "raw")
SRC_LETTER, DST_LETTER = "S:", "X:"
DPR = 2.0
W, H = 920, 400  # lienzo lógico de cada ilustración (se guarda a 2×)


# ── datos de ejemplo ─────────────────────────────────────────────────────────

def _rmtree(path: str) -> None:
    if os.path.exists(path):
        shutil.rmtree(path, onexc=lambda f, p, _e: (os.chmod(p, stat.S_IWRITE), f(p)))


def make_demo(base: str) -> tuple[str, str]:
    random.seed(7)
    src_vol, dst_vol = os.path.join(base, "src_vol"), os.path.join(base, "dst_vol")
    _rmtree(src_vol)
    _rmtree(dst_vol)
    src = os.path.join(src_vol, "Proyectos")
    os.makedirs(dst_vol)

    def write(rel: str, n: int) -> None:
        p = os.path.join(src, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            if n >= 1 << 20:
                block = os.urandom(1 << 20)
                for _ in range(n >> 20):
                    fh.write(block)
            else:
                fh.write(os.urandom(n))

    projects = {
        "App Móvil": ["lib/screens", "lib/widgets", "assets/img", "build/outputs"],
        "API Pagos": ["app/routes", "app/models", "tests"],
        "Firmware ESP32": ["src", "include", "data"],
        "Juego Arcade": ["Assets/Sprites", "Assets/Audio", "Scripts"],
        "Tienda Online": ["src/components", "src/pages", "public/img"],
        "Web Corporativa": ["css", "js", "img"],
    }
    for proj, dirs in projects.items():
        for d in dirs:
            for i in range(random.randint(30, 60)):
                ext = random.choice([".ts", ".tsx", ".py", ".cpp", ".png", ".json", ".md"])
                write(f"{proj}/{d}/archivo_{i:03d}{ext}", random.randint(800, 60000))
        write(f"{proj}/node_modules/react/index.js", 2000)
        write(f"{proj}/README.md", 3000)
    # recursos medianos repartidos: datos y archivos avanzan a la par y el tiempo restante es realista
    media = {"Juego Arcade/Assets/Sprites": ".png", "Juego Arcade/Assets/Audio": ".wav",
             "Tienda Online/public/img": ".jpg", "App Móvil/assets/img": ".png"}
    for folder, ext in media.items():
        for i in range(30):
            write(f"{folder}/recurso_{i:03d}{ext}", random.randint(1, 3) << 20)
    return src_vol, dst_vol


def subst(letter: str, target: str | None) -> None:
    subprocess.run(["subst", letter, "/d"], capture_output=True)
    if target:
        subprocess.run(["subst", letter, target], check=True)


# ── captura ──────────────────────────────────────────────────────────────────

def capture() -> None:
    import engine

    slow = {"on": True}
    orig_progress = engine._PROGRESS
    # copia lenta para poder fotografiar el progreso a mitad
    engine._PROGRESS = lambda fn: orig_progress(lambda *a: (time.sleep(0.012) if slow["on"] else None, fn(*a))[1])
    real_info = engine.volume_info
    engine.volume_info = lambda root: (("Disco USB",) + real_info(root)[1:]) if root.upper().startswith(DST_LETTER) \
        else real_info(root)

    import backup_studio as bs
    from PyQt6.QtCore import QPoint, QRect, Qt, QTimer
    from PyQt6.QtGui import QFont, QPixmap

    _rmtree(WORK)
    os.makedirs(RAW)
    src_vol, dst_vol = make_demo(WORK)
    subst(SRC_LETTER, src_vol)
    subst(DST_LETTER, dst_vol)
    src, dst = SRC_LETTER + "\\Proyectos", DST_LETTER + "\\Copia"

    appdata = os.path.join(WORK, "appdata")
    bs.DATA_DIR = appdata
    bs.LOG_DIR = os.path.join(appdata, "logs")
    bs.set_autostart = lambda _on: None
    app = bs.App(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    bs.pick_fonts()
    app.setFont(QFont(bs.FONT["ui"], 10))
    app.setPalette(bs.dark_palette())
    app.setStyleSheet(bs.build_qss(bs.write_assets()))
    win = bs.MainWindow()
    win.settings.setValue("tour", 99)  # que no salga la propia guía en las capturas
    win.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    win.resize(1440, 920)
    win.move(-3000, 40)
    win.show()
    win.excl_map.clear()
    win.src_card.set_path(src)
    win._on_src_changed()
    win.dst_card.set_path(dst)
    win._on_dst_changed()
    win.watch_box.setChecked(True)
    meta: dict[str, dict] = {}

    def grab(name: str, widget, crop: QRect | None = None, rects: dict | None = None) -> None:
        size = widget.size()
        pm = QPixmap(int(size.width() * DPR), int(size.height() * DPR))
        pm.setDevicePixelRatio(DPR)
        pm.fill(Qt.GlobalColor.transparent)
        widget.render(pm)
        pm.save(os.path.join(RAW, name + ".png"))
        info = {"w": size.width(), "h": size.height()}
        for key, r in (rects or {}).items():
            info[key] = [r.x(), r.y(), r.width(), r.height()]
        meta[name] = info
        print("captura", name, size.width(), "x", size.height(), flush=True)

    def rect_in(child, ancestor) -> QRect:
        return QRect(child.mapTo(ancestor, QPoint(0, 0)), child.size())

    steps: list = []

    def after(ms: int, fn) -> None:
        steps.append((ms, fn))

    def run() -> None:
        if not steps:
            app.quit()
            return
        ms, fn = steps.pop(0)

        def go():
            if fn() is False:
                steps.insert(0, (120, fn))
            run()
        QTimer.singleShot(ms, go)

    def wait_state(*states):
        return lambda: None if win._state in states else False

    def tree_setup():
        win.seg_buttons[bs.PAGE_TREE].click()
        tree = win.tree
        for i in range(tree.topLevelItemCount()):
            it = tree.topLevelItem(i)
            if it.text(0) == "App Móvil":
                it.setExpanded(True)
                for j in range(it.childCount()):
                    if it.child(j).text(0) == "build":
                        it.child(j).setCheckState(0, Qt.CheckState.Unchecked)

    def tree_rects() -> dict:
        tree = win.tree
        page = win.stack.widget(bs.PAGE_TREE)
        out = {}
        for i in range(tree.topLevelItemCount()):
            it = tree.topLevelItem(i)
            for j in range(it.childCount()):
                if it.child(j).text(0) == "build":
                    r = tree.visualItemRect(it.child(j))
                    top_left = tree.viewport().mapTo(page, r.topLeft())
                    out["build"] = QRect(top_left, r.size())
        return out

    def progress_mid():
        job = win.job
        if job is None or job.phase != "copying" or win._progress(job.stats) < 0.58:
            return False
        win.seg_buttons[bs.PAGE_PROGRESS].click()
        grab("window_copy", win.centralWidget())
        grab("ring", win.ring.parentWidget())
        return None

    def mutate():
        slow["on"] = False
        p = os.path.join(src, "API Pagos", "app", "models", "archivo_003.py")
        if not os.path.exists(p):
            p = next(os.path.join(r, f) for r, _d, fs in os.walk(os.path.join(src, "API Pagos")) for f in fs)
        m = os.stat(p).st_mtime
        data = bytearray(open(p, "rb").read())
        data[0] ^= 1
        open(p, "wb").write(bytes(data))
        os.utime(p, (m + 7, m + 7))
        os.rename(os.path.join(src, "Web Corporativa"), os.path.join(src, "Web Corporativa 2026"))
        os.remove(os.path.join(src, "Tienda Online", "README.md"))
        for i in range(4):
            with open(os.path.join(src, "Juego Arcade", "Scripts", f"nivel_{5 + i}.cs"), "w") as fh:
                fh.write("// nuevo nivel\n" * 60)
        subst(DST_LETTER, None)

    def dialog_ready():
        d = win.dialog
        return None if (d and d.isVisible() and win.job and win.job.phase == "ready") else False

    def footer_rects() -> dict:
        f = win.btn_start.parentWidget()
        return {"scan": rect_in(win.btn_scan, f), "start": rect_in(win.btn_start, f),
                "pause": rect_in(win.btn_pause, f), "open": rect_in(win.btn_open, f)}

    after(700, tree_setup)
    after(400, lambda: None)
    after(100, lambda: win._start(copy=False))
    after(50, wait_state("ready"))
    after(300, lambda: win._start(copy=True))
    after(300, progress_mid)
    after(100, wait_state("done", "cancelled", "failed"))
    after(800, lambda: (win.seg_buttons[bs.PAGE_TREE].click(), None)[1])
    after(400, lambda: grab("tree", win.stack.widget(bs.PAGE_TREE), rects=tree_rects()))
    after(50, lambda: grab("src", win.src_card, rects={"edit": rect_in(win.src_card.edit, win.src_card),
                                                        "btn": rect_in(win.src_card.btn, win.src_card)}))
    after(50, lambda: grab("dst", win.dst_card, rects={"edit": rect_in(win.dst_card.edit, win.dst_card),
                                                        "btn": rect_in(win.dst_card.btn, win.dst_card)}))
    after(50, lambda: grab("rules", win.chips_box.parentWidget(),
                           rects={"chips": rect_in(win.chips_box, win.chips_box.parentWidget())}))
    after(50, lambda: grab("disk", win.watch_box.parentWidget(),
                           rects={"watch": rect_in(win.watch_box, win.watch_box.parentWidget())}))
    after(50, lambda: grab("power", win.awake_box.parentWidget()))
    after(50, lambda: grab("footer", win.btn_start.parentWidget(), rects=footer_rects()))
    after(50, lambda: grab("window_done", win.centralWidget()))
    after(200, mutate)
    after(2600, lambda: subst(DST_LETTER, dst_vol))  # «enchufar» el disco
    after(200, dialog_ready)
    after(300, lambda: (win.seg_buttons[bs.PAGE_CHANGES].click(), None)[1])
    after(400, lambda: grab("changes", win.stack.widget(bs.PAGE_CHANGES)))
    after(300, lambda: grab("dialog", win.dialog, rects={"sync": rect_in(win.dialog.b_sync, win.dialog),
                                                         "tiles": rect_in(win.dialog.t_new.parentWidget(),
                                                                          win.dialog)}))
    after(100, lambda: win.dialog._sync())
    after(200, wait_state("done", "cancelled", "failed"))
    after(800, lambda: (win.seg_buttons[bs.PAGE_PROGRESS].click(), None)[1])
    after(500, lambda: grab("summary", win.summary_card))

    def tour_open():
        win.settings.setValue("tour", 0)
        win.seg_buttons[bs.PAGE_PROGRESS].click()
        win.show_tour(animate=False)

    def tour_doc():
        # captura del README: la guía abierta sobre la app
        size = win.size()
        pm = QPixmap(int(size.width() * DPR), int(size.height() * DPR))
        pm.setDevicePixelRatio(DPR)
        win.render(pm)
        out = pm.toImage().scaledToWidth(1800, Qt.TransformationMode.SmoothTransformation)
        out.save(os.path.join(ROOT, "docs", "guia.png"))
        print("captura docs/guia.png", flush=True)
        win.tour.finish(False)

    after(300, tour_open)
    after(800, tour_doc)

    try:
        run()
        app.exec()
    finally:
        subst(SRC_LETTER, None)
        subst(DST_LETTER, None)
    with open(os.path.join(RAW, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1)
    if win.job:
        s = win.job.stats
        print("última copia:", win.job.phase, "nuevos", s.new_files, "modificados", s.changed_files,
              "movidos", s.moved_files, "a versiones", s.del_files, flush=True)


# ── composición ──────────────────────────────────────────────────────────────

def compose() -> None:
    from PyQt6.QtCore import QPointF, QRectF, Qt
    from PyQt6.QtGui import (QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                             QRadialGradient, QTransform)
    from PyQt6.QtWidgets import QApplication, QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene

    import backup_studio as bs

    _app = QApplication.instance() or QApplication(sys.argv)  # hace falta para pintar
    with open(os.path.join(RAW, "meta.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    os.makedirs(OUT, exist_ok=True)
    spots: dict[str, list] = {}

    def raw(name: str) -> QPixmap:
        pm = QPixmap(os.path.join(RAW, name + ".png"))
        pm.setDevicePixelRatio(DPR)
        return pm

    def canvas() -> tuple[QImage, QPainter]:
        img = QImage(int(W * DPR), int(H * DPR), QImage.Format.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(DPR)
        p = QPainter(img)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        g = QLinearGradient(0, 0, 0, H)
        g.setColorAt(0, QColor("#151A26"))
        g.setColorAt(1, QColor("#0B0E15"))
        p.fillRect(QRectF(0, 0, W, H), g)
        glow(p, QPointF(W * 0.16, -60), 520, QColor(124, 108, 255, 92))
        glow(p, QPointF(W * 0.94, H + 40), 460, QColor(45, 212, 238, 58))
        p.setPen(Qt.PenStyle.NoPen)
        for x in range(10, W, 22):
            for y in range(10, H, 22):
                p.setBrush(QColor(255, 255, 255, 9))
                p.drawEllipse(QPointF(x, y), 0.9, 0.9)
        return img, p

    def glow(p: QPainter, c: QPointF, r: float, color: QColor) -> None:
        g = QRadialGradient(c, r)
        g.setColorAt(0, color)
        edge = QColor(color)
        edge.setAlpha(0)
        g.setColorAt(1, edge)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(c, r, r)

    def blur_image(img: QImage, radius: float) -> QImage:
        scene = QGraphicsScene()
        item = QGraphicsPixmapItem(QPixmap.fromImage(img))
        eff = QGraphicsBlurEffect()
        eff.setBlurRadius(radius)
        eff.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
        item.setGraphicsEffect(eff)
        scene.addItem(item)
        out = QImage(img.size(), QImage.Format.Format_ARGB32_Premultiplied)
        out.fill(Qt.GlobalColor.transparent)
        q = QPainter(out)
        scene.render(q, QRectF(out.rect()), QRectF(img.rect()))
        q.end()
        return out

    def shadow(p: QPainter, rect: QRectF, radius: float, blur: float = 26, alpha: int = 170, dy: float = 14):
        pad = blur * 2
        img = QImage(int((rect.width() + 2 * pad) * DPR), int((rect.height() + 2 * pad) * DPR),
                     QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        q = QPainter(img)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        q.setPen(Qt.PenStyle.NoPen)
        q.setBrush(QColor(0, 0, 0, alpha))
        q.drawRoundedRect(QRectF(pad * DPR, pad * DPR, rect.width() * DPR, rect.height() * DPR),
                          radius * DPR, radius * DPR)
        q.end()
        img = blur_image(img, blur * DPR)
        img.setDevicePixelRatio(DPR)
        p.drawImage(QPointF(rect.x() - pad, rect.y() - pad + dy), img)

    def panel(p: QPainter, name: str, x: float, y: float, scale: float = 1.0,
              crop: tuple[float, float, float, float] | None = None, radius: float = 14,
              strong: bool = False) -> tuple[QRectF, callable]:
        """Pinta una captura como tarjeta flotante y devuelve su rectángulo y un conversor de coordenadas."""
        pm = raw(name)
        info = meta[name]
        cx, cy, cw, ch = crop or (0, 0, info["w"], info["h"])
        dest = QRectF(x, y, cw * scale, ch * scale)
        shadow(p, dest, radius, blur=30 if strong else 22, alpha=200 if strong else 150, dy=16 if strong else 10)
        path = QPainterPath()
        path.addRoundedRect(dest, radius, radius)
        p.save()
        p.setClipPath(path)
        p.fillRect(dest, QColor(bs.C["card"]))
        p.drawPixmap(dest, pm, QRectF(cx * DPR, cy * DPR, cw * DPR, ch * DPR))
        p.restore()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 30 if strong else 22), 1))
        p.drawRoundedRect(dest.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)

        def to_canvas(key: str, grow: float = 6) -> list[float]:
            rx, ry, rw, rh = info[key]
            r = QRectF(x + (rx - cx) * scale, y + (ry - cy) * scale, rw * scale, rh * scale)
            r = r.adjusted(-grow, -grow, grow, grow)
            return [round(r.x(), 1), round(r.y(), 1), round(r.width(), 1), round(r.height(), 1)]
        return dest, to_canvas

    def arrow_badge(p: QPainter, c: QPointF, r: float = 24):
        glow(p, c, r * 2.6, QColor(124, 108, 255, 70))
        g = QLinearGradient(c.x() - r, c.y() - r, c.x() + r, c.y() + r)
        g.setColorAt(0, QColor(bs.C["accent"]))
        g.setColorAt(1, QColor(bs.C["accent2"]))
        p.setPen(QPen(QColor(255, 255, 255, 60), 1))
        p.setBrush(g)
        p.drawEllipse(c, r, r)
        icon = bs.glyph_pixmap("arrow", "#FFFFFF", int(r * 0.95), dpr=DPR)
        s = r * 0.95
        p.drawPixmap(QRectF(c.x() - s / 2, c.y() - s / 2, s, s), icon, QRectF(icon.rect()))

    def save(img: QImage, p: QPainter, name: str, spot_list: list) -> None:
        p.end()
        path = os.path.join(OUT, name + ".jpg")
        img.convertToFormat(QImage.Format.Format_RGB32).save(path, "JPG", 92)
        spots[name] = spot_list
        print("ilustración", path, f"{os.path.getsize(path) // 1024} KB", flush=True)

    # 1 · bienvenida: la app entera, inclinada, que se funde por abajo
    img, p = canvas()
    glow(p, QPointF(W / 2, 170), 360, QColor(124, 108, 255, 70))
    layer = QImage(img.size(), QImage.Format.Format_ARGB32_Premultiplied)
    layer.setDevicePixelRatio(DPR)
    layer.fill(Qt.GlobalColor.transparent)
    q = QPainter(layer)
    q.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
    info = meta["window_copy"]
    scale = 0.6
    ww, wh = info["w"] * scale, info["h"] * scale
    t = QTransform()
    t.translate(W / 2, 40)
    t.rotate(16, Qt.Axis.XAxis)
    t.translate(-W / 2, -40)
    q.setTransform(t)
    rect = QRectF((W - ww) / 2, 40, ww, wh)
    shadow(q, rect, 16, blur=34, alpha=220, dy=20)
    path = QPainterPath()
    path.addRoundedRect(rect, 16, 16)
    q.setClipPath(path)
    q.drawPixmap(rect, raw("window_copy"), QRectF(0, 0, info["w"] * DPR, info["h"] * DPR))
    q.setClipping(False)
    q.setPen(QPen(QColor(255, 255, 255, 40), 1.2))
    q.setBrush(Qt.BrushStyle.NoBrush)
    q.drawRoundedRect(rect, 16, 16)
    q.resetTransform()
    fade = QLinearGradient(0, 230, 0, H - 6)
    fade.setColorAt(0, QColor(0, 0, 0, 255))
    fade.setColorAt(1, QColor(0, 0, 0, 0))
    q.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    q.fillRect(QRectF(0, 0, W, H), Qt.GlobalColor.black)
    q.fillRect(QRectF(0, 230, W, H), fade)
    q.end()
    p.drawImage(QPointF(0, 0), layer)
    save(img, p, "1-bienvenida", [])

    # 2 · origen y destino
    img, p = canvas()
    cw = meta["src"]["w"]
    gap = 66
    x0 = (W - 2 * cw - gap) / 2
    y0 = (H - meta["src"]["h"]) / 2 - 4
    _r1, src_at = panel(p, "src", x0, y0, strong=True)
    _r2, dst_at = panel(p, "dst", x0 + cw + gap, y0, strong=True)
    arrow_badge(p, QPointF(W / 2, y0 + meta["src"]["h"] / 2))
    save(img, p, "2-origen-destino", [src_at("edit", 5), dst_at("edit", 5)])

    # 3 · qué copiar: árbol con una carpeta desmarcada y reglas por nombre
    img, p = canvas()
    tree_crop = (0, 0, 620, 352)
    _r, tree_at = panel(p, "tree", 34, 30, crop=tree_crop)
    rules = meta["rules"]
    rs = 0.92
    _r, rules_at = panel(p, "rules", W - rules["w"] * rs - 30, H - rules["h"] * rs - 26, scale=rs, strong=True)
    spot = [rules_at("chips", 6)]
    if "build" in meta["tree"]:
        row = tree_at("build", 3)
        row[2] = 132.0  # la fila ocupa todo el árbol: se destaca solo la casilla, el icono y el nombre
        spot.insert(0, row)
    save(img, p, "3-que-copiar", spot)

    # 4 · analizar y sincronizar
    img, p = canvas()
    _r, _ch = panel(p, "changes", 30, 26, crop=(0, 0, 640, 300))
    ring = meta["ring"]
    rsc = 0.86
    panel(p, "ring", W - ring["w"] * rsc - 34, 20, scale=rsc, strong=True)
    foot = meta["footer"]
    fx0 = foot["scan"][0] - 10
    fx1 = foot["start"][0] + foot["start"][2] + 10
    fy0 = foot["start"][1] - 8
    fh = foot["start"][3] + 16
    fs = 0.9
    fw = (fx1 - fx0) * fs
    _r, foot_at = panel(p, "footer", (W - fw) / 2 - 40, H - fh * fs - 20, scale=fs, crop=(fx0, fy0, fx1 - fx0, fh),
                        radius=12, strong=True)
    save(img, p, "4-sincroniza", [foot_at("scan", 4), foot_at("start", 4)])

    # 5 · conectar el disco: vigilancia + diálogo al enchufarlo
    img, p = canvas()
    _r, disk_at = panel(p, "disk", 30, 34, scale=0.96)
    dlg = meta["dialog"]
    dsc = 0.76
    _r, dlg_at = panel(p, "dialog", W - dlg["w"] * dsc - 28, H - dlg["h"] * dsc - 30, scale=dsc, strong=True)
    save(img, p, "5-conecta", [disk_at("watch", 6), dlg_at("sync", 5)])

    with open(os.path.join(OUT, "tour.json"), "w", encoding="utf-8") as fh:
        json.dump({"size": [W, H], "spots": spots}, fh, indent=1, ensure_ascii=False)
    print("tour.json listo", flush=True)


if __name__ == "__main__":
    if "--compose" not in sys.argv:
        capture()
    compose()
