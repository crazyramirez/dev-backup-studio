# 1.0.3-1

* Backups in progress, uploads and their logs now live in /var/lib/dev-backup-studio. Plesk includes
  each extension's data folder in its server backups, so the backup being written was packed into
  itself and could fill the disk ("No space left on device").
* Plesk 18.0.8x writes JSON trace lines in its backup logs (every command it runs, such as
  "sw-tar --ignore-failed-read"): they are no longer shown as activity or mistaken for errors.

# 1.0.2-1

* Live progress while Plesk backs up or restores: how much it has written so far and the last lines
  of its own log (/var/log/plesk/PMM), in the app and on this page, every 10 seconds.

# 1.0.1-1

* The page updates by itself: when a computer uses the code it appears at once, with a notice;
  the code countdown runs live and backup activity refreshes without reloading.

# 1.0.0-1

* First version: pairing with a 6-digit code, full server backup with pleskbackup, download with
  resume and SHA-256, restore with pleskrestore or upload to the Backup Manager.
