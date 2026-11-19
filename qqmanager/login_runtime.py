"""Prepare the application's own, loopback-only QQ login service."""
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

NAME = 'qqmanager-napcat'
IMAGES = {
    'amd64': ('6c1b1a2736588060f484ea273a190309bdeca32c15d6a9c61325d3499ee091fa',
              '039f78c89e67e0403d9cf11f7acda1ace7cf9564456c5db65ba67aa1f52fce7e'),
    'arm64': ('149fae81e2ccec10e5d1be2f9f86a1a2d6e1d5c70691f892dce00b7328f2ca72',
              '73111807d6256a23d4feafeef576356dbacee4a2db81830bba969ada4dd50bf7'),
}


def docker_cli():
    cli = shutil.which('docker') or next((str(p) for p in (
        Path('/usr/local/bin/docker'), Path('/Applications/Docker.app/Contents/Resources/bin/docker'),
        Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Docker/Docker/resources/bin/docker.exe'
    ) if p.is_file()), None)
    if not cli:
        raise ValueError('首次扫码需要本机容器引擎。请安装并启动 Docker Desktop 后重试。')
    return cli


def write_private(path, data):
    # Only create missing app-owned configuration. Preserve existing accounts and credentials.
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(data, f)


class LoginRuntime:
    def __init__(self, directory):
        self.base = Path(directory).resolve() / 'napcat'
        self.lock = threading.Lock()
        self.phase = 'idle'
        self.error = ''
        self.started = 0
        self.keys = {}

    def remember_account(self, account):
        """Give the owned QQ process the user's choice of an already authorized login."""
        path = self.base / 'config' / 'qqmanager-account'
        if not account:
            path.unlink(missing_ok=True)
            return
        if not (account.isascii() and account.isdigit() and 5<=len(account)<=20):
            raise ValueError('QQ 账号无效')
        path.parent.mkdir(exist_ok=True,parents=True,mode=0o700)
        with tempfile.NamedTemporaryFile(dir=path.parent,mode='w',delete=False) as f:
            f.write(account)
        os.replace(f.name,path)

    def prepare(self, retry=False):
        with self.lock:
            if self.phase == 'idle' or (retry and self.phase in ('failed', 'ready')):
                self.phase = 'starting'
                self.error = ''
                threading.Thread(target=self._worker, daemon=True).start()
            return self.phase, self.error

    def _worker(self):
        try:
            keys = self._start()
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            message = str(exc) if isinstance(exc, ValueError) else '登录组件启动失败，请检查本机容器引擎后重试。'
            with self.lock:
                self.phase, self.error = 'failed', message
            return
        with self.lock:
            self.keys = keys
            self.started = time.monotonic()
            self.phase = 'ready'

    def _start(self):
        cli = docker_cli()
        # Resolve the active local engine explicitly. Never mount user data on a remote engine.
        def run(args, timeout=25, env=None):
            return subprocess.run([cli, *args], capture_output=True, text=True, timeout=timeout,
                                  env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

        context = run(['context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'])
        host = context.stdout.strip()
        if context.returncode or not host.startswith(('unix:///', 'npipe:////./pipe/')):
            raise ValueError('请选择本机容器引擎，再重新连接。')

        def docker(*args, timeout=25, env=None):
            return run(['--host', host, *args], timeout, env)

        info = docker('info', '--format', '{{.OSType}}/{{.Architecture}}')
        if info.returncode:
            starter = (Path('/Applications/OrbStack.app/Contents/MacOS/bin/orbctl')
                       if 'orbstack' in host else None)
            if starter and starter.is_file():
                subprocess.run([str(starter), 'start'], capture_output=True, timeout=90)
            else:
                run(['desktop', 'start'], timeout=120)
            info = docker('info', '--format', '{{.OSType}}/{{.Architecture}}')
        if info.returncode:
            raise ValueError('本机容器引擎未就绪，请启动 Docker Desktop 或 OrbStack 后重试。')
        os_type, _, machine = info.stdout.strip().partition('/')
        arch = {'x86_64': 'amd64', 'aarch64': 'arm64'}.get(machine, machine)
        if os_type != 'linux' or arch not in IMAGES:
            raise ValueError('登录组件需要运行 Linux 容器的 x64 或 ARM64 引擎。')
        manifest, config = IMAGES[arch]
        expected_images = {'sha256:' + manifest, 'sha256:' + config}
        image = 'docker.io/mlikiowa/napcat-docker@sha256:' + manifest
        base = self.base
        for path in (base, base / 'config', base / 'qq'):
            path.mkdir(exist_ok=True, parents=True, mode=0o700)
        exists = docker('container', 'ls', '-a', '--filter', 'name=^/' + NAME + '$', '--format', '{{.ID}}')
        if exists.returncode:
            raise ValueError('无法检查本机登录组件，请重试。')
        credentials = base / 'credentials.json'
        if not exists.stdout.strip():
            write_private(credentials, {'webui_token': secrets.token_urlsafe(32),
                                        'onebot_token': secrets.token_urlsafe(32)})
        if not credentials.is_file():
            raise ValueError('已有同名登录组件未与当前数据目录配对。请在高级接入中连接。')
        keys = json.loads(credentials.read_text(encoding='utf-8'))
        if not all(isinstance(keys.get(k), str) and keys[k] for k in ('webui_token', 'onebot_token')):
            raise ValueError('本机登录配置不完整，请检查应用数据目录。')
        entry = Path(__file__).resolve().parents[1] / 'scripts/napcat-entrypoint.sh'
        script = base / 'entrypoint.sh'
        startup = entry.read_bytes().replace(b'\r\n', b'\n')
        if not exists.stdout.strip():
            write_private(base / 'config/webui.json', {'host': '0.0.0.0', 'port': 6099, 'token': keys['webui_token']})
            write_private(base / 'config/onebot11.json', {'network': {'websocketServers': [{
                'name': 'qqmanager', 'enable': True, 'host': '0.0.0.0', 'port': 3001,
                'messagePostFormat': 'array', 'token': keys['onebot_token'], 'heartInterval': 30000
            }]}})
            write_private(base / 'config/napcat.json', {'fileLog': False, 'consoleLog': True, 'consoleLogLevel': 'warn'})
            # Reuse an already verified local image even when the archive was loaded under a mirror tag.
            for candidate in (manifest, config):
                known = docker('image', 'inspect', '--format', '{{.Id}}', 'sha256:' + candidate)
                if not known.returncode and known.stdout.strip() in expected_images:
                    image = known.stdout.strip()
                    break
            script.write_bytes(startup)
            result = docker('create', '--name', NAME, '--restart', 'unless-stopped',
                            '--cap-drop', 'ALL', '--cap-add', 'CHOWN', '--cap-add', 'SETUID',
                            '--cap-add', 'SETGID', '--cap-add', 'DAC_OVERRIDE', '--security-opt', 'no-new-privileges',
                            '-p', '127.0.0.1:6099:6099', '-p', '127.0.0.1:3001:3001',
                            '-v', str(base / 'qq') + ':/app/.config/QQ',
                            '-v', str(base / 'config') + ':/app/napcat/config',
                            '-v', str(script) + ':/qqmanager-entrypoint.sh:ro', '--entrypoint', 'bash',
                            image, '/qqmanager-entrypoint.sh', timeout=600)
            if result.returncode:
                raise ValueError('首次准备登录组件失败。请检查磁盘空间和容器镜像下载连接后重试。')
        result = docker('container', 'inspect', NAME)
        if result.returncode:
            raise ValueError('无法读取登录组件。')
        meta = json.loads(result.stdout)[0]
        ports = meta['HostConfig'].get('PortBindings') or {}
        mounts = {m['Destination']: m['Source'] for m in meta['Mounts']}
        def same_mount(dest, source):
            actual = mounts.get(dest, '').replace('\\', '/')
            expected = str(source).replace('\\', '/')
            if os.name == 'nt' and ':' in expected:
                drive, rest = expected.split(':', 1)
                return actual.casefold() in {p.casefold() for p in (
                    expected, '/run/desktop/mnt/host/' + drive + rest, '/host_mnt/' + drive + rest)}
            return actual == expected or actual == '/host_mnt' + expected
        if (meta['Image'] not in expected_images or not same_mount('/app/napcat/config', base / 'config')
                or not same_mount('/app/.config/QQ', base / 'qq')
                or any(ports.get(p + '/tcp') != [{'HostIp': '127.0.0.1', 'HostPort': p}] for p in ('3001','6099'))):
            raise ValueError('已有登录组件与此应用的本机配置不匹配。请检查高级接入。')
        if same_mount('/qqmanager-entrypoint.sh', script) and script.read_bytes() != startup:
            # Bind mounts retain the inode of the original script.
            script.write_bytes(startup)
        if not meta['State']['Running'] and docker('start', meta['Id'], timeout=90).returncode:
            raise ValueError('登录组件启动失败，请检查本机端口占用后重试。')
        return keys
