"""Tests for `farm.py guide` and the help examples in farm/cli.py. They keep farm/guide.md and the parser in step."""

import argparse
import io
import re
import unittest
from contextlib import redirect_stdout
from unittest import mock

from farm import board as fb
from farm import cli


def read_guide():
    with open(cli.GUIDE_PATH) as f:
        return f.read()


def subcommands():
    """The parser's commands, by name."""
    for action in cli.build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    raise AssertionError("build_parser() has no commands")


def names_command(text, name):
    """True when the text shows the command in backticks, alone or with its arguments: `show` or `show <item>`."""
    return re.search(r"`{}[` ]".format(re.escape(name)), text) is not None


def section(text, heading):
    """The guide text from a bold heading (for example **People only:**) to the next bold or ## heading."""
    start = text.index("**{}:**".format(heading))
    next_heading = re.compile(r"\n(\*\*|## )").search(text, start + 1)
    if next_heading is None:
        return text[start:]
    return text[start:next_heading.start()]


class GuideCommandTest(unittest.TestCase):
    def test_prints_the_guide_file(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["guide"]), 0)
        self.assertEqual(out.getvalue(), read_guide())

    def test_does_not_read_env(self):
        with mock.patch.object(cli, "project_env", side_effect=AssertionError("project_env called")) as project_env, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["guide"]), 0)
        project_env.assert_not_called()


class GuideTextTest(unittest.TestCase):
    def setUp(self):
        self.guide = read_guide()

    def test_names_every_command(self):
        for name in subcommands():
            self.assertTrue(names_command(self.guide, name), msg=name)

    def test_role_agents_only_list(self):
        text = section(self.guide, "Role agents only")
        for name in ("claim", "renew", "post", "doc", "summary", "release"):
            self.assertTrue(names_command(text, name), msg=name)

    def test_people_only_list(self):
        text = section(self.guide, "People only")
        for name in ("run", "stop", "chat"):
            self.assertTrue(names_command(text, name), msg=name)

    def test_names_every_stage_and_farm_status(self):
        for stage in fb.STAGES:
            self.assertIn("`{}`".format(stage), self.guide, msg=stage)
        for label in fb.FARM_STATUSES:
            self.assertIn("| {} |".format(label), self.guide, msg=label)


class HelpExamplesTest(unittest.TestCase):
    def test_every_command_has_an_example(self):
        for name, command in subcommands().items():
            text = command.format_help()
            self.assertIn("Example:", text, msg=name)
            self.assertIn("farm.py " + name, text, msg=name)

    def test_top_level_help_names_guide(self):
        self.assertIn("farm.py guide", cli.build_parser().format_help())


if __name__ == "__main__":
    unittest.main()
