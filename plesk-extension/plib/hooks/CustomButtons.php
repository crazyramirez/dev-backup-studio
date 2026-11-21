<?php
// Acceso a la extensión desde el menú de la izquierda del administrador.

class Modules_DevBackupStudio_CustomButtons extends pm_Hook_CustomButtons
{
    public function getButtons()
    {
        if (!defined('pm_Hook_CustomButtons::PLACE_ADMIN_NAVIGATION')) {
            return [];
        }
        return [[
            'place' => self::PLACE_ADMIN_NAVIGATION,
            'title' => pm_Locale::lmsg('navTitle'),
            'description' => pm_Locale::lmsg('navDescription'),
            'icon' => pm_Context::getBaseUrl() . 'images/icon.png',
            'link' => pm_Context::getBaseUrl(),
        ]];
    }
}
