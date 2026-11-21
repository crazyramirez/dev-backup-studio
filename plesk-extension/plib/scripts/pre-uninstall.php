<?php
// Al desinstalar: fuera las copias y subidas que queden en el servidor (pueden ser muy grandes).
pm_Context::init('dev-backup-studio');
require_once pm_Context::getPlibDir() . 'library/Jobs.php';
foreach (Modules_DevBackupStudio_Jobs::all() as $id) {
    Modules_DevBackupStudio_Jobs::remove($id);
}
Modules_DevBackupStudio_Jobs::rmtree(Modules_DevBackupStudio_Jobs::uploadsDir());
