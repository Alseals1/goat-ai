"""Tests that keep the Research agent's skill, protocol section, guide and team entry in step."""

import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from goat import board as fb
from goat import cli, team
from goat import discord as dc
from goat.config import REPO_ROOT, load_shared

SKILL = os.path.join(REPO_ROOT, "skills", "goat-research", "SKILL.md")


def read(path):
    with open(path) as f:
        return f.read()


class ResearchAgentTests(unittest.TestCase):
    def test_skill_runs_on_sonnet_and_reads_its_protocol_sections(self):
        text = read(SKILL)
        self.assertIn("name: goat-research\n", text)
        self.assertIn("model: sonnet\n", text)
        self.assertIn("goat.py protocol --sections 1,10,11,18", text)

    def test_skill_talks_to_the_project_manager_and_files_with_the_marker(self):
        text = read(SKILL)
        self.assertIn("Talk it over with the project manager", text)
        self.assertIn("goat-pm/SKILL.md", text)
        # The template's first body line is exactly the marker the autopilot checks.
        self.assertIn("--file - <<'EOF'\n   {}\n   Depends on:".format(fb.RESEARCH_MARKER), text)
        self.assertIn("Never pass `--priority`", text)

    def test_protocol_section_18_holds_the_rules(self):
        section = cli.protocol_sections(read(cli.PROTOCOL_PATH), [18])
        self.assertIn("## 18. Research agent", section)
        for rule in ("No claim, no lease, no release", "The project's rules win", "Public pages only",
                     "never with `--ready`"):
            self.assertIn(rule, section)

    def test_guide_and_team_know_the_role(self):
        self.assertIn("**Research agent only:**", read(cli.GUIDE_PATH))
        shared = load_shared()
        self.assertEqual(team.persona(shared, "research")["name"], "Rae")
        self.assertTrue(any(line.startswith("Research agent: Rae") for line in team.lines(shared, {}, None)))


if __name__ == "__main__":
    unittest.main()


class ResearchSayTests(unittest.TestCase):
    URL = "https://discord.com/api/webhooks/123456/abcDEF-secret"

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.env_file = os.path.join(directory.name, "discord.env")
        self.sent = []
        for patcher in (mock.patch.object(dc, "ENV_FILE", self.env_file),
                        mock.patch.object(dc.Webhook, "send",
                                          lambda hook, username, content, embed=None: self.sent.append((username, content)))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_cli(self, role, text):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(cli.sys, "stdin", io.StringIO(text)):
            code = cli.main(["research-say", "--as", role, "--file", "-"])
        return code, out.getvalue(), err.getvalue()

    def configure(self, key="DISCORD_WEBHOOK_RESEARCH"):
        with open(self.env_file, "w") as f:
            f.write("{}={}\n".format(key, self.URL))

    def test_posts_as_rae_or_morgan(self):
        self.configure()
        self.assertEqual(self.run_cli("research", "Five ideas for phase 3.")[0], 0)
        self.run_cli("pm", "Two fit now.")
        self.assertEqual(self.sent, [("Rae · Research agent", "Five ideas for phase 3."),
                                     ("Morgan · Project manager agent", "Two fit now.")])

    def test_without_the_research_webhook_it_posts_nothing_and_succeeds(self):
        self.configure("DISCORD_WEBHOOK_DISPATCH")
        code, out, _ = self.run_cli("research", "Hello")
        self.assertEqual((code, self.sent), (0, []))
        self.assertIn("nothing posted", out)
        self.assertNotIn("abcDEF", out)

    def test_long_turns_are_split_at_paragraphs(self):
        self.configure()
        paragraphs = ["Paragraph {} ".format(n) + "x" * 600 for n in range(5)]
        self.run_cli("research", "\n\n".join(paragraphs))
        self.assertGreater(len(self.sent), 1)
        self.assertTrue(all(len(content) <= dc.MAX_CONTENT for _, content in self.sent))
        self.assertTrue(all(content.startswith("Paragraph") for _, content in self.sent))
        self.assertEqual("".join(content for _, content in self.sent).count("x"), 3000)

    def test_an_empty_turn_is_refused(self):
        self.configure()
        self.assertNotEqual(self.run_cli("research", "  \n")[0], 0)
        self.assertEqual(self.sent, [])

    def test_split_message_without_breaks(self):
        parts = dc.split_message("y" * 4000)
        self.assertEqual([len(part) for part in parts], [dc.MAX_CONTENT, dc.MAX_CONTENT, 200])
