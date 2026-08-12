"""PyInstaller entry point: opens the window when launched with no arguments."""

import multiprocessing
import sys

from workspace_checker.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
