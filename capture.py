"""Compatibility entry point for old shortcuts. Launch the installed Codex directly."""

import os
import subprocess
import sys

from runtime_paths import codex_binary


def run(binary, args):
    command = [str(binary), *args]
    if os.name == "nt":
        return subprocess.call(command)
    os.execv(binary, command)


if __name__ == "__main__":
    try:
        sys.exit(run(codex_binary(), sys.argv[1:]))
    except (OSError, RuntimeError) as error:
        sys.exit(str(error))
