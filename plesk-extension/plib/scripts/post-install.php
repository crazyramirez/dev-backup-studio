<?php
// Al instalar o actualizar: la carpeta de los trabajos, fuera de la carpeta de la extensión.
pm_Context::init('dev-backup-studio');
try {
    pm_ApiCli::callSbin('devbackup', ['init'], pm_ApiCli::RESULT_FULL);
} catch (Exception $e) {
    // se vuelve a intentar al primer uso (Jobs::base())
}
