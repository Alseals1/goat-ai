"""Tests that keep the Research agent's skill, protocol section, guide and team entry in step."""

import os
import unittest

from goat import board as fb
from goat import cli, team
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
