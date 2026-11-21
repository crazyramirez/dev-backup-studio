<?php
// Página de la extensión en Plesk (solo administradores): conectar un ordenador con un código de 6
// cifras, ver y desconectar los ordenadores conectados, y la actividad reciente.

require_once pm_Context::getPlibDir() . 'library/Store.php';
require_once pm_Context::getPlibDir() . 'library/Jobs.php';
require_once pm_Context::getPlibDir() . 'library/Api.php';

class IndexController extends pm_Controller_Action
{
    public function init()
    {
        parent::init();
        if (!pm_Session::getClient()->isAdmin()) {
            throw new pm_Exception(pm_Locale::lmsg('accessDenied'));
        }
        $this->view->pageTitle = 'DEV Backup Studio';
    }

    public function indexAction()
    {
        $pairForm = new pm_Form_Simple();
        $pairForm->addElement('hidden', 'op', ['value' => 'pair']);
        $pairForm->addControlButtons([
            'sendTitle' => pm_Locale::lmsg(Modules_DevBackupStudio_Store::currentCode() ? 'newCodeButton' : 'pairButton'),
            'cancelHidden' => true,
        ]);

        $devices = Modules_DevBackupStudio_Store::devices();
        $revokeForm = null;
        if ($devices) {
            $options = [];
            foreach ($devices as $id => $d) {
                $options[$id] = $d['name'];
            }
            $revokeForm = new pm_Form_Simple();
            $revokeForm->addElement('hidden', 'op', ['value' => 'revoke']);
            $revokeForm->addElement('select', 'device', [
                'label' => pm_Locale::lmsg('deviceLabel'),
                'multiOptions' => $options,
                'required' => true,
            ]);
            $revokeForm->addControlButtons([
                'sendTitle' => pm_Locale::lmsg('revokeButton'),
                'cancelHidden' => true,
            ]);
        }

        $request = $this->getRequest();
        if ($request->isPost()) {
            $op = (string)$request->getPost('op');
            if ($op === 'pair' && $pairForm->isValid($request->getPost())) {
                Modules_DevBackupStudio_Store::newCode();
                $this->_helper->json(['redirect' => pm_Context::getBaseUrl()]);
                return;
            }
            if ($op === 'revoke' && $revokeForm && $revokeForm->isValid($request->getPost())) {
                $id = (string)$revokeForm->getValue('device');
                if (isset($devices[$id])) {
                    Modules_DevBackupStudio_Store::removeDevice($id);
                    $this->_status->addMessage('info', pm_Locale::lmsg('revoked', ['name' => $devices[$id]['name']]));
                }
                $this->_helper->json(['redirect' => pm_Context::getBaseUrl()]);
                return;
            }
        }

        Modules_DevBackupStudio_Jobs::cleanup();
        $jobs = [];
        foreach (Modules_DevBackupStudio_Jobs::all() as $id) {
            $j = Modules_DevBackupStudio_Jobs::info($id);
            $dev = Modules_DevBackupStudio_Jobs::read($id, 'device');
            $j['device'] = isset($devices[$dev]) ? $devices[$dev]['name'] : '—';
            unset($j['log']);
            $jobs[] = $j;
        }
        usort($jobs, function ($a, $b) {
            return strcmp((string)$b['started'], (string)$a['started']);
        });

        list($host, $port) = $this->panelAddress();
        $this->view->code = Modules_DevBackupStudio_Store::currentCode();
        $this->view->address = $port === 8443 ? $host : "$host:$port";
        $this->view->fingerprint = $this->fingerprint($host, $port);
        $this->view->pairForm = $pairForm;
        $this->view->revokeForm = $revokeForm;
        $this->view->devices = $devices;
        $this->view->jobs = array_slice($jobs, 0, 20);
        $this->view->version = Modules_DevBackupStudio_Api::VERSION;
        $this->view->tools = is_executable('/usr/local/psa/bin/pleskbackup') || !is_dir('/usr/local/psa/bin');
    }

    private function panelAddress()
    {
        $raw = (string)($_SERVER['HTTP_HOST'] ?? '');
        $host = parse_url('https://' . $raw, PHP_URL_HOST) ?: (gethostname() ?: 'localhost');
        $port = (int)(parse_url('https://' . $raw, PHP_URL_PORT) ?: 8443);
        return [trim($host, '[]'), $port];
    }

    // Huella SHA-256 del certificado del panel, la misma que la app pide confirmar si no es de una
    // autoridad reconocida.
    private function fingerprint($host, $port)
    {
        $ctx = stream_context_create(['ssl' => [
            'capture_peer_cert' => true, 'verify_peer' => false, 'verify_peer_name' => false,
            'peer_name' => $host, 'SNI_enabled' => true,
        ]]);
        $s = @stream_socket_client("ssl://127.0.0.1:$port", $errno, $errstr, 5, STREAM_CLIENT_CONNECT, $ctx);
        if (!$s) {
            return '';
        }
        $params = stream_context_get_params($s);
        fclose($s);
        $cert = $params['options']['ssl']['peer_certificate'] ?? null;
        if (!$cert) {
            return '';
        }
        $fp = openssl_x509_fingerprint($cert, 'sha256');
        return $fp ? strtoupper(implode(':', str_split($fp, 2))) : '';
    }
}
