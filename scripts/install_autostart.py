"""Install/remove the current user's QQmanager LaunchAgent. Run deliberately."""
import argparse
import os
import plistlib
import subprocess
import sys
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LABEL = 'local.qqmanager.service'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--remove', action='store_true')
    parser.add_argument('--with-orbstack', action='store_true', help='登录时也启动 OrbStack 容器引擎')
    args = parser.parse_args()
    if sys.platform != 'darwin':
        raise SystemExit('此脚本仅用于 macOS 登录启动。')
    agent_path = Path.home() / 'Library' / 'LaunchAgents' / (LABEL + '.plist')
    domain = 'gui/' + str(os.getuid())
    if args.remove:
        subprocess.run(['launchctl', 'bootout', domain + '/' + LABEL], check=False, capture_output=True)
        agent_path.unlink(missing_ok=True)
        engine_label = LABEL + '.engine'
        subprocess.run(['launchctl', 'bootout', domain + '/' + engine_label], check=False, capture_output=True)
        (agent_path.parent / (engine_label + '.plist')).unlink(missing_ok=True)
        print('已取消 QQmanager 登录启动；数据保留。')
        return
    logs = ROOT / 'data' / 'service'
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = {'Label': LABEL, 'ProgramArguments': [str(ROOT / '.venv/bin/python'), '-B', str(ROOT / 'run.py')],
              'WorkingDirectory': str(ROOT), 'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 10,
              'StandardOutPath': str(logs / 'stdout.log'), 'StandardErrorPath': str(logs / 'stderr.log')}
    agent_path.parent.mkdir(parents=True, exist_ok=True)
    agent_path.write_bytes(plistlib.dumps(config))
    agent_path.chmod(0o600)
    subprocess.run(['launchctl', 'bootout', domain + '/' + LABEL], check=False, capture_output=True)
    subprocess.run(['launchctl', 'bootstrap', domain, str(agent_path)], check=True)
    if args.with_orbstack:
        orbctl = shutil.which('orbctl')
        if not orbctl:
            raise SystemExit('QQmanager 已安装登录启动，但未找到 orbctl，容器引擎需手动启动。')
        engine_label = LABEL + '.engine'
        engine_path = agent_path.parent / (engine_label + '.plist')
        engine_config = {'Label': engine_label, 'ProgramArguments': [orbctl, 'start'],
                         'RunAtLoad': True, 'StandardOutPath': str(logs / 'engine.log'),
                         'StandardErrorPath': str(logs / 'engine-error.log')}
        engine_path.write_bytes(plistlib.dumps(engine_config)); engine_path.chmod(0o600)
        subprocess.run(['launchctl', 'bootout', domain + '/' + engine_label], check=False, capture_output=True)
        subprocess.run(['launchctl', 'bootstrap', domain, str(engine_path)], check=True)
        print('已配置登录时启动 OrbStack；NapCat 使用容器重启策略恢复。')
    print('QQmanager 已加入当前用户登录启动，并保持后台运行。')


if __name__ == '__main__': main()
