<?php
// API JSON para la app de escritorio (htdocs/public/api.php?a=<acción>).
//
//   pair          POST {code, device}           → {token, device, server}     (sin token)
//   info          GET                           → {server, free_bytes, jobs}
//   backup        POST {password, exclude_logs} → {job}     pleskbackup --server
//   job           GET  id                       → {job}
//   download      GET  id  (Range)              → el archivo de la copia
//   release       POST id                       → borra la copia del servidor
//   cancel        POST id
//   upload_start  POST {name, size, sha256}     → {upload: {id, received}}  (sigue una subida a medias)
//   upload_chunk  POST id, offset  (cuerpo binario)
//   restore       POST {upload, password, mode} → {job}     pleskrestore -level server | pmm-ras import
//   unpair        POST                          → este ordenador deja de estar conectado

class Modules_DevBackupStudio_ApiError extends Exception
{
    public $kind;
    public $status;

    public function __construct($kind, $status, $message = '')
    {
        parent::__construct($message);
        $this->kind = $kind;
        $this->status = $status;
    }
}

class Modules_DevBackupStudio_Api
{
    const PROTOCOL = 1;
    const VERSION = '1.0.2';            // la de meta.xml (tools/build_plesk_extension.py lo comprueba)
    const CHUNK_MAX = 16777216;          // 16 MB por parte (la app manda 8)
    const SPACE_RESERVE = 1073741824;    // 1 GB libre de más para que Plesk pueda trabajar

    private static $device = null;

    public static function handle()
    {
        $ip = (string)($_SERVER['REMOTE_ADDR'] ?? '');
        try {
            if (Modules_DevBackupStudio_Store::blocked($ip)) {
                throw new Modules_DevBackupStudio_ApiError('busy', 429);
            }
            $action = (string)($_GET['a'] ?? '');
            $method = strtoupper((string)($_SERVER['REQUEST_METHOD'] ?? 'GET'));
            if ($action === 'pair') {
                self::need($method, 'POST');
                self::reply(self::pair($ip));
            }
            $token = (string)($_SERVER['HTTP_X_DBS_TOKEN'] ?? '');
            self::$device = Modules_DevBackupStudio_Store::deviceForToken($token);
            if (self::$device === null) {
                Modules_DevBackupStudio_Store::failed($ip);
                throw new Modules_DevBackupStudio_ApiError('auth', 401);
            }
            switch ($action) {
                case 'info':
                    Modules_DevBackupStudio_Jobs::cleanup();
                    self::reply(self::info());
                case 'backup':
                    self::need($method, 'POST');
                    self::reply(['job' => self::backup(self::body())]);
                case 'job':
                    self::reply(['job' => Modules_DevBackupStudio_Jobs::info(self::ownJob())]);
                case 'download':
                    self::download(self::ownJob());
                    exit;
                case 'release':
                    self::need($method, 'POST');
                    Modules_DevBackupStudio_Jobs::remove(self::ownJob());
                    self::reply(['released' => true]);
                case 'cancel':
                    self::need($method, 'POST');
                    Modules_DevBackupStudio_Jobs::cancel(self::ownJob());
                    self::reply(['job' => Modules_DevBackupStudio_Jobs::info(self::ownJob())]);
                case 'upload_start':
                    self::need($method, 'POST');
                    self::reply(['upload' => self::uploadStart(self::body())]);
                case 'upload_chunk':
                    self::need($method, 'POST');
                    self::reply(['upload' => self::uploadChunk()]);
                case 'restore':
                    self::need($method, 'POST');
                    self::reply(['job' => self::restore(self::body())]);
                case 'unpair':
                    self::need($method, 'POST');
                    Modules_DevBackupStudio_Store::removeDevice(self::$device);
                    self::reply(['unpaired' => true]);
                default:
                    throw new Modules_DevBackupStudio_ApiError('server', 400, 'unknown action');
            }
        } catch (Modules_DevBackupStudio_ApiError $e) {
            self::reply(['error' => $e->kind, 'message' => $e->getMessage()], $e->status);
        } catch (Exception $e) {
            self::reply(['error' => 'server', 'message' => $e->getMessage()], 500);
        }
    }

