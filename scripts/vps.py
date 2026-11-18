"""Run an operator Python file over PRIVATE, pinned SSH access only. No credentials logged.

Host-specific values (private server address, SSH identity, pinned host-key alias, host root,
bot username, protected neighbour service) live only in the operator's private, git-ignored
settings file and are passed to the remote script as environment variables.

Rule (see CLAUDE.md): the public server address is never used. Every remote operation first
makes ONE connection check; on failure it stops and reports the stage — no retries, no loops.
"""
import argparse
import ipaddress
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess

SETTINGS = Path(os.environ.get('OH_SETTINGS_FILE') or
                Path.home() / 'Projects/hermes-platform/scripts/local.settings.ps1')
ENV_NAME = re.compile(r'[A-Z][A-Z0-9_]{0,63}')


def setting(name):
    """Return a single-quoted or double-quoted `$Name = '...'` value, or ''."""
    text = SETTINGS.read_text(encoding='utf-8-sig')
    match = re.search(r'\$' + re.escape(name) + r'''\s*=\s*['"]([^'"]+)['"]''', text)
    return match[1] if match else ''


# Explicit allowlist: RFC 1918, Tailscale (CGNAT 100.64/10) and IPv6 ULA. ipaddress.is_private is too
# broad (it also covers documentation and other special ranges that are not a private network).
PRIVATE_NETS = [ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',
                                                    '100.64.0.0/10', 'fc00::/7')]


def is_private_host(host, resolve=socket.getaddrinfo):
    """True only if EVERY address the host resolves to is private (RFC 1918, Tailscale, IPv6 ULA)."""
    try:
        infos = resolve(host, 22, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return False
    addresses = set()
    for info in infos:
        try:
            addresses.add(ipaddress.ip_address(info[4][0].split('%')[0]))
        except ValueError:
            return False
    return bool(addresses) and all(any(a.version == n.version and a in n for n in PRIVATE_NETS) for a in addresses)


def ssh_args():
    """SSH command for the PRIVATE server address. The public address is refused by design."""
    server = setting('HermesPrivateServer')
    identity = setting('HermesIdentityFile').replace('$env:USERPROFILE', str(Path.home()))
    if not server or not identity:
        raise SystemExit('PRIVATE_SSH_NOT_CONFIGURED: set $HermesPrivateServer (user@private-address) and '
                         '$HermesIdentityFile in the private settings; deploys over the public address are disabled')
    user, _, host = server.rpartition('@')
    if not user or not host or not is_private_host(host):
        raise SystemExit('PRIVATE_SSH_REFUSED: $HermesPrivateServer must resolve only to private addresses')
    args = ['ssh', '-i', identity, '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', '-o',
            'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15']
    alias = setting('HermesHostKeyAlias')  # known_hosts name of the server's pinned key (verifies it is OUR server)
    if alias:
        args += ['-o', 'HostKeyAlias=' + alias]
    return args + [server]


def _redact(text):
    return re.sub(r'\b\d{1,3}(?:\.\d{1,3}){3}\b', 'x.x.x.x', text)


def preflight():
    """One private connection check before any remote work. Stops with the failing stage; never retries."""
    try:
        result = subprocess.run(ssh_args() + ['echo OH_PRIVATE_SSH_OK'], capture_output=True, text=True, timeout=40)
    except subprocess.TimeoutExpired:
        raise SystemExit('PRIVATE_SSH_FAILED at network: no answer within 40 s') from None
    if result.returncode == 0 and 'OH_PRIVATE_SSH_OK' in result.stdout:
        return
    error = result.stderr or ''
    if 'Host key verification failed' in error or 'IDENTIFICATION HAS CHANGED' in error:
        stage = 'host key (not the pinned server key)'
    elif 'Permission denied (' in error:
        stage = 'login (key rejected)'
    elif any(s in error for s in ('timed out', 'Connection refused', 'No route', 'connect to host', 'Could not resolve')):
        stage = 'network (port 22 unreachable over the private address)'
    else:
        stage = 'unknown'
    last = _redact(error.strip().splitlines()[-1] if error.strip() else 'no output')[:200]
    raise SystemExit(f'PRIVATE_SSH_FAILED at {stage}: {last}')


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
    preflight()  # check the private connection first; stop here if it does not work
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
    parser.add_argument('file', type=Path, nargs='?')
    parser.add_argument('--check', action='store_true', help='only check the private SSH connection')
    parser.add_argument('--container', action='store_true')
    parser.add_argument('--env', action='append', metavar='KEY=VALUE',
                        help='extra variable for the remote script, e.g. OH_BACKUP_DIR=<path>')
    args = parser.parse_args()
    if args.check:
        preflight()
        print('PRIVATE_SSH_OK')
        raise SystemExit(0)
    if args.file is None:
        parser.error('file is required unless --check is given')
    raise SystemExit(run(args.file.read_text(encoding='utf-8-sig'), args.container,
                         parse_env(args.env)).returncode)
