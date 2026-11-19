<p align="center">
  <img src="docs/header.png" alt="DEV Backup Studio — Ihre Projekte, immer sicher" width="100%">
</p>

<p align="center"><a href="README.md">Español</a> · <b>Deutsch</b></p>

Sicherung und Synchronisierung von Projektordnern auf ein externes Laufwerk, für Windows, macOS und
Linux. Erkennt, was sich geändert hat, verschiebt Umbenanntes, statt es erneut zu kopieren, bewahrt
Versionen dessen auf, was Sie löschen oder ändern, prüft jede Kopie mit SHA-256 und fragt, ob
synchronisiert werden soll, sobald Sie das Laufwerk anschließen.

## Herunterladen und installieren

Unter [Releases](https://github.com/crazyramirez/dev-backup-studio/releases/latest):

| System | Datei | Installation |
|---|---|---|
| Windows 10/11 (64 Bit) | `DEV-Backup-Studio-Setup-x.y.z.exe` | Installationsassistent. Standardmäßig nur für Ihr Benutzerkonto, ohne Administratorrechte; legt eine Verknüpfung im Startmenü an (auf Wunsch auch auf dem Desktop) und wird unter *Einstellungen → Apps* deinstalliert. |
| macOS 12+ (Apple Silicon) | `DEV-Backup-Studio-x.y.z-macOS.dmg` | Die `.dmg` öffnen und die App in *Programme* ziehen. |
| Linux x86_64 (Ubuntu 22.04+, Debian 12+, Fedora 36+ …) | `DEV-Backup-Studio-x.y.z-x86_64.AppImage` | Eine einzige Datei, ohne Installation: an einem beliebigen Ort speichern (z. B. `~/Anwendungen`), ausführbar machen (*Eigenschaften → Als Programm ausführen* oder `chmod +x`) und öffnen. Beim ersten Start trägt sie sich selbst ins Anwendungsmenü ein. |

Zum Aktualisieren die neue Version einfach darüber installieren: Ist die App geöffnet (auch im
Infobereich), bittet der Assistent darum, sie zu schließen. Einstellungen, Indizes und Sicherungen
bleiben erhalten, auch beim Deinstallieren. Die App meldet neue Versionen und aktualisiert sich auf
Wunsch selbst (unter Linux ersetzt sie die `.AppImage` dort, wo sie liegt).

Unter Linux funktioniert der Infobereich in KDE, XFCE, Cinnamon, MATE und Ubuntu; im unveränderten
GNOME wird die Erweiterung *AppIndicator* benötigt. Ohne Infobereich startet die App mit der Sitzung
minimiert. Zum Entfernen: die `.AppImage`, `~/.local/share/applications/dev-backup-studio.desktop`
und, falls aktiviert, `~/.config/autostart/dev-backup-studio.desktop` löschen.

Die Apps sind nicht mit einem Entwicklerzertifikat signiert, daher warnt das System beim ersten Start:

- **Windows** (SmartScreen): *Weitere Informationen* → *Trotzdem ausführen*.
- **macOS**: Rechtsklick auf die App → *Öffnen*, oder *Systemeinstellungen → Datenschutz & Sicherheit
  → Dennoch öffnen*.

![Fortschritt einer Synchronisierung](docs/de/progreso.png)

## Was die App kann

- **Spiegel-Synchronisierung mit Versionen.** Das Ziel wird zum genauen Abbild der Quelle. Was Sie
  ändern oder löschen, geht nicht verloren: Die vorherige Version wandert auf dem Sicherungslaufwerk
  nach `.devbackup/versiones/<Datum>/` und bleibt so lange erhalten, wie Sie wählen (7, 30 oder
  60 Tage). Danach wird sie automatisch entfernt – bei Platzmangel auch früher, die ältesten zuerst.
- **Wiederherstellen wie mit einer Zeitmaschine.** Der Tab *Wiederherstellen* zeigt das Beiseitegelegte
  nach Datum (heute, gestern, diese Woche, nach Monaten) oder alles zusammen, mit Suche und den Filtern
  *Gelöscht* und *Frühere Versionen*. Ein Klick legt Dateien oder ganze Ordner an ihren Platz in der
  Quelle zurück oder in einen anderen Ordner. Es wird nie etwas überschrieben: Gibt es bereits eine
  andere Datei mit diesem Namen, wird die Version daneben als
  `Name (wiederhergestellt <Datum>).ext` gespeichert.

  ![Wiederherstellen: Beiseitegelegtes nach Datum](docs/de/recuperar.png)

- **Schutz vor Massenlöschungen.** Würde eine Synchronisierung einen großen Teil der Sicherung
  beiseitelegen (etwa weil die Quelle leer erscheint oder ein Laufwerk nicht richtig verbunden ist),
  hält sie an, bevor sie etwas anrührt, zeigt, welche Ordner verschwinden würden, und fragt nach.
- **Erkennt Verschobenes und Umbenanntes.** Ein umbenannter oder in einen anderen Unterordner
  verschobener Ordner wird auf dem Laufwerk in Sekunden verschoben, statt erneut kopiert zu werden –
  auch mit doppelten Dateien (mehrere leere `__init__.py`, `.gitkeep` …) und selbst ohne eine einzige
  eindeutige Datei. Von Hand ausgeschlossene Unterordner bleiben unter dem neuen Namen ausgeschlossen,
  und eine reine Änderung der Groß-/Kleinschreibung (`projekt` → `Projekt`) wird ebenfalls übernommen.
- **Lässt weg, was sich neu erzeugen lässt.** `node_modules`, `.venv`, `__pycache__`, `dist` … per Name
  auf jeder Ebene, oder einzelne Ordner durch Entfernen des Hakens im Baum.
- **Erkennt das Laufwerk beim Anschließen.** Die App bleibt im Infobereich (unter macOS in der
  Menüleiste) und analysiert beim Einstecken des Laufwerks, was neu, geändert, verschoben oder gelöscht
  ist, bevor irgendetwas kopiert wird. Sie erkennt es auch, wenn Windows einen anderen
  Laufwerksbuchstaben vergibt.
- **Prüft jede Kopie.** Jede kopierte Datei wird vom Laufwerk neu gelesen, am Systemcache vorbei, und
  ihr SHA-256 mit dem Original verglichen. Stimmt er nicht überein, wird erneut kopiert.
- **Prüft die Integrität des Laufwerks nach und nach.** Nach jeder Synchronisierung und für die gewählte
  Dauer (standardmäßig 5 Minuten, auch bei einer halb gelesenen riesigen Datei nicht länger) prüft sie
  einen Teil der bereits kopierten Dateien auf stille Beschädigung: zuerst die noch nie geprüften, dann
  die vor mehr als 30 Tagen geprüften. Ist das Ziel beschädigt, wird es aus dem Original repariert; ist
  es das Original, warnt die App und lässt die gute Kopie unangetastet. Die Prüfung lässt sich jederzeit
  überspringen: Die Sicherung ist bereits vollständig.
- **Genauer Fortschritt und Zusammenfassung am Ende.** Prozent, Tempo, Restzeit, voraussichtliches Ende,
  laufende Dateien pro Thread und Gesamtdauer auf die Millisekunde. Danach eine Zusammenfassung mit
  neuen, geänderten, verschobenen und in Versionen verschobenen Dateien, angelegten und entfernten
  Ordnern, bereits aktuellen und geprüften Dateien. Pausieren, Abbrechen und Fortsetzen, ohne halbe
  Dateien zu hinterlassen.
- **Ganzes Laufwerk als Quelle.** Ist die Quelle das Stammverzeichnis eines Laufwerks (`F:\`), werden
  Papierkorb (`$RECYCLE.BIN`), `System Volume Information` und die Systemordner von macOS ausgelassen.
- **Sichert auch in einen Netzwerkordner.** Das Ziel kann ein freigegebener Ordner eines anderen
  Rechners zu Hause sein, z. B. der Ordner `DEV_Backup_Studio` von
  [Personal Cloud Storage](#zusammen-mit-personal-cloud-storage): ein Netzlaufwerk (`Z:`) oder der Pfad
  `\\cloud.local\DEV_Backup_Studio\MeinPC` unter Windows, die unter `/Volumes` eingebundene Freigabe
  unter macOS oder ein im Dateimanager geöffneter Netzwerkordner unter Linux. Vorher prüft die App in
  einer Sekunde, ob der Server antwortet, sodass ein ausgeschalteter Server sie nicht warten lässt.
  Sobald das Netz wieder da ist, bietet sie die Synchronisierung an, wie beim Anschließen des Laufwerks.
- **Verbindet sich mit einem Code mit Personal Cloud Storage.** *Mit meiner Cloud verbinden…* findet
  die Cloud im Netzwerk und fragt nach dem 6-stelligen Code aus deren Weboberfläche. Die Zugangsdaten
  werden im System gespeichert und das Ziel automatisch eingetragen: keine Adressen, keine Passwörter.
  Unter Linux wird der Ordner mit GVFS eingebunden (wie beim Öffnen von `smb://` im Dateimanager, ohne
  Administratorrechte), die Zugangsdaten liegen in einer nur für Ihr Benutzerkonto lesbaren Datei
  (`~/.config/DEV Backup Studio/nube-credenciales.json`), und nach einem Neustart wird der Ordner
  automatisch wieder eingebunden, sobald die Cloud antwortet.
- **Nichts unterbricht die Sicherung.** Während der Analyse oder Synchronisierung geht der Computer
  nicht wegen Inaktivität in den Ruhezustand, und das USB-Laufwerk schläft nicht ein. Herunterfahren
  oder Neustarten verlangt eine Bestätigung: Unter Windows erscheint „Sicherung läuft“, unter macOS
  fragt die App selbst (unter Linux verhindert `systemd-inhibit` den Ruhezustand). Geht der Computer
  trotzdem in den Ruhezustand oder wird das Laufwerk getrennt, wartet die Sicherung und setzt sich von
  selbst fort, ohne etwas als fehlgeschlagen zu werten. Abschaltbar unter *Energie*.
- **Erinnert ans Sichern.** Die App merkt sich jede Sicherung (Quelle und Laufwerk) und meldet sich
  einmal pro Zeitraum, wenn die gewählte Anzahl Tage mit ungesicherten Änderungen vergangen ist:
  „Nicht gesicherte Änderungen in DEV · 37 Dateien seit Ihrer letzten Sicherung, vor 4 Tagen“. Sie
  prüft das, ohne Dateien zu öffnen; ohne Änderungen keine Erinnerung. Ist das Laufwerk verbunden, wird
  direkt aus der Erinnerung synchronisiert.
- **In sieben Sprachen.** Spanisch, Englisch, Französisch, Deutsch, Italienisch, Chinesisch und
  Japanisch: automatisch nach Systemsprache oder unter *System → Sprache* wählbar.
- **Kurzanleitung.** Beim ersten Start zeigt sie in sechs Schritten mit Bildschirmfotos der App, wie
  alles eingerichtet wird: Quelle und Ziel (Laufwerk oder Cloud), was gesichert wird, Analysieren und
  Synchronisieren, Gelöschtes wiederherstellen und das Laufwerk anschließen. Nach einem Update mit
  wichtigen Neuerungen erscheint sie einmal erneut. Wieder öffnen lässt sie sich mit der Schaltfläche
  *Anleitung*, mit F1 (⌘? unter macOS) oder über das Symbol im Infobereich.
- **Aktualisiert sich selbst, nach Rückfrage.** Prüft auf GitHub, ob es eine neue Version gibt
  (höchstens einmal am Tag), zeigt die Neuerungen und lädt auf Wunsch das Installationsprogramm herunter,
  prüft dessen SHA-256, installiert es und startet neu. Abschaltbar oder manuell prüfbar unter
  *Updates*. Neuerungen jeder Version in [CHANGELOG.md](CHANGELOG.md) (auf Spanisch).

| Beim Anschließen des Laufwerks | Änderungen vor dem Kopieren prüfen |
|---|---|
| ![Dialog beim Anschließen](docs/de/conectar.png) | ![Liste der Änderungen](docs/de/cambios.png) |

![Kurzanleitung](docs/de/guia.png)

## Zusammen mit Personal Cloud Storage

[Personal Cloud Storage](https://github.com/crazyramirez/personal-cloud-storage) ist eine persönliche
Cloud, die Sie zu Hause mit einem Raspberry Pi (oder Mini-PC) und einem USB-Laufwerk betreiben, ohne
Dienste Dritter. DEV Backup Studio kann die Sicherungen Ihrer Computer **über das Heimnetz in diese
Cloud** schreiben – statt oder zusätzlich zu einem USB-Laufwerk.

So ergänzen sich beide:

- **Ihre Projekte landen von selbst in der Cloud.** DEV Backup Studio kopiert, was sich geändert hat,
  bewahrt Versionen von Geändertem und Gelöschtem auf und prüft jede Datei mit SHA-256. Kein Laufwerk
  mehr anschließen: Es genügt, zu Hause (oder in Tailscale) zu sein.
- **Ihre Sicherungen auf jedem Gerät ansehen und herunterladen**: Handy, anderer Computer, Browser.
  Sie liegen im Ordner `DEV_Backup_Studio` der Cloud, einer pro Computer.
- **Drei Kopien ohne Aufwand.** Das Original auf Ihrem Computer, die Kopie in der Cloud und – wenn Sie
  die Sicherung auf ein externes Laufwerk von Personal Cloud Storage nutzen – eine dritte auf einem
  weiteren Laufwerk.

Die Verbindung ist in einer Minute eingerichtet, ohne Adressen oder Passwörter einzugeben:

1. In der Cloud einmalig den Netzwerkordner aktivieren:
   `sudo bash /opt/pcloud/current/deploy/linux/setup/pcloud-install-smb.sh --execute`
   ([Anleitung, auf Spanisch](https://github.com/crazyramirez/personal-cloud-storage/blob/main/docs/OPERATIONS.md#8-quinquies-carpeta-de-red-para-copias-de-los-ordenadores-smb-opcional)).
2. In der Weboberfläche der Cloud: *Einstellungen → Sicherungen Ihrer Computer → Computer verbinden*
   (auf Spanisch: *Ajustes → Copias de tus ordenadores → Conectar un ordenador*). Es erscheint ein
   6-stelliger Code.
3. In DEV Backup Studio unter dem Ziel: *Mit meiner Cloud verbinden…* Die App findet die Cloud im
   Netzwerk; Code eingeben und auf *Verbinden* klicken. Das Ziel zeigt nun auf den Ordner dieses
   Computers.

Ist die Cloud ausgeschaltet oder nicht erreichbar, meldet die App das in einer Sekunde und bietet die
Synchronisierung an, sobald sie wieder da ist – wie beim Anschließen eines Laufwerks.

## Wie entschieden wird, was kopiert wird

Eine Datei gilt als geändert, wenn sich **Größe oder Änderungsdatum** unterscheiden – wie bei
robocopy, rsync oder FreeFileSync in der Standardeinstellung. Auf NTFS und APFS wird das Datum auf die
Nanosekunde verglichen, sodass auch eine Bearbeitung ohne Größenänderung erkannt wird; auf FAT/exFAT
gilt deren Toleranz von 2 s.

Damit das externe Laufwerk nicht jeden Tag komplett gelesen werden muss, wird am Ende ein Index
(SQLite) mit dem Zustand des Ziels gespeichert. Die tägliche Analyse vergleicht die Quelle mit diesem
Index und greift nur für Geändertes auf das Laufwerk zu. Alle 7 Tage oder nach einer Änderung der
Einstellungen wird das gesamte Ziel gelesen.

| | Erkennt Änderungen über | Gelöschtes | Umbenanntes | Verlauf |
|---|---|---|---|---|
| robocopy /MIR | Größe + Datum | wird entfernt | wird neu kopiert | nein |
| rsync | Größe + Datum (oder Inhalt) | `--delete` | wird neu kopiert | mit `--backup-dir` |
| FreeFileSync | Größe + Datum, mit Datenbank | in Versionen | wird erkannt | ja |
| restic / Borg | Inhalt in Blöcken | Snapshots | ohne Aufwand | ja, aber zum Ansehen muss wiederhergestellt werden |
| **DEV Backup Studio** | Größe + exaktes Datum, mit Index | in Versionen | wird erkannt | ja, normale Dateien, die sich direkt öffnen lassen |

## Wo was gespeichert wird

- **Auf dem Sicherungslaufwerk**: versteckter Ordner `.devbackup/` im Stammverzeichnis mit der Kennung
  des Laufwerks (`disco.json`), dem Sicherungsverlauf, den Versionen und `latido`, der winzigen Datei,
  die das Laufwerk wach hält.
- **Auf dem Computer**: Einstellungen (mit den regelmäßigen Sicherungen für die Erinnerungen), Indizes
  und Protokolle in `%APPDATA%\DEV Backup Studio` (Windows),
  `~/Library/Application Support/DEV Backup Studio` (macOS) oder `~/.config/DEV Backup Studio` (Linux).

## Aus dem Quellcode starten

```bash
pip install -r requirements.txt
python backup_studio.py
```

`python backup_studio.py --selftest` führt ohne Fenster eine vollständige Test-Synchronisierung durch
(Kopieren, Prüfen, Verschieben und Reparieren) und schreibt das Ergebnis in `selftest.log`.

## Kompilieren

Beim Veröffentlichen eines Tags `v*` (muss mit `APP_VERSION` in `backup_studio.py` übereinstimmen und
einen Abschnitt in `CHANGELOG.md` haben, aus dem die Versionshinweise stammen) erledigt GitHub Actions:

1. kompiliert die App mit PyInstaller unter Windows, macOS und Linux und führt auf jeder den Selbsttest
   aus;
2. erstellt unter Windows das Installationsprogramm mit Inno Setup (`installer/windows.iss`),
   installiert es ohne Fenster, wiederholt den Selbsttest mit der installierten App und deinstalliert
   sie wieder;
3. packt unter macOS die `.app` in eine `.dmg`;
4. erstellt unter Linux (Ubuntu 22.04) die `.AppImage` (`installer/linux/build-appimage.sh`) und
   wiederholt den Selbsttest damit;
5. veröffentlicht die Version mit Installationsprogramm, `.dmg` und `.AppImage`.

```bash
git tag v1.8.0
git push origin v1.8.0
```

Die Texte werden mit `locales/<Sprache>.json` übersetzt (Textliste: `python tools/i18n_extract.py`,
Prüfung: `--check`). Die Bilder der Kurzanleitung (`assets/tour/<Sprache>/`) und die Bildschirmfotos
der READMEs (`docs/`, `docs/de/`) entstehen aus echten Aufnahmen der App:
`python tools/make_tour.py` (Windows; bindet Beispieldaten mit `subst` ein).

## Einschränkungen

- Eine Sicherung auf einem einzigen Laufwerk schützt nicht vor Brand oder Diebstahl: Dafür braucht es
  zusätzlich eine Kopie außer Haus.
- Versionen speichern die vollständige Datei (keine Deduplizierung in Blöcken wie bei restic).
- Die macOS-Version ist für Apple Silicon kompiliert, die Linux-Version für x86_64.
- Unter Linux benötigt *Mit meiner Cloud verbinden…* GVFS mit SMB-Modul (`gvfs-backends` unter Ubuntu
  und Debian, `gvfs-smb` unter Fedora und Arch), das GNOME, Cinnamon, MATE und XFCE mitbringen. Unter
  KDE muss es installiert werden; fehlt es, sagt die App Bescheid.
- Linux unterscheidet Groß- und Kleinschreibung, FAT-, exFAT- und NTFS-Laufwerke nicht: Liegen im
  selben Ordner `Foto.jpg` und `foto.jpg` und ist das Ziel eines dieser Laufwerke, wird nur eine Datei
  kopiert und die andere als Fehler gemeldet.
