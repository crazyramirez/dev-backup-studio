<?php
// Ordenadores conectados y código de emparejamiento, en pm_Settings.
//
// El token de cada ordenador nunca se guarda: solo su SHA-256. El código de 6 cifras sirve una vez,
// caduca a los 10 minutos y se anula tras 5 intentos fallidos. Además, cada IP tiene un límite de
// intentos fallidos (sirve para cualquier token o código).

class Modules_DevBackupStudio_Store
{
    const CODE_TTL = 600;
    const CODE_TRIES = 5;
    const IP_FAILS = 20;          // fallos por IP...
    const IP_WINDOW = 600;        // ...en 10 minutos

    public static function devices()
    {
        $data = json_decode((string)pm_Settings::get('devices', '{}'), true);
        return is_array($data) ? $data : [];
    }

    public static function saveDevices(array $devices)
    {
        pm_Settings::set('devices', json_encode($devices));
    }

    // ── código de emparejamiento ──
    public static function newCode()
    {
        $code = sprintf('%06d', random_int(0, 999999));
        pm_Settings::set('pair_hash', hash('sha256', $code));
        pm_Settings::set('pair_expires', (string)(time() + self::CODE_TTL));
        pm_Settings::set('pair_tries', '0');
        self::setSecret('pair_code', $code);
        return $code;
    }

    // Código en pantalla: [código, segundos que le quedan] o null.
    public static function currentCode()
    {
        $expires = (int)pm_Settings::get('pair_expires', '0');
        $code = self::getSecret('pair_code');
        if (!$code || $expires <= time()) {
            return null;
        }
        return [$code, $expires - time()];
    }

    public static function clearCode()
    {
        pm_Settings::set('pair_hash', '');
        pm_Settings::set('pair_expires', '0');
        self::setSecret('pair_code', '');
    }

    public static function checkCode($code)
    {
        $hash = (string)pm_Settings::get('pair_hash', '');
        $expires = (int)pm_Settings::get('pair_expires', '0');
        if ($hash === '' || $expires <= time() || !preg_match('/^\d{6}$/', (string)$code)) {
            return false;
        }
        if (hash_equals($hash, hash('sha256', (string)$code))) {
            self::clearCode();
            return true;
        }
        $tries = (int)pm_Settings::get('pair_tries', '0') + 1;
        pm_Settings::set('pair_tries', (string)$tries);
        if ($tries >= self::CODE_TRIES) {
            self::clearCode();
        }
        return false;
    }

    // ── ordenadores ──
    public static function addDevice($name, $ip)
    {
        $token = bin2hex(random_bytes(32));
        $id = bin2hex(random_bytes(8));
        $devices = self::devices();
        $devices[$id] = [
            'name' => mb_substr(trim(strip_tags((string)$name)) ?: 'PC', 0, 64),
            'hash' => hash('sha256', $token),
            'created' => time(),
            'last_seen' => time(),
            'ip' => (string)$ip,
        ];
        self::saveDevices($devices);
        pm_Settings::set('just_paired', $devices[$id]['name']);   // la página lo anuncia al ponerse al día
        return [$id, $token];
    }

    // El ordenador que acaba de conectarse (una sola vez), para anunciarlo en la página.
    public static function takeJustPaired()
    {
        $name = (string)pm_Settings::get('just_paired', '');
        if ($name !== '') {
            pm_Settings::set('just_paired', '');
        }
        return $name;
    }

    public static function deviceForToken($token)
    {
        if (!is_string($token) || !preg_match('/^[a-f0-9]{64}$/', $token)) {
            return null;
        }
        $hash = hash('sha256', $token);
        $devices = self::devices();
        foreach ($devices as $id => $d) {
            if (isset($d['hash']) && hash_equals($d['hash'], $hash)) {
                if (time() - (int)($d['last_seen'] ?? 0) > 60) {
                    $devices[$id]['last_seen'] = time();
                    self::saveDevices($devices);
                }
                return (string)$id;
            }
        }
        return null;
    }

    public static function removeDevice($id)
    {
        $devices = self::devices();
        unset($devices[$id]);
        self::saveDevices($devices);
    }

    // ── límite de intentos por IP ──
    public static function blocked($ip)
    {
        $t = self::throttle();
        return isset($t[$ip]) && $t[$ip][0] >= self::IP_FAILS && time() - $t[$ip][1] < self::IP_WINDOW;
    }

    public static function failed($ip)
    {
        $t = self::throttle();
        if (!isset($t[$ip]) || time() - $t[$ip][1] >= self::IP_WINDOW) {
            $t[$ip] = [0, time()];
        }
        $t[$ip][0]++;
        foreach ($t as $k => $v) {
            if (time() - $v[1] >= self::IP_WINDOW) {
                unset($t[$k]);
            }
        }
        pm_Settings::set('throttle', json_encode($t));
    }

    private static function throttle()
    {
        $t = json_decode((string)pm_Settings::get('throttle', '{}'), true);
        return is_array($t) ? $t : [];
    }

    // ── secretos (cifrados si la versión de Plesk lo permite) ──
    private static function setSecret($key, $value)
    {
        if (method_exists('pm_Settings', 'setEncrypted')) {
            pm_Settings::setEncrypted($key, (string)$value);
        } else {
            pm_Settings::set($key, (string)$value);
        }
    }

    private static function getSecret($key)
    {
        if (method_exists('pm_Settings', 'getDecrypted')) {
            return (string)pm_Settings::getDecrypted($key);
        }
        return (string)pm_Settings::get($key, '');
    }
}
