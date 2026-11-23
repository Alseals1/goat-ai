"""Tests for G.O.A.T.

Safety net: no test may read the developer's real Discord settings or send a real message. The env file path is
pointed at an empty temporary folder for the whole test run, so a webhook is only ever set by a test on purpose.
The pause switch is pointed there too, so a paused coordinator on the developer's machine cannot change a test.
"""

import os
import tempfile

from goat import discord as _discord
from goat import pause as _pause

_DISCORD_TEST_DIR = tempfile.mkdtemp(prefix="goat-tests-")
_discord.ENV_FILE = os.path.join(_DISCORD_TEST_DIR, "discord.env")
_pause.PAUSE_FILE = os.path.join(_DISCORD_TEST_DIR, "paused.json")
