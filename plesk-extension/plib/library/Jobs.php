<?php
// Copias y restauraciones en marcha. Cada trabajo es una carpeta en var/jobs/<id>/ con un archivo por
// dato (estado, tamaño, SHA-256, registro…). El script sbin/devbackup, que Plesk ejecuta con
// privilegios, lanza las herramientas oficiales en segundo plano y va escribiendo esos archivos; PHP
// solo los lee, y crea el trabajo antes de lanzarlo.

class Modules_DevBackupStudio_Jobs
{
    const KEEP_DAYS = 3;                 // lo que nadie recoge se borra a los 3 días
    const UPLOAD_KEEP_DAYS = 2;
    const SBIN = 'devbackup';

    public static function base()
    {
        return rtrim(pm_Context::getVarDir(), '/') . '/';
    }

    public static function jobsDir()
    {
        return self::ensureDir(self::base() . 'jobs');
    }

    public static function uploadsDir()
    {
        return self::ensureDir(self::base() . 'uploads');
    }

    private static function ensureDir($dir)
    {
        if (!is_dir($dir)) {
            @mkdir($dir, 0700, true);
        }
        return $dir . '/';
    }

    public static function validId($id)
    {
        return is_string($id) && preg_match('/^[a-f0-9]{32}$/', $id);
    }

    public static function dir($id)
    {
        return self::jobsDir() . $id . '/';
    }

    public static function read($id, $name, $default = '')
    {
        $f = self::dir($id) . $name;
        return is_file($f) ? trim((string)@file_get_contents($f)) : $default;
    }

    private static function write($dir, $name, $value, $mode = 0600)
    {
        $f = $dir . $name;
        @unlink($f);   // puede ser de root: se sustituye en vez de reescribirlo
        file_put_contents($f, (string)$value);
        @chmod($f, $mode);
    }

    public static function exists($id)
    {
        return self::validId($id) && is_dir(self::dir($id));
    }

    // ¿Hay algo en marcha en el servidor? (una sola copia o restauración a la vez)
    public static function running()
    {
        foreach (self::all() as $id) {
            if (in_array(self::state($id), ['queued', 'running'], true)) {
                return $id;
            }
        }
        return null;
    }

    public static function all()
    {
        $out = [];
        foreach ((array)@scandir(self::jobsDir()) as $name) {
            if (self::validId($name) && is_dir(self::dir($name))) {
                $out[] = $name;
            }
        }
        return $out;
    }

    public static function state($id)
    {
        $state = self::read($id, 'state', 'queued');
        if ($state === 'running' || $state === 'queued') {
            // el proceso toca «alive» cada 20 s; si el servidor se reinició a mitad, deja de hacerlo
            // (no se mira /proc: con hidepid el panel no ve los procesos de root)
            clearstatcache(true, self::dir($id) . 'alive');
            $beat = @filemtime(self::dir($id) . 'alive') ?: (int)self::read($id, 'started', (string)time());
            if (time() - $beat > 180) {
                self::write(self::dir($id), 'state', 'failed', 0644);
                if (self::read($id, 'error') === '') {
                    self::write(self::dir($id), 'error', 'interrupted', 0644);
                }
                return 'failed';
            }
        }
        return $state;
    }

    public static function create($kind, $device, array $files)
    {
        $id = bin2hex(random_bytes(16));
        $dir = self::dir($id);
        mkdir($dir, 0700);
        self::write($dir, 'kind', $kind, 0644);
        self::write($dir, 'device', $device, 0644);
        self::write($dir, 'state', 'queued', 0644);
        self::write($dir, 'started', (string)time(), 0644);
        foreach ($files as $name => $value) {
            self::write($dir, $name, $value);
        }
        return $id;
    }

    public static function launch($id, $command)
    {
        $res = pm_ApiCli::callSbin(self::SBIN, [$command, $id], pm_ApiCli::RESULT_FULL);
        if ((int)$res['code'] !== 0) {
            $err = trim($res['stderr'] ?: $res['stdout']) ?: ('exit ' . $res['code']);
            self::write(self::dir($id), 'state', 'failed', 0644);
            self::write(self::dir($id), 'error', $err, 0644);
            throw new Exception($err);
        }
    }

