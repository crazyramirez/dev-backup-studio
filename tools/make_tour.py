"""Genera las ilustraciones de la guía rápida (assets/tour/<idioma>/) con capturas reales de la app.

    python tools/make_tour.py                    # todos los idiomas (un proceso por idioma)
    python tools/make_tour.py --lang en          # solo un idioma
    python tools/make_tour.py --lang en --compose  # solo recompone a partir de la última captura

En español también rehace las capturas del README (docs/), y en alemán las de README.de.md (docs/de/).

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
LANG = sys.argv[sys.argv.index("--lang") + 1] if "--lang" in sys.argv else ""
OUT = os.path.join(ROOT, "assets", "tour", LANG)
WORK = os.path.join(tempfile.gettempdir(), f"devbackup_tour_{LANG}")
RAW = os.path.join(WORK, "raw")
# idiomas con README propio y dónde van sus capturas (README.md → docs/, README.de.md → docs/de/)
DOC_DIRS = {"es": "docs", "de": os.path.join("docs", "de")}
DOCS = os.path.join(ROOT, DOC_DIRS.get(LANG, "docs"))

# datos de ejemplo en el idioma de cada captura
DEMO = {
    "es": {"root": "Proyectos", "backup": "Copia", "label": "Disco USB", "file": "archivo", "res": "recurso",
           "level": "nivel", "order": "pedido",
           "p": {"app": "App Móvil", "api": "API Pagos", "fw": "Firmware ESP32", "game": "Juego Arcade",
                 "shop": "Tienda Online", "web": "Web Corporativa"}},
    "en": {"root": "Projects", "backup": "Backup", "label": "USB Drive", "file": "file", "res": "asset",
           "level": "level", "order": "order",
           "p": {"app": "Mobile App", "api": "Payments API", "fw": "ESP32 Firmware", "game": "Arcade Game",
                 "shop": "Online Store", "web": "Company Website"}},
    "fr": {"root": "Projets", "backup": "Sauvegarde", "label": "Disque USB", "file": "fichier", "res": "ressource",
           "level": "niveau", "order": "commande",
           "p": {"app": "App mobile", "api": "API Paiements", "fw": "Firmware ESP32", "game": "Jeu d'arcade",
                 "shop": "Boutique en ligne", "web": "Site vitrine"}},
    "de": {"root": "Projekte", "backup": "Sicherung", "label": "USB-Laufwerk", "file": "datei", "res": "ressource",
           "level": "level", "order": "bestellung",
           "p": {"app": "Mobile App", "api": "Zahlungs-API", "fw": "ESP32-Firmware", "game": "Arcade-Spiel",
                 "shop": "Onlineshop", "web": "Firmenwebsite"}},
    "it": {"root": "Progetti", "backup": "Backup", "label": "Disco USB", "file": "file", "res": "risorsa",
           "level": "livello", "order": "ordine",
           "p": {"app": "App mobile", "api": "API Pagamenti", "fw": "Firmware ESP32", "game": "Gioco arcade",
                 "shop": "Negozio online", "web": "Sito aziendale"}},
    "zh": {"root": "项目", "backup": "备份", "label": "USB 磁盘", "file": "file", "res": "asset",
           "level": "level", "order": "order",
           "p": {"app": "移动应用", "api": "支付 API", "fw": "ESP32 固件", "game": "街机游戏",
                 "shop": "网上商店", "web": "企业网站"}},
    "ja": {"root": "プロジェクト", "backup": "バックアップ", "label": "USB ディスク", "file": "file", "res": "asset",
           "level": "level", "order": "order",
           "p": {"app": "モバイルアプリ", "api": "決済 API", "fw": "ESP32 ファームウェア", "game": "アーケードゲーム",
                 "shop": "オンラインストア", "web": "企業サイト"}},
}
D = DEMO.get(LANG, DEMO["es"])
P = D["p"]
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
    src = os.path.join(src_vol, D["root"])
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
        P["app"]: ["lib/screens", "lib/widgets", "assets/img", "build/outputs"],
        P["api"]: ["app/routes", "app/models", "tests"],
        P["fw"]: ["src", "include", "data"],
        P["game"]: ["Assets/Sprites", "Assets/Audio", "Scripts"],
        P["shop"]: ["src/components", "src/pages", "public/img"],
        P["web"]: ["css", "js", "img"],
    }
    for proj, dirs in projects.items():
        for d in dirs:
            for i in range(random.randint(30, 60)):
                ext = random.choice([".ts", ".tsx", ".py", ".cpp", ".png", ".json", ".md"])
                write(f"{proj}/{d}/{D['file']}_{i:03d}{ext}", random.randint(800, 60000))
        write(f"{proj}/node_modules/react/index.js", 2000)
        write(f"{proj}/README.md", 3000)
    # recursos medianos repartidos: datos y archivos avanzan a la par y el tiempo restante es realista
    media = {f"{P['game']}/Assets/Sprites": ".png", f"{P['game']}/Assets/Audio": ".wav",
             f"{P['shop']}/public/img": ".jpg", f"{P['app']}/assets/img": ".png"}
    for folder, ext in media.items():
        for i in range(30):
            write(f"{folder}/{D['res']}_{i:03d}{ext}", random.randint(1, 3) << 20)
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
    engine.volume_info = lambda root: ((D["label"],) + real_info(root)[1:]) if root.upper().startswith(DST_LETTER) \
        else real_info(root)

    import backup_studio as bs
    from datetime import datetime, timedelta

    from PyQt6.QtCore import QPoint, QRect, Qt, QTimer

    offset = [timedelta(0)]

    class Clock(datetime):  # reloj de la app adelantable, para que salte el recordatorio
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + offset[0]
    bs.datetime = Clock
    from PyQt6.QtGui import QFont, QPixmap

    os.makedirs(RAW, exist_ok=True)
    src_vol, dst_vol = make_demo(WORK)
    subst(SRC_LETTER, src_vol)
    subst(DST_LETTER, dst_vol)
    src, dst = SRC_LETTER + "\\" + D["root"], DST_LETTER + "\\" + D["backup"]
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
            if it.text(0) == P["app"]:
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
        p = os.path.join(src, P["api"], "app", "models", f"{D['file']}_003.py")
        if not os.path.exists(p):
            p = next(os.path.join(r, f) for r, _d, fs in os.walk(os.path.join(src, P["api"])) for f in fs)
        m = os.stat(p).st_mtime
        data = bytearray(open(p, "rb").read())
        data[0] ^= 1
        open(p, "wb").write(bytes(data))
        os.utime(p, (m + 7, m + 7))
        os.rename(os.path.join(src, P["web"]), os.path.join(src, P["web"] + " 2026"))
        os.remove(os.path.join(src, P["shop"], "README.md"))
        # unos cuantos borrados más, para que Recuperar tenga qué enseñar (lejos del umbral de borrado masivo)
        for parts, n in (((P["game"], "Assets", "Sprites"), 3), ((P["app"], "lib", "widgets"), 2)):
            folder = os.path.join(src, *parts)
            for name in sorted(os.listdir(folder))[:n]:
                os.remove(os.path.join(folder, name))
        old_versions()
        for i in range(4):
            with open(os.path.join(src, P["game"], "Scripts", f"{D['level']}_{5 + i}.cs"), "w") as fh:
                fh.write("// nuevo nivel\n" * 60)
        subst(DST_LETTER, None)

    def old_versions():
        """Versiones de días anteriores, para que la línea de tiempo de Recuperar tenga varias fechas."""
        now = datetime.now()
        picks = {3: [(P["api"], "app", "routes"), (P["fw"], "src")], 12: [(P["shop"], "src", "components")],
                 34: [(P["app"], "lib", "screens"), (P["game"], "Scripts")]}
        for days, folders in picks.items():
            stamp = (now - timedelta(days=days, hours=days % 5)).strftime(engine.STAMP_FMT)
            for parts in folders:
                folder = os.path.join(src, *parts)
                names = sorted(os.listdir(folder))[:4 + days % 3]
                for name in names:
                    target = os.path.join(dst_vol, engine.META_DIR, "versiones", stamp, D["backup"], *parts, name)
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    shutil.copy2(os.path.join(folder, name), target)

    def recover_grab():
        rp = win.recover
        if rp.timeline.count() < 3 or not rp.timeline.isVisible():
            return False
        row = next(i for i in range(rp.timeline.count())
                   if rp.timeline.item(i).data(bs.TimelineDelegate.KIND) == "snap")  # la fecha más reciente
        rp.timeline.setCurrentRow(row)
        rp.tree.expandAll()
        from PyQt6.QtWidgets import QTreeWidgetItemIterator
        it = QTreeWidgetItemIterator(rp.tree)
        while it.value() is not None:  # un archivo borrado, que es lo que más se busca
            item = it.value()
            if item.childCount() == 0 and item.text(1) == bs._("Borrado"):
                rp.tree.setCurrentItem(item)
                break
            it += 1
        page = win.stack.widget(bs.PAGE_RECOVER)
        sel = rp.timeline.visualItemRect(rp.timeline.item(row))
        sel = QRect(rp.timeline.viewport().mapTo(page, sel.topLeft()), sel.size())
        acts = rp.b_restore.parentWidget()
        grab("recover", page, rects={"date": sel, "tree": rect_in(rp.tree, page)})
        grab("recover_actions", acts, rects={"open": rect_in(rp.b_open, acts), "to": rect_in(rp.b_to, acts),
                                             "restore": rect_in(rp.b_restore, acts)})
        grab("window_recover", win.centralWidget())
        return None

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
    after(50, lambda: grab("pair", win.card_pair, rects={"src": rect_in(win.src_card.edit, win.card_pair),
                                                          "dst": rect_in(win.dst_card.edit, win.card_pair),
                                                          "cloud": rect_in(win.cloud_btn, win.card_pair)}))
    after(50, lambda: grab("rules", win.card_rules, rects={"chips": rect_in(win.chips_box, win.card_rules)}))
    after(50, lambda: grab("disk", win.card_disk, rects={"status": rect_in(win.disk_status, win.card_disk),
                                                          "watch": rect_in(win.watch_box, win.card_disk)}))
    after(50, lambda: grab("footer", win.btn_start.parentWidget(), rects=footer_rects()))
    after(50, lambda: grab("window_done", win.centralWidget()))
    after(200, mutate)
    after(2600, lambda: subst(DST_LETTER, dst_vol))  # «enchufar» el disco
    after(200, dialog_ready)
    after(300, lambda: (win.seg_buttons[bs.PAGE_CHANGES].click(), None)[1])
    after(400, lambda: (grab("changes", win.stack.widget(bs.PAGE_CHANGES)),
                        grab("window_changes", win.centralWidget())))
    after(300, lambda: grab("dialog", win.dialog, rects={"sync": rect_in(win.dialog.b_sync, win.dialog),
                                                         "tiles": rect_in(win.dialog.t_new.parentWidget(),
                                                                          win.dialog)}))
    after(100, lambda: win.dialog._sync())
    after(200, wait_state("done", "cancelled", "failed"))
    after(800, lambda: (win.seg_buttons[bs.PAGE_PROGRESS].click(), None)[1])
    after(500, lambda: grab("summary", win.summary_card))
    after(200, lambda: (win.seg_buttons[bs.PAGE_RECOVER].click(), None)[1])
    after(600, recover_grab)
    after(300, lambda: (win.seg_buttons[bs.PAGE_PROGRESS].click(), None)[1])

    def remind_due():
        for i in range(6):
            with open(os.path.join(src, P["shop"], "src", "pages", f"{D['order']}_{i}.tsx"), "w") as fh:
                fh.write("export default {}\n" * 40)
        offset[0] = timedelta(days=4, hours=2)
        win.seg_buttons[bs.PAGE_TREE].click()
        win._reminder_tick()

    def remind_grab():
        if win.rem_checker.busy or not win.remind_bar.isVisible() or win.remind_bar._anim.state().name == "Running":
            return False
        bar, card = win.remind_bar, win.card_remind
        grab("remind_bar", bar, rects={"go": rect_in(bar.go, bar)})
        grab("remind_card", card)
        return None

    after(300, remind_due)
    after(900, remind_grab)

    def tour_open():
        win.settings.setValue("tour", 0)
        win.seg_buttons[bs.PAGE_PROGRESS].click()
        win.show_tour(animate=False)

    def tour_doc():
        # captura del README (README.md en español, README.<idioma>.md en otros): la guía abierta sobre la app
        if LANG not in DOC_DIRS:
            win.tour.finish(False)
            return
        size = win.size()
        pm = QPixmap(int(size.width() * DPR), int(size.height() * DPR))
        pm.setDevicePixelRatio(DPR)
        win.render(pm)
        out = pm.toImage().scaledToWidth(1800, Qt.TransformationMode.SmoothTransformation)
        os.makedirs(DOCS, exist_ok=True)
        out.save(os.path.join(DOCS, "guia.png"))
        print(f"captura {DOCS}\\guia.png", flush=True)
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

    # 2 · origen → destino en su tarjeta, y el disco de copia que se reconoce aunque cambie de letra
    img, p = canvas()
    pair, disk = meta["pair"], meta["disk"]
    gap = 54
    x0 = (W - pair["w"] - disk["w"] - gap) / 2
    _r, pair_at = panel(p, "pair", x0, (H - pair["h"]) / 2, strong=True)
    _r, disk_at = panel(p, "disk", x0 + pair["w"] + gap, (H - disk["h"]) / 2 + 6, strong=True)
    save(img, p, "2-origen-destino", [pair_at("src", 5), pair_at("dst", 5), pair_at("cloud", 4),
                                      disk_at("status", 6)])

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

    # 5 · recuperar: la línea de tiempo con lo apartado y los botones para devolverlo
    img, p = canvas()
    rec = meta["recover"]
    rsc = 0.62
    _r, rec_at = panel(p, "recover", 30, 24, scale=rsc, crop=(0, 0, rec["w"], min(rec["h"], (H - 48) / rsc)))
    act = meta["recover_actions"]
    ax0 = act["open"][0] - 10
    ax1 = act["restore"][0] + act["restore"][2] + 10
    ay0 = act["restore"][1] - 8
    ah = act["restore"][3] + 16
    asc = 0.95
    _r, act_at = panel(p, "recover_actions", W - (ax1 - ax0) * asc - 28, H - ah * asc - 22, scale=asc,
                       crop=(ax0, ay0, ax1 - ax0, ah), radius=12, strong=True)
    save(img, p, "5-recupera", [rec_at("date", 4), act_at("restore", 4)])

    # 6 · conectar el disco y recordatorios: el aviso arriba, la tarjeta y el diálogo al enchufar el disco
    img, p = canvas()
    bar = meta["remind_bar"]
    bsc = 0.84
    _r, bar_at = panel(p, "remind_bar", (W - bar["w"] * bsc) / 2, 20, scale=bsc, radius=12, strong=True)
    card = meta["remind_card"]
    csc = 0.72
    panel(p, "remind_card", 30, 104, scale=csc, crop=(0, 0, card["w"], min(card["h"], 380)))
    dlg = meta["dialog"]
    dsc = 0.66
    _r, dlg_at = panel(p, "dialog", W - dlg["w"] * dsc - 28, H - dlg["h"] * dsc - 18, scale=dsc, strong=True)
    save(img, p, "6-conecta", [bar_at("go", 4), dlg_at("sync", 4)])

    # capturas del README de este idioma con el diseño actual
    for name, out, width in [] if LANG not in DOC_DIRS else (
            ("window_copy", "progreso.png", 1800), ("window_changes", "cambios.png", 1800),
            ("dialog", "conectar.png", 875), ("window_recover", "recuperar.png", 1800)):
        os.makedirs(DOCS, exist_ok=True)
        raw(name).toImage().scaledToWidth(width, Qt.TransformationMode.SmoothTransformation).save(
            os.path.join(DOCS, out))
        print(f"captura {DOCS}\\{out}", flush=True)

    with open(os.path.join(OUT, "tour.json"), "w", encoding="utf-8") as fh:
        json.dump({"size": [W, H], "spots": spots}, fh, indent=1, ensure_ascii=False)
    print("tour.json listo", flush=True)


def prepare() -> None:
    """Ajustes de la app de captura en una carpeta propia, con el idioma elegido (se lee al importar la app)."""
    if "--compose" not in sys.argv:
        _rmtree(WORK)
    data = os.path.join(WORK, "env", "DEV Backup Studio")
    os.makedirs(data, exist_ok=True)
    with open(os.path.join(data, "settings.ini"), "w", encoding="utf-8") as fh:
        fh.write(f"[General]\nlanguage={LANG}\ntour=99\n")
    os.environ["APPDATA"] = os.path.join(WORK, "env")


if __name__ == "__main__":
    if not LANG:  # todos los idiomas, cada uno en su proceso: los textos se fijan al arrancar la app
        import i18n
        extra = [a for a in sys.argv[1:] if a == "--compose"]
        for code in i18n.LANGS:
            print(f"== {code}", flush=True)
            subprocess.run([sys.executable, "-X", "utf8", os.path.abspath(__file__), "--lang", code] + extra,
                           check=True)
        sys.exit(0)
    prepare()
    if "--compose" not in sys.argv:
        capture()
    compose()
