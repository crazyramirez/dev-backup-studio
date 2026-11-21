<?php
// API para la app DEV Backup Studio. No usa la sesión de Plesk: cada ordenador se identifica con su
// token (cabecera X-DBS-Token), que recibe al canjear el código de 6 cifras del panel.
require_once('sdk.php');
pm_Context::init('dev-backup-studio');
require_once pm_Context::getPlibDir() . 'library/Store.php';
require_once pm_Context::getPlibDir() . 'library/Jobs.php';
require_once pm_Context::getPlibDir() . 'library/Api.php';
Modules_DevBackupStudio_Api::handle();
