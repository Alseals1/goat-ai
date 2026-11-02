#!/usr/bin/env python3
"""G.O.A.T. AI CLI. See goat/cli.py, or run: python3 goat.py --help"""

import os
import sys

# realpath: install.sh links ~/.goat-ai/goat.py to this file, and G.O.A.T. package is next to the real file.
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from goat.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
