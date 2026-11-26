import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import capture
import manage


@unittest.skipUnless(os.name == "posix", "macOS launch paths use POSIX semantics")
class MacLauncherTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(Path, "home", return_value=self.home))
        self.enterContext(patch.object(sys, "platform", "darwin"))
        self.enterContext(patch.object(manage, "RUNTIME", self.home / "runtime"))
        self.plist = self.home / "Library" / "LaunchAgents" / f"{manage.LAUNCH_AGENT}.plist"
        self.wrapper = manage.APP / "codex-capture"
        self.login_cli = ""
        self.calls = []
        self.enterContext(patch.object(subprocess, "run", side_effect=self.launch_command))

    def launch_command(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if cmd[:2] == ["/bin/launchctl", "getenv"]:
            return subprocess.CompletedProcess(cmd, 0, self.login_cli)
        if cmd[:2] == ["/bin/launchctl", "unsetenv"]:
            self.login_cli = ""
        if cmd[:2] == ["/bin/launchctl", "setenv"]:
            self.fail("The collector must not register a macOS GUI executable")
        return subprocess.CompletedProcess(cmd, 0)

    def test_upgrade_after_removing_old_release(self):
        old = self.home / "previous installation"
        self.login_cli = str(old / "codex-capture")
        self.plist.parent.mkdir(parents=True)
        self.plist.write_bytes(plistlib.dumps({
            "Label": manage.LAUNCH_AGENT,
            "ProgramArguments": ["python3", str(old / "manage.py"), "run", "--register-capture"],
        }))
        with patch.object(manage, "stop"), patch.object(manage, "health", return_value={"pid": 12}):
            manage.enable_autostart()
        self.assertEqual(self.login_cli, "")
        job = plistlib.loads(self.plist.read_bytes())
        self.assertIn("run", job["ProgramArguments"])
        self.assertNotIn("--register-capture", job["ProgramArguments"])
        self.assertTrue(job["RunAtLoad"] and job["KeepAlive"])

    def test_owned_and_unrelated_launchers(self):
        other = self.home / "codex-capture"
        shutil.copyfile(self.wrapper, other)
        self.login_cli = str(other)
        manage.restore_mac_cli()
        self.assertEqual(self.login_cli, "")
        other.write_text("#!/bin/sh\n# user's own launcher\n")
        for value in ("", str(other), str(self.home / "bin" / "codex")):
            with self.subTest(value=value):
                self.login_cli = value
                manage.restore_mac_cli()
                self.assertEqual(self.login_cli, value)

    def test_open_from_stale_shell(self):
        self.login_cli = str(self.wrapper)
        app = self.home / "Codex.app"
        app.mkdir()
        env = {"CODEX_CLI_PATH": str(self.wrapper), "CODEX_MONITOR_MAC_APP": str(app)}
        with patch.dict(os.environ, env), patch.object(manage, "start"):
            manage.launch_codex()
        args, options = self.calls[-1]
        self.assertEqual(args[:2], ["/usr/bin/open", "-a"])
        self.assertEqual(len(args), 3)
        self.assertNotIn("CODEX_CLI_PATH", options["env"])
        self.assertEqual(self.login_cli, "")

    def test_legacy_login_job_can_still_start(self):
        self.login_cli = str(self.wrapper)
        args = ["manage.py", "run", "--register-capture", "--data-dir", str(manage.RUNTIME)]
        with (
            patch.object(sys, "argv", args), patch.object(os, "umask"),
            patch.object(manage, "stop"), patch("monitor.serve") as serve,
        ):
            manage.main()
        serve.assert_called_once()
        self.assertEqual(self.login_cli, "")

    def test_legacy_app_server_shortcut_replaces_its_process(self):
        binary = self.home / "codex"
        # A successful exec never returns to the Python process.
        with patch.object(os, "execv", side_effect=SystemExit) as execute:
            with self.assertRaises(SystemExit):
                capture.run(binary, ["app-server"], manage.RUNTIME)
        execute.assert_called_once_with(binary, [str(binary), "app-server"])
