"""Run an operator Python file over existing pinned SSH access. No credentials logged.

Host-specific values (server, SSH identity, host root, bot username, protected
neighbour service) live only in the operator's private, git-ignored settings
file and are passed to the remote script as environment variables.
"""
import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess

SETTINGS = Path(os.environ.get('OH_SETTINGS_FILE') or
                Path.home() / 'Projects/hermes-platform/scripts/local.settings.ps1')
ENV_NAME = re.compile(r'[A-Z][A-Z0-9_]{0,63}')


def setting(name):
    """Return a single-quoted or double-quoted `$Name = '...'` value, or ''."""
    text = SETTINGS.read_text(encoding='utf-8-sig')
    match = re.search(r'\$' + re.escape(name) + r'''\s*=\s*['"]([^'"]+)['"]''', text)
    return match[1] if match else ''


def ssh_args():
    server = setting('HermesServer')
    identity = setting('HermesIdentityFile').replace('$env:USERPROFILE', str(Path.home()))
    if not server or not identity:
        raise SystemExit('HermesServer/HermesIdentityFile missing in private settings')
    return ['ssh', '-i', identity, '-o', 'BatchMode=yes', '-o',
            'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15', server]


def protected_service():
    """Optional neighbour systemd unit name from the private settings."""
    value = setting('HermesProtectedService')
    return value if re.fullmatch(r'[A-Za-z0-9@._-]+', value or '') else ''


def bot_username():
    """Expected Telegram bot username of the opportunity-hunter profile."""
    value = setting('OpportunityBotUsername').lstrip('@')
    if not re.fullmatch(r'[A-Za-z0-9_]{5,32}', value):
        raise SystemExit('OpportunityBotUsername missing or invalid in private settings')
    return value


def remote_env(extra=None):
    env = {'OH_PROTECTED_SERVICE': protected_service(),
           'OH_HOST_ROOT': setting('HermesHostRoot'),
           'OH_BOT_USERNAME': setting('OpportunityBotUsername').lstrip('@')}
    env.update(extra or {})
    for key in env:
        if not ENV_NAME.fullmatch(key):
            raise SystemExit('Invalid environment variable name: ' + key)
    return {key: value for key, value in env.items() if value}


def run(source, container=False, extra_env=None):
    env = remote_env(extra_env)
    if container:
        flags = ''.join(f'-e {key}={shlex.quote(value)} ' for key, value in env.items())
        command = (f'docker exec -i {flags}-u hermes -w /opt/hermes hermes-agent '
                   '/opt/hermes/.venv/bin/python -')
    else:
        prefix = ''.join(f'{key}={shlex.quote(value)} ' for key, value in env.items())
        command = prefix + 'python3 -'
    return subprocess.run(ssh_args() + [command], input=source.encode('utf-8'), check=False)


def parse_env(pairs):
    result = {}
    for pair in pairs or []:
        key, sep, value = pair.partition('=')
        if not sep:
            raise SystemExit('--env expects KEY=VALUE')
        if re.match(r'^[A-Za-z]:[\\/]', value):
            # Git Bash rewrites "KEY=/opt/..." into a Windows path before Python sees it.
            raise SystemExit(f'{key} looks like a local Windows path; in Git Bash run with MSYS_NO_PATHCONV=1')
        result[key] = value
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path)
    parser.add_argument('--container', action='store_true')
    parser.add_argument('--env', action='append', metavar='KEY=VALUE',
                        help='extra variable for the remote script, e.g. OH_BACKUP_DIR=<path>')
    args = parser.parse_args()
    raise SystemExit(run(args.file.read_text(encoding='utf-8-sig'), args.container,
                         parse_env(args.env)).returncode)