    private static function reply(array $data, $status = 200)
    {
        $data['protocol'] = self::PROTOCOL;
        $data['ext_version'] = self::extVersion();
        http_response_code($status);
        header('Content-Type: application/json; charset=utf-8');
        header('Cache-Control: no-store');
        echo json_encode($data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
        exit;
    }

    private static function need($method, $want)
    {
        if ($method !== $want) {
            throw new Modules_DevBackupStudio_ApiError('server', 405, 'method');
        }
    }

    private static function body()
    {
        $data = json_decode((string)file_get_contents('php://input'), true);
        return is_array($data) ? $data : [];
    }

    private static function ownJob()
    {
        $id = (string)($_GET['id'] ?? '');
        if (!Modules_DevBackupStudio_Jobs::exists($id)
            || Modules_DevBackupStudio_Jobs::read($id, 'device') !== self::$device) {
            throw new Modules_DevBackupStudio_ApiError('missing', 404);
        }
        return $id;
    }

    public static function extVersion()
    {
        return self::VERSION;
    }

    public static function serverInfo()
    {
        $info = ['hostname' => gethostname() ?: '', 'plesk_version' => '', 'os' => '', 'ext_version' => self::extVersion()];
        if (class_exists('pm_ProductInfo')) {
            foreach (['plesk_version' => 'getVersion', 'os' => 'getOsName'] as $key => $fn) {
                if (method_exists('pm_ProductInfo', $fn)) {
                    try {
                        $info[$key] = (string)call_user_func(['pm_ProductInfo', $fn]);
                    } catch (Exception $e) {
                    }
                }
            }
            if ($info['os'] !== '' && method_exists('pm_ProductInfo', 'getOsVersion')) {
                try {
                    $info['os'] .= ' ' . pm_ProductInfo::getOsVersion();
                } catch (Exception $e) {
                }
            }
        }
        return $info;
    }

    // ── acciones ──
    private static function pair($ip)
    {
        $data = self::body();
        if (!Modules_DevBackupStudio_Store::checkCode((string)($data['code'] ?? ''))) {
            Modules_DevBackupStudio_Store::failed($ip);
            throw new Modules_DevBackupStudio_ApiError('code', 403);
        }
        list($device, $token) = Modules_DevBackupStudio_Store::addDevice((string)($data['device'] ?? 'PC'), $ip);
        return ['token' => $token, 'device' => $device, 'server' => self::serverInfo()];
    }

    private static function info()
    {
        $jobs = [];
        foreach (Modules_DevBackupStudio_Jobs::all() as $id) {
            if (Modules_DevBackupStudio_Jobs::read($id, 'device') === self::$device) {
                $j = Modules_DevBackupStudio_Jobs::info($id);
                unset($j['log']);
                $jobs[] = $j;
            }
        }
        return [
            'server' => self::serverInfo(),
            'free_bytes' => (int)@disk_free_space(Modules_DevBackupStudio_Jobs::base()),
            'jobs' => $jobs,
        ];
    }

    private static function notBusy()
    {
        $other = Modules_DevBackupStudio_Jobs::running();
        if ($other !== null) {
            throw new Modules_DevBackupStudio_ApiError('running', 409, $other);
        }
    }

    private static function backup(array $data)
    {
        self::notBusy();
        $files = ['password' => (string)($data['password'] ?? '')];
        if (!array_key_exists('exclude_logs', $data) || $data['exclude_logs']) {
            $files['opt_exclude_logs'] = '1';
        }
        $id = Modules_DevBackupStudio_Jobs::create('backup', self::$device, $files);
        Modules_DevBackupStudio_Jobs::launch($id, 'start-backup');
        return Modules_DevBackupStudio_Jobs::info($id);
    }

    private static function download($id)
    {
        if (Modules_DevBackupStudio_Jobs::state($id) !== 'done' || Modules_DevBackupStudio_Jobs::read($id, 'kind') !== 'backup') {
            throw new Modules_DevBackupStudio_ApiError('missing', 404);
        }
        $file = Modules_DevBackupStudio_Jobs::backupFile($id);
        $size = (int)Modules_DevBackupStudio_Jobs::read($id, 'size', '0');
        $fh = @fopen($file, 'rb');
        if (!$fh || $size <= 0) {
            throw new Modules_DevBackupStudio_ApiError('missing', 404);
        }
        $start = 0;
        $range = (string)($_SERVER['HTTP_RANGE'] ?? '');
        if (preg_match('/^bytes=(\d+)-$/', $range, $m)) {
            $start = (int)$m[1];
            if ($start >= $size) {
                http_response_code(416);
                header("Content-Range: bytes */$size");
                exit;
            }
        }
        @set_time_limit(0);
        while (ob_get_level() > 0) {
            ob_end_clean();
        }
        http_response_code($start ? 206 : 200);
        header('Content-Type: application/octet-stream');
        header('Content-Length: ' . ($size - $start));
        header('Accept-Ranges: bytes');
        header('X-Accel-Buffering: no');   // nginx (sw-cp-server) no lo guarda entero antes de mandarlo
        header('Cache-Control: no-store');
        if ($start) {
            header("Content-Range: bytes $start-" . ($size - 1) . "/$size");
            fseek($fh, $start);
        }
        $left = $size - $start;
        while ($left > 0 && !feof($fh) && !connection_aborted()) {
            $block = fread($fh, min(1048576, $left));
            if ($block === false || $block === '') {
                break;
            }
            echo $block;
            flush();
            $left -= strlen($block);
        }
        fclose($fh);
    }

    private static function uploadStart(array $data)
    {
        $size = (int)($data['size'] ?? 0);
        $sha = strtolower((string)($data['sha256'] ?? ''));
        $name = basename((string)($data['name'] ?? 'backup.tar'));
        if ($size <= 0 || !preg_match('/^[a-f0-9]{64}$/', $sha)) {
            throw new Modules_DevBackupStudio_ApiError('server', 400, 'size/sha256');
        }
        $id = Modules_DevBackupStudio_Jobs::findUpload(self::$device, $size, $sha);
        if ($id === null) {
            // la restauración necesita sitio para el archivo y para que Plesk lo desempaquete
            $free = (float)@disk_free_space(Modules_DevBackupStudio_Jobs::base());
            if ($free < 2 * $size + self::SPACE_RESERVE) {
                throw new Modules_DevBackupStudio_ApiError('serverspace', 507, (string)(2 * $size + self::SPACE_RESERVE - $free));
            }
            $id = Modules_DevBackupStudio_Jobs::newUpload(self::$device, $name, $size, $sha);
        }
        $meta = Modules_DevBackupStudio_Jobs::uploadMeta($id);
        return ['id' => $id, 'received' => $meta['received'], 'size' => $size];
    }

    private static function ownUpload($id)
    {
        $meta = Modules_DevBackupStudio_Jobs::uploadMeta($id);
        if (!$meta || $meta['device'] !== self::$device) {
            throw new Modules_DevBackupStudio_ApiError('missing', 404);
        }
        return $meta;
    }

    private static function uploadChunk()
    {
        $id = (string)($_GET['id'] ?? '');
        $offset = (int)($_GET['offset'] ?? -1);
        $meta = self::ownUpload($id);
        $received = (int)$meta['received'];
        if ($offset !== $received) {
            // la app sigue desde donde de verdad se quedó
            return ['id' => $id, 'received' => $received, 'size' => (int)$meta['size']];
        }
        $in = fopen('php://input', 'rb');
        $out = fopen(Modules_DevBackupStudio_Jobs::uploadDir($id) . 'data', 'ab');
        $written = stream_copy_to_stream($in, $out, min(self::CHUNK_MAX, (int)$meta['size'] - $received));
        fflush($out);
        fclose($out);
        fclose($in);
        if ($written === false) {
            throw new Modules_DevBackupStudio_ApiError('server', 500, 'write');
        }
        $meta = Modules_DevBackupStudio_Jobs::uploadMeta($id);
        return ['id' => $id, 'received' => (int)$meta['received'], 'size' => (int)$meta['size']];
    }

    private static function restore(array $data)
    {
        $uid = (string)($data['upload'] ?? '');
        $mode = (string)($data['mode'] ?? 'import');
        if (!in_array($mode, ['server', 'import'], true)) {
            throw new Modules_DevBackupStudio_ApiError('server', 400, 'mode');
        }
        $meta = self::ownUpload($uid);
        if ((int)$meta['received'] !== (int)$meta['size']) {
            throw new Modules_DevBackupStudio_ApiError('server', 409, 'upload incomplete');
        }
        self::notBusy();
        $id = Modules_DevBackupStudio_Jobs::create('restore', self::$device, [
            'password' => (string)($data['password'] ?? ''),
            'mode' => $mode,
            'expected_sha256' => $meta['sha256'],
            'name' => $meta['name'],
        ]);
        // misma carpeta var: se mueve, no se copia
        if (!rename(Modules_DevBackupStudio_Jobs::uploadDir($uid) . 'data', Modules_DevBackupStudio_Jobs::backupFile($id))) {
            Modules_DevBackupStudio_Jobs::rmtree(Modules_DevBackupStudio_Jobs::dir($id));
            throw new Modules_DevBackupStudio_ApiError('server', 500, 'move');
        }
        Modules_DevBackupStudio_Jobs::rmtree(Modules_DevBackupStudio_Jobs::uploadDir($uid));
        Modules_DevBackupStudio_Jobs::launch($id, 'start-restore');
        return Modules_DevBackupStudio_Jobs::info($id);
    }
}
