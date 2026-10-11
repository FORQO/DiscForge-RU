"""Regression tests for DiscForge LAN-host bind and macOS unattended launch."""
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

from tv_server.private_bind import private_bind_ip

ROOT = Path(__file__).resolve().parents[1]


class HostBindTests(unittest.TestCase):
    def test_accepts_rfc1918_hosts(self):
        for ip in ("10.0.0.1", "10.255.255.254", "172.16.5.9",
                   "172.31.255.254", "192.168.50.204"):
            with self.subTest(ip=ip):
                self.assertEqual(private_bind_ip(ip), ip)

    def test_rejects_public_loopback_and_wildcard(self):
        for ip in ("", "0.0.0.0", "127.0.0.1", "8.8.8.8", "1.1.1.1",
                   "169.254.1.1", "100.64.0.1", "192.0.2.1", "::1",
                   "192.168.50.204:8098", "localhost", "192.168.50.999"):
            with self.subTest(ip=ip), self.assertRaises(ValueError):
                private_bind_ip(ip)

    def test_cli_errors(self):
        for ip, expected in (("192.168.50.204", 0), ("0.0.0.0", 2), ("8.8.8.8", 2)):
            proc = subprocess.run([sys.executable, str(ROOT / "tv_server/private_bind.py"), ip],
                                  capture_output=True, text=True, timeout=5)
            self.assertEqual(proc.returncode, expected)

    def test_compose_needs_lan_bind_for_both_ports(self):
        compose = (ROOT / "tv/docker/compose.yaml").read_text()
        variable = "${DISCFORGE_LAN_BIND_IP:?Specify NAS private LAN IPv4 in .env}"
        for port in ("8098:8098/tcp", "8099:8099/udp"):
            self.assertIn(f'"{variable}:{port}"', compose)
        self.assertIn("DISCFORGE_LAN_BIND_IP=", (ROOT / "tv/docker/.env.example").read_text())

    def test_mac_invalid_ip_does_not_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            media = Path(tmp) / "media"
            media.mkdir()
            env = dict(os.environ, HOME=str(home))
            script = ROOT / "tv/macos/install_launchagent.sh"
            for args in ((str(media),), (str(media), "8098", "0.0.0.0"),
                         (str(media), "8098", "8.8.8.8")):
                with self.subTest(args=args):
                    proc = subprocess.run(["bash", str(script), *args],
                                          env=env, text=True, capture_output=True, timeout=7)
                    self.assertEqual(proc.returncode, 2, proc.stderr)
                    self.assertFalse((home / "Library/LaunchAgents").exists())

    def test_mac_writes_private_ip_to_plist(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            media = Path(tmp) / "media"
            media.mkdir()
            bin_path = Path(tmp) / "bin"
            bin_path.mkdir()
            launchctl = bin_path / "launchctl"
            launchctl.write_text("#!/bin/sh\nexit 0\n")
            launchctl.chmod(0o755)
            env = dict(os.environ, HOME=str(home),
                       PATH=str(bin_path) + os.pathsep + os.environ["PATH"])
            script = ROOT / "tv/macos/install_launchagent.sh"
            proc = subprocess.run(["bash", str(script), str(media), "8098", "192.168.50.204"],
                                  env=env, text=True, capture_output=True, timeout=10)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            plist = home / "Library/LaunchAgents/ru.discforge.tv.server.plist"
            with plist.open("rb") as stream:
                args = plistlib.load(stream)["ProgramArguments"]
            self.assertEqual(args[args.index("--host") + 1], "192.168.50.204")
            self.assertNotIn("0.0.0.0", args)


if __name__ == "__main__":
    unittest.main()
