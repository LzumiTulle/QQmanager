"""Inspect the pinned archive and prepare a project-local Docker deployment.

This script does not load or start containers. Credentials stay under data/.
"""
import hashlib
import json
from pathlib import Path
import secrets
import sys
import tarfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from qqmanager.store import Store
from scripts.download_napcat_archive import SHA256, SIZE


def read_member(archive, name, limit=1024 * 1024):
    member = archive.getmember(name)
    if not member.isfile() or member.size > limit:
        raise ValueError('异常归档元数据')
    return archive.extractfile(member).read()


def inspect_image(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''): digest.update(block)
    if path.stat().st_size != SIZE or digest.hexdigest() != SHA256:
        raise ValueError('大小或 SHA-256 不匹配，未准备部署')
    with tarfile.open(path) as archive:
        manifest = json.loads(read_member(archive, 'manifest.json'))
        if len(manifest) != 1:
            raise ValueError('归档包含多个镜像，需单独审查')
        config = json.loads(read_member(archive, manifest[0]['Config']))
        if config.get('architecture') != 'arm64' or config.get('os') != 'linux':
            raise ValueError('镜像不是 Linux ARM64')
        image_id = 'sha256:' + hashlib.sha256(read_member(archive, manifest[0]['Config'])).hexdigest()
        return {'image': image_id, 'tags': manifest[0].get('RepoTags', []),
                'architecture': config['architecture'], 'os': config['os'],
                'entrypoint': config.get('config', {}).get('Entrypoint'),
                'command': config.get('config', {}).get('Cmd'), 'archive_sha256': SHA256}


def main():
    path = ROOT / 'data/napcat-install/napcat-arm64.tar'
    info = inspect_image(path)
    base = ROOT / 'data/napcat'
    for directory in (base, base / 'config', base / 'qq'):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    secret_file = base / 'credentials.json'
    if secret_file.exists():
        credentials = json.loads(secret_file.read_text())
    else:
        credentials = {'webui_token': secrets.token_urlsafe(32), 'onebot_token': secrets.token_urlsafe(32)}
        secret_file.write_text(json.dumps(credentials)); secret_file.chmod(0o600)
    network = {'network': {'httpServers': [], 'httpClients': [], 'httpSseServers': [], 'websocketClients': [],
                           'websocketServers': [{'name': 'qqmanager', 'enable': True, 'host': '0.0.0.0',
                                                 'port': 3001, 'messagePostFormat': 'array', 'reportSelfMessage': True,
                                                 'token': credentials['onebot_token'], 'enableForcePushEvent': True,
                                                 'debug': False, 'heartInterval': 30000}]},
               'musicSignUrl': '', 'enableLocalFile2Url': False, 'parseMultMsg': False, 'imageDownloadProxy': ''}
    # NapCat's default OneBot configuration is copied to the account-specific file on first login.
    config_path = base / 'config' / 'onebot11.json'
    config_path.write_text(json.dumps(network, ensure_ascii=False, indent=2)); config_path.chmod(0o600)
    webui_path = base / 'config' / 'webui.json'
    if not webui_path.exists():
        webui_path.write_text(json.dumps({'host': '0.0.0.0', 'port': 6099, 'prefix': '',
                                         'token': credentials['webui_token'], 'loginRate': 3}))
        webui_path.chmod(0o600)
    # NapCat receives protocol events from all joined groups before our allowlist.
    # Avoid retaining their message bodies in NapCat's normal informational logs.
    log_paths = list((base / 'config').glob('napcat*.json'))
    if not log_paths:
        log_paths = [base / 'config' / 'napcat.json']
    for log_path in log_paths:
        log_config = json.loads(log_path.read_text()) if log_path.exists() else {}
        if log_path.name == 'napcat.json' or 'consoleLog' in log_config:
            log_config.update(fileLog=False, consoleLog=True, consoleLogLevel='warn')
            log_path.write_text(json.dumps(log_config, indent=2)); log_path.chmod(0o600)
    compose = {'name': 'qqmanager', 'services': {'napcat': {
        # Docker's containerd store exposes the manifest ID, while classic Docker
        # exposes the config ID. Use the exact archive tag with pulling disabled.
        'image': info['tags'][0], 'pull_policy': 'never', 'platform': 'linux/arm64',
        'container_name': 'qqmanager-napcat', 'restart': 'unless-stopped',
        'hostname': 'qqmanager-napcat',
        'mac_address': '02:51:51:6d:67:01',
        'entrypoint': ['bash', '/qqmanager-entrypoint.sh'],
        'cap_drop': ['ALL'], 'cap_add': ['CHOWN', 'SETUID', 'SETGID', 'DAC_OVERRIDE'],
        'security_opt': ['no-new-privileges:true'],
        'environment': {'MODE': 'ws', 'WEBUI_TOKEN': credentials['webui_token'], 'NAPCAT_UID': '1000', 'NAPCAT_GID': '1000'},
        'ports': ['127.0.0.1:6099:6099', '127.0.0.1:3001:3001'],
        'volumes': [str(base / 'qq') + ':/app/.config/QQ', str(base / 'config') + ':/app/napcat/config',
                    str(ROOT / 'scripts/napcat-entrypoint.sh') + ':/qqmanager-entrypoint.sh:ro'],
        'ulimits': {'core': 0}, 'logging': {'driver': 'json-file', 'options': {'max-size': '10m', 'max-file': '3'}}
    }}}
    compose_path = base / 'compose.json'
    if compose_path.exists():
        previous = json.loads(compose_path.read_text())
        account = previous.get('services', {}).get('napcat', {}).get('environment', {}).get('ACCOUNT')
        if account:
            compose['services']['napcat']['environment']['ACCOUNT'] = account
    compose_path.write_text(json.dumps(compose, indent=2)); compose_path.chmod(0o600)
    (ROOT / 'data/napcat-install/inspection.json').write_text(json.dumps(info, indent=2))
    store = Store(ROOT / 'data')
    store.set_setting('ws_url', 'ws://127.0.0.1:3001')
    store.set_setting('ws_token', credentials['onebot_token'])
    store.set_setting('ws_enabled', True)
    store.db.close()
    print(json.dumps(info, indent=2))
    print('已准备本机配置；本脚本不导入或启动镜像。群选择保持原状。')


if __name__ == '__main__': main()
