#!/usr/bin/env python3
"""Software Farm CLI. See farm/cli.py, or run: python3 farm.py --help"""

import os
import sys

# realpath: install.sh links ~/.software-farm/farm.py to this file, and the farm package is next to the real file.
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from farm.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
