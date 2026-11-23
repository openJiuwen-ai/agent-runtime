"""Run the local Manager stack under this macOS login session's launchd."""
import argparse
import hashlib
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
STATE = ROOT / '.manager-dev'
LABEL = 'com.openjiuwen.manager-local.' + hashlib.sha256(str(ROOT).encode()).hexdigest()[:10]
DOMAIN = f'gui/{os.getuid()}'
SERVICE = f'{DOMAIN}/{LABEL}'
PLIST = STATE / 'local.launchd.plist'


def run(*args):
    return subprocess.run(['launchctl', *args], capture_output=True, text=True)


def loaded():
    return run('print', SERVICE).returncode == 0


def status():
    result = run('print', SERVICE)
    if result.returncode:
        print('Local Manager background service is not loaded.')
        return 1
    print(f'Service: {SERVICE}')
    for line in result.stdout.splitlines():
        if line.strip().startswith(('state =', 'pid =', 'last exit code =', 'runs =')):
            print(line.strip())
    print(f'Logs: {STATE}/{{local-service,web,manager,identity}}.log')
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'stop', 'restart', 'status'])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--offline', action='store_true', help='Use local offline development (default).')
    mode.add_argument('--live', action='store_true', help='Enable real component checks and configuration delivery.')
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('This helper requires macOS. Use local.sh on other systems.')
    if args.action == 'status':
        return status()
    if args.action == 'stop':
        if loaded():
            result = run('bootout', SERVICE)
            if result.returncode:
                raise SystemExit(result.stderr.strip())
        print('Local Manager background service stopped. Saved data is retained.')
        return 0
    if loaded():
        if args.action == 'start':
            print('Already loaded; use restart to change development mode.')
            return status()
        result = run('bootout', SERVICE)
        if result.returncode:
            raise SystemExit(result.stderr.strip())
    node = shutil.which('node')
    if not node or not (ROOT / '.venv-manager/bin/python').exists():
        parser.error('Install Node and .venv-manager first; see dev/README.md.')
    STATE.mkdir(exist_ok=True)
    config = {
        'Label': LABEL,
        'ProgramArguments': ['/bin/bash', str(ROOT / 'applications/manager/dev/local.sh'), '--live' if args.live else '--offline'],
        'WorkingDirectory': str(ROOT),
        'EnvironmentVariables': {'PATH': str(Path(node).parent) + os.pathsep + os.environ.get('PATH', '/usr/bin:/bin')},
        'RunAtLoad': True,
        'KeepAlive': True,
        'ThrottleInterval': 10,
        'ExitTimeOut': 10,
        'StandardOutPath': str(STATE / 'local-service.log'),
        'StandardErrorPath': str(STATE / 'local-service.log'),
    }
    with PLIST.open('wb') as file:
        plistlib.dump(config, file)
    result = run('bootstrap', DOMAIN, str(PLIST))
    if result.returncode:
        raise SystemExit(result.stderr.strip())
    print('Background service loaded; startup and health checks may take a few seconds.')
    print('Manager: http://127.0.0.1:5273/manager')
    print('Mode: ' + ('live' if args.live else 'offline'))
    return status()


if __name__ == '__main__':
    raise SystemExit(main())