    public static function info($id)
    {
        $log = [];
        $f = self::dir($id) . 'log';
        if (is_file($f)) {
            $size = filesize($f);
            $fh = fopen($f, 'rb');
            if ($size > 32768) {
                fseek($fh, -32768, SEEK_END);
            }
            $tail = (string)stream_get_contents($fh);
            fclose($fh);
            $lines = preg_split('/\r\n|\r|\n/', $tail);
            if ($size > 32768) {
                array_shift($lines);   // la primera puede estar cortada
            }
            $log = array_values(array_filter(array_map('rtrim', $lines), 'strlen'));
            $log = array_slice($log, -200);
        }
        $started = (int)self::read($id, 'started', '0');
        $finished = (int)self::read($id, 'finished', '0');
        return [
            'id' => $id,
            'kind' => self::read($id, 'kind'),
            'state' => self::state($id),
            'mode' => self::read($id, 'mode'),
            'started' => $started ? date('c', $started) : null,
            'finished' => $finished ? date('c', $finished) : null,
            'size' => (int)self::read($id, 'size', '0'),
            'sha256' => self::read($id, 'sha256'),
            'error' => self::read($id, 'error'),
            'log' => $log,
        ];
    }

    // Lo que enseña la página de la extensión, resumido: si cambia (un ordenador se conecta, se gasta
    // o caduca el código, un trabajo avanza de estado), la página se recarga sola.
    public static function pageSignature()
    {
        $parts = [Modules_DevBackupStudio_Store::currentCode() ? 'code' : 'nocode'];
        foreach (Modules_DevBackupStudio_Store::devices() as $id => $d) {
            $parts[] = 'd:' . $id;
        }
        foreach (self::all() as $id) {
            $parts[] = 'j:' . $id . ':' . self::state($id);
        }
        sort($parts);
        return sha1(implode('|', $parts));
    }

    public static function backupFile($id)
    {
        return self::dir($id) . 'backup.tar';
    }

    public static function cancel($id)
    {
        pm_ApiCli::callSbin(self::SBIN, ['cancel', $id], pm_ApiCli::RESULT_FULL);
    }

    public static function remove($id)
    {
        if (in_array(self::state($id), ['queued', 'running'], true)) {
            self::cancel($id);
        }
        self::rmtree(self::dir($id));
    }

    // Lo viejo sin recoger: trabajos terminados hace días y subidas abandonadas.
    public static function cleanup()
    {
        $limit = time() - self::KEEP_DAYS * 86400;
        foreach (self::all() as $id) {
            $state = self::state($id);
            if (!in_array($state, ['queued', 'running'], true) && filemtime(self::dir($id) . 'state') < $limit) {
                self::rmtree(self::dir($id));
            }
        }
        $limit = time() - self::UPLOAD_KEEP_DAYS * 86400;
        foreach ((array)@scandir(self::uploadsDir()) as $name) {
            $d = self::uploadsDir() . $name;
            if (self::validId($name) && is_dir($d) && @filemtime($d . '/data') < $limit && @filemtime($d . '/meta.json') < $limit) {
                self::rmtree($d . '/');
            }
        }
    }

    public static function rmtree($dir)
    {
        $dir = rtrim($dir, '/');
        if (!is_dir($dir) || is_link($dir)) {
            return;
        }
        foreach ((array)scandir($dir) as $name) {
            if ($name === '.' || $name === '..') {
                continue;
            }
            $p = "$dir/$name";
            if (is_dir($p) && !is_link($p)) {
                self::rmtree($p);
            } else {
                @unlink($p);
            }
        }
        @rmdir($dir);
    }

    // ── subidas (para restaurar) ──
    public static function uploadDir($id)
    {
        return self::uploadsDir() . $id . '/';
    }

    public static function uploadMeta($id)
    {
        if (!self::validId($id)) {
            return null;
        }
        $meta = json_decode((string)@file_get_contents(self::uploadDir($id) . 'meta.json'), true);
        if (!is_array($meta)) {
            return null;
        }
        clearstatcache(true, self::uploadDir($id) . 'data');
        $meta['received'] = is_file(self::uploadDir($id) . 'data') ? filesize(self::uploadDir($id) . 'data') : 0;
        return $meta;
    }

    public static function findUpload($device, $size, $sha256)
    {
        foreach ((array)@scandir(self::uploadsDir()) as $name) {
            $m = self::uploadMeta($name);
            if ($m && $m['device'] === $device && (int)$m['size'] === (int)$size && $m['sha256'] === $sha256) {
                return $name;
            }
        }
        return null;
    }

    public static function newUpload($device, $name, $size, $sha256)
    {
        $id = bin2hex(random_bytes(16));
        $dir = self::uploadDir($id);
        mkdir($dir, 0700);
        file_put_contents($dir . 'meta.json', json_encode([
            'device' => $device, 'name' => $name, 'size' => (int)$size, 'sha256' => $sha256, 'created' => time(),
        ]));
        touch($dir . 'data');
        @chmod($dir . 'data', 0600);
        return $id;
    }
}
