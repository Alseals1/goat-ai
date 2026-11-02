"""Tests for the coordinator config that scripts/install.sh writes.

Each test runs the real script with HOME set to a temporary folder, so it never touches the developer's
~/.claude or ~/.software-farm.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from farm import coordinator
from farm.config import REPO_ROOT

INSTALL_SCRIPT = os.path.join(REPO_ROOT, "scripts", "install.sh")
DEFAULT_CAPS = {"intake": 2, "plan": 2, "implement": 2, "review": 2, "retro": 1}


@unittest.skipUnless(shutil.which("bash"), "bash is not installed")
class InstallConfigTests(unittest.TestCase):
    def setUp(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.home = home.name
        self.config_file = os.path.join(self.home, ".software-farm", "config.json")

    def install(self):
        """Runs the install script. Returns its output (stdout and stderr)."""
        env = dict(os.environ, HOME=self.home)
        result = subprocess.run(["bash", INSTALL_SCRIPT], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout + result.stderr

    def write_config(self, text):
        os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
        with open(self.config_file, "w") as f:
            f.write(text)

    def read_text(self):
        with open(self.config_file) as f:
            return f.read()

    def test_new_config_has_the_default_limits(self):
        self.install()
        config = json.loads(self.read_text())
        self.assertEqual(config["coordinator"], {"maxAgents": 3, "intervalSeconds": 900, "maxAgentsByRole": DEFAULT_CAPS})

    def test_second_run_does_not_change_a_new_config(self):
        self.install()
        before = self.read_text()
        output = self.install()
        self.assertEqual(self.read_text(), before)
        self.assertIn("Kept existing config", output)

    def test_adds_default_caps_to_an_older_config(self):
        old = {"repos": {"org/repo": "/code/repo"}, "coordinator": {"maxAgents": 5, "maxAgentsPerRepo": 1}}
        self.write_config(json.dumps(old))
        output = self.install()
        self.assertIn("Added default maxAgentsByRole to " + self.config_file, output)
        config = json.loads(self.read_text())
        self.assertEqual(config["repos"], old["repos"])
        self.assertEqual(config["coordinator"], dict(old["coordinator"], maxAgentsByRole=DEFAULT_CAPS))

    def test_keeps_a_config_that_has_role_caps(self):
        text = '{"repos": {}, "coordinator": {"maxAgentsByRole": {"implement": 1}}}'
        self.write_config(text)
        output = self.install()
        self.assertEqual(self.read_text(), text)
        self.assertIn("Kept existing config", output)

    def test_leaves_a_broken_config_as_it_is(self):
        self.write_config("{ not json")
        output = self.install()
        self.assertEqual(self.read_text(), "{ not json")
        self.assertIn("not valid JSON", output)

    def test_written_config_passes_load_config(self):
        self.install()
        config = json.loads(self.read_text())
        config["repos"] = {"org/repo": "/code/repo"}
        self.write_config(json.dumps(config))
        with mock.patch.object(coordinator, "CONFIG_FILE", self.config_file):
            loaded = coordinator.load_config()
        self.assertEqual(loaded["coordinator"]["maxAgentsByRole"], DEFAULT_CAPS)


if __name__ == "__main__":
    unittest.main()
