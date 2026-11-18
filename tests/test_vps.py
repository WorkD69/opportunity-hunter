"""Operator SSH: private address only, one connection check first, stop with the failing stage."""
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import vps  # noqa: E402


def resolver(*addresses):
    return lambda host, port, proto=0: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (a, port)) for a in addresses]


class PrivateSshTests(unittest.TestCase):
    def settings(self, text):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / 'local.settings.ps1'
        path.write_text(text, encoding='utf-8')
        patcher = patch.object(vps, 'SETTINGS', path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_only_private_addresses_count_as_private(self):
        for address in ('100.64.0.10', '10.0.0.5', '192.168.1.2', '172.16.0.9', 'fd7a:115c:a1e0::1'):
            self.assertTrue(vps.is_private_host('h', resolver(address)), address)
        for address in ('203.0.113.7', '8.8.8.8', '127.0.0.1', '0.0.0.0'):
            self.assertFalse(vps.is_private_host('h', resolver(address)), address)
        self.assertFalse(vps.is_private_host('h', resolver('100.64.0.10', '203.0.113.7')))  # mixed → refused

    def test_public_address_is_refused_and_missing_private_setting_stops(self):
        self.settings("$HermesServer = 'root@203.0.113.7'\n$HermesIdentityFile = 'C:/key'\n")
        with self.assertRaisesRegex(SystemExit, 'PRIVATE_SSH_NOT_CONFIGURED'):
            vps.ssh_args()  # the public $HermesServer is never used as a fallback
        self.settings("$HermesPrivateServer = 'root@203.0.113.7'\n$HermesIdentityFile = 'C:/key'\n")
        with self.assertRaisesRegex(SystemExit, 'PRIVATE_SSH_REFUSED'):
            vps.ssh_args()

    def test_private_address_with_pinned_host_key(self):
        self.settings("$HermesPrivateServer = 'root@100.64.0.10'\n$HermesIdentityFile = 'C:/key'\n"
                      "$HermesHostKeyAlias = 'pinned-server'\n")
        args = vps.ssh_args()
        self.assertEqual(args[-1], 'root@100.64.0.10')
        self.assertIn('HostKeyAlias=pinned-server', args)
        self.assertIn('StrictHostKeyChecking=yes', args)

    def test_preflight_reports_the_failing_stage_and_run_stops_before_the_command(self):
        self.settings("$HermesPrivateServer = 'root@100.64.0.10'\n$HermesIdentityFile = 'C:/key'\n")
        cases = {'ssh: connect to host 100.64.0.10 port 22: Connection timed out': 'network',
                 'root@100.64.0.10: Permission denied (publickey).': 'login',
                 'Host key verification failed.': 'host key'}
        for stderr, stage in cases.items():
            calls = []

            def fake_run(cmd, **kwargs):
                calls.append(cmd)
                return SimpleNamespace(returncode=255, stdout='', stderr=stderr)
            with patch.object(vps.subprocess, 'run', fake_run):
                with self.assertRaises(SystemExit) as raised:
                    vps.run('print(1)')
            self.assertIn('at ' + stage, str(raised.exception))
            self.assertNotIn('100.64.0.10', str(raised.exception))  # addresses are redacted
            self.assertEqual(len(calls), 1)  # only the check ran: no remote command, no retry

    def test_preflight_timeout_and_success(self):
        self.settings("$HermesPrivateServer = 'root@100.64.0.10'\n$HermesIdentityFile = 'C:/key'\n")
        def timeout(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 40)
        with patch.object(vps.subprocess, 'run', timeout):
            with self.assertRaisesRegex(SystemExit, 'at network'):
                vps.preflight()
        with patch.object(vps.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='OH_PRIVATE_SSH_OK\n', stderr='')):
            vps.preflight()


if __name__ == '__main__':
    unittest.main()
